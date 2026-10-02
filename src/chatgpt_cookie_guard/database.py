"""Private snapshots and aggregate-only cookie diagnosis."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .profiles import GuardError


def target(host):
    host = host.lower().lstrip('.')
    return any(host == d or host.endswith('.' + d) for d in ('chatgpt.com', 'openai.com'))


def narrow(host, name):
    return host.lower() in ('chatgpt.com', '.chatgpt.com') and name.startswith('conv_key_')


def risk(size):
    return 'HIGH' if size >= 10000 else 'MEDIUM' if size >= 8000 else 'LOW'


@contextmanager
def snapshot(profile):
    # All SQLite work happens on a private copy, including WAL recovery.
    with tempfile.TemporaryDirectory(prefix='cookie-guard-') as td:
        files = [profile / ('cookies.sqlite' + s) for s in ('', '-wal', '-shm')]
        def signature():
            return [(p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in files]
        for _ in range(3):
            before = signature()
            for src in files:
                dest = Path(td) / src.name
                if dest.exists():
                    dest.unlink()
                if src.exists():
                    shutil.copyfile(src, dest)
                    dest.chmod(0o600)
            if before == signature():
                break
        else:
            raise GuardError('Cookie database is changing; close Firefox and retry.')
        con = sqlite3.connect(Path(td) / 'cookies.sqlite')
        try:
            if con.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise GuardError('Cookie snapshot failed integrity verification.')
            yield con
        finally:
            con.close()


def inspect(profile):
    sizes, counts = {}, {}
    conv = 0
    with snapshot(profile) as con:
        for host, name, value in con.execute('SELECT host, name, value FROM moz_cookies'):
            if not target(host):
                continue
            sizes[host] = sizes.get(host, 0) + len(name.encode()) + 1 + len(value.encode())
            counts[host] = counts.get(host, 0) + 1
            conv += narrow(host, name)
    sizes = {h: n + 2 * (counts[h] - 1) for h, n in sizes.items()}
    combined = sum(sizes.values()) + 2 * max(0, len(sizes) - 1)
    chat = [n for h, n in sizes.items() if h.lower().lstrip('.') == 'chatgpt.com'
            or h.lower().endswith('.chatgpt.com')]
    chat_size = sum(chat) + 2 * max(0, len(chat) - 1)
    return dict(total=sum(counts.values()), conv=conv, hosts=sizes,
                combined=combined, chat=chat_size, risk=risk(combined))
