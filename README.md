# chatgpt-cookie-guard

Diagnose and safely repair oversized ChatGPT/OpenAI Firefox cookie state.

Accumulated browser cookies can cause or aggravate HTTP 431 / oversized-request-header
symptoms when using ChatGPT. This utility diagnoses that cookie state and offers
explicit, backed-up cleanup. Not every HTTP 431 is caused by these cookies.

## Support and installation

v0.1.0 supports **Linux + Firefox**, with Python 3.10 or newer and no third-party
runtime dependencies. Discovery supports these layouts:

- `~/.mozilla/firefox`
- `~/snap/firefox/common/.mozilla/firefox`
- `~/.var/app/org.mozilla.firefox/.mozilla/firefox`

Install from source. No PyPI release is currently advertised.

```sh
git clone "https://github.com/dr-richard/chatgpt-cookie-guard.git"
cd chatgpt-cookie-guard
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

For development, in the same virtual environment:

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
```

## Commands

```sh
chatgpt-cookie-guard status
chatgpt-cookie-guard auto
chatgpt-cookie-guard clean
chatgpt-cookie-guard aggressive-clean
chatgpt-cookie-guard status --profile /path/to/profile
```

- **status:** read-only aggregate diagnosis: cookie count, `conv_key_*` count,
  size estimates, and LOW / MEDIUM / HIGH risk.
- **auto:** read-only diagnosis and a recommendation. It **never cleans automatically**.
- **clean:** removes only names beginning with literal `conv_key_` on exact
  `chatgpt.com` / `.chatgpt.com` hosts. It refuses while Firefox is running and
  creates and verifies a backup first. Other cookies and subdomains are preserved.
- **aggressive-clean:** destructive fallback that removes cookies for ChatGPT/OpenAI
  domains and their subdomains. **This can sign you out.** Interactive use requires
  typing exactly `DELETE`; non-interactive use requires an explicit `--yes`.

Use `--profile /path/to/profile` with any command. Automatic selection requires
one usable profile; ambiguous discovery asks you to select explicitly.
`--yes` is available only for `aggressive-clean`; never add it casually.
Exit codes: 0 success, 1 operational failure, 2 usage error or cancelled cleanup.

MEDIUM starts at 8,000 bytes and HIGH at 10,000 bytes. These are heuristics,
not universal HTTP limits. Size figures are diagnostic estimates across stored
cookies, not exact request headers; which cookies a request sends can differ.

## Privacy and backups

There is no telemetry, no network access, and no automatic browser termination.
No cookie values are printed or logged. Values are read internally only to
estimate sizes. Status uses a private temporary SQLite snapshot, including
existing WAL/SHM sidecars, and removes that snapshot afterward. No OpenAI
credentials, API keys, or browser passwords are required.

Cleanup requires Firefox to be closed. Keep it closed throughout the operation.
Backups remain local at:

```text
<Firefox profile>/cookie-guard-backups/<timestamp>/
```

Before opening the live database for mutation, cleanup copies `cookies.sqlite`
and any existing `cookies.sqlite-wal` / `cookies.sqlite-shm` files, verifies each
copy against its source with SHA-256, and checks database integrity. Backup
failure aborts cleanup. Cleanup uses a transaction with integrity checks before
and after deletion; failures trigger a rollback attempt and retain the backup.
Backup directories use mode 0700 and files use mode 0600. These copies contain
sensitive cookies, including unrelated sites: keep them private and local.
Retain the backup until Firefox is confirmed healthy.

To restore manually:

1. Fully close Firefox and keep it closed during restoration.
2. Locate the desired timestamped backup for the same profile.
3. Make an additional private copy of the current cookie database and existing
   sidecars, then move the current files aside rather than deleting them.
4. Restore the backed-up `cookies.sqlite` and any backed-up WAL/SHM sidecars
   together into the profile. Do not mix current sidecars with the restored DB;
   if the backup has no sidecars, leave no old sidecars at the live paths.
5. Preserve private permissions: cookie files mode 0600, backup directories 0700.
6. Reopen Firefox and check that it works. Restoration reverts cookie changes
   for all sites since that backup, not just ChatGPT/OpenAI.

## Limitations

v0.1.0 supports Linux + Firefox only. Browser/database formats may change.
Profile selection refuses ambiguity. Size estimates do not reproduce exact HTTP
request headers, and HTTP 431 can have causes unrelated to ChatGPT/OpenAI cookies.
Cleanup is an explicit user action; backup restoration is manual. Keep Firefox
closed during cleanup, and do not run concurrent cleanup or restoration operations.

This is an independent community project and is not affiliated with,
endorsed by, or maintained by OpenAI.
