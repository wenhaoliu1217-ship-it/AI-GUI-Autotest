# Frontend Source Status

The original desktop delivery contained only a compiled bundle, but the
canonical repository now contains a reviewed source tree at `frontend/`. It is
based on the recovered 1.30.20 React/Vite source and includes the 1.32.01 API
health/version diagnostics additions. `frontend-dist/` remains a generated
release directory and is never edited as source.

## Recovered historical baseline

A filesystem-wide local backup audit found a complete source tree at:

```text
C:\Users\14054\Documents\Codex\2026-07-22\ni-xian\1.30.20-work\源码
```

It has been copied without changing its source version into:

```text
frontend-recovered/1.30.20
```

The recovered tree includes `src/App.tsx`, tests, API types and client,
`package.json`, `package-lock.json`, Vite and TypeScript configuration, and the
production build helper. The recovered `App.tsx` SHA-256 is
`8B7B0B7B846493AB35BD4801114396D6DDF148B98B800EDB9654150F42D9173F`,
which matches the local backup.

This is a verified source backup for version 1.30.20. It is not claimed to be
the missing 1.32.01 source: its historical bundle hash and feature surface do
not match the current `frontend-dist` bundle. Replacing `frontend-dist` with a
build from this directory would regress current UI behavior.

## Required migration gate

The current frontend implementation must pass all of the following before
publishing:

1. Locked dependency installation, TypeScript checks, unit tests and build.
2. API contract comparison against the current backend.
3. Browser parity for project setup, model probe, login, scan, Agent run,
   confirmation, evidence, report and recovery screens.
4. Real regression of the existing 192.168.31.218:7991 and Cesium flows.
5. A reviewed switch of the generated release input from `frontend/dist` to
   `frontend-dist` only after the preceding checks pass.

Reverse-engineering or directly editing the minified bundle is not accepted as
source recovery.
