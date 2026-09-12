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
the file's last complete line and fetches only messages after it, a page at a time,
appending as it goes. An interrupted run loses at most the page it was writing; the next
run drops a half-written last line before appending, and carries on from there. A
channel whose ``last_message_id`` is already in its file is not fetched at all. Only one
run at a time may write to a folder; another is refused with :class:`ExportInProgress`.

**Content check.** Without the Message Content intent, Discord still returns every
message, but with its text, attachments and embeds blanked. An export in that state
looks like a success and holds nothing. So before anything is written,
:func:`check_message_content` samples recent messages written by people, a few from
each channel, and refuses to go on when most of them are blank. Until a full sample has
been judged, the export goes on judging the messages it fetches in the same way, and
refuses as soon as they show blank bodies, or at the latest when the run ends. Messages
written before that point stay on disk, so delete an export refused this way.
"""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from .base import NO_HISTORY_KINDS, Backend, NotFound, _dflt_backend, channel_kind

__all__ = [
    "export_guild",
    "check_message_content",
    "MessageContentMissing",
    "ExportInProgress",
    "ChannelLogs",
    "default_export_dir",
]

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
MESSAGES_DIRNAME = "messages"
LOCK_FILENAME = ".export.lock"
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
#: How many messages written by people make a full content-check sample.
CONTENT_SAMPLE_SIZE = 20
#: The fewest channels the preflight spreads its sample over, when there are that many.
CONTENT_SAMPLE_CHANNELS = 4
#: The share of blank messages in a sample above which content counts as missing.
MAX_BLANK_SHARE = 0.5
#: Lock errors that mean another run holds the folder, rather than "cannot lock here".
_LOCKED_ERRNOS = frozenset(
    {
        errno.EAGAIN,
        errno.EWOULDBLOCK,
        errno.EACCES,
        getattr(errno, "EDEADLOCK", errno.EDEADLK),
    }
)


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
# The content check
# --------------------------------------------------------------------------------------


class MessageContentMissing(RuntimeError):
    """Message bodies come back blank: the bot's Message Content Intent is off."""


class ExportInProgress(RuntimeError):
    """Another export is already writing to the same folder."""


