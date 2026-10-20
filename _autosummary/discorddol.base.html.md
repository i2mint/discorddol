# discorddol.base

Core of discorddol: a Discord read backend and the `Mapping` stores over it.

The design has one hinge: **the backend**. Everything user-facing is a `Mapping`
whose data comes from a backend object exposing five methods – `guilds`,
`channels`, `threads`, `archived_threads`, `messages` – each returning plain
JSON-able dicts. The default backend ([`DiscordRest`](#discorddol.base.DiscordRest)) talks to the live Discord
API; swapping it for one that reads a Discord data export, or a dict of fixtures,
changes nothing above it.

The stores:

```default
Guilds()                       # Mapping: guild name/id -> Channels
Channels(guild_id)             # Mapping: channel name/id -> list of message dicts
Channels(guild_id).info        # Mapping: channel name/id -> channel metadata
```

so the common case reads like plain Python:

```default
from discorddol import Guilds

msgs = Guilds()['Example Guild']['feedback']
```

`DiscordRest` uses discord.py in REST-only mode – it logs in over HTTP but never
opens a gateway connection. That makes every call a one-shot fetch that returns
promptly, instead of a bot process you have to keep alive and shut down.

### Module Attributes

| [`CHANNEL_KINDS`](#discorddol.base.CHANNEL_KINDS)   | Discord's channel type (`str(channel.type)`) -> the kind discorddol reports.   |
|------------------------------------------------------------------|--------------------------------------------------------------------------------|

### Functions

| [`get_token`](#discorddol.base.get_token)([key])                         | Resolve a Discord token: environment variable first, then the config2py store.   |
|-------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`message_to_dict`](#discorddol.base.message_to_dict)(message)                 | Serialize a `discord.Message` to a plain, JSON-safe dict.                        |
| [`channel_to_dict`](#discorddol.base.channel_to_dict)(channel)                 | Serialize a channel or thread to a plain dict.                                   |
| [`channel_kind`](#discorddol.base.channel_kind)(record)                     | The kind of a channel record: `'text'`, `'announcement'`, `'forum'`, ...         |
| [`as_text`](#discorddol.base.as_text)(messages, \*[, with_timestamps]) | Render message dicts as a readable transcript -- the form you feed to an LLM.    |

### Classes

| [`DiscordRest`](#discorddol.base.DiscordRest)(\*[, token, message_to_dict])          | Live Discord backend: discord.py in REST-only mode (login, no gateway).                                   |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| [`DictBackend`](#discorddol.base.DictBackend)(\*[, guilds, channels, messages, ...]) | In-memory backend over plain dicts.                                                                       |
| [`Channels`](#discorddol.base.Channels)(guild_id, \*[, backend, token, ...])      | Mapping of one guild's channels: key -> list of message dicts.                                            |
| [`Guilds`](#discorddol.base.Guilds)(\*[, backend, token, cache_store])          | Mapping of the servers the bot can see: key -> [`Channels`](#discorddol.base.Channels). |

### Exceptions

| [`Forbidden`](#discorddol.base.Forbidden)   | Discord refused a request (HTTP 403): the bot's role lacks a permission for it.   |
|--------------------------------------------------------------|-----------------------------------------------------------------------------------|
| [`NotFound`](#discorddol.base.NotFound)    | Discord has no such object (HTTP 404), or does not show it to the bot.            |

### discorddol.base.CHANNEL_KINDS *= {'category': 'category', 'forum': 'forum', 'group': 'group_dm', 'media': 'media', 'news': 'announcement', 'news_thread': 'thread', 'private': 'dm', 'private_thread': 'thread', 'public_thread': 'thread', 'stage_voice': 'stage', 'text': 'text', 'voice': 'voice'}*

Discord’s channel type (`str(channel.type)`) -> the kind discorddol reports.

### *class* discorddol.base.Channels(guild_id, , backend=None, token=None, cache_store=None, include_threads=False, \*\*message_kwargs)

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)

Mapping of one guild’s channels: key -> list of message dicts.

Keys are channel names when unambiguous, and channel ids otherwise. Lookup accepts
either form, plus a leading `#`, so `channels['#dev']` and `channels['dev']`
and `channels['10']` all work.

A forum or a category has no messages of its own, so it maps to an empty list; with
`include_threads=True` a forum maps to the messages of its posts.

```pycon
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
```

#### refresh()

Drop the cached channel listing so the next access refetches it.

* **Return type:**
  [`Channels`](#discorddol.base.Channels)

### *class* discorddol.base.DictBackend(, guilds=None, channels=None, messages=None, threads=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

In-memory backend over plain dicts. Real enough for tests, fixtures and exports.

```pycon
>>> backend = DictBackend(
...     guilds=[{'id': '1', 'name': 'Example Guild'}],
...     channels={'1': [{'id': '10', 'name': 'dev'}]},
...     messages={'10': [{'id': '100', 'content': 'hi'}]},
... )
>>> backend.guilds()
[{'id': '1', 'name': 'Example Guild'}]
>>> backend.messages('10')
[{'id': '100', 'content': 'hi'}]
```

#### archived_threads(channel_id, , private=False)

The threads under `channel_id` marked `archived`: public, or private ones.

```pycon
>>> backend = DictBackend(threads={'10': [
...     {'id': '20', 'type': 'public_thread', 'archived': True},
...     {'id': '21', 'type': 'private_thread', 'archived': True},
...     {'id': '22', 'type': 'public_thread'},
... ]})
>>> [t['id'] for t in backend.archived_threads('10')]
['20']
>>> [t['id'] for t in backend.archived_threads('10', private=True)]
['21']
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### channel(channel_id)

The stored record of one channel or thread, wherever it is stored.

DMs belong to no guild: store them under any key, `'@me'` by convention.

```pycon
>>> backend = DictBackend(channels={
...     '1': [{'id': '10', 'name': 'dev', 'guild_id': '1'}],
...     '@me': [{'id': '50', 'type': 'private', 'recipients': ['7']}],
... })
>>> backend.channel('10')['name']
'dev'
>>> channel_kind(backend.channel(50))
'dm'
```

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

#### messages(channel_id, , limit=None, after=None, before=None, oldest_first=True, \*\*kwargs)

Stored messages, filtered the way [`DiscordRest.messages()`](#discorddol.base.DiscordRest.messages) filters them.

Messages are stored oldest first. `after` and `before` take a message id,
compared as a Discord snowflake (an integer), or a datetime, compared with each
message’s `created_at`. Other keyword arguments are accepted and ignored.

```pycon
>>> backend = DictBackend(messages={'10': [{'id': '1'}, {'id': '2'}, {'id': '3'}]})
>>> [m['id'] for m in backend.messages('10', after='1', limit=1)]
['2']
>>> [m['id'] for m in backend.messages('10', oldest_first=False, limit=1)]
['3']
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### *class* discorddol.base.DiscordRest(\*, token=None, message_to_dict=<function message_to_dict>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Live Discord backend: discord.py in REST-only mode (login, no gateway).

```pycon
>>> backend = DiscordRest(token='fake')  # no network happens until a call is made
>>> backend.token
'fake'
```

#### archived_threads(channel_id, , private=False)

List a channel’s archived threads: the public ones, or the private ones.

Listing private archived threads needs the Manage Threads permission; without it
Discord answers 403, raised as [`Forbidden`](#discorddol.base.Forbidden). Only text channels hold
private threads, so `private=True` on any other channel returns an empty list.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### channel(channel_id)

Look up one channel, thread or DM by id (see [`channel_to_dict()`](#discorddol.base.channel_to_dict)).

Tells a DM ([`channel_kind()`](#discorddol.base.channel_kind) `'dm'`, with its `recipients`) from a
server channel (with its `guild_id`). A channel the bot cannot see raises
[`NotFound`](#discorddol.base.NotFound) or [`Forbidden`](#discorddol.base.Forbidden).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

#### channels(guild_id)

List a guild’s channels, including its currently active threads.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### guilds()

List servers the bot has been added to.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### messages(channel_id, , limit=None, after=None, before=None, oldest_first=True)

Fetch a channel’s message history. `limit=None` means the whole thing.

`after` and `before` take a datetime or a message id, so the id of the last
message already stored works as a cursor.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### send_message(channel_id, text, , allowed_mentions=None, reply_to=None)

Post a message to a channel. The one write operation; needs Send Messages.

Read access is what discorddol is for – this exists so that acting on what you
read does not require a second library.

* **Parameters:**
  * **allowed_mentions** ([`Any`](https://docs.python.org/3/library/typing.html#typing.Any)) – which mentions in `text` Discord may turn into pings: a
    `discord.AllowedMentions` or a dict such as
    `{"everyone": False, "roles": False, "users": ["123"]}` (`users` and
    `roles` take `True`/`False` or a list of ids). The default,
    `DFLT_ALLOWED_MENTIONS`, blocks `@everyone`, `@here` and role
    mentions, so relaying quoted text cannot ping a whole server.
  * **reply_to** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – id of a message in this channel to answer (a Discord reply).
* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

#### threads(channel_id)

List a channel’s threads, archived ones included – often where the content is.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

#### *property* token *: [str](https://docs.python.org/3/builtins/stdtypes.html#str)*

The token, resolved lazily so constructing a backend never needs credentials.

### *exception* discorddol.base.Forbidden

Bases: [`PermissionError`](https://docs.python.org/3/builtins/exceptions.html#PermissionError)

Discord refused a request (HTTP 403): the bot’s role lacks a permission for it.

### *class* discorddol.base.Guilds(, backend=None, token=None, cache_store=None, \*\*channels_kwargs)

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)

Mapping of the servers the bot can see: key -> [`Channels`](#discorddol.base.Channels).

Keys are guild names when unambiguous, ids otherwise; lookup accepts either.

```pycon
>>> backend = DictBackend(
...     guilds=[{'id': '1', 'name': 'Example Guild'}],
...     channels={'1': [{'id': '10', 'name': 'dev'}]},
...     messages={'10': [{'id': '100', 'content': 'hi'}]},
... )
>>> guilds = Guilds(backend=backend)
>>> list(guilds)
['Example Guild']
>>> guilds['Example Guild']['dev']
[{'id': '100', 'content': 'hi'}]
```

### *exception* discorddol.base.NotFound

Bases: [`LookupError`](https://docs.python.org/3/builtins/exceptions.html#LookupError)

Discord has no such object (HTTP 404), or does not show it to the bot.

### discorddol.base.as_text(messages, , with_timestamps=True)

Render message dicts as a readable transcript – the form you feed to an LLM.

```pycon
>>> print(as_text([{'created_at': '2026-09-04T10:00:00',
...                 'author': {'display_name': 'ada'},
...                 'clean_content': 'hello'}]))
[2026-09-04T10:00] ada: hello
```

Stickers, polls and forwards each get a one-line form, so a message made only of
one of them is not blank:

```pycon
>>> print(as_text([{'author': {'display_name': 'ada'}, 'stickers': [{'name': 'wave'}],
...                 'poll': {'question': 'Lunch?', 'answers': [{'text': 'yes'}, {'text': 'no'}]},
...                 'forwarded': [{'content': 'original text'}]}],
...               with_timestamps=False))
ada:
    [sticker: wave]
    [poll: Lunch? -- yes | no]
    [forwarded: original text]
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### discorddol.base.channel_kind(record)

The kind of a channel record: `'text'`, `'announcement'`, `'forum'`, …

A record without a type counts as a text channel, which is what a hand-written
fixture usually means. An unknown type comes back as it is.

```pycon
>>> channel_kind({'type': 'news'})
'announcement'
>>> channel_kind({'type': 'ChannelType.stage_voice'})
'stage'
>>> channel_kind({'id': '10'})
'text'
>>> channel_kind({'type': 'ChannelType.private'})
'dm'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### discorddol.base.channel_to_dict(channel)

Serialize a channel or thread to a plain dict.

A direct-message channel (a DM, or a group DM) also carries `recipients`, the ids
of the users in it other than the bot, and `me_id`, the bot’s own user id (when
known), so its audience can be read off the record.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### discorddol.base.get_token(key='DISCORD_BOT_TOKEN')

Resolve a Discord token: environment variable first, then the config2py store.

Raises a [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError) naming the exact fix rather than a bare `KeyError`,
because a missing token is the single most common first-run failure.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### discorddol.base.message_to_dict(message)

Serialize a `discord.Message` to a plain, JSON-safe dict.

A message that is only a sticker, a poll or a forward has no text, attachments or
embeds of its own, so those live under `stickers`, `poll` (`None` when the
message has none) and `forwarded` (one entry per forwarded message, which is how
Discord holds a forward: a snapshot of the original).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
