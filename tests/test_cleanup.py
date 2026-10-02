"""Cleanup safety tests: exclusively synthetic temporary Firefox databases."""
import hashlib
from pathlib import Path
import sqlite3
import stat

import pytest

from chatgpt_cookie_guard import cleanup
from chatgpt_cookie_guard.profiles import GuardError

FAKE_VALUE = 'FAKE_SUPER_SECRET'
ROWS = [
    ('chatgpt.com', 'conv_key_fake'),
    ('.chatgpt.com', 'conv_key_other'),
    ('chatgpt.com', 'session_fake'),
    ('.chatgpt.com', 'convXkey_fake'),
    ('chatgpt.com', 'CONV_KEY_fake'),
    ('foo.chatgpt.com', 'conv_key_fake'),
    ('openai.com', 'conv_key_fake'),
    ('.openai.com', 'session_fake'),
    ('api.openai.com', 'session_fake'),
    ('unrelated.example', 'conv_key_fake'),
    ('notchatgpt.com', 'conv_key_fake'),
    ('chatgpt.com.unrelated.example', 'conv_key_fake'),
    ('notopenai.com', 'session_fake'),
]


@pytest.fixture(autouse=True)
def closed_firefox(monkeypatch, capsys):
    monkeypatch.setattr(cleanup.profiles, 'firefox_running', lambda: False)
    yield
    output = capsys.readouterr()
    assert FAKE_VALUE not in output.out
    assert FAKE_VALUE not in output.err


@pytest.fixture
def profile(tmp_path):
    with sqlite3.connect(tmp_path / 'cookies.sqlite') as con:
        con.execute('CREATE TABLE moz_cookies ('
                    'id INTEGER PRIMARY KEY, originAttributes TEXT, '
                    'host TEXT, name TEXT, value TEXT, path TEXT, '
                    'expiry INTEGER, isSecure INTEGER, isHttpOnly INTEGER)')
        con.executemany('INSERT INTO moz_cookies '
                        '(originAttributes, host, name, value, path, expiry, '
                        'isSecure, isHttpOnly) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                        [('', host, name, FAKE_VALUE, '/', 2000000000, 1, 0)
                         for host, name in ROWS])
    return tmp_path


def pairs(profile):
    with sqlite3.connect(profile / 'cookies.sqlite') as con:
        return set(con.execute('SELECT host, name FROM moz_cookies'))


def test_targeted_scope(profile):
    removed, backup = cleanup.clean(profile)
    intended = {('chatgpt.com', 'conv_key_fake'), ('.chatgpt.com', 'conv_key_other')}
    assert removed == 2
    assert pairs(profile) == set(ROWS) - intended
    assert backup.is_dir()


def test_aggressive_scope(profile):
    removed, backup = cleanup.aggressive_clean(profile)
    unrelated = {(h, n) for h, n in ROWS if h in (
        'unrelated.example', 'notchatgpt.com',
        'chatgpt.com.unrelated.example', 'notopenai.com')}
    assert removed == len(ROWS) - len(unrelated)
    assert pairs(profile) == unrelated
    assert backup.is_dir()


@pytest.mark.parametrize('operation', [cleanup.clean, cleanup.aggressive_clean])
def test_firefox_running_refuses_before_backup(profile, monkeypatch, operation):
    before = (profile / 'cookies.sqlite').read_bytes()
    monkeypatch.setattr(cleanup.profiles, 'firefox_running', lambda: True)
    with pytest.raises(GuardError) as exc:
        operation(profile)
    assert str(exc.value) == 'Firefox is running. Close it completely before cleanup.'
    assert (profile / 'cookies.sqlite').read_bytes() == before
    assert not (profile / 'cookie-guard-backups').exists()


