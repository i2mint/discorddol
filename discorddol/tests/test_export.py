"""Tests for export_guild: a whole guild to JSONL, offline, against a fake guild.

The fake guild holds one of everything the exporter has to tell apart: a category, a
text channel with an active, a public archived and a private archived thread, an
announcement channel, a forum with an active and an archived post, a voice and a stage
channel, and a text channel the bot is not allowed to read. Every name is invented.
"""

import json
from datetime import datetime, timezone

import cw
import discord
import pytest

from discorddol import Channels, DictBackend, Forbidden
from discorddol.base import channel_to_dict
from discorddol.export import (
    DFLT_PAGE_SIZE,
    ChannelLogs,
    ExportInProgress,
    MessageContentMissing,
    _exclusive,
    check_message_content,
    default_export_dir,
    export_guild,
)

GUILD = '1'


def msg(id, day, text='hello', **extra):
    """A message dict shaped like ``message_to_dict``'s output."""
    return {
        'id': str(id),
        'created_at': f'2026-03-{day:02d}T10:00:00+00:00',
        'author': {'id': '900', 'name': 'ada', 'display_name': 'Ada', 'bot': False},
        'content': text,
        'clean_content': text,
        'attachments': [],
        'embeds': [],
        'type': 'MessageType.default',
        **extra,
    }


class FakeGuild(DictBackend):
    """A DictBackend that refuses what a real guild would refuse, and logs its calls."""

    def __init__(self, *, unreadable=(), private_threads_refused=False, **kwargs):
        super().__init__(**kwargs)
        self.unreadable = set(unreadable)
        self.private_threads_refused = private_threads_refused
        self.calls = []

    def messages(self, channel_id, **kwargs):
        self.calls.append(('messages', channel_id, kwargs))
        if channel_id in self.unreadable:
            raise Forbidden('Discord refused the request (403): Missing Access')
        return super().messages(channel_id, **kwargs)

    def archived_threads(self, channel_id, *, private=False):
        self.calls.append(('archived_threads', channel_id, private))
        if private and self.private_threads_refused:
            raise Forbidden('Discord refused the request (403): Missing Permissions')
        return super().archived_threads(channel_id, private=private)

    def pages(self):
        """(channel id, kwargs) of each history page fetched; content-check peeks excluded."""
        return [
            (channel_id, kwargs)
            for name, channel_id, kwargs in self.calls
            if name == 'messages' and kwargs.get('oldest_first')
        ]


def thread(id, name, parent, **extra):
    return {'id': id, 'name': name, 'type': 'public_thread', 'parent_id': parent, **extra}


@pytest.fixture
def guild():
    """One of each channel kind. Ids grow in creation order, as snowflakes do."""
    active_release, active_install = (
        thread('201', 'release-plans', '101'),
        thread('203', 'install-fails', '103'),
    )
    return FakeGuild(
        guilds=[{'id': GUILD, 'name': 'Example Guild'}],
        channels={
            GUILD: [
                {'id': '100', 'name': 'Community', 'type': 'category'},
                {'id': '101', 'name': 'general', 'type': 'text'},
                {'id': '102', 'name': 'news', 'type': 'news'},
                {'id': '103', 'name': 'help', 'type': 'forum'},
                {'id': '104', 'name': 'lounge', 'type': 'voice'},
                {'id': '105', 'name': 'town-hall', 'type': 'stage_voice'},
                {'id': '106', 'name': 'staff-only', 'type': 'text'},
                # Discord lists the active threads along with the guild's channels.
                active_release,
                active_install,
            ]
        },
        threads={
            '101': [
                active_release,
                thread('202', 'old-idea', '101', archived=True),
                thread('204', 'moderation', '101', archived=True, type='private_thread'),
            ],
            '103': [active_install, thread('205', 'how-to-export', '103', archived=True)],
        },
        messages={
            '101': [msg(1001, 1), msg(1002, 2), msg(1003, 3)],
            '102': [msg(1011, 4, 'version 2 is out')],
            '106': [msg(1021, 5, 'not for the bot')],
            '201': [msg(1031, 6)],
            '202': [msg(1041, 7)],
            '203': [msg(1051, 8, 'it fails on step 2')],
            '204': [msg(1061, 9, 'a private note')],
            '205': [msg(1071, 10, 'use export-guild')],
        },
        unreadable={'106'},
    )


