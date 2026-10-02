# Security

Do not post cookie values, session tokens, cookies.sqlite files, browser profiles,
backups, or authentication data in public issues. Share only sanitized aggregate
diagnostics; remove identifying profile paths. Reproduce problems with synthetic
data when possible.

Until a private reporting channel exists, avoid posting secrets publicly. You may
open a minimal issue asking for a private reporting method without attaching
sensitive material. There is no telemetry or network reporting in the utility.

Security-sensitive cleanup changes must preserve these invariants: refuse while
Firefox is running, create and verify backups before mutation, use transactions
and integrity checks, and never output cookie values. Tests must use synthetic
temporary databases, never real browser profiles.
