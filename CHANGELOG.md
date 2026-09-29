# Changelog

## 0.1.0 — 2026-09-29

First release.

- `Client` library API plus a `psx-dps` CLI, stdlib only.
- Node pool with health probing and pinning, which works around PSX serving
  404 for data routes on some nodes while DNS advertises exactly those.
- Shared on-disk cache with market-hours-aware TTLs.
- Machine-wide cross-process rate limiting and a 24h runaway-loop fuse.
- Endpoint reference, discovery write-up, and fair-use guidance in `docs/`.
- 54 offline tests.
