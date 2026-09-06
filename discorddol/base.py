"""Core of discorddol: a Discord read backend and the ``Mapping`` stores over it.

The design has one hinge: **the backend**. Everything user-facing is a ``Mapping``
whose data comes from a backend object exposing four methods -- ``guilds``,
``channels``, ``threads``, ``messages`` -- each returning plain JSON-able dicts. The
default backend (:class:`DiscordRest`) talks to the live Discord API; swapping it for
one that reads a Discord data export, or a dict of fixtures, changes nothing above it.

The stores::

    Guilds()                       # Mapping: guild name/id -> Channels
    Channels(guild_id)             # Mapping: channel name/id -> list of message dicts
    Channels(guild_id).info        # Mapping: channel name/id -> channel metadata

so the common case reads like plain Python::

    from discorddol import Guilds

    msgs = Guilds()['Cosmograph']['user-feedback']

``DiscordRest`` uses discord.py in REST-only mode -- it logs in over HTTP but never
opens a gateway connection. That makes every call a one-shot fetch that returns
promptly, instead of a bot process you have to keep alive and shut down.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Callable, Iterator, MutableMapping, Optional, Protocol

__all__ = [
    "get_token",
    "message_to_dict",
    "channel_to_dict",
    "DiscordRest",
    "DictBackend",
    "Channels",
    "Guilds",
    "as_text",
]

DFLT_TOKEN_KEY = "DISCORD_BOT_TOKEN"


# --------------------------------------------------------------------------------------
# Seam 1: where the token comes from
# --------------------------------------------------------------------------------------


def get_token(key: str = DFLT_TOKEN_KEY) -> str:
    """Resolve a Discord token: environment variable first, then the config2py store.

    Raises a :class:`RuntimeError` naming the exact fix rather than a bare ``KeyError``,
    because a missing token is the single most common first-run failure.
    """
    if value := os.environ.get(key):
        return value
    try:
        from config2py import simple_config_getter

        token = simple_config_getter()(key)
    except Exception as error:  # noqa: BLE001 -- the actionable message beats the trace
        raise RuntimeError(_missing_token_message(key)) from error
    if not token:
        raise RuntimeError(_missing_token_message(key))
    return token


def _missing_token_message(key: str) -> str:
    return (
        f"No Discord token found under {key!r}.\n"
        f"Fix it with either:\n"
        f"  export {key}=...\n"
        f'  printf %s "<token>" > ~/.config/config2py/configs/{key}\n'
        f"Get a token at https://discord.com/developers/applications "
        f"-> your app -> Bot -> Reset Token.\n"
        f'Remember to enable the "Message Content Intent" on that same Bot page, '
        f"or message bodies come back empty."
    )


# --------------------------------------------------------------------------------------
# Seam 3: the record schema
# --------------------------------------------------------------------------------------


def message_to_dict(message) -> dict:
    """Serialize a ``discord.Message`` to a plain, JSON-safe dict."""

    def attachment(a):
        return {"filename": a.filename, "url": a.url, "size": a.size, "id": str(a.id)}

    return {
        "id": str(message.id),
        "channel_id": str(message.channel.id),
        "created_at": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "author": {
            "id": str(message.author.id),
            "name": message.author.name,
            "display_name": getattr(
                message.author, "display_name", message.author.name
            ),
            "bot": message.author.bot,
        },
        "content": message.content,
        "clean_content": message.clean_content,
        "attachments": [attachment(a) for a in message.attachments],
        "embeds": [e.to_dict() for e in message.embeds],
        "reactions": [
            {"emoji": str(r.emoji), "count": r.count} for r in message.reactions
        ],
        "reply_to": (
            str(message.reference.message_id)
            if message.reference and message.reference.message_id
            else None
        ),
        "pinned": message.pinned,
        "type": str(message.type),
        "jump_url": message.jump_url,
    }


def channel_to_dict(channel) -> dict:
    """Serialize a channel or thread to a plain dict."""
    guild = getattr(channel, "guild", None)
    return {
        "id": str(channel.id),
        "name": getattr(channel, "name", None),
        "type": str(getattr(channel, "type", type(channel).__name__)),
        "category": getattr(getattr(channel, "category", None), "name", None),
        "topic": getattr(channel, "topic", None),
        "created_at": (
            channel.created_at.isoformat()
            if getattr(channel, "created_at", None)
            else None
        ),
        "guild_id": str(guild.id) if guild else None,
        "guild_name": getattr(guild, "name", None),
        "parent_id": str(channel.parent_id)
        if getattr(channel, "parent_id", None)
        else None,
        "is_thread": type(channel).__name__ == "Thread",
    }


# --------------------------------------------------------------------------------------
# Seam 2: the backend
# --------------------------------------------------------------------------------------


class Backend(Protocol):
    """What a discorddol backend must provide. Four methods, all returning plain dicts."""

    def guilds(self) -> list[dict]: ...
    def channels(self, guild_id: str) -> list[dict]: ...
    def threads(self, channel_id: str) -> list[dict]: ...
    def messages(self, channel_id: str, **kwargs) -> list[dict]: ...


class DiscordRest:
    """Live Discord backend: discord.py in REST-only mode (login, no gateway).

    >>> backend = DiscordRest(token='fake')  # no network happens until a call is made
    >>> backend.token
    'fake'
    """

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        message_to_dict: Callable[[Any], dict] = message_to_dict,
    ):
        self._token = token
        self.message_to_dict = message_to_dict

    @property
    def token(self) -> str:
        """The token, resolved lazily so constructing a backend never needs credentials."""
        if self._token is None:
            self._token = get_token()
        return self._token

    def _run(self, coro_factory):
        """Log in, run one interaction, close. No gateway connection is opened."""
        import discord

        token = self.token

        async def main():
            client = discord.Client(intents=discord.Intents.none())
            async with client:
                await client.login(token)
                return await coro_factory(client)

        return asyncio.run(main())

    def guilds(self) -> list[dict]:
        """List servers the bot has been added to."""

        async def fetch(client):
            return [
                {"id": str(g.id), "name": g.name}
                async for g in client.fetch_guilds(limit=200)
            ]

        return self._run(fetch)

    def channels(self, guild_id: str) -> list[dict]:
        """List a guild's channels, including its currently active threads."""

        async def fetch(client):
            guild = await client.fetch_guild(int(guild_id))
            found = list(await guild.fetch_channels())
            found.extend(await guild.active_threads())
            return [channel_to_dict(c) for c in found]

        return self._run(fetch)

    def threads(self, channel_id: str) -> list[dict]:
        """List a channel's threads, archived ones included -- often where the content is."""

        async def fetch(client):
            channel = await client.fetch_channel(int(channel_id))
            found = []
            if hasattr(channel, "archived_threads"):
                found.extend([t async for t in channel.archived_threads(limit=None)])
            guild = getattr(channel, "guild", None)
            if guild is not None:
                found.extend(
                    [
                        t
                        for t in await guild.active_threads()
                        if t.parent_id == channel.id
                    ]
                )
            return [channel_to_dict(t) for t in found]

        return self._run(fetch)

    def messages(
        self,
        channel_id: str,
        *,
        limit: Optional[int] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        oldest_first: bool = True,
    ) -> list[dict]:
        """Fetch a channel's message history. ``limit=None`` means the whole thing."""

        async def fetch(client):
            channel = await client.fetch_channel(int(channel_id))
            return [
                self.message_to_dict(m)
                async for m in channel.history(
                    limit=limit, after=after, before=before, oldest_first=oldest_first
                )
            ]

        return self._run(fetch)

    def send_message(self, channel_id: str, text: str) -> dict:
        """Post a message to a channel. The one write operation; needs Send Messages.

        Read access is what discorddol is for -- this exists so that acting on what you
        read does not require a second library.
        """

        async def send(client):
            channel = await client.fetch_channel(int(channel_id))
            return self.message_to_dict(await channel.send(text))

        return self._run(send)


