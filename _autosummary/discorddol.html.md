# discorddol

Discord data object layers: read guilds, channels, threads and messages as Mappings.

Read a Discord server the way you read a dict:

```default
from discorddol import Guilds

guilds = Guilds()                       # servers the bot can see
channels = guilds['Example Guild']      # Mapping: channel name -> messages
messages = channels['feedback']         # list of message dicts
print(as_text(messages))                # readable transcript
```

Credentials come from the `DISCORD_BOT_TOKEN` environment variable, falling back to
`config2py`’s store (`~/.config/config2py/configs/DISCORD_BOT_TOKEN`). See the README
for the five-minute bot setup, including the Message Content Intent that message bodies
depend on.

To export a whole server to disk – every readable channel, resumably – see
[`discorddol.export`](discorddol.export.html.md#module-discorddol.export).

For a channel that has been **deleted**, see [`discorddol.local_cache`](discorddol.local_cache.html.md#module-discorddol.local_cache): Discord has
no undelete, but the desktop client’s on-disk cache still holds attachments it displayed.

### Functions

| [`as_text`](#discorddol.as_text)(messages, \*[, with_timestamps])         | Render message dicts as a readable transcript -- the form you feed to an LLM.   |
|---------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| [`get_token`](#discorddol.get_token)([key])                                 | Resolve a Discord token: environment variable first, then the config2py store.  |
| [`message_to_dict`](#discorddol.message_to_dict)(message)                         | Serialize a `discord.Message` to a plain, JSON-safe dict.                       |
| [`channel_to_dict`](#discorddol.channel_to_dict)(channel)                         | Serialize a channel or thread to a plain dict.                                  |
| [`channel_kind`](#discorddol.channel_kind)(record)                             | The kind of a channel record: `'text'`, `'announcement'`, `'forum'`, ...        |
| [`export_guild`](#discorddol.export_guild)(guild_id, out_dir, \*[, ...])       | Export every channel of a guild the bot can read into `out_dir`.                |
| [`cached_channel_ids`](#discorddol.cached_channel_ids)(\*[, cache_dir])              | Channel ids appearing in cached attachment URLs, counted by cached attachment.  |
| [`orphan_channel_ids`](#discorddol.orphan_channel_ids)(guild_id, \*[, backend, ...]) | Cached channel ids that no longer exist in `guild_id` -- deleted channels.      |
| [`extract_channel_attachments`](#discorddol.extract_channel_attachments)(channel_id, \*, ...) | Write every cached attachment belonging to `channel_id` out to `out_dir`.       |

### Classes

| [`Guilds`](#discorddol.Guilds)(\*[, backend, token, cache_store])          | Mapping of the servers the bot can see: key -> [`Channels`](#discorddol.Channels).   |
|-----------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| [`Channels`](#discorddol.Channels)(guild_id, \*[, backend, token, ...])      | Mapping of one guild's channels: key -> list of message dicts.                                              |
| [`DiscordRest`](#discorddol.DiscordRest)(\*[, token, message_to_dict])          | Live Discord backend: discord.py in REST-only mode (login, no gateway).                                     |
| [`DictBackend`](#discorddol.DictBackend)(\*[, guilds, channels, messages, ...]) | In-memory backend over plain dicts.                                                                         |
| [`ChannelLogs`](#discorddol.ChannelLogs)(rootdir)                               | Mapping: channel id -> that channel's exported messages, one JSONL file each.                               |
| [`CachedAttachments`](#discorddol.CachedAttachments)(\*[, cache_dir])                 | Mapping: channel id -> list of cached attachment records on this machine.                                   |

### Exceptions

| [`Forbidden`](#discorddol.Forbidden)             | Discord refused a request (HTTP 403): the bot's role lacks a permission for it.   |
|------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| [`NotFound`](#discorddol.NotFound)              | Discord has no such object (HTTP 404), or does not show it to the bot.            |
| [`MessageContentMissing`](#discorddol.MessageContentMissing) | Message bodies come back blank: the bot's Message Content Intent is off.          |
| [`ExportInProgress`](#discorddol.ExportInProgress)      | Another export is already writing to the same folder.                             |

### *class* discorddol.CachedAttachments(, cache_dir=None)

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)

Mapping: channel id -> list of cached attachment records on this machine.

A read-only view of what the local Discord client still holds, usable after the
channel itself is gone.

### *class* discorddol.ChannelLogs(rootdir)

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)

Mapping: channel id -> that channel’s exported messages, one JSONL file each.

The export’s writer as well as its reader: [`extend()`](#discorddol.ChannelLogs.extend) appends messages, and
[`summary()`](#discorddol.ChannelLogs.summary) gives a file’s message count, date range and cursor, parsing only
its first and last lines. Files hold messages oldest first, in the order appended.
Reading never changes a file; a half-written last line is ignored until the next
[`extend()`](#discorddol.ChannelLogs.extend) drops it.

```pycon
>>> import tempfile
>>> with tempfile.TemporaryDirectory() as tmp:
...     logs = ChannelLogs(tmp)
...     _ = logs.extend('10', [{'id': '1', 'created_at': '2026-01-01T09:00:00'}])
...     _ = logs.extend('10', [{'id': '2', 'created_at': '2026-01-02T09:00:00'}])
...     print(list(logs), [m['id'] for m in logs['10']], logs.summary('10'))
['10'] ['1', '2'] {'message_count': 2, 'first_message_at': '2026-01-01T09:00:00', 'last_message_at': '2026-01-02T09:00:00', 'last_message_id': '2'}
```

#### extend(channel_id, messages)

Append messages to a channel’s file, creating it if needed. Returns how many.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

#### path(channel_id)

The JSONL file of one channel.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

#### summary(channel_id)

A channel’s message count, first and last dates, and last id (its cursor).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### *class* discorddol.Channels(guild_id, , backend=None, token=None, cache_store=None, include_threads=False, \*\*message_kwargs)

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
  [`Channels`](discorddol.base.html.md#discorddol.base.Channels)

### *class* discorddol.DictBackend(, guilds=None, channels=None, messages=None, threads=None)

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

#### messages(channel_id, , limit=None, after=None, before=None, oldest_first=True, \*\*kwargs)

Stored messages, filtered the way [`DiscordRest.messages()`](#discorddol.DiscordRest.messages) filters them.

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

### *class* discorddol.DiscordRest(\*, token=None, message_to_dict=<function message_to_dict>)

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
Discord answers 403, raised as [`Forbidden`](#discorddol.Forbidden). Only text channels hold
private threads, so `private=True` on any other channel returns an empty list.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

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

### *exception* discorddol.ExportInProgress

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Another export is already writing to the same folder.

### *exception* discorddol.Forbidden

Bases: [`PermissionError`](https://docs.python.org/3/builtins/exceptions.html#PermissionError)

Discord refused a request (HTTP 403): the bot’s role lacks a permission for it.

### *class* discorddol.Guilds(, backend=None, token=None, cache_store=None, \*\*channels_kwargs)

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)

Mapping of the servers the bot can see: key -> [`Channels`](#discorddol.Channels).

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

### *exception* discorddol.MessageContentMissing

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Message bodies come back blank: the bot’s Message Content Intent is off.

### *exception* discorddol.NotFound

Bases: [`LookupError`](https://docs.python.org/3/builtins/exceptions.html#LookupError)

Discord has no such object (HTTP 404), or does not show it to the bot.

### discorddol.as_text(messages, , with_timestamps=True)

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

### discorddol.cached_channel_ids(, cache_dir=None)

Channel ids appearing in cached attachment URLs, counted by cached attachment.

The counts are a usefully blunt signal: a channel with many cached attachments was
one you actually spent time in.

* **Return type:**
  [`Counter`](https://docs.python.org/3/library/collections.html#collections.Counter)

### discorddol.channel_kind(record)

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
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### discorddol.channel_to_dict(channel)

Serialize a channel or thread to a plain dict.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### discorddol.export_guild(guild_id, out_dir, , backend=None, token=None, guild_name=None, page_size=1000, private_threads=True, preflight=True, sample_size=20, log=None)

Export every channel of a guild the bot can read into `out_dir`.

Returns the manifest, which is also written to `out_dir`. Re-running on the same
`out_dir` fetches only what is new (see the module docstring).
`private_threads=False` does not ask for private archived threads.
`preflight=False` turns the content check off; `sample_size` is how many
messages make a full sample for it. `log` gets one line per channel.

```pycon
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
```

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### discorddol.extract_channel_attachments(channel_id, , out_dir, cache_dir=None)

Write every cached attachment belonging to `channel_id` out to `out_dir`.

Returns one record per file written, with the original filename and the bytes
recovered. Extraction is heuristic – it locates a known file signature inside the
cache entry and reads to the entry’s EOF record – so verify anything that matters
by opening it.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### discorddol.get_token(key='DISCORD_BOT_TOKEN')

Resolve a Discord token: environment variable first, then the config2py store.

Raises a [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError) naming the exact fix rather than a bare `KeyError`,
because a missing token is the single most common first-run failure.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### discorddol.message_to_dict(message)

Serialize a `discord.Message` to a plain, JSON-safe dict.

A message that is only a sticker, a poll or a forward has no text, attachments or
embeds of its own, so those live under `stickers`, `poll` (`None` when the
message has none) and `forwarded` (one entry per forwarded message, which is how
Discord holds a forward: a snapshot of the original).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### discorddol.orphan_channel_ids(guild_id, , backend=None, token=None, cache_dir=None)

Cached channel ids that no longer exist in `guild_id` – deleted channels.

Returns `{'orphans': {channel_id: count}, 'live': {channel_id: count}}`, each
ordered by count descending.

An id under `orphans` is a *candidate*, not a verdict: it is a channel this
machine cached attachments from that is not currently visible in `guild_id`. That
covers deleted channels, but also channels in other servers and channels the bot
lacks permission to see. Cross-check a candidate against
[`extract_channel_attachments()`](#discorddol.extract_channel_attachments) output before concluding anything.

```pycon
>>> import tempfile
>>> from discorddol.base import DictBackend
>>> backend = DictBackend(channels={'1': [{'id': '10', 'name': 'dev'}]})
>>> with tempfile.TemporaryDirectory() as tmp:
...     result = orphan_channel_ids('1', backend=backend, cache_dir=tmp)
>>> sorted(result)
['live', 'orphans']
```

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### Modules

| [`base`](discorddol.base.html.md#module-discorddol.base)               | Core of discorddol: a Discord read backend and the `Mapping` stores over it.            |
|--------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| [`export`](discorddol.export.html.md#module-discorddol.export)           | Export a whole guild to disk: every channel the bot can read, one JSONL file each.      |
| [`local_cache`](discorddol.local_cache.html.md#module-discorddol.local_cache) | Read the Discord desktop client's on-disk HTTP cache -- including for deleted channels. |
| [`tools`](discorddol.tools.html.md#module-discorddol.tools)             | SSOT verb list for discorddol: plain functions, JSON-able in, JSON-able out.            |
