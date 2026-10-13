# discorddol.export

Export a whole guild to disk: every channel the bot can read, one JSONL file each.

[`export_guild()`](#discorddol.export.export_guild) walks a guild’s channel listing and writes:

```default
<out_dir>/
    manifest.json                 what was exported, what was skipped, and why
    messages/<channel_id>.jsonl   one message dict per line, oldest first
```

What gets exported, by channel kind (see [`discorddol.base.CHANNEL_KINDS`](discorddol.base.html.md#discorddol.base.CHANNEL_KINDS)):

- **text** and **announcement** channels: their own history, then their threads;
- **forum** and **media** channels: their posts, which are threads;
- **threads**: the active ones, the public archived ones, and the private archived ones
  when the bot has Manage Threads. When it does not, the manifest lists what was not
  fetched under `not_fetched`, and the export carries on.

Categories, voice and stage channels are not exported, and neither is a channel whose
history Discord refuses to hand over. Each is listed under `skipped` with its reason.

**Incremental and resumable.** A channel’s file is its own cursor: a run reads the id on
the file’s last complete line and fetches only messages after it, a page at a time,
appending as it goes. An interrupted run loses at most the page it was writing; the next
run drops a half-written last line before appending, and carries on from there. A
channel whose `last_message_id` is already in its file is not fetched at all. Only one
run at a time may write to a folder; another is refused with [`ExportInProgress`](#discorddol.export.ExportInProgress).

**Content check.** Without the Message Content intent, Discord still returns every
message, but with its text, attachments and embeds blanked. An export in that state
looks like a success and holds nothing. So before anything is written,
[`check_message_content()`](#discorddol.export.check_message_content) samples recent messages written by people, a few from
each channel, and refuses to go on when most of them are blank. Until a full sample has
been judged, the export goes on judging the messages it fetches in the same way, and
refuses as soon as they show blank bodies, or at the latest when the run ends. Messages
written before that point stay on disk, so delete an export refused this way.

### Functions

| [`export_guild`](#discorddol.export.export_guild)(guild_id, out_dir, \*[, ...])   | Export every channel of a guild the bot can read into `out_dir`.                 |
|-----------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`check_message_content`](#discorddol.export.check_message_content)(backend, channels, \*) | Refuse to export blank messages: sample recent messages and check their content. |
| [`default_export_dir`](#discorddol.export.default_export_dir)(guild_id)                 | Where a guild's export goes when no `out_dir` is given: `<data>/exports/<id>`.   |

### Classes

| [`ChannelLogs`](#discorddol.export.ChannelLogs)(rootdir)   | Mapping: channel id -> that channel's exported messages, one JSONL file each.   |
|-------------------------------------------------------------------------|---------------------------------------------------------------------------------|

### Exceptions

| [`MessageContentMissing`](#discorddol.export.MessageContentMissing)   | Message bodies come back blank: the bot's Message Content Intent is off.   |
|--------------------------------------------------------------------------|----------------------------------------------------------------------------|
| [`ExportInProgress`](#discorddol.export.ExportInProgress)        | Another export is already writing to the same folder.                      |

### *class* discorddol.export.ChannelLogs(rootdir)

Bases: [`Mapping`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)

Mapping: channel id -> that channel’s exported messages, one JSONL file each.

The export’s writer as well as its reader: [`extend()`](#discorddol.export.ChannelLogs.extend) appends messages, and
[`summary()`](#discorddol.export.ChannelLogs.summary) gives a file’s message count, date range and cursor, parsing only
its first and last lines. Files hold messages oldest first, in the order appended.
Reading never changes a file; a half-written last line is ignored until the next
[`extend()`](#discorddol.export.ChannelLogs.extend) drops it.

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

### *exception* discorddol.export.ExportInProgress

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Another export is already writing to the same folder.

### *exception* discorddol.export.MessageContentMissing

Bases: [`RuntimeError`](https://docs.python.org/3/builtins/exceptions.html#RuntimeError)

Message bodies come back blank: the bot’s Message Content Intent is off.

### discorddol.export.check_message_content(backend, channels, , sample_size=20, min_channels=4, max_blank_share=0.5)

Refuse to export blank messages: sample recent messages and check their content.

Fetches the newest messages of each channel in `channels` (records with an
`id`), one call per channel, until `sample_size` messages written by people have
been collected. No channel supplies more than `sample_size // min_channels` of
them, so one channel full of stickers cannot decide the verdict. System notices and
bot messages do not count: a notice can be blank anyway, and a bot’s own messages
keep their content without the intent. A message is blank when it has no text, no
attachments and no embeds, which is how Discord sends every message when the intent
is off.

Raises [`MessageContentMissing`](#discorddol.export.MessageContentMissing), naming the fix, when more than
`max_blank_share` of the sample is blank. Returns `{'status': 'passed',
'checked', 'blank'}`, or `{'status': 'inconclusive', 'checked': 0, 'blank': 0}`
when there was nothing to judge.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> from discorddol.base import DictBackend
>>> backend = DictBackend(messages={'10': [{'id': '1', 'content': 'hi'}]})
>>> check_message_content(backend, [{'id': '10'}])
{'status': 'passed', 'checked': 1, 'blank': 0}
```

### discorddol.export.default_export_dir(guild_id)

Where a guild’s export goes when no `out_dir` is given: `<data>/exports/<id>`.

`<data>` is `$DISCORDDOL_DATA_DIR` when that is set, else discorddol’s app data
folder (`~/.local/share/discorddol` on macOS and Linux). Never inside a
repository, so exported messages are not committed by accident.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### discorddol.export.export_guild(guild_id, out_dir, , backend=None, token=None, guild_name=None, page_size=1000, private_threads=True, preflight=True, sample_size=20, log=None)

Export every channel of a guild the bot can read into `out_dir`.

Returns the manifest, which is also written to `out_dir`. Re-running on the same
`out_dir` fetches only what is new (see the module docstring).
`private_threads=False` does not ask for private archived threads.
`preflight=False` turns the content check off; `sample_size` is how many
messages make a full sample for it. `log` gets one line per channel.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

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