class DictBackend:
    """In-memory backend over plain dicts. Real enough for tests, fixtures and exports.

    >>> backend = DictBackend(
    ...     guilds=[{'id': '1', 'name': 'Cosmograph'}],
    ...     channels={'1': [{'id': '10', 'name': 'dev'}]},
    ...     messages={'10': [{'id': '100', 'content': 'hi'}]},
    ... )
    >>> backend.guilds()
    [{'id': '1', 'name': 'Cosmograph'}]
    >>> backend.messages('10')
    [{'id': '100', 'content': 'hi'}]
    """

    def __init__(
        self,
        *,
        guilds: Optional[list[dict]] = None,
        channels: Optional[Mapping] = None,
        messages: Optional[Mapping] = None,
        threads: Optional[Mapping] = None,
    ):
        self._guilds = list(guilds or ())
        self._channels = dict(channels or {})
        self._messages = dict(messages or {})
        self._threads = dict(threads or {})

    def guilds(self) -> list[dict]:
        return list(self._guilds)

    def channels(self, guild_id: str) -> list[dict]:
        return list(self._channels.get(str(guild_id), ()))

    def threads(self, channel_id: str) -> list[dict]:
        return list(self._threads.get(str(channel_id), ()))

    def messages(self, channel_id: str, **kwargs) -> list[dict]:
        found = list(self._messages.get(str(channel_id), ()))
        if (limit := kwargs.get("limit")) is not None:
            found = found[:limit]
        return found


