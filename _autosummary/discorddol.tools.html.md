# discorddol.tools

SSOT verb list for discorddol: plain functions, JSON-able in, JSON-able out.

Deliberately agnostic about how it is reached. This module knows nothing about MCP,
HTTP, argparse or any agent host; a wrapper references e.g. `discorddol.tools:messages`
and gets a clean JSON-ready value back. `__main__.py` builds the CLI off the same list
(`DISPATCH_FUNCS`), so a new verb becomes available on every surface at once.

### Module Attributes

| [`DISPATCH_FUNCS`](#discorddol.tools.DISPATCH_FUNCS)   | The SSOT list every surface dispatches over.   |
|-------------------------------------------------------------------|------------------------------------------------|

### Functions

| [`guilds`](#discorddol.tools.guilds)(\*[, token])                                | List the Discord servers the bot has been added to.                            |
|-----------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| [`channels`](#discorddol.tools.channels)(guild, \*[, token, names_only])           | List a server's channels and threads.                                          |
| [`messages`](#discorddol.tools.messages)(guild, channel, \*[, token, limit, ...])  | Fetch a channel's messages as dicts.                                           |
| [`transcript`](#discorddol.tools.transcript)(guild, channel, \*[, token, ...])       | Render a channel as readable transcript text -- the form to feed an LLM.       |
| [`export_channel`](#discorddol.tools.export_channel)(guild, channel, \*[, out_dir, ...]) | Export a channel (threads included by default) to a JSON file.                 |
| [`export_guild`](#discorddol.tools.export_guild)(guild, \*[, out_dir, token, ...])     | Export every channel the bot can read in a server, one JSONL file per channel. |
| [`find_deleted_channels`](#discorddol.tools.find_deleted_channels)(guild, \*[, token, ...])     | Find channel ids cached on this machine that no longer exist in a server.      |
| [`recover_attachments`](#discorddol.tools.recover_attachments)(channel_id, \*[, ...])         | Write out every attachment this machine cached for a channel, deleted or not.  |
| [`post_message`](#discorddol.tools.post_message)(channel_id, text, \*[, token, ...])   | Post a message to a channel.                                                   |

### discorddol.tools.DISPATCH_FUNCS *= [<function guilds>, <function channels>, <function messages>, <function transcript>, <function export_channel>, <function export_guild>, <function find_deleted_channels>, <function recover_attachments>, <function post_message>]*

The SSOT list every surface dispatches over. Add a verb here, get it everywhere.

### discorddol.tools.channels(guild, , token=None, names_only=False)

List a server’s channels and threads. `guild` may be a guild name or id.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)] | [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### discorddol.tools.export_channel(guild, channel, , out_dir='~/Downloads/discord', token=None, include_threads=True)

Export a channel (threads included by default) to a JSON file. Returns a summary.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### discorddol.tools.export_guild(guild, , out_dir=None, token=None, skip_preflight=False, quiet=False)

Export every channel the bot can read in a server, one JSONL file per channel.

Text and announcement channels, forum posts, and threads (active, archived, and
private archived ones when the bot has Manage Threads) are exported. Categories,
voice and stage channels are skipped, and the manifest says why. Re-running on the
same `out_dir` fetches only new messages, so an interrupted export resumes.

Before writing anything, recent messages are sampled to check that message bodies
are not blank, which is what a disabled Message Content Intent looks like;
`skip_preflight` turns that check off. Only one export at a time may write to a
folder. `out_dir` defaults to
`exports/<guild id>` in discorddol’s data folder. Progress goes to stderr unless
`quiet`. Returns a summary; the details are in the manifest.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### discorddol.tools.find_deleted_channels(guild, , token=None, cache_dir=None)

Find channel ids cached on this machine that no longer exist in a server.

Discord cannot undelete a channel, but the desktop client cached its attachments.
This is how you find the id of a deleted channel, so you can recover those
attachments with recover-attachments and locate the channel in a data export.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### discorddol.tools.guilds(, token=None)

List the Discord servers the bot has been added to.

Run this first: if it returns an empty list, the bot exists but has not been invited
to any server yet.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### discorddol.tools.messages(guild, channel, , token=None, limit=None, include_threads=False)

Fetch a channel’s messages as dicts. `limit=None` fetches the whole history.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### discorddol.tools.post_message(channel_id, text, , token=None, allowed_mentions=None, reply_to=None)

Post a message to a channel. Requires the bot to have Send Messages.

`@everyone`, `@here` and role mentions are not turned into pings unless
`allowed_mentions` (a dict, see `DiscordRest.send_message`) says so;
`reply_to` is the id of a message to answer.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### discorddol.tools.recover_attachments(channel_id, , out_dir='~/Downloads/discord_recovered', cache_dir=None)

Write out every attachment this machine cached for a channel, deleted or not.

Needs no token and no network: the bytes come from the local Discord client cache.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### discorddol.tools.transcript(guild, channel, , token=None, limit=None, include_threads=True)

Render a channel as readable transcript text – the form to feed an LLM.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
