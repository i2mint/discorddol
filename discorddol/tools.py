"""SSOT verb list for discorddol: plain functions, JSON-able in, JSON-able out.

Deliberately agnostic about how it is reached. This module knows nothing about MCP,
HTTP, argparse or any agent host; a wrapper references e.g. ``discorddol.tools:messages``
and gets a clean JSON-ready value back. ``__main__.py`` builds the CLI off the same list
(``DISPATCH_FUNCS``), so a new verb becomes available on every surface at once.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from .base import Channels, DiscordRest, Guilds, as_text
from .export import MANIFEST_FILENAME, default_export_dir
from .export import export_guild as _export_guild

__all__ = [
    "guilds",
    "channels",
    "messages",
    "transcript",
    "export_channel",
    "export_guild",
    "find_deleted_channels",
    "recover_attachments",
    "post_message",
    "DISPATCH_FUNCS",
]


def guilds(*, token: Optional[str] = None) -> list[dict]:
    """List the Discord servers the bot has been added to.

    Run this first: if it returns an empty list, the bot exists but has not been invited
    to any server yet.
    """
    return DiscordRest(token=token).guilds()


def channels(
    guild: str, *, token: Optional[str] = None, names_only: bool = False
) -> list[dict] | list[str]:
    """List a server's channels and threads. ``guild`` may be a guild name or id."""
    store = Guilds(token=token)
    channel_store = store[guild]
    if names_only:
        return sorted(channel_store)
    return [channel_store.info[key] for key in channel_store]


def messages(
    guild: str,
    channel: str,
    *,
    token: Optional[str] = None,
    limit: Optional[int] = None,
    include_threads: bool = False,
) -> list[dict]:
    """Fetch a channel's messages as dicts. ``limit=None`` fetches the whole history."""
    store = Guilds(token=token, limit=limit, include_threads=include_threads)
    return store[guild][channel]


def transcript(
    guild: str,
    channel: str,
    *,
    token: Optional[str] = None,
    limit: Optional[int] = None,
    include_threads: bool = True,
) -> str:
    """Render a channel as readable transcript text -- the form to feed an LLM."""
    return as_text(
        messages(
            guild, channel, token=token, limit=limit, include_threads=include_threads
        )
    )


def export_channel(
    guild: str,
    channel: str,
    *,
    out_dir: str = "~/Downloads/discord",
    token: Optional[str] = None,
    include_threads: bool = True,
) -> dict:
    """Export a channel (threads included by default) to a JSON file. Returns a summary."""
    store = Guilds(token=token, include_threads=include_threads)
    channel_store = store[guild]
    info = channel_store.info[channel_store._resolve(channel)]
    records = channel_store[channel]
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{info.get('name') or info['id']}_{info['id']}.json"
    path.write_text(
        json.dumps({"channel": info, "messages": records}, indent=2, ensure_ascii=False)
    )
    return {"path": str(path), "n_messages": len(records), "channel": info}


def export_guild(
    guild: str,
    *,
    out_dir: Optional[str] = None,
    token: Optional[str] = None,
    skip_preflight: bool = False,
    quiet: bool = False,
) -> dict:
    """Export every channel the bot can read in a server, one JSONL file per channel.

    Text and announcement channels, forum posts, and threads (active, archived, and
    private archived ones when the bot has Manage Threads) are exported. Categories,
    voice and stage channels are skipped, and the manifest says why. Re-running on the
    same ``out_dir`` fetches only new messages, so an interrupted export resumes.

    Before writing anything, recent messages are sampled to check that message bodies
    are not blank, which is what a disabled Message Content Intent looks like;
    ``skip_preflight`` turns that check off. Only one export at a time may write to a
    folder. ``out_dir`` defaults to
    ``exports/<guild id>`` in discorddol's data folder. Progress goes to stderr unless
    ``quiet``. Returns a summary; the details are in the manifest.
    """
    store = Guilds(token=token)
    record = store._records()[store._resolve(guild)]
    out = Path(out_dir).expanduser() if out_dir else default_export_dir(record["id"])
    manifest = _export_guild(
        record["id"],
        out,
        backend=store.backend,
        guild_name=record.get("name"),
        preflight=not skip_preflight,
        log=None if quiet else (lambda line: print(line, file=sys.stderr)),
    )
    return {
        "out_dir": str(out),
        "manifest": str(out / MANIFEST_FILENAME),
        **manifest["run"],
        "channels": len(manifest["channels"]),
        "messages": sum(c["message_count"] for c in manifest["channels"]),
        "skipped": len(manifest["skipped"]),
        "not_fetched": len(manifest["not_fetched"]),
    }


def find_deleted_channels(
    guild: str, *, token: Optional[str] = None, cache_dir: Optional[str] = None
) -> dict:
    """Find channel ids cached on this machine that no longer exist in a server.

    Discord cannot undelete a channel, but the desktop client cached its attachments.
    This is how you find the id of a deleted channel, so you can recover those
    attachments with recover-attachments and locate the channel in a data export.
    """
    from .local_cache import orphan_channel_ids

    store = Guilds(token=token)
    guild_id = store._records()[store._resolve(guild)]["id"]
    return orphan_channel_ids(guild_id, token=token, cache_dir=cache_dir)


def recover_attachments(
    channel_id: str, *, out_dir: str = "~/Downloads/discord_recovered", cache_dir=None
) -> dict:
    """Write out every attachment this machine cached for a channel, deleted or not.

    Needs no token and no network: the bytes come from the local Discord client cache.
    """
    from .local_cache import extract_channel_attachments

    written = extract_channel_attachments(
        channel_id, out_dir=out_dir, cache_dir=cache_dir
    )
    return {
        "out_dir": str(Path(out_dir).expanduser()),
        "n_files": len(written),
        "total_bytes": sum(w["bytes"] for w in written),
        "files": written,
    }


def post_message(channel_id: str, text: str, *, token: Optional[str] = None) -> dict:
    """Post a message to a channel. Requires the bot to have Send Messages."""
    return DiscordRest(token=token).send_message(channel_id, text)


#: The SSOT list every surface dispatches over. Add a verb here, get it everywhere.
DISPATCH_FUNCS = [
    guilds,
    channels,
    messages,
    transcript,
    export_channel,
    export_guild,
    find_deleted_channels,
    recover_attachments,
    post_message,
]
