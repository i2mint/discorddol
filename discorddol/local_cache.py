"""Read the Discord desktop client's on-disk HTTP cache -- including for deleted channels.

Discord has no undelete: deleting a channel destroys its messages server-side, and no
API, bot or owner permission brings them back. But the desktop client is an Electron
app, so every attachment it ever displayed passed through Chromium's HTTP cache and the
**bytes are still on disk**. Attachment URLs embed the channel id::

    https://cdn.discordapp.com/attachments/{channel_id}/{attachment_id}/{filename}

which gives a recovery path for a channel that no longer exists:

1. :func:`cached_channel_ids` -- every channel id with cached attachments.
2. :func:`orphan_channel_ids` -- those that no longer resolve against the live API.
   A deleted channel shows up here.
3. :func:`extract_channel_attachments` -- write those bytes back out to real files.

Step 3 matters because Discord attachment URLs are signed and expire, so re-downloading
them is not an option even when you know the URL. The cache holds the payload itself.

Message **text** is not recoverable this way -- Discord's API responses are sent
``no-store`` and never hit the disk cache. For text, request your Discord data export
(Settings -> Data & Privacy -> Request all of my Data), which files your own sent
messages by channel id, including channels that no longer exist. The channel id from
step 2 is what lets you find the right folder in that export.

This is a read-only reader. It never writes to, moves, or deletes anything in the live
cache directory, which the running Discord client owns.
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Iterator, Optional

__all__ = [
    "discord_cache_dir",
    "cached_attachment_refs",
    "cached_channel_ids",
    "orphan_channel_ids",
    "extract_channel_attachments",
    "CachedAttachments",
]

# Chromium "simple cache" entry markers. The payload of an entry sits between the key
# (the URL) and the EOF record; we bound extraction with the EOF magic when present.
SIMPLE_CACHE_EOF_MAGIC = b"\xd8\x41\x0d\x97\x45\x6f\xfa\xf4"

# The filename is one URL path segment, so it cannot contain '/', a query delimiter,
# whitespace, a quote, or a control byte. Bounding it that tightly matters: cache entries
# are binary, and a looser class runs the match past the URL into whatever follows it.
ATTACHMENT_URL_RE = re.compile(
    rb"(?:cdn|media)\.discordapp\.(?:com|net)/(?:ephemeral-)?attachments/"
    rb'(\d+)/(\d+)/([^\s"\'?&/\\<>\x00-\x1f]{1,200})'
)

# (magic bytes, extension) -- how we find where a payload starts inside a cache entry.
FILE_MAGICS = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"%PDF-", ".pdf"),
    (b"RIFF", ".webp"),
    (b"\x1aE\xdf\xa3", ".webm"),
    (b"PK\x03\x04", ".zip"),
)


def discord_cache_dir(*, app: str = "discord") -> Path:
    """Default Discord desktop cache directory for this platform.

    >>> isinstance(discord_cache_dir(), Path)
    True
    """
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / app
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", home)) / app
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / app
    return base / "Cache" / "Cache_Data"


def _cache_files(cache_dir: Optional[Path]) -> Iterator[Path]:
    cache_dir = Path(cache_dir) if cache_dir else discord_cache_dir()
    if not cache_dir.is_dir():
        raise FileNotFoundError(
            f"No Discord cache directory at {cache_dir}. "
            f"Pass cache_dir= explicitly, or point it at a snapshot copy."
        )
    for path in cache_dir.iterdir():
        if path.is_file():
            yield path


def cached_attachment_refs(*, cache_dir: Optional[Path] = None) -> Iterator[dict]:
    """Yield one record per cached attachment URL found in the cache.

    Each record has ``channel_id``, ``attachment_id``, ``filename`` and ``cache_file``.
    Streams rather than accumulating, so a multi-gigabyte cache stays cheap to scan.
    """
    for path in _cache_files(cache_dir):
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        seen = set()
        for channel_id, attachment_id, filename in ATTACHMENT_URL_RE.findall(blob):
            key = (channel_id, attachment_id)
            if key in seen:
                continue
            seen.add(key)
            yield {
                "channel_id": channel_id.decode(),
                "attachment_id": attachment_id.decode(),
                "filename": filename.decode("utf-8", "replace"),
                "cache_file": str(path),
            }


def cached_channel_ids(*, cache_dir: Optional[Path] = None) -> Counter:
    """Channel ids appearing in cached attachment URLs, counted by cached attachment.

    The counts are a usefully blunt signal: a channel with many cached attachments was
    one you actually spent time in.
    """
    return Counter(r["channel_id"] for r in cached_attachment_refs(cache_dir=cache_dir))


def orphan_channel_ids(
    guild_id: str,
    *,
    backend=None,
    token: Optional[str] = None,
    cache_dir: Optional[Path] = None,
) -> dict:
    """Cached channel ids that no longer exist in ``guild_id`` -- deleted channels.

    Returns ``{'orphans': {channel_id: count}, 'live': {channel_id: count}}``, each
    ordered by count descending.

    An id under ``orphans`` is a *candidate*, not a verdict: it is a channel this
    machine cached attachments from that is not currently visible in ``guild_id``. That
    covers deleted channels, but also channels in other servers and channels the bot
    lacks permission to see. Cross-check a candidate against
    :func:`extract_channel_attachments` output before concluding anything.

    >>> import tempfile
    >>> from discorddol.base import DictBackend
    >>> backend = DictBackend(channels={'1': [{'id': '10', 'name': 'dev'}]})
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     result = orphan_channel_ids('1', backend=backend, cache_dir=tmp)
    >>> sorted(result)
    ['live', 'orphans']
    """
    from .base import _dflt_backend

    backend = _dflt_backend(backend, token)
    live = {c["id"] for c in backend.channels(guild_id)}
    counts = cached_channel_ids(cache_dir=cache_dir)

    def ordered(predicate):
        return dict(
            sorted(
                ((k, v) for k, v in counts.items() if predicate(k)),
                key=lambda kv: -kv[1],
            )
        )

    return {
        "orphans": ordered(lambda k: k not in live),
        "live": ordered(lambda k: k in live),
    }


def _extract_payload(
    blob: bytes, *, start_hint: int = 0
) -> Optional[tuple[bytes, str]]:
    """Best-effort slice of a cache entry's body: from a known file magic to the EOF record."""
    best = None
    for magic, ext in FILE_MAGICS:
        index = blob.find(magic, start_hint)
        if index != -1 and (best is None or index < best[0]):
            best = (index, ext)
    if best is None:
        return None
    start, ext = best
    end = blob.find(SIMPLE_CACHE_EOF_MAGIC, start)
    return blob[start : end if end != -1 else len(blob)], ext


