# Changelog

Notable changes are recorded here in Keep a Changelog style.

## [0.1.0] - 2026-10-02

### Added

- Linux Firefox profile discovery for conventional, Snap, and Flatpak layouts.
- Aggregate ChatGPT/OpenAI cookie counts and diagnostic size estimates.
- LOW/MEDIUM/HIGH risk heuristics with 8,000/10,000-byte warning thresholds.
- Private temporary SQLite snapshots with existing WAL/SHM sidecars.
- Explicit targeted conv_key_* cleanup on exact ChatGPT hosts.
- Timestamped local backups verified with SHA-256 before mutation.
- Aggressive ChatGPT/OpenAI cleanup with interactive DELETE or explicit --yes.
- Transactional cleanup, integrity checks, rollback attempts, and safe errors.
- Synthetic privacy and safety tests; read-only status and auto commands.