def exported(out_dir):
    return ChannelLogs(out_dir / 'messages')


def entry(manifest, name):
    return next(c for c in manifest['channels'] if c['name'] == name)


READABLE = ['101', '102', '201', '202', '203', '204', '205']


# --------------------------------------------------------------------------------------
# What gets exported, and what does not
# --------------------------------------------------------------------------------------


def test_every_readable_channel_gets_one_jsonl_file(guild, tmp_path):
    export_guild(GUILD, tmp_path, backend=guild)
    assert sorted(exported(tmp_path)) == READABLE
    assert [m['id'] for m in exported(tmp_path)['101']] == ['1001', '1002', '1003']


def test_manifest_gives_kind_status_count_and_date_range(guild, tmp_path):
    manifest = export_guild(GUILD, tmp_path, backend=guild)

    general = entry(manifest, 'general')
    assert (general['kind'], general['status']) == ('text', 'exported')
    assert general['file'] == 'messages/101.jsonl'
    assert general['message_count'] == 3
    assert general['first_message_at'].startswith('2026-03-01')
    assert general['last_message_at'].startswith('2026-03-03')
    assert general['thread_count'] == 3
    assert general['channel'] == guild._channels[GUILD][1]
    assert entry(manifest, 'news')['kind'] == 'announcement'

    on_disk = json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8'))
    assert on_disk == manifest
    assert manifest['guild'] == {'id': GUILD, 'name': None}
    assert manifest['run']['preflight']['status'] == 'passed'
    assert manifest['run']['finished_at'] is not None


def test_category_voice_and_stage_are_skipped_with_a_reason(guild, tmp_path):
    manifest = export_guild(GUILD, tmp_path, backend=guild)
    reasons = {s['name']: s['reason'] for s in manifest['skipped']}
    assert 'category' in reasons['Community']
    assert 'voice' in reasons['lounge']
    assert 'stage' in reasons['town-hall']
    assert {s['name']: s['type'] for s in manifest['skipped']}['town-hall'] == 'stage_voice'
    assert not {channel_id for channel_id, _ in guild.pages()} & {'100', '104', '105'}


def test_forum_posts_are_exported_as_threads(guild, tmp_path):
    manifest = export_guild(GUILD, tmp_path, backend=guild)

    posts = {c['name']: c for c in manifest['channels'] if c['parent_id'] == '103'}
    assert set(posts) == {'install-fails', 'how-to-export'}  # active and archived
    assert {(p['kind'], p['parent_name']) for p in posts.values()} == {('thread', 'help')}

    forum = entry(manifest, 'help')
    assert (forum['kind'], forum['file'], forum['thread_count']) == ('forum', None, 2)
    assert '103' not in {channel_id for channel_id, _ in guild.pages()}


def test_private_archived_thread_is_exported_when_permitted(guild, tmp_path):
    manifest = export_guild(GUILD, tmp_path, backend=guild)
    assert [m['content'] for m in exported(tmp_path)['204']] == ['a private note']
    assert entry(manifest, 'moderation')['channel']['type'] == 'private_thread'
    assert manifest['not_fetched'] == []


def test_refused_private_threads_are_recorded_and_the_export_goes_on(guild, tmp_path):
    guild.private_threads_refused = True
    manifest = export_guild(GUILD, tmp_path, backend=guild)

    assert [(n['name'], n['what']) for n in manifest['not_fetched']] == [
        ('general', 'private archived threads'),
        ('staff-only', 'private archived threads'),
    ]
    assert '204' not in exported(tmp_path)
    assert '202' in exported(tmp_path)  # the public archived thread still came


def test_private_threads_are_only_asked_of_text_channels(guild, tmp_path):
    export_guild(GUILD, tmp_path, backend=guild)
    asked = {cid for name, cid, private in guild.calls if name == 'archived_threads' and private}
    assert asked == {'101', '106'}