def extract_channel_attachments(
    channel_id: str,
    *,
    out_dir,
    cache_dir: Optional[Path] = None,
) -> list[dict]:
    """Write every cached attachment belonging to ``channel_id`` out to ``out_dir``.

    Returns one record per file written, with the original filename and the bytes
    recovered. Extraction is heuristic -- it locates a known file signature inside the
    cache entry and reads to the entry's EOF record -- so verify anything that matters
    by opening it.
    """
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for ref in cached_attachment_refs(cache_dir=cache_dir):
        if ref["channel_id"] != str(channel_id):
            continue
        try:
            blob = Path(ref["cache_file"]).read_bytes()
        except OSError:
            continue
        extracted = _extract_payload(blob)
        if extracted is None:
            continue
        payload, ext = extracted
        name = ref["filename"] or f"{ref['attachment_id']}{ext}"
        if not Path(name).suffix:
            name += ext
        target = out_dir / f"{ref['attachment_id']}_{name}"
        target.write_bytes(payload)
        written.append(
            {
                "path": str(target),
                "filename": ref["filename"],
                "bytes": len(payload),
                "attachment_id": ref["attachment_id"],
            }
        )
    return written


class CachedAttachments(Mapping):
    """Mapping: channel id -> list of cached attachment records on this machine.

    A read-only view of what the local Discord client still holds, usable after the
    channel itself is gone.
    """

    def __init__(self, *, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir
        self._by_channel: Optional[dict] = None

    def _scan(self) -> dict:
        if self._by_channel is None:
            grouped: dict[str, list] = {}
            for ref in cached_attachment_refs(cache_dir=self.cache_dir):
                grouped.setdefault(ref["channel_id"], []).append(ref)
            self._by_channel = grouped
        return self._by_channel

    def __getitem__(self, channel_id) -> list[dict]:
        return self._scan()[str(channel_id)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._scan())

    def __len__(self) -> int:
        return len(self._scan())
