"""Discord data object layers: read guilds, channels, threads and messages as Mappings.

Read a Discord server the way you read a dict::

    from discorddol import Guilds

    guilds = Guilds()                       # servers the bot can see
    channels = guilds['Cosmograph']         # Mapping: channel name -> messages
    messages = channels['user-feedback']    # list of message dicts
    print(as_text(messages))                # readable transcript

Credentials come from the ``DISCORD_BOT_TOKEN`` environment variable, falling back to
``config2py``'s store (``~/.config/config2py/configs/DISCORD_BOT_TOKEN``). See the README
for the five-minute bot setup, including the Message Content Intent that message bodies
depend on.

For a channel that has been **deleted**, see :mod:`discorddol.local_cache`: Discord has
no undelete, but the desktop client's on-disk cache still holds attachments it displayed.
"""

from .base import (
    Channels,
    DictBackend,
    DiscordRest,
    Guilds,
    as_text,
    channel_to_dict,
    get_token,
    message_to_dict,
)
from .local_cache import (
    CachedAttachments,
    cached_channel_ids,
    extract_channel_attachments,
    orphan_channel_ids,
)

__all__ = [
    "Guilds",
    "Channels",
    "DiscordRest",
    "DictBackend",
    "as_text",
    "get_token",
    "message_to_dict",
    "channel_to_dict",
    "CachedAttachments",
    "cached_channel_ids",
    "orphan_channel_ids",
    "extract_channel_attachments",
]