def test_unreadable_channel_is_skipped_with_the_reason(guild, tmp_path):
    manifest = export_guild(GUILD, tmp_path, backend=guild)
    staff = next(s for s in manifest['skipped'] if s['name'] == 'staff-only')
    assert '403' in staff['reason']
    assert '106' not in exported(tmp_path)
    assert '106' not in {c['id'] for c in manifest['channels']}


def test_a_local_file_error_is_not_mistaken_for_a_refusal(guild, tmp_path, monkeypatch):
    def disk_refuses(self, channel_id, messages):
        raise PermissionError('the disk refused')

    monkeypatch.setattr(ChannelLogs, 'extend', disk_refuses)
    with pytest.raises(PermissionError, match='the disk refused'):
        export_guild(GUILD, tmp_path, backend=guild)


# --------------------------------------------------------------------------------------
# The content check
# --------------------------------------------------------------------------------------


def test_preflight_refuses_blank_messages_and_names_the_intent(guild, tmp_path):
    for messages in guild._messages.values():
        for message in messages:
            message.update(content='', clean_content='')

    with pytest.raises(MessageContentMissing, match='Message Content Intent'):
        export_guild(GUILD, tmp_path, backend=guild)

    assert list(tmp_path.iterdir()) == []  # refused before writing anything
    assert guild.pages() == []


def test_preflight_tolerates_a_few_blank_messages(guild, tmp_path):
    guild._messages['101'].append(msg(1004, 11, ''))  # a sticker, say
    manifest = export_guild(GUILD, tmp_path, backend=guild)
    assert manifest['run']['preflight']['status'] == 'passed'
    assert manifest['run']['preflight']['blank'] == 1


def test_preflight_does_not_count_bot_messages():
    """A bot's own messages keep their content without the intent, so they prove nothing."""
    bot = {'id': '901', 'name': 'helper', 'display_name': 'Helper', 'bot': True}
    backend = DictBackend(
        messages={'10': [msg(1, 1, 'a bot keeps its text', author=bot), msg(2, 1, ''), msg(3, 1, '')]}
    )
    with pytest.raises(MessageContentMissing):
        check_message_content(backend, [{'id': '10'}])


def test_preflight_can_be_skipped(guild, tmp_path):
    for message in guild._messages['101']:
        message.update(content='', clean_content='')
    manifest = export_guild(GUILD, tmp_path, backend=guild, preflight=False)
    assert manifest['run']['preflight'] == {'status': 'skipped'}
    assert sorted(exported(tmp_path)) == READABLE


def test_preflight_passes_an_attachment_only_message():
    image_only = msg(1, 1, '', attachments=[{'filename': 'plot.png'}])
    backend = DictBackend(messages={'10': [image_only]})
    assert check_message_content(backend, [{'id': '10'}])['status'] == 'passed'


def test_preflight_looks_past_system_notices_and_unreadable_channels():
    backend = FakeGuild(
        messages={
            '10': [msg(1, 1, '', type='MessageType.pins_add')],
            '12': [msg(3, 1, 'hello')],
        },
        unreadable={'11'},
    )
    checked = check_message_content(backend, [{'id': '10'}, {'id': '11'}, {'id': '12'}])
    assert checked == {'status': 'passed', 'checked': 1, 'blank': 0}


def test_preflight_is_inconclusive_when_there_is_nothing_to_judge():
    checked = check_message_content(DictBackend(), [{'id': '10'}])
    assert checked == {'status': 'inconclusive', 'checked': 0, 'blank': 0}


def quiet_guild(thread_text):
    """A guild whose channel shows only a pin notice; people write in an archived thread."""
    return FakeGuild(
        channels={GUILD: [{'id': '101', 'name': 'general', 'type': 'text'}]},
        threads={'101': [thread('202', 'old-idea', '101', archived=True)]},
        messages={
            '101': [msg(1001, 1, '', type='MessageType.pins_add')],
            '202': [msg(1041 + i, 7, thread_text) for i in range(5)],
        },
    )


def test_export_judges_what_it_fetches_when_the_preflight_had_nothing_to_judge(tmp_path):
    with pytest.raises(MessageContentMissing):
        export_guild(GUILD, tmp_path, backend=quiet_guild(''), sample_size=3)
    assert '202' not in exported(tmp_path)  # refused before the blank page was written


