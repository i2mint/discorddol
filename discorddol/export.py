"""Export a whole guild to disk: every channel the bot can read, one JSONL file each.

:func:`export_guild` walks a guild's channel listing and writes::

    <out_dir>/
        manifest.json                 what was exported, what was skipped, and why
        messages/<channel_id>.jsonl   one message dict per line, oldest first

What gets exported, by channel kind (see :data:`discorddol.base.CHANNEL_KINDS`):

- **text** and **announcement** channels: their own history, then their threads;
- **forum** and **media** channels: their posts, which are threads;
- **threads**: the active ones, the public archived ones, and the private archived ones
  when the bot has Manage Threads. When it does not, the manifest lists what was not
  fetched under ``not_fetched``, and the export carries on.

Categories, voice and stage channels are not exported, and neither is a channel whose
history Discord refuses to hand over. Each is listed under ``skipped`` with its reason.

**Incremental and resumable.** A channel's file is its own cursor: a run reads the id on
the file's last line and fetches only messages after it, a page at a time, appending as
it goes. An interrupted run loses at most the page it was writing; a half-written last
line is dropped by the next run, which carries on from there. A channel whose
``last_message_id`` is already in its file is not fetched at all.

**Preflight.** Without the Message Content intent, Discord still returns every message,
but with its text, attachments and embeds blanked. An export in that state looks like a
success and holds nothing, so :func:`check_message_content` fetches one message before
anything is written, and refuses to go on if it is blank.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from .base import NO_HISTORY_KINDS, Backend, _dflt_backend, channel_kind

__all__ = [
    "export_guild",
    "check_message_content",
    "MessageContentMissing",
    "ChannelLogs",
    "default_export_dir",
]

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
MESSAGES_DIRNAME = "messages"
DFLT_PAGE_SIZE = 1000
APP_NAME = "discorddol"
DATA_DIR_ENVVAR = "DISCORDDOL_DATA_DIR"

#: Kinds whose threads are exported along with them.
THREAD_PARENT_KINDS = frozenset({"text", "announcement", "forum", "media"})
#: Kinds that can hold private threads: Discord allows them in text channels only.
PRIVATE_THREAD_PARENT_KINDS = frozenset({"text"})
#: Kinds that are never exported, with the reason the manifest gives.
SKIP_REASONS = {
    "category": "category: it groups other channels and holds no messages",
    "voice": "voice channel: not exported",
    "stage": "stage channel: not exported",
}
#: Message types a person writes. A system notice (a pin, a join) can be blank anyway.
REGULAR_MESSAGE_TYPES = frozenset({"default", "reply"})


def default_export_dir(guild_id) -> Path:
    """Where a guild's export goes when no ``out_dir`` is given: ``<data>/exports/<id>``.

    ``<data>`` is ``$DISCORDDOL_DATA_DIR`` when that is set, else discorddol's app data
    folder (``~/.local/share/discorddol`` on macOS and Linux). Never inside a
    repository, so exported messages are not committed by accident.
    """
    root = os.environ.get(DATA_DIR_ENVVAR)
    if root:
        data = Path(root).expanduser()
    else:
        from config2py import AppData

        data = AppData(APP_NAME).app_folder()
    return data / "exports" / str(guild_id)


# --------------------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------------------


class MessageContentMissing(RuntimeError):
    """Message bodies come back blank: the bot's Message Content Intent is off."""


def check_message_content(backend: Backend, channels: Iterable[dict]) -> dict:
    """Refuse to export blank messages: fetch one message and check it has content.

    Goes through ``channels`` (records with an ``id``) in order, fetching each one's
    newest message, until it finds a regular one: written by someone, rather than a
    system notice such as a pin, which may be blank anyway. If that message has no
    text, no attachments and no embeds, raises :class:`MessageContentMissing`, which
    names the fix. Discord blanks all three when the intent is off, so checking all
    three lets an image-only message pass, as it should.

    Returns what was checked: ``{'status': 'passed', 'channel_id', 'message_id'}``, or
    ``{'status': 'inconclusive', 'reason'}`` when no channel had a message to check.

    >>> from discorddol.base import DictBackend
    >>> backend = DictBackend(messages={'10': [{'id': '1', 'content': 'hi'}]})
    >>> check_message_content(backend, [{'id': '10'}])['status']
    'passed'
    """

    def is_regular(message):
        kind = str(message.get("type") or "default").rsplit(".", 1)[-1]
        return kind in REGULAR_MESSAGE_TYPES

    for record in channels:
        try:
            newest = backend.messages(record["id"], limit=1, oldest_first=False)
        except (PermissionError, LookupError):
            continue
        if not newest or not is_regular(newest[0]):
            continue
        message = newest[0]
        if (
            message.get("content")
            or message.get("attachments")
            or message.get("embeds")
        ):
            return {
                "status": "passed",
                "channel_id": record["id"],
                "message_id": message.get("id"),
            }
        raise MessageContentMissing(
            f"Message {message.get('id')} in #{_name(record)} came back with no text, "
            f"attachments or embeds. That is what Discord sends when the bot's "
            f'"Message Content Intent" is off, and the export would hold nothing.\n'
            f"Fix: https://discord.com/developers/applications -> your app -> Bot -> "
            f'Privileged Gateway Intents -> enable "Message Content Intent", then '
            f"re-run.\n"
            f"If that message really is blank, re-run with preflight=False "
            f"(CLI: --skip-preflight)."
        )
    return {
        "status": "inconclusive",
        "reason": "no readable channel had a message to check",
    }


