"""Snapshot regression tests using synthetic databases only."""
import sqlite3

import pytest

from chatgpt_cookie_guard import database


@pytest.fixture
def profile(tmp_path):
    db = tmp_path / 'cookies.sqlite'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)')
        con.executemany('INSERT INTO moz_cookies VALUES (?, ?, ?)', [
            ('chatgpt.com', 'conv_key_fake', 'FAKE'),
            ('.openai.com', 'fake', 'FAKE'),
            ('unrelated.example', 'fake', 'FAKE'),
        ])
    return tmp_path


def test_inspect_reads_copied_table(profile, monkeypatch):
    copied = []
    original = database.shutil.copyfile
    def record(src, dest):
        copied.append((src, dest))
        return original(src, dest)
    monkeypatch.setattr(database.shutil, 'copyfile', record)
    info = database.inspect(profile)
    assert info['total'] == 2
    assert info['conv'] == 1
    assert info['hosts'] == {'chatgpt.com': 18, '.openai.com': 9}
    assert info['combined'] == 29
    assert copied[0][0] == profile / 'cookies.sqlite'
    assert copied[0][1].parent != profile


def test_snapshot_without_sidecars(profile):
    assert not (profile / 'cookies.sqlite-wal').exists()
    assert not (profile / 'cookies.sqlite-shm').exists()
    with database.snapshot(profile) as con:
        assert con.execute('SELECT count(*) FROM moz_cookies').fetchone() == (3,)


def test_live_source_unchanged(profile):
    db = profile / 'cookies.sqlite'
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    with database.snapshot(profile) as con:
        con.execute('DELETE FROM moz_cookies')
        con.commit()
    database.inspect(profile)
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before
    assert sorted(p.name for p in profile.iterdir()) == ['cookies.sqlite']


def test_retry_removes_disappearing_sidecars(profile, monkeypatch):
    sidecars = [profile / ('cookies.sqlite' + s) for s in ('-wal', '-shm')]
    for path in sidecars:
        path.write_bytes(b'FAKE disposable sidecar')
    original = database.shutil.copyfile
    def disappearing(src, dest):
        result = original(src, dest)
        if src == sidecars[-1]:
            for path in sidecars:
                path.unlink()
        return result
    monkeypatch.setattr(database.shutil, 'copyfile', disappearing)
    with database.snapshot(profile) as con:
        db = con.execute('PRAGMA database_list').fetchone()[2]
        from pathlib import Path
        assert not Path(db + '-wal').exists()
        assert not Path(db + '-shm').exists()
        assert con.execute('SELECT count(*) FROM moz_cookies').fetchone() == (3,)
