"""Offline command line interface with explicit cleanup intent."""
import argparse
from pathlib import Path
import shlex
import sqlite3
import sys

from . import cleanup, database, profiles
from .profiles import GuardError


def _parser():
    parser = argparse.ArgumentParser(description='Diagnose and repair Firefox cookie bloat offline.')
    parser.add_argument('--profile', type=Path, help='Firefox profile directory')
    parser.set_defaults(command='status')
    commands = parser.add_subparsers(dest='command')
    for name in ('status', 'auto', 'clean', 'aggressive-clean'):
        command = commands.add_parser(name)
        command.add_argument('--profile', type=Path, default=argparse.SUPPRESS,
                             help='Firefox profile directory')
        if name == 'aggressive-clean':
            command.add_argument('--yes', action='store_true',
                                 help='Explicitly confirm deletion of ChatGPT/OpenAI cookies')
    return parser


def _select_profile(explicit):
    if explicit is not None:
        if not explicit.is_dir():
            raise GuardError('The selected Firefox profile is not a directory.')
        return explicit
    candidates = [p for p in profiles.discover()
                  if p.is_dir() and (p / 'cookies.sqlite').is_file()]
    if not candidates:
        raise GuardError('No usable Firefox profile found. Use --profile PATH.')
    if len(candidates) != 1:
        # Discovery returns paths, not default-profile metadata. Never guess.
        paths = '\n'.join(str(p) for p in candidates)
        raise GuardError('Multiple Firefox profiles found. Use --profile PATH:\n' + paths)
    return candidates[0]


def _diagnose(profile):
    if not (profile / 'cookies.sqlite').is_file():
        raise GuardError('cookies.sqlite is missing; diagnosis refused.')
    info = database.inspect(profile)
    print(f'Profile: {profile}')
    print(f"ChatGPT/OpenAI cookies: {info['total']}")
    print(f"conv_key_* cookies: {info['conv']}")
    print(f"ChatGPT estimate: {info['chat']} bytes")
    print(f"Combined diagnostic estimate: {info['combined']} bytes")
    print(f"Risk: {info['risk']}")
    print('Heuristics: MEDIUM >= 8000 bytes; HIGH >= 10000 bytes, not universal HTTP limits.')
    return info


def _confirm_aggressive(yes):
    print('Warning: deleting ChatGPT/OpenAI cookies can sign you out.', file=sys.stderr)
    if yes:
        return True
    if not sys.stdin.isatty():
        print('Aggressive cleanup requires an interactive terminal or explicit --yes.',
              file=sys.stderr)
        return False
    try:
        confirmed = input('Type DELETE to confirm: ') == 'DELETE'
    except EOFError:
        confirmed = False
    if not confirmed:
        print('Aggressive cleanup cancelled; no cookies changed.', file=sys.stderr)
    return confirmed


def main(argv=None):
    """Return 0 on success, 1 on operational failure, or 2 on cancellation/usage."""
    args = _parser().parse_args(argv)
    command = args.command or 'status'
    try:
        profile = _select_profile(args.profile)
        if command in ('status', 'auto'):
            info = _diagnose(profile)
            if command == 'auto':
                if info['risk'] == 'LOW':
                    print('No cleanup appears necessary.')
                elif info['conv'] > 0:
                    recommendation = shlex.join([
                        'chatgpt-cookie-guard', 'clean', '--profile', str(profile)])
                    print('Close Firefox, then run: ' + recommendation)
                else:
                    print('Targeted cleanup has nothing to remove. '
                          'aggressive-clean is a manual fallback only and can sign you out.')
            return 0
        if command == 'aggressive-clean':
            if not _confirm_aggressive(args.yes):
                return 2
            removed, backup = cleanup.aggressive_clean(profile)
        else:
            removed, backup = cleanup.clean(profile)
        print(f'Removed cookies: {removed}')
        print(f'Backup directory: {backup}')
        print('Retain the backup until Firefox is confirmed healthy.')
        return 0
    except GuardError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        # Raw errors can contain database content or other private data.
        print('Error: local profile/database operation failed.', file=sys.stderr)
        return 1
