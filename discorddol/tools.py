"""SSOT verb list for discorddol: plain functions, JSON-able in, JSON-able out.

Deliberately agnostic about how it is reached. This module knows nothing about MCP,
HTTP, argparse or any agent host; a wrapper references e.g. ``discorddol.tools:messages``
and gets a clean JSON-ready value back. ``__main__.py`` builds the CLI off the same list
(``DISPATCH_FUNCS``), so a new verb becomes available on every surface at once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .base import Channels, DiscordRest, Guilds, as_text

__all__ = [
    "guilds",
    "channels",
    "messages",
    "transcript",
    "export_channel",
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
    find_deleted_channels,
    recover_attachments,
    post_message,
]
