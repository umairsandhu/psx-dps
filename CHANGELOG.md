# Changelog

## Unreleased

- Stand down instead of retrying when PSX pushes back: a 429 or 503 stops
  immediately and starts a shared, escalating cooldown (60s → 1h) honouring
  `Retry-After`. Cached data keeps serving throughout.
- Request gzip — the market watch drops from ~476 KB to ~60 KB on the wire.
- `Client.snapshot()`: one timestamped, numeric snapshot of the whole market,
  so pollers never need per-symbol calls.
- `psx-dps cooldown` to inspect or clear the back-off state; `doctor` and
  `health()` now report it.
- Renamed `CircuitOpen` to `CoolingDown` because the old name was jargon.
  `CircuitOpen` remains as an alias.
- New [docs/STAYING-UNBLOCKED.md](docs/STAYING-UNBLOCKED.md).
- `psx-dps diagnose [--deep] [--rescan]`, also importable as `diagnose.run()`,
  which separates the failure modes that all look like "no data" and repins
  a stale node automatically. `--deep` validates every endpoint's payload
  *shape*, catching a PSX redesign that still returns 200.
- `psx-dps nodes` records every probe with a timestamp, so "when did the IP
  change?" is answerable after the fact; `--sample` harvests the rotating A
  record over time using DNS only.
- Corrected a wrong finding: `X-Requested-With` does **not** trigger a 403.
  A 403 on a data route means you are on a node that does not serve them.
  There is no WAF on this service.
- New [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md),
  [docs/SYMBOLS.md](docs/SYMBOLS.md) and
  [docs/EVALUATION-PROMPT.md](docs/EVALUATION-PROMPT.md).

## 0.1.0 — 2026-09-29

First release.

- `Client` library API plus a `psx-dps` CLI, stdlib only.
- Node pool with health probing and pinning, which works around PSX serving
  404 for data routes on some nodes while DNS advertises exactly those.
- Shared on-disk cache with market-hours-aware TTLs.
- Machine-wide cross-process rate limiting and a 24h runaway-loop fuse.
- Endpoint reference, discovery write-up, and fair-use guidance in `docs/`.
- 54 offline tests.