# --------------------------------------------------------------------------------------
# The files
# --------------------------------------------------------------------------------------


class ChannelLogs(Mapping):
    """Mapping: channel id -> that channel's exported messages, one JSONL file each.

    The export's writer as well as its reader: :meth:`extend` appends messages, and
    :meth:`summary` gives a file's message count, date range and cursor, parsing only
    its first and last lines. Files hold messages oldest first, in the order appended.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     logs = ChannelLogs(tmp)
    ...     _ = logs.extend('10', [{'id': '1', 'created_at': '2026-01-01T09:00:00'}])
    ...     _ = logs.extend('10', [{'id': '2', 'created_at': '2026-01-02T09:00:00'}])
    ...     print(list(logs), [m['id'] for m in logs['10']], logs.summary('10'))
    ['10'] ['1', '2'] {'message_count': 2, 'first_message_at': '2026-01-01T09:00:00', 'last_message_at': '2026-01-02T09:00:00', 'last_message_id': '2'}
    """

    def __init__(self, rootdir):
        self.rootdir = Path(rootdir).expanduser()

    def path(self, channel_id) -> Path:
        """The JSONL file of one channel."""
        return self.rootdir / f"{channel_id}.jsonl"

    def __getitem__(self, channel_id) -> list[dict]:
        path = self.path(channel_id)
        if not path.is_file():
            raise KeyError(channel_id)
        with path.open(encoding="utf-8") as lines:
            return [json.loads(line) for line in lines if line.endswith("\n")]

    def __contains__(self, channel_id) -> bool:
        return self.path(channel_id).is_file()

    def __iter__(self) -> Iterator[str]:
        if self.rootdir.is_dir():
            yield from sorted(path.stem for path in self.rootdir.glob("*.jsonl"))

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def extend(self, channel_id, messages: Iterable[dict]) -> int:
        """Append messages to a channel's file, creating it if needed. Returns how many."""
        path = self.path(channel_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _drop_partial_last_line(path)
        lines = [json.dumps(message, ensure_ascii=False) + "\n" for message in messages]
        with path.open("a", encoding="utf-8", newline="\n") as file:
            file.writelines(lines)
        return len(lines)

    def summary(self, channel_id) -> dict:
        """A channel's message count, first and last dates, and last id (its cursor)."""
        path = self.path(channel_id)
        if not path.is_file():
            return _empty_summary()
        _drop_partial_last_line(path)
        count, first, last = 0, None, None
        with path.open("rb") as file:
            for last in file:
                count += 1
                first = first or last
        if not count:
            return _empty_summary()
        head, tail = json.loads(first), json.loads(last)
        return {
            "message_count": count,
            "first_message_at": head.get("created_at"),
            "last_message_at": tail.get("created_at"),
            "last_message_id": tail.get("id"),
        }


# --------------------------------------------------------------------------------------
# The export
# --------------------------------------------------------------------------------------


def export_guild(
    guild_id: str,
    out_dir,
    *,
    backend: Optional[Backend] = None,
    token: Optional[str] = None,
    guild_name: Optional[str] = None,
    page_size: int = DFLT_PAGE_SIZE,
    private_threads: bool = True,
    preflight: bool = True,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """Export every channel of a guild the bot can read into ``out_dir``.

    Returns the manifest, which is also written to ``out_dir``. Re-running on the same
    ``out_dir`` fetches only what is new (see the module docstring).
    ``private_threads=False`` does not ask for private archived threads, and
    ``preflight=False`` skips :func:`check_message_content`. ``log`` gets one line per
    channel.

    >>> import tempfile
    >>> from discorddol.base import DictBackend
    >>> backend = DictBackend(
    ...     channels={'1': [
    ...         {'id': '10', 'name': 'general', 'type': 'text'},
    ...         {'id': '11', 'name': 'Community', 'type': 'category'},
    ...     ]},
    ...     messages={'10': [{'id': '100', 'content': 'hi', 'created_at': '2026-01-01'}]},
    ... )
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     manifest = export_guild('1', tmp, backend=backend)
    >>> [(c['name'], c['kind'], c['message_count']) for c in manifest['channels']]
    [('general', 'text', 1)]
    >>> [(s['name'], s['reason']) for s in manifest['skipped']]
    [('Community', 'category: it groups other channels and holds no messages')]
    """
    backend = _dflt_backend(backend, token)
    parents, threads_by_parent, skipped = _plan(backend.channels(guild_id))
    if preflight:
        own_history = [p for p in parents if channel_kind(p) not in NO_HISTORY_KINDS]
        active_threads = [t for group in threads_by_parent.values() for t in group]
        checked = check_message_content(backend, own_history + active_threads)
    else:
        checked = {"status": "skipped"}
    run = _ExportRun(
        backend,
        Path(out_dir).expanduser(),
        guild={"id": str(guild_id), "name": guild_name},
        preflight=checked,
        skipped=skipped,
        page_size=page_size,
        log=log or (lambda line: None),
    )
    for parent in parents:
        run.export_with_threads(
            parent,
            active_threads=threads_by_parent.pop(parent["id"], []),
            private_threads=(
                private_threads and channel_kind(parent) in PRIVATE_THREAD_PARENT_KINDS
            ),
        )
    for orphans in threads_by_parent.values():  # active threads with no listed parent
        for thread in orphans:
            run.export(thread)
    return run.finish()


class _ExportRun:
    """One run of :func:`export_guild`: appends messages and keeps the manifest current.

    Channel entries from an earlier run's manifest are kept and updated, so a run that
    stops early still leaves every channel exported so far described. ``skipped`` and
    ``not_fetched`` describe this run only.
    """

    def __init__(
        self, backend, out_dir: Path, *, guild, preflight, skipped, page_size, log
    ):
        self.backend = backend
        self.logs = ChannelLogs(out_dir / MESSAGES_DIRNAME)
        self.manifest_path = out_dir / MANIFEST_FILENAME
        self.page_size = page_size
        self.log = log
        previous = _read_json(self.manifest_path)
        self.entries = {entry["id"]: entry for entry in previous.get("channels", ())}
        earlier_name = previous.get("guild", {}).get("name")
        self.manifest = {
            "manifest_version": MANIFEST_VERSION,
            "guild": {"id": guild["id"], "name": guild["name"] or earlier_name},
            "run": {
                "started_at": _now(),
                "finished_at": None,
                "preflight": preflight,
                "channels_exported": 0,
                "new_messages": 0,
            },
            "channels": [],
            "skipped": list(skipped),
            "not_fetched": [],
        }
        for entry in skipped:
            log(f"skipped #{_name(entry)} ({entry['kind']}): {entry['reason']}")
        self.save()

    def export_with_threads(
        self, parent: dict, *, active_threads: list, private_threads: bool
    ) -> None:
        """Export a channel, then every thread under it, then save the manifest."""
        if channel_kind(parent) in NO_HISTORY_KINDS:
            self._set_entry(parent, file=None)  # its messages are in its posts
        else:
            self.export(parent)
        threads = self._threads_of(parent, active_threads, private=private_threads)
        for thread in threads:
            self.export(thread, parent=parent)
        if parent["id"] in self.entries:
            self.entries[parent["id"]]["thread_count"] = len(threads)
        self.save()

    def export(self, record: dict, *, parent: Optional[dict] = None) -> None:
        """Append a channel's new messages to its file and update its manifest entry."""
        channel_id = record["id"]
        try:
            added = self._fetch_new_messages(channel_id, record.get("last_message_id"))
        except (PermissionError, LookupError) as error:
            reason = f"its messages could not be read: {error}"
            self.manifest["skipped"].append(_skipped(record, reason))
            self.log(f"skipped #{_name(record)} ({channel_kind(record)}): {error}")
            return
        if channel_id not in self.logs:
            self.logs.extend(channel_id, ())  # every exported channel gets a file
        entry = self._set_entry(
            record, parent=parent, file=f"{MESSAGES_DIRNAME}/{channel_id}.jsonl"
        )
        self.manifest["run"]["channels_exported"] += 1
        self.manifest["run"]["new_messages"] += added
        self.log(
            f"#{_name(record)} ({entry['kind']}): "
            f"{added} new, {entry['message_count']} in all"
        )

    def finish(self) -> dict:
        """Stamp the run as finished, save the manifest and return it."""
        self.manifest["run"]["finished_at"] = _now()
        self.save()
        return self.manifest

    def save(self) -> None:
        """Write the manifest as it stands."""
        self.manifest["channels"] = list(self.entries.values())
        _write_json(self.manifest_path, self.manifest)

    def _fetch_new_messages(self, channel_id: str, last_message_id) -> int:
        """Page through the messages newer than the file's last line, appending each page."""
        cursor = self.logs.summary(channel_id)["last_message_id"]
        if cursor is not None and last_message_id is not None:
            if int(last_message_id) <= int(cursor):
                return 0  # Discord's newest message is already in the file
        added = 0
        while True:
            page = self.backend.messages(
                channel_id, limit=self.page_size, after=cursor, oldest_first=True
            )
            fresh = sorted(
                (m for m in page if cursor is None or int(m["id"]) > int(cursor)),
                key=_snowflake,
            )
            if fresh:
                added += self.logs.extend(channel_id, fresh)
                cursor = fresh[-1]["id"]
            if not fresh or len(page) < self.page_size:
                return added

    def _threads_of(self, parent: dict, active_threads, *, private: bool) -> list:
        """A channel's threads: its active ones, plus public and private archived ones."""
        found = {thread["id"]: thread for thread in active_threads}
        sources = [("public archived threads", False)]
        if private:
            sources.append(("private archived threads", True))
        for what, is_private in sources:
            try:
                archived = self.backend.archived_threads(
                    parent["id"], private=is_private
                )
            except (PermissionError, LookupError) as error:
                self.manifest["not_fetched"].append(
                    {
                        "channel_id": parent["id"],
                        "name": parent.get("name"),
                        "what": what,
                        "reason": str(error),
                    }
                )
                self.log(f"#{_name(parent)}: {what} not fetched: {error}")
                continue
            for thread in archived:
                found.setdefault(thread["id"], thread)
        return sorted(found.values(), key=_snowflake)

    def _set_entry(
        self, record: dict, *, file: Optional[str], parent: Optional[dict] = None
    ) -> dict:
        """Describe a channel in the manifest, from its record and its file."""
        entry = {
            "id": record["id"],
            "name": record.get("name"),
            "kind": channel_kind(record),
            "type": record.get("type"),
            "parent_id": record.get("parent_id"),
            "parent_name": parent.get("name") if parent else None,
            "archived": record.get("archived"),
            "file": file,
            **(self.logs.summary(record["id"]) if file else _empty_summary()),
            "exported_at": _now(),
        }
        self.entries[record["id"]] = entry
        return entry


def _plan(listing: Iterable[dict]) -> tuple[list, dict, list]:
    """Split a channel listing into thread parents, active threads by parent, and skips."""
    parents, threads_by_parent, skipped = [], {}, []
    for record in sorted(listing, key=_snowflake):
        kind = channel_kind(record)
        if kind == "thread":
            threads_by_parent.setdefault(record.get("parent_id"), []).append(record)
        elif kind in THREAD_PARENT_KINDS:
            parents.append(record)
        else:
            reason = SKIP_REASONS.get(kind, f"unsupported channel type: {kind}")
            skipped.append(_skipped(record, reason))
    return parents, threads_by_parent, skipped


def _skipped(record: dict, reason: str) -> dict:
    return {
        "id": record["id"],
        "name": record.get("name"),
        "kind": channel_kind(record),
        "reason": reason,
    }


def _name(record: dict) -> str:
    return record.get("name") or record["id"]


def _snowflake(record: dict) -> int:
    return int(record["id"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_summary() -> dict:
    return {
        "message_count": 0,
        "first_message_at": None,
        "last_message_at": None,
        "last_message_id": None,
    }


def _drop_partial_last_line(path: Path) -> None:
    """Cut a file back to its last newline: what an interrupted append leaves behind."""
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open("rb+") as file:
        file.seek(-1, os.SEEK_END)
        if file.read(1) == b"\n":
            return
        file.seek(0)
        file.truncate(file.read().rfind(b"\n") + 1)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _write_json(path: Path, data: dict) -> None:
    """Write through a temporary file, so an interrupted run never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(temporary, path)