def check_message_content(
    backend: Backend,
    channels: Iterable[dict],
    *,
    sample_size: int = CONTENT_SAMPLE_SIZE,
    min_channels: int = CONTENT_SAMPLE_CHANNELS,
    max_blank_share: float = MAX_BLANK_SHARE,
) -> dict:
    """Refuse to export blank messages: sample recent messages and check their content.

    Fetches the newest messages of each channel in ``channels`` (records with an
    ``id``), one call per channel, until ``sample_size`` messages written by people have
    been collected. No channel supplies more than ``sample_size // min_channels`` of
    them, so one channel full of stickers cannot decide the verdict. System notices and
    bot messages do not count: a notice can be blank anyway, and a bot's own messages
    keep their content without the intent. A message is blank when it has no text, no
    attachments and no embeds, which is how Discord sends every message when the intent
    is off.

    Raises :class:`MessageContentMissing`, naming the fix, when more than
    ``max_blank_share`` of the sample is blank. Returns ``{'status': 'passed',
    'checked', 'blank'}``, or ``{'status': 'inconclusive', 'checked': 0, 'blank': 0}``
    when there was nothing to judge.

    >>> from discorddol.base import DictBackend
    >>> backend = DictBackend(messages={'10': [{'id': '1', 'content': 'hi'}]})
    >>> check_message_content(backend, [{'id': '10'}])
    {'status': 'passed', 'checked': 1, 'blank': 0}
    """
    per_channel = max(1, sample_size // min_channels)
    sample = []
    for record in channels:
        if len(sample) >= sample_size:
            break
        try:
            recent = backend.messages(
                record["id"], limit=sample_size, oldest_first=False
            )
        except (PermissionError, NotFound):
            continue
        telling = [(record, message) for message in recent if _is_telling(message)]
        sample.extend(telling[:per_channel])
    return _judge_content(sample[:sample_size], max_blank_share=max_blank_share)


def _judge_content(sample: list, *, max_blank_share: float) -> dict:
    """The verdict on ``(channel record, message)`` pairs; raises when most are blank."""
    if not sample:
        return {"status": "inconclusive", "checked": 0, "blank": 0}
    blank = [(record, message) for record, message in sample if _is_blank(message)]
    if len(blank) > max_blank_share * len(sample):
        record, message = blank[0]
        raise MessageContentMissing(
            f"{len(blank)} of {len(sample)} messages written by people came back with "
            f"no text, attachments or embeds (message {message.get('id')} in "
            f"#{_name(record)}, for one). That is what Discord sends when the bot's "
            f'"Message Content Intent" is off, and the export would hold nothing.\n'
            f"Fix: https://discord.com/developers/applications -> your app -> Bot -> "
            f'Privileged Gateway Intents -> enable "Message Content Intent", then run '
            f"the export again. Anything already exported without the intent is blank "
            f"and will not be fetched again, so delete it first.\n"
            f"If these messages really are blank, run with preflight=False "
            f"(CLI: --skip-preflight)."
        )
    return {"status": "passed", "checked": len(sample), "blank": len(blank)}


def _is_telling(message: dict) -> bool:
    """Whether a blank body would say something: written by a person, not a notice."""
    kind = str(message.get("type") or "default").rsplit(".", 1)[-1]
    return kind in REGULAR_MESSAGE_TYPES and not (message.get("author") or {}).get(
        "bot"
    )


def _is_blank(message: dict) -> bool:
    return not (
        message.get("content") or message.get("attachments") or message.get("embeds")
    )


# --------------------------------------------------------------------------------------
# The files
# --------------------------------------------------------------------------------------


class ChannelLogs(Mapping):
    """Mapping: channel id -> that channel's exported messages, one JSONL file each.

    The export's writer as well as its reader: :meth:`extend` appends messages, and
    :meth:`summary` gives a file's message count, date range and cursor, parsing only
    its first and last lines. Files hold messages oldest first, in the order appended.
    Reading never changes a file; a half-written last line is ignored until the next
    :meth:`extend` drops it.

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
        count, first, last = 0, None, None
        if path.is_file():
            with path.open("rb") as file:
                for line in file:
                    if not line.endswith(b"\n"):
                        break  # an interrupted append
                    count += 1
                    first = first or line
                    last = line
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
    sample_size: int = CONTENT_SAMPLE_SIZE,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """Export every channel of a guild the bot can read into ``out_dir``.

    Returns the manifest, which is also written to ``out_dir``. Re-running on the same
    ``out_dir`` fetches only what is new (see the module docstring).
    ``private_threads=False`` does not ask for private archived threads.
    ``preflight=False`` turns the content check off; ``sample_size`` is how many
    messages make a full sample for it. ``log`` gets one line per channel.

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
    out_dir = Path(out_dir).expanduser()
    log = log or (lambda line: None)
    parents, threads_by_parent, skipped = _plan(backend.channels(guild_id))
    if preflight:
        own_history = [p for p in parents if channel_kind(p) not in NO_HISTORY_KINDS]
        active_threads = [t for group in threads_by_parent.values() for t in group]
        checked = check_message_content(
            backend, own_history + active_threads, sample_size=sample_size
        )
    else:
        checked = {"status": "skipped"}
    with _exclusive(out_dir, log=log):
        run = _ExportRun(
            backend,
            out_dir,
            guild={"id": str(guild_id), "name": guild_name},
            preflight=checked,
            skipped=skipped,
            page_size=page_size,
            sample_size=sample_size,
            log=log,
        )
        for parent in parents:
            run.export_with_threads(
                parent,
                active_threads=threads_by_parent.pop(parent["id"], []),
                private_threads=(
                    private_threads
                    and channel_kind(parent) in PRIVATE_THREAD_PARENT_KINDS
                ),
            )
        for orphans in threads_by_parent.values():  # active threads, no listed parent
            for thread in orphans:
                run.export(thread)
        return run.finish()


class _ExportRun:
    """One run of :func:`export_guild`: appends messages and keeps the manifest current.

    Entries from an earlier run's manifest are kept, so a run that stops early still
    describes every channel exported so far. An entry's ``status`` says what the latest
    run did with it: ``exported``; ``skipped``, when Discord refused its history this
    time (``skipped`` says why); or, once a run finishes without reaching it,
    ``not in last run``, as for a channel deleted or hidden from the bot since.
    ``skipped`` and ``not_fetched`` describe the latest run only.

    ``run.preflight`` is the verdict before anything was written. When that did not
    judge a full sample, ``run.content_check`` is the verdict on the messages the export
    itself fetched.
    """

    def __init__(
        self,
        backend,
        out_dir: Path,
        *,
        guild,
        preflight,
        skipped,
        page_size,
        sample_size,
        log,
    ):
        self.backend = backend
        self.logs = ChannelLogs(out_dir / MESSAGES_DIRNAME)
        self.manifest_path = out_dir / MANIFEST_FILENAME
        self.page_size = page_size
        self.sample_size = sample_size
        self.log = log
        self.seen = set()
        #: Fetched messages to judge, until a full sample has been; None when not needed.
        judged_enough = preflight.get("checked", 0) >= sample_size
        skipped_check = preflight["status"] == "skipped"
        self.sample = None if judged_enough or skipped_check else []
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
                "content_check": None,
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
            self.seen.add(parent["id"])  # its messages are in its posts
            self._set_entry(parent, status="exported")
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
        self.seen.add(channel_id)
        before = self.logs.summary(channel_id)
        summary, refusal = self._fetch_new_messages(record, before)
        added = summary["message_count"] - before["message_count"]
        self.manifest["run"]["new_messages"] += added
        if refusal is not None:
            reason = f"its messages could not be read: {refusal}"
            self.manifest["skipped"].append(_skipped(record, reason))
            self.log(f"skipped #{_name(record)} ({channel_kind(record)}): {refusal}")
            if channel_id in self.logs:  # an earlier page or run wrote some
                self._set_entry(
                    record, status="skipped", summary=summary, parent=parent
                )
            elif channel_id in self.entries:  # exported before, file deleted since
                self._set_entry(record, status="skipped", parent=parent)
            return
        if channel_id not in self.logs:
            self.logs.extend(channel_id, ())  # every exported channel gets a file
        entry = self._set_entry(
            record, status="exported", summary=summary, parent=parent
        )
        self.manifest["run"]["channels_exported"] += 1
        self.log(
            f"#{_name(record)} ({entry['kind']}): "
            f"{added} new, {entry['message_count']} in all"
        )

    def finish(self) -> dict:
        """Mark what this run did not reach, conclude the content check, save, return.

        A content check still open is concluded on whatever sample the run gathered,
        so a small export whose messages are mostly blank is refused here.
        """
        for channel_id, entry in self.entries.items():
            if channel_id not in self.seen:
                entry["status"] = "not in last run"
        if self.sample is not None:
            self._conclude_content_check()
        self.manifest["run"]["finished_at"] = _now()
        self.save()
        return self.manifest

    def save(self) -> None:
        """Write the manifest as it stands."""
        self.manifest["channels"] = list(self.entries.values())
        _write_json(self.manifest_path, self.manifest)

    def _fetch_new_messages(self, record: dict, summary: dict) -> tuple:
        """Page through messages newer than the file's last line, appending each page.

        Returns the file's summary afterwards, and Discord's refusal if one stopped the
        paging. Only the backend call is guarded, so a local file error still raises.
        """
        channel_id, cursor = record["id"], summary["last_message_id"]
        latest = record.get("last_message_id")
        if cursor is not None and latest is not None and int(latest) <= int(cursor):
            return summary, None  # Discord's newest message is already in the file
        while True:
            try:
                page = self.backend.messages(
                    channel_id, limit=self.page_size, after=cursor, oldest_first=True
                )
            except (PermissionError, NotFound) as refusal:
                return summary, refusal
            fresh = sorted(
                (m for m in page if cursor is None or int(m["id"]) > int(cursor)),
                key=_snowflake,
            )
            if fresh:
                self._judge(record, fresh)
                self.logs.extend(channel_id, fresh)
                summary = _extended(summary, fresh)
                cursor = summary["last_message_id"]
            if not fresh or len(page) < self.page_size:
                return summary, None

    def _judge(self, record: dict, page: list) -> None:
        """Until a full sample has been judged, judge a fetched page before writing it."""
        if self.sample is None:
            return
        self.sample.extend((record, m) for m in page if _is_telling(m))
        if len(self.sample) >= self.sample_size:
            self._conclude_content_check()

    def _conclude_content_check(self) -> None:
        """Judge the export's own sample and record the verdict, saved before refusing."""
        sample, self.sample = self.sample, None
        try:
            verdict = _judge_content(sample, max_blank_share=MAX_BLANK_SHARE)
        except MessageContentMissing:
            self.manifest["run"]["content_check"] = {
                "status": "failed",
                "checked": len(sample),
                "blank": sum(_is_blank(message) for _, message in sample),
            }
            self.save()
            raise
        self.manifest["run"]["content_check"] = verdict

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
            except (PermissionError, NotFound) as error:
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
        self,
        record: dict,
        *,
        status: str,
        summary: Optional[dict] = None,
        parent: Optional[dict] = None,
    ) -> dict:
        """Describe a channel in the manifest: what it is, and what its file holds.

        ``channel`` keeps the backend's full record, so an export can be read back as
        a channel listing.
        """
        channel_id = record["id"]
        entry = {
            "id": channel_id,
            "name": record.get("name"),
            "kind": channel_kind(record),
            "parent_id": record.get("parent_id"),
            "parent_name": parent.get("name") if parent else None,
            "status": status,
            "file": f"{MESSAGES_DIRNAME}/{channel_id}.jsonl" if summary else None,
            **(summary or _empty_summary()),
            "exported_at": _now(),
            "channel": record,
        }
        self.entries[channel_id] = entry
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


