"""The one-command test: the whole path, end to end, on every seam's default shape.

This is the definition of v1 -- guild listing to channel listing to messages to
rendered transcript. It runs against :class:`DictBackend` rather than live Discord so it
stays runnable in CI, but it exercises the same store code the live backend drives, and
it must keep passing after any seam swap. That is what makes "iterate by ADDING" a
checkable claim rather than a hope.
"""

import json

import pytest

from discorddol import Channels, DictBackend, Guilds, as_text
from discorddol.base import get_token


@pytest.fixture
def backend():
    """A small but realistic two-channel server."""
    return DictBackend(
        guilds=[{'id': '1', 'name': 'Example Guild'}],
        channels={
            '1': [
                {'id': '10', 'name': 'feedback', 'type': 'text'},
                {'id': '11', 'name': 'dev', 'type': 'text'},
            ]
        },
        threads={'10': [{'id': '20', 'name': 'thread-on-feedback'}]},
        messages={
            '10': [
                {
                    'id': '100',
                    'created_at': '2026-09-01T10:00:00',
                    'author': {'display_name': 'ana', 'name': 'ana', 'bot': False},
                    'clean_content': 'the page is slow to load',
                    'attachments': [],
                },
                {
                    'id': '101',
                    'created_at': '2026-09-02T11:00:00',
                    'author': {'display_name': 'bo', 'name': 'bo', 'bot': False},
                    'clean_content': 'same here, and the menu overlaps',
                    'attachments': [],
                },
            ],
            '11': [
                {
                    'id': '200',
                    'created_at': '2026-09-03T09:00:00',
                    'author': {'display_name': 'cy', 'name': 'cy', 'bot': False},
                    'clean_content': 'shipped the fix',
                    'attachments': [],
                }
            ],
            '20': [
                {
                    'id': '300',
                    'created_at': '2026-09-01T12:00:00',
                    'author': {'display_name': 'ana', 'name': 'ana', 'bot': False},
                    'clean_content': 'repro attached',
                    'attachments': [],
                }
            ],
        },
    )


def test_end_to_end_guild_to_transcript(backend):
    """Guilds -> Channels -> messages -> transcript, with a non-empty useful result."""
    guilds = Guilds(backend=backend)
    assert list(guilds) == ['Example Guild']

    channels = guilds['Example Guild']
    assert sorted(channels) == ['dev', 'feedback']

    messages = channels['feedback']
    assert [m['id'] for m in messages] == ['100', '101']

    text = as_text(messages)
    assert 'ana: the page is slow to load' in text
    assert '[2026-09-01T10:00]' in text


def test_lookup_accepts_name_id_and_hash(backend):
    """A channel is reachable by name, by '#name', and by id."""
    channels = Channels('1', backend=backend)
    assert channels['feedback'] == channels['#feedback'] == channels['10']


def test_missing_channel_error_lists_what_exists(backend):
    """A wrong key says what the right ones are -- errors are part of the UX."""
    channels = Channels('1', backend=backend)
    with pytest.raises(KeyError, match='feedback'):
        channels['no-such-channel']


def test_info_exposes_channel_metadata(backend):
    channels = Channels('1', backend=backend)
    assert channels.info['dev']['id'] == '11'
    assert set(channels.info) == set(channels)


def test_include_threads_merges_thread_messages_in_time_order(backend):
    """Thread content is part of 'the whole channel', and interleaves by timestamp."""
    without = Channels('1', backend=backend)['feedback']
    with_threads = Channels('1', backend=backend, include_threads=True)['feedback']
    assert len(with_threads) == len(without) + 1
    stamps = [m['created_at'] for m in with_threads]
    assert stamps == sorted(stamps)


def test_cache_store_seam_avoids_refetching(backend):
    """With a cache_store, a second read serves from the store instead of the backend."""
    cache = {}
    channels = Channels('1', backend=backend, cache_store=cache)
    first = channels['feedback']
    assert '10' in cache

    # Empty the backend; the cached read must still succeed.
    backend._messages['10'] = []
    assert channels['feedback'] == first


def test_limit_is_passed_through_to_the_backend(backend):
    assert len(Channels('1', backend=backend, limit=1)['feedback']) == 1


def test_records_are_json_serializable(backend):
    """Every surface (CLI, MCP, HTTP) needs plain JSON to cross its boundary."""
    messages = Guilds(backend=backend)['Example Guild']['feedback']
    assert json.loads(json.dumps(messages)) == messages


def test_missing_token_error_is_actionable(monkeypatch):
    """The most common first-run failure must explain the fix, not raise KeyError."""
    monkeypatch.delenv('DISCORD_BOT_TOKEN', raising=False)
    monkeypatch.setattr(
        'config2py.simple_config_getter',
        lambda *a, **k: (_ for _ in ()).throw(KeyError('nope')),
    )
    with pytest.raises(RuntimeError) as caught:
        get_token()
    message = str(caught.value)
    assert 'DISCORD_BOT_TOKEN' in message
    assert 'Message Content Intent' in message


def test_backend_construction_needs_no_credentials():
    """Token resolution is lazy, so importing and wiring never demands a secret."""
    from discorddol import DiscordRest

    assert DiscordRest(token='fake').token == 'fake'


def test_dol_store_works_as_a_persistent_cache(backend, tmp_path):
    """The cache_store seam's named replacement, exercised rather than asserted.

    README tells people to reach for a dol store here, so the claim is tested: the cache
    must outlive the Channels instance that filled it, which a plain dict would not do.
    """
    from dol import JsonFiles

    first = Channels('1', backend=backend, cache_store=JsonFiles(str(tmp_path)))[
        'feedback'
    ]
    backend._messages['10'] = []  # a fresh fetch would now return nothing

    second = Channels('1', backend=backend, cache_store=JsonFiles(str(tmp_path)))[
        'feedback'
    ]
    assert second == first
