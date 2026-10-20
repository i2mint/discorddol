# discorddol.local_cache

Read the Discord desktop client’s on-disk HTTP cache – including for deleted channels.

Discord has no undelete: deleting a channel destroys its messages server-side, and no
API, bot or owner permission brings them back. But the desktop client is an Electron
app, so every attachment it ever displayed passed through Chromium’s HTTP cache and the
**bytes are still on disk**. Attachment URLs embed the channel id:

```default
https://cdn.discordapp.com/attachments/{channel_id}/{attachment_id}/{filename}
```

which gives a recovery path for a channel that no longer exists:

1. [`cached_channel_ids()`](#discorddol.local_cache.cached_channel_ids) – every channel id with cached attachments.
2. [`orphan_channel_ids()`](#discorddol.local_cache.orphan_channel_ids) – those that no longer resolve against the live API.
   A deleted channel shows up here.
3. [`extract_channel_attachments()`](#discorddol.local_cache.extract_channel_attachments) – write those bytes back out to real files.

Step 3 matters because Discord attachment URLs are signed and expire, so re-downloading
them is not an option even when you know the URL. The cache holds the payload itself.

Message **text** is not recoverable this way – Discord’s API responses are sent
`no-store` and never hit the disk cache. For text, request your Discord data export
(Settings -> Data & Privacy -> Request all of my Data), which files your own sent
messages by channel id, including channels that no longer exist. The channel id from
step 2 is what lets you find the right folder in that export.

This is a read-only reader. It never writes to, moves, or deletes anything in the live
cache directory, which the running Discord client owns.

### Functions

| [`discord_cache_dir`](#discorddol.local_cache.discord_cache_dir)(\*[, app])                     | Default Discord desktop cache directory for this platform.                     |
|---------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`cached_attachment_refs`](#discorddol.local_cache.cached_attachment_refs)(\*[, cache_dir])          | Yield one record per cached attachment URL found in the cache.                 |
| [`cached_channel_ids`](#discorddol.local_cache.cached_channel_ids)(\*[, cache_dir])              | Channel ids appearing in cached attachment URLs, counted by cached attachment. |
| [`orphan_channel_ids`](#discorddol.local_cache.orphan_channel_ids)(guild_id, \*[, backend, ...]) | Cached channel ids that no longer exist in `guild_id` -- deleted channels.     |
| [`extract_channel_attachments`](#discorddol.local_cache.extract_channel_attachments)(channel_id, \*, ...) | Write every cached attachment belonging to `channel_id` out to `out_dir`.      |

### Classes

| [`CachedAttachments`](#discorddol.local_cache.CachedAttachments)(\*[, cache_dir])   | Mapping: channel id -> list of cached attachment records on this machine.   |
|---------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|

### *class* discorddol.local_cache.CachedAttachments(, cache_dir=None)

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)

Mapping: channel id -> list of cached attachment records on this machine.

A read-only view of what the local Discord client still holds, usable after the
channel itself is gone.

### discorddol.local_cache.cached_attachment_refs(, cache_dir=None)

Yield one record per cached attachment URL found in the cache.

Each record has `channel_id`, `attachment_id`, `filename` and `cache_file`.
Streams rather than accumulating, so a multi-gigabyte cache stays cheap to scan.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### discorddol.local_cache.cached_channel_ids(, cache_dir=None)

Channel ids appearing in cached attachment URLs, counted by cached attachment.

The counts are a usefully blunt signal: a channel with many cached attachments was
one you actually spent time in.

* **Return type:**
  [`Counter`](https://docs.python.org/3/library/collections.html#collections.Counter)

### discorddol.local_cache.discord_cache_dir(, app='discord')

Default Discord desktop cache directory for this platform.

```pycon
>>> isinstance(discord_cache_dir(), Path)
True
```

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### discorddol.local_cache.extract_channel_attachments(channel_id, , out_dir, cache_dir=None)

Write every cached attachment belonging to `channel_id` out to `out_dir`.

Returns one record per file written, with the original filename and the bytes
recovered. Extraction is heuristic – it locates a known file signature inside the
cache entry and reads to the entry’s EOF record – so verify anything that matters
by opening it.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### discorddol.local_cache.orphan_channel_ids(guild_id, , backend=None, token=None, cache_dir=None)

Cached channel ids that no longer exist in `guild_id` – deleted channels.

Returns `{'orphans': {channel_id: count}, 'live': {channel_id: count}}`, each
ordered by count descending.

An id under `orphans` is a *candidate*, not a verdict: it is a channel this
machine cached attachments from that is not currently visible in `guild_id`. That
covers deleted channels, but also channels in other servers and channels the bot
lacks permission to see. Cross-check a candidate against
[`extract_channel_attachments()`](#discorddol.local_cache.extract_channel_attachments) output before concluding anything.

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