def _dflt_backend(backend, token):
    return DiscordRest(token=token) if backend is None else backend


# --------------------------------------------------------------------------------------
# The stores
# --------------------------------------------------------------------------------------


class _ChannelInfo(Mapping):
    """Mapping: channel name (or id) -> channel metadata dict."""

    def __init__(self, channels: "Channels"):
        self._channels = channels

    def __getitem__(self, key) -> dict:
        return self._channels._records()[self._channels._resolve(key)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._channels)

    def __len__(self) -> int:
        return len(self._channels)


class Channels(Mapping):
    """Mapping of one guild's channels: key -> list of message dicts.

    Keys are channel names when unambiguous, and channel ids otherwise. Lookup accepts
    either form, plus a leading ``#``, so ``channels['#dev']`` and ``channels['dev']``
    and ``channels['10']`` all work.

    >>> backend = DictBackend(
    ...     channels={'1': [{'id': '10', 'name': 'dev'}, {'id': '11', 'name': 'feedback'}]},
    ...     messages={'10': [{'id': '100', 'content': 'hi'}]},
    ... )
    >>> channels = Channels('1', backend=backend)
    >>> sorted(channels)
    ['dev', 'feedback']
    >>> channels['dev']
    [{'id': '100', 'content': 'hi'}]
    >>> channels.info['dev']['id']
    '10'
    """

    def __init__(
        self,
        guild_id: str,
        *,
        backend: Optional[Backend] = None,
        token: Optional[str] = None,
        cache_store: Optional[MutableMapping] = None,
        include_threads: bool = False,
        **message_kwargs,
    ):
        self.guild_id = str(guild_id)
        self.backend = _dflt_backend(backend, token)
        self.cache_store = cache_store
        self.include_threads = include_threads
        self.message_kwargs = message_kwargs
        self._records_cache: Optional[dict] = None
        self.info = _ChannelInfo(self)

    def _records(self) -> dict:
        """key -> channel record. Fetched once per instance; call ``refresh`` to redo."""
        if self._records_cache is None:
            records = self.backend.channels(self.guild_id)
            by_name: dict[str, list] = {}
            for record in records:
                by_name.setdefault(record.get("name") or record["id"], []).append(
                    record
                )
            self._records_cache = {
                (name if len(group) == 1 else group[i]["id"]): group[i]
                for name, group in by_name.items()
                for i in range(len(group))
            }
        return self._records_cache

    def refresh(self) -> "Channels":
        """Drop the cached channel listing so the next access refetches it."""
        self._records_cache = None
        return self

    def _resolve(self, key) -> str:
        """Map a user-supplied key (name, '#name', or id) onto a store key."""
        key = str(key).lstrip("#")
        records = self._records()
        if key in records:
            return key
        for store_key, record in records.items():
            if record["id"] == key or record.get("name") == key:
                return store_key
        raise KeyError(
            f"No channel {key!r} in guild {self.guild_id}. "
            f"Available: {sorted(records)[:20]}" + ("..." if len(records) > 20 else "")
        )

    def __getitem__(self, key) -> list[dict]:
        store_key = self._resolve(key)
        channel_id = self._records()[store_key]["id"]
        if self.cache_store is not None and channel_id in self.cache_store:
            return self.cache_store[channel_id]
        messages = self.backend.messages(channel_id, **self.message_kwargs)
        if self.include_threads:
            for thread in self.backend.threads(channel_id):
                messages.extend(
                    self.backend.messages(thread["id"], **self.message_kwargs)
                )
            messages.sort(key=lambda m: m.get("created_at") or "")
        if self.cache_store is not None:
            self.cache_store[channel_id] = messages
        return messages

    def __iter__(self) -> Iterator[str]:
        return iter(self._records())

    def __len__(self) -> int:
        return len(self._records())

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.guild_id!r})"


