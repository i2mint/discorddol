"""``send_message``: safe mention defaults and replies. No network: the client is a stub."""

from types import SimpleNamespace as NS

import discord
import pytest

from discorddol.base import DiscordRest, _allowed_mentions, message_to_dict
from discorddol.tests.test_message_to_dict import _message


def test_default_blocks_everyone_and_roles_but_not_users():
    am = _allowed_mentions(None)
    assert am.everyone is False and am.roles is False and am.users is not False  # users stay pingable


def test_dict_and_object_specs():
    am = _allowed_mentions({"everyone": False, "roles": False, "users": ["5", "6"]})
    assert [u.id for u in am.users] == [5, 6]
    explicit = discord.AllowedMentions.none()
    assert _allowed_mentions(explicit) is explicit
    with pytest.raises(ValueError):
        _allowed_mentions({"evryone": True})


class _Channel:
    def __init__(self):
        self.sent = None

    async def send(self, text, **kwargs):
        self.sent = (text, kwargs)
        return _message(id=101, content=text)


def _send(**options):
    channel = _Channel()

    class _Client:
        async def fetch_channel(self, channel_id):
            return channel

    class _Rest(DiscordRest):
        def _run(self, factory):  # skip login: run against the stub client
            import asyncio

            return asyncio.run(factory(_Client()))

    result = _Rest(token="t").send_message("10", "hi @everyone", **options)
    return result, channel.sent


def test_send_message_is_safe_by_default_and_does_not_reply():
    result, (text, kwargs) = _send()
    assert result["content"] == "hi @everyone"
    assert kwargs["allowed_mentions"].everyone is False
    assert "reference" not in kwargs


def test_send_message_reply_to_and_override():
    _, (_, kwargs) = _send(reply_to="99", allowed_mentions={"everyone": True})
    ref = kwargs["reference"]
    assert (ref.message_id, ref.channel_id) == (99, 10)
    assert kwargs["allowed_mentions"].everyone is True


def test_author_role_ids():
    everyone = NS(id=1, is_default=lambda: True)
    mod = NS(id=77, is_default=lambda: False)
    message = _message(author=NS(id=1, name="ana", display_name="Ana", bot=False, roles=[everyone, mod]))
    assert message_to_dict(message)["author"]["role_ids"] == ["77"]
    assert message_to_dict(_message())["author"]["role_ids"] == []