def test_verified_backup_before_live_open(profile, monkeypatch):
    before = (profile / 'cookies.sqlite').read_bytes()
    expected = hashlib.sha256(before).digest()
    original = sqlite3.connect
    live_opened = []
    def checked_connect(*args, **kwargs):
        if str(args[0]).endswith('?mode=rw'):
            backups = list((profile / 'cookie-guard-backups').iterdir())
            assert len(backups) == 1
            saved = backups[0] / 'cookies.sqlite'
            assert saved.read_bytes() == before
            assert hashlib.sha256(saved.read_bytes()).digest() == expected
            assert (profile / 'cookies.sqlite').read_bytes() == before
            live_opened.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(cleanup.sqlite3, 'connect', checked_connect)
    removed, backup = cleanup.clean(profile)
    assert removed == 2 and live_opened == [True]
    assert backup.parent == profile / 'cookie-guard-backups'
    assert (backup / 'cookies.sqlite').read_bytes() == before
    assert pairs(backup) == set(ROWS)
    assert stat.S_IMODE((backup / 'cookies.sqlite').stat().st_mode) & ~0o600 == 0
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize('failure', ['copy', 'verify'])
def test_failed_backup_prevents_mutation(profile, monkeypatch, failure):
    before = (profile / 'cookies.sqlite').read_bytes()
    def fail(*args):
        if failure == 'copy':
            raise OSError(FAKE_VALUE)
        raise GuardError('Backup verification failed; cleanup aborted.')
    if failure == 'copy':
        monkeypatch.setattr(cleanup.shutil, 'copyfileobj', fail)
        expected = 'Backup or filesystem operation failed; cleanup aborted.'
    else:
        monkeypatch.setattr(cleanup, '_verify', fail)
        expected = 'Backup verification failed; cleanup aborted.'
    original = sqlite3.connect
    def no_live_open(*args, **kwargs):
        assert not str(args[0]).endswith('?mode=rw')
        return original(*args, **kwargs)
    monkeypatch.setattr(cleanup.sqlite3, 'connect', no_live_open)
    with pytest.raises(GuardError) as exc:
        cleanup.clean(profile)
    assert str(exc.value) == expected
    assert FAKE_VALUE not in str(exc.value)
    assert (profile / 'cookies.sqlite').read_bytes() == before


@pytest.mark.parametrize('operation', [cleanup.clean, cleanup.aggressive_clean])
def test_missing_database(tmp_path, operation):
    with pytest.raises(GuardError) as exc:
        operation(tmp_path)
    assert str(exc.value) == 'cookies.sqlite is missing; cleanup refused.'
    assert list(tmp_path.iterdir()) == []


def test_precheck_failure_preserves_live_database(profile, monkeypatch):
    before = (profile / 'cookies.sqlite').read_bytes()
    def fail_check(con):
        raise GuardError('Database integrity check failed; cleanup aborted.')
    monkeypatch.setattr(cleanup, '_quick_check', fail_check)
    original = sqlite3.connect
    def no_live_open(*args, **kwargs):
        assert not str(args[0]).endswith('?mode=rw')
        return original(*args, **kwargs)
    monkeypatch.setattr(cleanup.sqlite3, 'connect', no_live_open)
    with pytest.raises(GuardError) as exc:
        cleanup.clean(profile)
    assert str(exc.value) == 'Database integrity check failed; cleanup aborted.'
    assert (profile / 'cookies.sqlite').read_bytes() == before
    assert list((profile / 'cookie-guard-backups').glob('*/cookies.sqlite'))


def test_actual_hash_mismatch_refuses_mutation(profile, monkeypatch):
    before = (profile / 'cookies.sqlite').read_bytes()
    original = cleanup.shutil.copyfileobj
    def corrupt_copy(source, destination):
        original(source, destination)
        destination.write(b'FAKE damaged backup')
    monkeypatch.setattr(cleanup.shutil, 'copyfileobj', corrupt_copy)
    with pytest.raises(GuardError) as exc:
        cleanup.clean(profile)
    assert str(exc.value) == 'Backup verification failed; cleanup aborted.'
    assert (profile / 'cookies.sqlite').read_bytes() == before


def test_wal_and_shm_are_backed_up(profile):
    # Keep this synthetic connection open so its committed WAL remains present.
    con = sqlite3.connect(profile / 'cookies.sqlite')
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('INSERT INTO moz_cookies (host, name, value) VALUES (?, ?, ?)',
                    ('chatgpt.com', 'conv_key_wal', FAKE_VALUE))
        con.commit()
        sources = [profile / ('cookies.sqlite' + s) for s in ('', '-wal', '-shm')]
        expected = {p.name: hashlib.sha256(p.read_bytes()).digest() for p in sources}
        removed, backup = cleanup.clean(profile)
        assert removed == 3
        for filename, digest in expected.items():
            saved = backup / filename
            assert hashlib.sha256(saved.read_bytes()).digest() == digest
            assert stat.S_IMODE(saved.stat().st_mode) & ~0o600 == 0
    finally:
        con.close()


