"""Explicit cleanup operations; no cookie values leave the database."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3

from . import database, profiles
from .profiles import GuardError


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.digest()


def _files(profile):
    return [profile / ('cookies.sqlite' + suffix) for suffix in ('', '-wal', '-shm')]


def _verify(profile, directory):
    for source in _files(profile):
        saved = directory / source.name
        if source.exists() != saved.exists():
            raise GuardError('Cookie files changed during backup; cleanup aborted.')
        if source.exists() and _sha256(source) != _sha256(saved):
            raise GuardError('Backup verification failed; cleanup aborted.')


def _backup(profile):
    root = profile / 'cookie-guard-backups'
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S_%fZ')
    directory = root / stamp
    directory.mkdir(mode=0o700)
    for source in _files(profile):
        if source.exists():
            dest = directory / source.name
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream, source.open('rb') as original:
                shutil.copyfileobj(original, stream)
                stream.flush()
                os.fsync(stream.fileno())
    _verify(profile, directory)
    return directory


def _quick_check(con):
    if con.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise GuardError('Database integrity check failed; cleanup aborted.')


def _ensure_closed():
    if profiles.firefox_running():
        raise GuardError('Firefox is running. Close it completely before cleanup.')


def _cleanup(profile, aggressive):
    profile = Path(profile)
    con = None
    try:
        _ensure_closed()
        live = profile / 'cookies.sqlite'
        try:
            if not live.is_file():
                raise GuardError('cookies.sqlite is missing; cleanup refused.')
            directory = _backup(profile)
            # Validate a disposable copy, preserving backup bytes and sidecars.
            with database.snapshot(directory) as saved:
                _quick_check(saved)
            _ensure_closed()
            _verify(profile, directory)
            live_uri = live.resolve().as_uri() + '?mode=rw'
        except OSError:
            raise GuardError('Backup or filesystem operation failed; cleanup aborted.') from None
        # mode=rw prevents accidentally creating an empty live database.
        con = sqlite3.connect(live_uri, uri=True, timeout=1)
        con.execute('BEGIN IMMEDIATE')
        _quick_check(con)
        _ensure_closed()
        if aggressive:
            con.create_function('guard_target', 1, database.target)
            removed = con.execute('DELETE FROM moz_cookies WHERE guard_target(host)').rowcount
        else:
            removed = con.execute(
                'DELETE FROM moz_cookies WHERE host COLLATE BINARY IN (?, ?) '
                'AND substr(name, 1, ?) = ?',
                ('chatgpt.com', '.chatgpt.com', len('conv_key_'), 'conv_key_'),
            ).rowcount
        _quick_check(con)
        con.commit()
        return removed, directory
    except GuardError:
        if con is not None:
            try:
                con.rollback()
            except sqlite3.Error:
                pass
        raise
    except sqlite3.Error:
        if con is not None:
            try:
                con.rollback()
            except sqlite3.Error:
                pass
        raise GuardError('SQLite operation failed; cleanup rolled back.') from None
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                # Closing must not mask an existing user-facing failure.
                pass


def clean(profile):
    """Remove targeted conv_key_* cookies; return count and backup directory."""
    return _cleanup(profile, aggressive=False)


def aggressive_clean(profile):
    """Remove ChatGPT/OpenAI cookies; may sign out. Return count and backup directory."""
    return _cleanup(profile, aggressive=True)