class Guilds(Mapping):
    """Mapping of the servers the bot can see: key -> :class:`Channels`.

    Keys are guild names when unambiguous, ids otherwise; lookup accepts either.

    >>> backend = DictBackend(
    ...     guilds=[{'id': '1', 'name': 'Cosmograph'}],
    ...     channels={'1': [{'id': '10', 'name': 'dev'}]},
    ...     messages={'10': [{'id': '100', 'content': 'hi'}]},
    ... )
    >>> guilds = Guilds(backend=backend)
    >>> list(guilds)
    ['Cosmograph']
    >>> guilds['Cosmograph']['dev']
    [{'id': '100', 'content': 'hi'}]
    """

    def __init__(
        self,
        *,
        backend: Optional[Backend] = None,
        token: Optional[str] = None,
        cache_store: Optional[MutableMapping] = None,
        **channels_kwargs,
    ):
        self.backend = _dflt_backend(backend, token)
        self.cache_store = cache_store
        self.channels_kwargs = channels_kwargs
        self._records_cache: Optional[dict] = None

    def _records(self) -> dict:
        if self._records_cache is None:
            records = self.backend.guilds()
            by_name: dict[str, list] = {}
            for record in records:
                by_name.setdefault(record.get("name") or record["id"], []).append(
                    record
                )
            self._records_cache = {
                (name if len(group) == 1 else group[i]["id"]): group[i]
                for name, group in by_name.items()
                for i in range(len(group))
            }
        return self._records_cache

    def _resolve(self, key) -> str:
        key = str(key)
        records = self._records()
        if key in records:
            return key
        for store_key, record in records.items():
            if record["id"] == key or record.get("name") == key:
                return store_key
        raise KeyError(f"No guild {key!r}. Available: {sorted(records)}")

    def __getitem__(self, key) -> Channels:
        record = self._records()[self._resolve(key)]
        return Channels(
            record["id"],
            backend=self.backend,
            cache_store=self.cache_store,
            **self.channels_kwargs,
        )

    def __iter__(self) -> Iterator[str]:
        return iter(self._records())

    def __len__(self) -> int:
        return len(self._records())


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def as_text(messages, *, with_timestamps: bool = True) -> str:
    """Render message dicts as a readable transcript -- the form you feed to an LLM.

    >>> print(as_text([{'created_at': '2026-09-04T10:00:00',
    ...                 'author': {'display_name': 'thor'},
    ...                 'clean_content': 'hello'}]))
    [2026-09-04T10:00] thor: hello
    """

    def line(m):
        who = (m.get("author") or {}).get("display_name", "?")
        body = m.get("clean_content") or m.get("content") or ""
        for a in m.get("attachments", ()):
            body += f"\n    [attachment: {a['filename']}]"
        stamp = f"[{(m.get('created_at') or '')[:16]}] " if with_timestamps else ""
        return f"{stamp}{who}: {body}"

    return "\n".join(map(line, messages))