@pytest.mark.parametrize('stage', ['begin', 'delete', 'postcheck', 'commit'])
@pytest.mark.parametrize('rollback_error', [False, True])
def test_sqlite_failures_roll_back_safely(profile, monkeypatch, stage, rollback_error):
    before = (profile / 'cookies.sqlite').read_bytes()
    original = sqlite3.connect
    rolled_back = []
    class FaultConnection:
        def __init__(self, con):
            self.con = con
            self.checks = 0
        def execute(self, sql, *args):
            result = self.con.execute(sql, *args)
            if sql == 'PRAGMA quick_check':
                self.checks += 1
            fail = (stage == 'begin' and sql == 'BEGIN IMMEDIATE'
                    or stage == 'delete' and sql.startswith('DELETE')
                    or stage == 'postcheck' and sql == 'PRAGMA quick_check'
                    and self.checks == 2)
            if fail:
                raise sqlite3.OperationalError(FAKE_VALUE)
            return result
        def commit(self):
            if stage == 'commit':
                raise sqlite3.OperationalError(FAKE_VALUE)
            self.con.commit()
        def rollback(self):
            self.con.rollback()
            rolled_back.append(True)
            if rollback_error:
                raise sqlite3.OperationalError(FAKE_VALUE)
        def close(self):
            self.con.close()
    def faulty_connect(*args, **kwargs):
        con = original(*args, **kwargs)
        return FaultConnection(con) if str(args[0]).endswith('?mode=rw') else con
    monkeypatch.setattr(cleanup.sqlite3, 'connect', faulty_connect)
    with pytest.raises(GuardError) as exc:
        cleanup.clean(profile)
    assert str(exc.value) == 'SQLite operation failed; cleanup rolled back.'
    assert FAKE_VALUE not in str(exc.value)
    assert rolled_back == [True]
    assert (profile / 'cookies.sqlite').read_bytes() == before
    backups = list((profile / 'cookie-guard-backups').glob('*/cookies.sqlite'))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert pairs(profile) == set(ROWS)


def test_postcheck_guarderror_rolls_back_and_preserves_message(profile, monkeypatch):
    before = (profile / 'cookies.sqlite').read_bytes()
    original = cleanup._quick_check
    calls = []
    def failing_postcheck(con):
        calls.append(True)
        if len(calls) == 3:
            raise GuardError('Database integrity check failed; cleanup aborted.')
        original(con)
    monkeypatch.setattr(cleanup, '_quick_check', failing_postcheck)
    with pytest.raises(GuardError) as exc:
        cleanup.clean(profile)
    assert str(exc.value) == 'Database integrity check failed; cleanup aborted.'
    assert (profile / 'cookies.sqlite').read_bytes() == before
    assert pairs(profile) == set(ROWS)


def test_live_precheck_failure_rolls_back_without_delete(
    profile, monkeypatch, capsys
):
    before = (profile / 'cookies.sqlite').read_bytes()
    original_connect = sqlite3.connect
    original_check = cleanup._quick_check
    events = []
    checks = []

    class TrackedConnection:
        def __init__(self, con):
            self.con = con

        def __getattr__(self, name):
            return getattr(self.con, name)

        def execute(self, sql, *args):
            if sql.startswith('DELETE'):
                events.append('delete')
            result = self.con.execute(sql, *args)
            if sql == 'BEGIN IMMEDIATE':
                events.append('begin')
            return result

        def rollback(self):
            events.append('rollback')
            self.con.rollback()

    def tracked_connect(*args, **kwargs):
        con = original_connect(*args, **kwargs)
        if str(args[0]).endswith('?mode=rw'):
            events.append('open')
            return TrackedConnection(con)
        return con

    def fail_live_precheck(con):
        checks.append(True)
        if len(checks) == 2:
            assert events == ['open', 'begin']
            raise GuardError(
                'Database integrity check failed; cleanup aborted.'
            )
        original_check(con)

    monkeypatch.setattr(cleanup.sqlite3, 'connect', tracked_connect)
    monkeypatch.setattr(cleanup, '_quick_check', fail_live_precheck)

    with pytest.raises(GuardError) as exc:
        cleanup.clean(profile)

    assert str(exc.value) == (
        'Database integrity check failed; cleanup aborted.'
    )
    assert len(checks) == 2
    assert events == ['open', 'begin', 'rollback']
    assert (profile / 'cookies.sqlite').read_bytes() == before
    assert pairs(profile) == set(ROWS)

    backups = list(
        (profile / 'cookie-guard-backups').glob('*/cookies.sqlite')
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == before
    assert pairs(backups[0].parent) == set(ROWS)

    output = capsys.readouterr()
    assert FAKE_VALUE not in str(exc.value)
    assert FAKE_VALUE not in output.out
    assert FAKE_VALUE not in output.err