@contextmanager
def _exclusive(folder: Path, *, log: Optional[Callable[[str], None]] = None):
    """Hold an OS lock on ``folder`` for one run. The OS releases it if the run dies.

    A filesystem that cannot lock at all, as some network mounts cannot, does not stop
    the export: it runs without the lock, and says so through ``log``.
    """
    folder.mkdir(parents=True, exist_ok=True)
    handle = (folder / LOCK_FILENAME).open("a+b")
    if os.name == "nt":
        import msvcrt

        def lock(mode=msvcrt.LK_NBLCK):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), mode, 1)

        def unlock():
            lock(msvcrt.LK_UNLCK)

    else:
        import fcntl

        def lock():
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        def unlock():
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    try:
        lock()
        locked = True
    except OSError as error:
        if error.errno in _LOCKED_ERRNOS:
            handle.close()
            raise ExportInProgress(
                f"Another export is already writing to {folder}. Let it finish, or "
                f"stop it, then run this one again."
            ) from error
        locked = False
        (log or (lambda line: None))(
            f"could not lock {folder} ({error}); nothing stops a second export from "
            f"writing to it at the same time"
        )
    try:
        yield
    finally:
        if locked:
            unlock()
        handle.close()


def _extended(summary: dict, fresh: list) -> dict:
    """A file's summary after appending ``fresh``, oldest first, to it."""
    return {
        "message_count": summary["message_count"] + len(fresh),
        "first_message_at": summary["first_message_at"] or fresh[0].get("created_at"),
        "last_message_at": fresh[-1].get("created_at"),
        "last_message_id": fresh[-1]["id"],
    }


def _skipped(record: dict, reason: str) -> dict:
    return {
        "id": record["id"],
        "name": record.get("name"),
        "kind": channel_kind(record),
        "type": record.get("type"),
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
