"""Isolated CLI tests using only synthetic profiles and mocked operations."""
import io
from pathlib import Path
import shlex
import sqlite3
import sys

import pytest

from chatgpt_cookie_guard import cli
from chatgpt_cookie_guard.profiles import GuardError

FAKE_VALUE = 'FAKE_SUPER_SECRET'


def output(capsys):
    captured = capsys.readouterr()
    assert FAKE_VALUE not in captured.out + captured.err
    assert 'Traceback' not in captured.out + captured.err
    return captured


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'fake-home'))
    monkeypatch.setattr(cli.profiles, 'discover', lambda: [])
    def unexpected(*args, **kwargs):
        pytest.fail('Unexpected cleanup or process inspection')
    monkeypatch.setattr(cli.cleanup, 'clean', unexpected)
    monkeypatch.setattr(cli.cleanup, 'aggressive_clean', unexpected)
    monkeypatch.setattr(cli.profiles, 'firefox_running', unexpected)
    yield
    output(capsys)


@pytest.fixture
def profile(tmp_path):
    directory = tmp_path / 'Firefox profile'
    directory.mkdir()
    with sqlite3.connect(directory / 'cookies.sqlite') as con:
        con.execute('CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)')
        con.executemany('INSERT INTO moz_cookies VALUES (?, ?, ?)', [
            ('chatgpt.com', 'conv_key_fake', FAKE_VALUE),
            ('.openai.com', 'fake_session_name', FAKE_VALUE),
            ('unrelated.example', 'private_fake_name', FAKE_VALUE),
        ])
    return directory


def info(risk='LOW', conv=1):
    size = {'LOW': 100, 'MEDIUM': 8000, 'HIGH': 10000}[risk]
    return dict(total=3, conv=conv, chat=size, combined=size, risk=risk,
                hosts={'chatgpt.com': size}, value=FAKE_VALUE)


def test_status_synthetic_aggregates_only(profile, capsys):
    assert cli.main(['status', '--profile', str(profile)]) == 0
    captured = output(capsys)
    assert f'Profile: {profile}' in captured.out
    assert 'ChatGPT/OpenAI cookies: 2' in captured.out
    assert 'conv_key_* cookies: 1' in captured.out
    assert 'ChatGPT estimate:' in captured.out
    assert 'Combined diagnostic estimate:' in captured.out
    assert 'Risk: LOW' in captured.out
    for name in ('conv_key_fake', 'fake_session_name', 'private_fake_name'):
        assert name not in captured.out
    assert captured.err == ''


@pytest.mark.parametrize('risk', ['LOW', 'MEDIUM', 'HIGH'])
def test_status_risk(profile, monkeypatch, capsys, risk):
    monkeypatch.setattr(cli.database, 'inspect', lambda p: info(risk))
    assert cli.main(['status', '--profile', str(profile)]) == 0
    assert f'Risk: {risk}' in output(capsys).out


@pytest.mark.parametrize('risk', ['LOW', 'MEDIUM', 'HIGH'])
@pytest.mark.parametrize('conv', [0, 1])
def test_auto_read_only_recommendations(profile, monkeypatch, capsys, risk, conv):
    before = (profile / 'cookies.sqlite').read_bytes()
    monkeypatch.setattr(cli.database, 'inspect', lambda p: info(risk, conv))
    assert cli.main(['auto', '--profile', str(profile)]) == 0
    captured = output(capsys)
    if risk == 'LOW':
        assert 'No cleanup appears necessary.' in captured.out
    elif conv:
        command = shlex.join(['chatgpt-cookie-guard', 'clean', '--profile', str(profile)])
        assert command in captured.out
        assert '--yes' not in captured.out
    else:
        assert 'Targeted cleanup has nothing to remove.' in captured.out
        assert 'aggressive-clean is a manual fallback only' in captured.out
    assert (profile / 'cookies.sqlite').read_bytes() == before
    assert not (profile / 'cookie-guard-backups').exists()


@pytest.mark.parametrize('arguments', [
    lambda p: ['status', '--profile', str(p)],
    lambda p: ['--profile', str(p), 'status'],
])
def test_explicit_profile_exact_and_bypasses_discovery(profile, monkeypatch, capsys, arguments):
    inspected = []
    def inspect(selected):
        inspected.append(selected)
        return info()
    def no_discovery():
        pytest.fail('Explicit selection must bypass discovery')
    monkeypatch.setattr(cli.profiles, 'discover', no_discovery)
    monkeypatch.setattr(cli.database, 'inspect', inspect)
    assert cli.main(arguments(profile)) == 0
    assert inspected == [profile]
    output(capsys)


@pytest.mark.parametrize('command', ['status', 'auto', 'clean', 'aggressive-clean'])
def test_ambiguous_discovery_refuses(profile, tmp_path, monkeypatch, capsys, command):
    other = tmp_path / 'second-profile'
    other.mkdir()
    (other / 'cookies.sqlite').write_bytes((profile / 'cookies.sqlite').read_bytes())
    monkeypatch.setattr(cli.profiles, 'discover', lambda: [profile, other])
    def no_inspection(p):
        pytest.fail('Ambiguous profiles must not be inspected')
    monkeypatch.setattr(cli.database, 'inspect', no_inspection)
    args = [command] + (['--yes'] if command == 'aggressive-clean' else [])
    assert cli.main(args) == 1
    captured = output(capsys)
    assert captured.out == ''
    assert '--profile PATH' in captured.err
    assert str(profile) in captured.err and str(other) in captured.err


