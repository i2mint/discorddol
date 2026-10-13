"""``message_to_dict`` on messages that are only a sticker, a poll or a forward.

No Discord connection: messages are :class:`~types.SimpleNamespace` stand-ins shaped like
``discord.Message``, with a real ``discord.Poll`` and ``discord.Embed`` where the library
lets us build one offline.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import discord
import pytest

from discorddol.base import message_to_dict

WHEN = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def _message(**overrides):
    """A blank message: no text, attachments, embeds, stickers, poll or forward."""
    fields = dict(
        id=100,
        channel=NS(id=10),
        created_at=WHEN,
        edited_at=None,
        author=NS(id=1, name="ana", display_name="Ana", bot=False),
        content="",
        clean_content="",
        attachments=[],
        embeds=[],
        stickers=[],
        reactions=[],
        reference=None,
        pinned=False,
        type="MessageType.default",
        jump_url="https://discord.com/channels/1/10/100",
    )
    return NS(**{**fields, **overrides})


def test_plain_message_has_empty_new_fields():
    record = message_to_dict(_message(content="hi"))
    assert record["stickers"] == []
    assert record["poll"] is None
    assert record["forwarded"] == []


def test_sticker_only_message():
    sticker = NS(id=7, name="wave", format="StickerFormatType.png", url="https://x/7.png")
    record = message_to_dict(_message(stickers=[sticker]))
    assert record["stickers"] == [
        {"id": "7", "name": "wave", "format": "StickerFormatType.png", "url": "https://x/7.png"}
    ]


def test_poll_only_message():
    poll = discord.Poll("Best colour?", timedelta(hours=24), multiple=True)
    poll.add_answer(text="red")
    poll.add_answer(text="blue", emoji="🔵")
    record = message_to_dict(_message(poll=poll))["poll"]
    assert record["question"] == "Best colour?"
    assert [(a["text"], a["emoji"], a["vote_count"]) for a in record["answers"]] == [
        ("red", None, 0),
        ("blue", "🔵", 0),
    ]
    assert record["multiple"] is True
    assert record["finalized"] is False
    assert record["total_votes"] == 0
    assert record["expires_at"] is None  # only Discord sets it, on a fetched poll


def test_forwarded_message_snapshot():
    snapshot = NS(
        content="original text",
        created_at=WHEN - timedelta(days=1),
        edited_at=None,
        attachments=[NS(filename="a.png", url="https://x/a.png", size=3, id=5)],
        embeds=[discord.Embed(title="t")],
        stickers=[NS(id=7, name="wave", format="png", url="https://x/7.png")],
        type="MessageType.default",
    )
    (record,) = message_to_dict(_message(message_snapshots=[snapshot]))["forwarded"]
    assert record["content"] == "original text"
    assert record["created_at"] == (WHEN - timedelta(days=1)).isoformat()
    assert record["edited_at"] is None
    assert record["attachments"][0]["filename"] == "a.png"
    assert record["embeds"][0]["title"] == "t"
    assert record["stickers"][0]["name"] == "wave"


def test_old_discord_py_without_poll_or_snapshots():
    """Messages from discord.py < 2.5 have no ``message_snapshots`` (nor maybe ``poll``)."""
    message = _message()
    assert not hasattr(message, "poll") and not hasattr(message, "message_snapshots")
    record = message_to_dict(message)
    assert record["poll"] is None and record["forwarded"] == []


def test_record_is_json_safe():
    poll = discord.Poll("q", timedelta(hours=1))
    poll.add_answer(text="a")
    snapshot = NS(
        content="c", created_at=WHEN, edited_at=WHEN, attachments=[], embeds=[],
        stickers=[], type="MessageType.default",
    )
    record = message_to_dict(_message(poll=poll, message_snapshots=[snapshot]))
    assert json.loads(json.dumps(record)) == record
