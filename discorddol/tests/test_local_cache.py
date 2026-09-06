"""Tests for reading the Discord desktop cache, built on synthetic cache entries.

The entries here mimic Chromium's simple-cache layout closely enough to exercise the
real parsing path: a header, the URL as the entry key, the payload, and the EOF record
that bounds it.
"""

import struct

import pytest

from discorddol.base import DictBackend
from discorddol.local_cache import (
    CachedAttachments,
    SIMPLE_CACHE_EOF_MAGIC,
    cached_attachment_refs,
    cached_channel_ids,
    discord_cache_dir,
    extract_channel_attachments,
    orphan_channel_ids,
)

PNG = b'\x89PNG\r\n\x1a\n' + b'fake-png-body' * 4


def write_cache_entry(cache_dir, *, channel_id, attachment_id, filename, payload=PNG):
    """Write a file shaped like a Chromium simple-cache entry for one attachment URL."""
    url = (
        f'https://cdn.discordapp.com/attachments/'
        f'{channel_id}/{attachment_id}/{filename}'
    ).encode()
    header = struct.pack('<QLL', 0xFCFB6D1BA7725C30, 5, len(url)) + b'\x00' * 8
    # Real entries store the URL key, then the response records; the '\x00' stands in for
    # the record boundary that separates the key from what follows it on disk.
    body = (
        header
        + url
        + b'\x00HTTP/1.1 200 OK\r\n\r\n'
        + payload
        + SIMPLE_CACHE_EOF_MAGIC
    )
    path = cache_dir / f'{attachment_id}_0'
    path.write_bytes(body)
    return path


@pytest.fixture
def cache_dir(tmp_path):
    """A cache holding two channels: '10' (two files) and '99' (one)."""
    d = tmp_path / 'Cache_Data'
    d.mkdir()
    write_cache_entry(d, channel_id='10', attachment_id='1000', filename='a.png')
    write_cache_entry(d, channel_id='10', attachment_id='1001', filename='b.png')
    write_cache_entry(d, channel_id='99', attachment_id='2000', filename='c.png')
    return d


def test_discord_cache_dir_is_platform_specific():
    path = discord_cache_dir()
    assert path.name == 'Cache_Data'
    assert 'discord' in str(path).lower()


def test_refs_parse_channel_attachment_and_filename(cache_dir):
    refs = sorted(
        cached_attachment_refs(cache_dir=cache_dir), key=lambda r: r['attachment_id']
    )
    assert [r['channel_id'] for r in refs] == ['10', '10', '99']
    assert refs[0]['filename'] == 'a.png'


def test_cached_channel_ids_counts_per_channel(cache_dir):
    assert dict(cached_channel_ids(cache_dir=cache_dir)) == {'10': 2, '99': 1}


def test_missing_cache_dir_raises_actionable_error(tmp_path):
    with pytest.raises(FileNotFoundError, match='cache_dir'):
        list(cached_attachment_refs(cache_dir=tmp_path / 'nope'))


def test_orphan_channel_ids_separates_live_from_deleted(cache_dir):
    """A channel the server still lists is 'live'; one it doesn't is a candidate."""
    backend = DictBackend(channels={'1': [{'id': '10', 'name': 'dev'}]})
    result = orphan_channel_ids('1', backend=backend, cache_dir=cache_dir)
    assert result['live'] == {'10': 2}
    assert result['orphans'] == {'99': 1}


def test_extract_recovers_the_payload_bytes(cache_dir, tmp_path):
    """The point of the whole module: bytes back out, without the network."""
    out = tmp_path / 'recovered'
    written = extract_channel_attachments('10', out_dir=out, cache_dir=cache_dir)
    assert len(written) == 2
    for record in written:
        data = (out / record['path'].split('/')[-1]).read_bytes()
        assert data == PNG
        assert record['bytes'] == len(PNG)


def test_extract_ignores_other_channels(cache_dir, tmp_path):
    written = extract_channel_attachments('99', out_dir=tmp_path / 'r', cache_dir=cache_dir)
    assert [w['filename'] for w in written] == ['c.png']


def test_cached_attachments_is_a_mapping(cache_dir):
    store = CachedAttachments(cache_dir=cache_dir)
    assert sorted(store) == ['10', '99']
    assert len(store) == 2
    assert len(store['10']) == 2
    with pytest.raises(KeyError):
        store['does-not-exist']
