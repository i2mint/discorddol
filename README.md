# discorddol

Read a Discord server the way you read a dict.

```python
from discorddol import Guilds, as_text

guilds = Guilds()                     # servers your bot can see
channels = guilds['Cosmograph']       # Mapping: channel name -> messages
messages = channels['user-feedback']  # list of plain dicts
print(as_text(messages))              # readable transcript
```

Or from the command line:

```bash
discorddol guilds
discorddol channels Cosmograph --names-only
discorddol transcript Cosmograph user-feedback
discorddol export-channel Cosmograph user-feedback --out-dir ~/Downloads/discord
```

## Install

```bash
pip install discorddol
```

## Setting up the bot (five minutes, once)

`discorddol` reads Discord through a bot account. You create it; someone with *Manage Server* on the target server invites it.

1. Go to the [Discord developer portal](https://discord.com/developers/applications) and click **New Application**.
2. **Bot** tab → *Reset Token* → copy the token.
3. **Bot** tab → *Privileged Gateway Intents* → enable **Message Content Intent**. Without this, every message body comes back empty. Leave Presence and Server Members off; `discorddol` does not use them.
4. **Bot** tab → turn **off** *Public Bot*, so only you can invite it.
5. **OAuth2 → URL Generator** → scope `bot`, permissions **View Channels** and **Read Message History** (add **Send Messages** if you want `post-message`). Adding a permission later means generating a new invite URL and getting it re-authorized, so pick them now.
6. Open the generated URL, or send it to whoever administers the server, and authorize it there.

Then store the token where `discorddol` looks for it — the `DISCORD_BOT_TOKEN` environment variable, or [config2py](https://github.com/i2mint/config2py)'s config store:

```bash
printf %s "<your token>" > ~/.config/config2py/configs/DISCORD_BOT_TOKEN
chmod 600 ~/.config/config2py/configs/DISCORD_BOT_TOKEN
```

Sanity check — if this returns an empty list, the bot exists but has not been invited anywhere yet:

```bash
discorddol guilds
```

A bot only sees channels its role grants access to. Private channels need an explicit permission overwrite, which the server's admin has to add.

## The stores

Everything is a `collections.abc.Mapping`, so the usual idioms work: `in`, `len`, `.keys()`, `.items()`, dict comprehensions.

| Store | Keys | Values |
|---|---|---|
| `Guilds()` | guild name (id if ambiguous) | a `Channels` store |
| `Channels(guild_id)` | channel name (id if ambiguous) | list of message dicts |
| `Channels(guild_id).info` | same keys | channel metadata dict |
| `CachedAttachments()` | channel id | locally cached attachment records |

Channel lookup accepts a name, a `#name`, or an id, so all three of these are the same channel:

```python
channels['user-feedback']
channels['#user-feedback']
channels['1053014662142251079']
```

Threads are where a lot of real discussion ends up. They are not merged in by default, because it changes what "the channel" means:

```python
Channels(guild_id, include_threads=True)['user-feedback']  # messages and thread replies, in time order
```

Fetching a busy channel is slow and rate-limited, so pass any `MutableMapping` as a cache and repeat reads come from it. A `dict` works; so does anything from [dol](https://github.com/i2mint/dol), which is how you get a persistent one:

```python
from dol import JsonFiles

channels = Channels(guild_id, cache_store=JsonFiles('~/.cache/discord'))
```

Use `JsonFiles` rather than `Files` — cached values are lists of dicts, not bytes. The cache is keyed by channel id and survives across processes, so a second run of a long export starts from what the first one already fetched.

## Recovering a deleted channel

**Discord has no undelete.** Deleting a channel destroys its messages server-side. No bot, no API call, and no amount of owner permission brings them back, so this package cannot recover the text of a deleted channel and neither can anything else.

What *is* recoverable is attachments, because the desktop client is an Electron app and every image it displayed passed through Chromium's on-disk cache. Those URLs embed the channel id, so:

```bash
discorddol find-deleted-channels Cosmograph      # cached channel ids the server no longer lists
discorddol recover-attachments <channel_id> --out-dir ~/Downloads/recovered
```

`find-deleted-channels` returns *candidates*, not a verdict: an id shows up as an orphan if this machine cached attachments from it and the server does not currently list it, which also covers other servers and channels the bot cannot see. Confirm before concluding.

This matters because Discord attachment URLs are signed and expire — knowing the URL is not enough to re-download it. The cache holds the payload itself. `recover-attachments` needs no token and no network.

For message **text** from a deleted channel, the only route is a [Discord data export](https://support.discord.com/hc/en-us/articles/360004027692) (Settings → Data & Privacy → *Request all of my Data*). It contains your own sent messages filed by channel id, including channels that no longer exist — which is exactly what the channel id from `find-deleted-channels` is for. It covers only your own messages, so each participant has to request their own.

The cache reader is read-only and never writes to, moves, or deletes anything in the live cache directory that the running Discord client owns.

## Swapping the pieces

Four seams, each one keyword argument:

| Seam | Default | Swap it for |
|---|---|---|
| `token` | env var, then config2py store | any string you resolve yourself |
| `backend` | `DiscordRest` (live API) | `DictBackend` for tests and fixtures, or your own reader |
| `message_to_dict` | full JSON-safe record | a slimmer schema for LLM context |
| `cache_store` | `None` (always fetch) | any `MutableMapping`, e.g. a `dol` store |

`DictBackend` is a real backend, not a mock, which is what makes the test suite runnable without credentials:

```python
from discorddol import Channels, DictBackend

backend = DictBackend(
    channels={'1': [{'id': '10', 'name': 'dev'}]},
    messages={'10': [{'id': '100', 'clean_content': 'hi'}]},
)
Channels('1', backend=backend)['dev']
```

## Other surfaces

`discorddol.tools` is the single list of verbs, written as plain functions taking and returning JSON-able values, knowing nothing about MCP, HTTP or argparse. The CLI dispatches over that list, and an MCP server needs no change to the core:

```python
from py2mcp import mk_mcp_from_refs

mk_mcp_from_refs(['discorddol.tools:transcript', 'discorddol.tools:channels'])
```
