"""``channel(channel_id)``: one channel by id, telling a DM from a server channel. No network."""

import asyncio
from types import SimpleNamespace as NS

import discord
import pytest

from discorddol import DictBackend, NotFound, channel_kind, channel_to_dict
from discorddol.base import DiscordRest


class _State:
    """The one ConnectionState method a DMChannel needs at construction."""

    def store_user(self, data):
        return NS(id=int(data["id"]), name=data.get("username"))


def _dm(recipient_id="7", me_id="3"):
    data = {
        "id": "50",
        "type": 1,
        "recipients": [{"id": recipient_id, "username": "example-user"}],
        "last_message_id": None,
    }
    return discord.DMChannel(me=NS(id=int(me_id)), state=_State(), data=data)


def test_a_dm_is_a_dm_with_its_recipient_and_the_bot():
    record = channel_to_dict(_dm())
    assert channel_kind(record) == "dm"
    assert record["guild_id"] is None
    assert record["recipients"] == ["7"] and record["me_id"] == "3"


def test_a_server_channel_has_no_recipients_key():
    channel = NS(
        id=10, name="dev", type=discord.ChannelType.text, guild=NS(id=1, name="g")
    )
    record = channel_to_dict(channel)
    assert channel_kind(record) == "text" and record["guild_id"] == "1"
    assert "recipients" not in record


def test_rest_channel_fetches_one_channel_by_id():
    asked = []

    class _Client:
        async def fetch_channel(self, channel_id):
            asked.append(channel_id)
            return _dm()

    class _Rest(DiscordRest):
        def _run(self, factory):  # skip login: run against the stub client
            return asyncio.run(factory(_Client()))

    record = _Rest(token="t").channel("50")
    assert asked == [50] and channel_kind(record) == "dm"


def test_dict_backend_finds_channels_threads_and_dms():
    backend = DictBackend(
        channels={
            "1": [{"id": "10", "guild_id": "1"}],
            "@me": [{"id": "50", "type": "private", "recipients": ["7"]}],
        },
        threads={"10": [{"id": "20", "type": "public_thread"}]},
    )
    assert backend.channel("10")["guild_id"] == "1"
    assert channel_kind(backend.channel(20)) == "thread"
    assert backend.channel("50")["recipients"] == ["7"]
    with pytest.raises(NotFound):
        backend.channel("99")