def test_export_confirms_content_when_the_preflight_had_nothing_to_judge(tmp_path):
    manifest = export_guild(GUILD, tmp_path, backend=quiet_guild('hello'), sample_size=3)
    assert manifest['run']['preflight'] == {
        'status': 'passed',
        'checked': 5,
        'blank': 0,
        'during': 'export',
    }
    assert len(exported(tmp_path)['202']) == 5


# --------------------------------------------------------------------------------------
# Incremental, resumable, one run at a time
# --------------------------------------------------------------------------------------


def test_rerun_fetches_only_new_messages(guild, tmp_path):
    export_guild(GUILD, tmp_path, backend=guild)
    guild._messages['101'].append(msg(1004, 11, 'a new one'))
    guild.calls.clear()

    manifest = export_guild(GUILD, tmp_path, backend=guild)

    assert [m['id'] for m in exported(tmp_path)['101']] == ['1001', '1002', '1003', '1004']
    after_cursor = {'limit': DFLT_PAGE_SIZE, 'after': '1003', 'oldest_first': True}
    assert ('101', after_cursor) in guild.pages()
    assert manifest['run']['new_messages'] == 1
    assert entry(manifest, 'general')['message_count'] == 4
    assert len(manifest['channels']) == len(READABLE) + 1  # the forum has an entry too


def test_rerun_does_not_fetch_a_channel_that_is_up_to_date(guild, tmp_path):
    guild._channels[GUILD][1]['last_message_id'] = '1003'  # general
    export_guild(GUILD, tmp_path, backend=guild)
    guild.calls.clear()

    export_guild(GUILD, tmp_path, backend=guild)

    assert '101' not in {channel_id for channel_id, _ in guild.pages()}
    assert '102' in {channel_id for channel_id, _ in guild.pages()}  # no last id: asks


def test_pages_through_a_long_history(guild, tmp_path):
    export_guild(GUILD, tmp_path, backend=guild, page_size=2)
    cursors = [kwargs['after'] for cid, kwargs in guild.pages() if cid == '101']
    assert cursors == [None, '1002']
    assert len(exported(tmp_path)['101']) == 3


def test_resumes_after_a_crash_without_losing_or_repeating_messages(guild, tmp_path):
    fetch = guild.messages
    pages = []

    def network_dies_on_the_third_page(channel_id, **kwargs):
        if kwargs.get('oldest_first'):
            if len(pages) == 2:
                raise ConnectionError('network went away')
            pages.append(channel_id)
        return fetch(channel_id, **kwargs)

    guild.messages = network_dies_on_the_third_page
    with pytest.raises(ConnectionError):
        export_guild(GUILD, tmp_path, backend=guild, page_size=1)
    # ... and it died in the middle of writing a line.
    with (tmp_path / 'messages' / '101.jsonl').open('a', encoding='utf-8') as file:
        file.write('{"id": "10')

    guild.messages = fetch
    manifest = export_guild(GUILD, tmp_path, backend=guild, page_size=1)

    assert [m['id'] for m in exported(tmp_path)['101']] == ['1001', '1002', '1003']
    assert entry(manifest, 'general')['message_count'] == 3
    assert sorted(exported(tmp_path)) == READABLE


def test_a_channel_gone_from_the_listing_is_marked_not_in_last_run(guild, tmp_path):
    export_guild(GUILD, tmp_path, backend=guild)
    guild._channels[GUILD] = [c for c in guild._channels[GUILD] if c['name'] != 'news']

    manifest = export_guild(GUILD, tmp_path, backend=guild)

    news = entry(manifest, 'news')
    assert (news['status'], news['message_count']) == ('not in last run', 1)
    assert entry(manifest, 'general')['status'] == 'exported'


def test_a_channel_refused_after_an_earlier_export_keeps_its_entry(guild, tmp_path):
    export_guild(GUILD, tmp_path, backend=guild)
    guild.unreadable.add('102')

    manifest = export_guild(GUILD, tmp_path, backend=guild)

    news = entry(manifest, 'news')
    assert (news['status'], news['message_count']) == ('skipped', 1)
    assert 'news' in {s['name'] for s in manifest['skipped']}


