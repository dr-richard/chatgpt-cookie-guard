"""Linux Firefox discovery and fail-closed process checks."""
import configparser
from pathlib import Path
import subprocess


class GuardError(Exception):
    """A safe user-facing error."""


def discover(home=None):
    home = Path.home() if home is None else Path(home)
    roots = [home / '.mozilla/firefox',
             home / 'snap/firefox/common/.mozilla/firefox',
             home / '.var/app/org.mozilla.firefox/.mozilla/firefox']
    found = set()
    for root in roots:
        if not root.is_dir():
            continue
        candidates = list(root.iterdir())
        ini = configparser.ConfigParser(interpolation=None)
        try:
            ini.read(root / 'profiles.ini')
            for section in ini.sections():
                if section.startswith('Profile') and ini.has_option(section, 'Path'):
                    path = Path(ini.get(section, 'Path'))
                    relative = ini.get(section, 'IsRelative', fallback='1') == '1'
                    candidates.append(root / path if relative else path)
        except configparser.Error:
            pass
        for path in candidates:
            if path.is_dir() and any((path / f).is_file() for f in ('cookies.sqlite', 'prefs.js')):
                found.add(path.resolve())
    return sorted(found)


def firefox_running():
    try:
        result = subprocess.run(['pgrep', '-x', 'firefox|firefox-bin'],
                                capture_output=True, check=False)
    except OSError:
        raise GuardError('Cannot check Firefox processes; cleanup refused.') from None
    if result.returncode not in (0, 1):
        raise GuardError('Cannot check Firefox processes; cleanup refused.')
    return result.returncode == 0