def test_one_discovered_profile_and_default_status(profile, monkeypatch, capsys):
    monkeypatch.setattr(cli.profiles, 'discover', lambda: [profile])
    assert cli.main([]) == 0
    assert f'Profile: {profile}' in output(capsys).out


def test_clean_invokes_only_targeted_cleanup(profile, monkeypatch, capsys):
    calls = []
    backup = profile / 'cookie-guard-backups' / 'fake-timestamp'
    def targeted(selected):
        calls.append(selected)
        return 2, backup
    monkeypatch.setattr(cli.cleanup, 'clean', targeted)
    assert cli.main(['clean', '--profile', str(profile)]) == 0
    assert calls == [profile]
    captured = output(capsys)
    assert captured.out.splitlines() == [
        'Removed cookies: 2', f'Backup directory: {backup}',
        'Retain the backup until Firefox is confirmed healthy.',
    ]
    assert captured.err == ''


@pytest.mark.parametrize('command', ['status', 'auto', 'clean', 'aggressive-clean'])
def test_guarderror_is_concise_stderr(profile, monkeypatch, capsys, command):
    message = 'Firefox is running. Close it completely before cleanup.'
    def fail(*args):
        raise GuardError(message)
    monkeypatch.setattr(cli.database, 'inspect', fail)
    monkeypatch.setattr(cli.cleanup, 'clean', fail)
    monkeypatch.setattr(cli.cleanup, 'aggressive_clean', fail)
    args = [command, '--profile', str(profile)]
    if command == 'aggressive-clean':
        args.append('--yes')
    assert cli.main(args) == 1
    captured = output(capsys)
    assert captured.out == ''
    assert captured.err.endswith(f'Error: {message}\n')


@pytest.mark.parametrize('exception', [OSError, sqlite3.OperationalError])
def test_raw_failure_data_is_hidden(profile, monkeypatch, capsys, exception):
    def fail(p):
        raise exception(FAKE_VALUE)
    monkeypatch.setattr(cli.database, 'inspect', fail)
    assert cli.main(['status', '--profile', str(profile)]) == 1
    captured = output(capsys)
    assert captured.out == ''
    assert captured.err == 'Error: local profile/database operation failed.\n'


@pytest.mark.parametrize('kind', ['missing', 'file'])
def test_explicit_profile_must_be_directory(tmp_path, capsys, kind):
    selected = tmp_path / 'selected'
    if kind == 'file':
        selected.write_text('synthetic file')
    assert cli.main(['status', '--profile', str(selected)]) == 1
    assert output(capsys).err == 'Error: The selected Firefox profile is not a directory.\n'


def test_missing_database_is_friendly(tmp_path, capsys):
    assert cli.main(['status', '--profile', str(tmp_path)]) == 1
    assert output(capsys).err == 'Error: cookies.sqlite is missing; diagnosis refused.\n'
    assert not (tmp_path / 'cookies.sqlite').exists()


def test_no_discovered_profile(capsys):
    assert cli.main(['status']) == 1
    assert output(capsys).err == (
        'Error: No usable Firefox profile found. Use --profile PATH.\n'
    )


@pytest.mark.parametrize(
    'interactive, answer, yes, expected',
    [
        (True, 'DELETE\n', False, 0),
        (True, 'delete\n', False, 2),
        (True, ' DELETE\n', False, 2),
        (True, 'DELETE \n', False, 2),
        (True, 'yes\n', False, 2),
        (True, '\n', False, 2),
        (True, '', False, 2),
        (False, 'DELETE\n', False, 2),
        (False, '', True, 0),
        (True, '', True, 0),
    ],
)
def test_aggressive_confirmation(
    profile, monkeypatch, capsys, interactive, answer, yes, expected
):
    class FakeStdin(io.StringIO):
        def isatty(self):
            return interactive

    monkeypatch.setattr(sys, 'stdin', FakeStdin(answer))
    if yes or not interactive:
        def forbidden_input(*args, **kwargs):
            pytest.fail('Confirmation input must not be requested')
        monkeypatch.setattr('builtins.input', forbidden_input)

    aggressive_calls = []
    targeted_calls = []
    backup = profile / 'cookie-guard-backups' / 'fake-timestamp'

    def aggressive(selected):
        aggressive_calls.append(selected)
        return 7, backup

    def targeted(selected):
        targeted_calls.append(selected)
        pytest.fail('Aggressive command must never invoke targeted cleanup')

    monkeypatch.setattr(cli.cleanup, 'aggressive_clean', aggressive)
    monkeypatch.setattr(cli.cleanup, 'clean', targeted)
    args = ['aggressive-clean', '--profile', str(profile)]
    if yes:
        args.append('--yes')

    assert cli.main(args) == expected
    assert targeted_calls == []
    assert aggressive_calls == ([profile] if expected == 0 else [])

    captured = output(capsys)
    assert 'can sign you out' in captured.err
    if expected == 0:
        assert 'Removed cookies: 7' in captured.out
        assert f'Backup directory: {backup}' in captured.out
    elif interactive:
        assert 'Aggressive cleanup cancelled; no cookies changed.' in captured.err
    else:
        assert 'requires an interactive terminal or explicit --yes' in captured.err
    if interactive and not yes:
        assert 'Type DELETE to confirm:' in captured.out
    assert FAKE_VALUE not in captured.out + captured.err
    assert 'Traceback' not in captured.out + captured.err