def test_a_second_export_into_the_same_folder_is_refused(guild, tmp_path):
    with _exclusive(tmp_path):
        with pytest.raises(ExportInProgress):
            export_guild(GUILD, tmp_path, backend=guild)
    assert list(exported(tmp_path)) == []  # nothing written while refused

    export_guild(GUILD, tmp_path, backend=guild)  # free again once the first is done
    assert sorted(exported(tmp_path)) == READABLE


def test_reading_an_export_never_changes_its_files(tmp_path):
    logs = ChannelLogs(tmp_path)
    logs.extend('10', [msg(1, 1)])
    with logs.path('10').open('a', encoding='utf-8') as file:
        file.write('{"id": "2')  # an interrupted append
    written = logs.path('10').read_bytes()

    assert logs.summary('10')['message_count'] == 1
    assert [m['id'] for m in logs['10']] == ['1']
    assert logs.path('10').read_bytes() == written

    logs.extend('10', [msg(3, 2)])  # the writer drops the half line first
    assert [m['id'] for m in logs['10']] == ['1', '3']


# --------------------------------------------------------------------------------------
# The backend and the stores
# --------------------------------------------------------------------------------------


class UncachedGuild:
    """A guild as REST-only discord.py sees it: nothing in its channel cache."""

    id = 1
    name = 'Example Guild'

    def get_channel(self, channel_id):
        return None


def test_a_real_thread_converts_although_its_parent_is_not_cached():
    """discord.py raises when asked an uncached thread's category; the record must not."""
    data = {
        'id': '201',
        'parent_id': '101',
        'owner_id': '900',
        'name': 'release-plans',
        'type': 11,
        'last_message_id': '1031',
        'message_count': 1,
        'member_count': 1,
        'thread_metadata': {
            'archived': True,
            'auto_archive_duration': 1440,
            'archive_timestamp': '2026-03-06T10:00:00+00:00',
            'locked': False,
        },
    }
    record = channel_to_dict(discord.Thread(guild=UncachedGuild(), state=None, data=data))
    assert record['type'] == 'public_thread'
    assert (record['parent_id'], record['archived'], record['last_message_id']) == (
        '101',
        True,
        '1031',
    )
    assert (record['is_thread'], record['category']) == (True, None)


def test_dict_backend_takes_datetime_bounds_and_ignores_other_keywords():
    backend = DictBackend(messages={'10': [msg(1, 1), msg(2, 2), msg(3, 3)]})
    noon_on_the_first = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
    assert [m['id'] for m in backend.messages('10', after=noon_on_the_first)] == ['2', '3']
    assert len(backend.messages('10', around='2')) == 3


def test_a_forum_in_the_channels_store_reads_as_its_posts(guild):
    """A forum has no history of its own, so the store no longer asks it for one."""
    assert Channels(GUILD, backend=guild)['help'] == []
    posts = Channels(GUILD, backend=guild, include_threads=True)['help']
    assert [m['id'] for m in posts] == ['1051', '1071']
    assert '103' not in {cid for name, cid, _ in guild.calls if name == 'messages'}


def test_default_export_dir_is_under_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('DISCORDDOL_DATA_DIR', str(tmp_path))
    assert default_export_dir('42') == tmp_path / 'exports' / '42'


def test_export_guild_runs_from_the_command_line(guild, tmp_path, monkeypatch, capsys):
    """The CLI verb goes through the real wiring: guild lookup, backend, export."""
    from discorddol import base
    from discorddol.tools import DISPATCH_FUNCS

    monkeypatch.setattr(base, 'DiscordRest', lambda **kwargs: guild)
    parser = cw.mk_parser(DISPATCH_FUNCS, prog='discorddol')
    argv = ['export-guild', 'Example Guild', '--out-dir', str(tmp_path), '--quiet']

    assert cw.run(parser, argv) == 0

    assert sorted(exported(tmp_path)) == READABLE
    manifest = json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['guild'] == {'id': GUILD, 'name': 'Example Guild'}
    assert 'manifest.json' in capsys.readouterr().out
