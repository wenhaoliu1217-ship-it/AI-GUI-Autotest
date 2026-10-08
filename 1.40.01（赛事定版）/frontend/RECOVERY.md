# 1.32.01 Frontend Source and Migration Record

## Provenance

Recovered on 2026-08-19 from the local source backup:

```text
C:\Users\14054\Documents\Codex\2026-07-22\ni-xian\1.30.20-work\源码
```

The source tree is now the canonical 1.32.01 migration source. It retains the
audited 1.30.20 baseline history and includes the 1.32.01 health/version
diagnostics contract additions. It is rebuilt into `frontend-dist`; minified
release assets are never edited as source.

## Included source material

- `src/App.tsx` and `src/App.test.tsx`
- `src/services/api.ts` and `src/services/types.ts`
- application entry point, styles and test setup
- `package.json` and locked `package-lock.json`
- Vite and TypeScript configuration
- production build and browser verification scripts

The recovered `src/App.tsx` SHA-256 is:

```text
8B7B0B7B846493AB35BD4801114396D6DDF148B98B800EDB9654150F42D9173F
```

## Safety boundary

The build is published to `../../frontend-dist` only through the explicit
frontend build and manifest verification step. The backend API contract is
`ai-gui-http-v1`; a bundle with a different contract or hash is rejected by the
launcher.

## Verification performed on 2026-08-19

- locked install: `npm ci` succeeded
- TypeScript: `npm run lint` succeeded
- unit tests: 19 of 19 passed across 2 suites
- production build: `npm run build` succeeded
- rebuilt JavaScript SHA-256:
  `E8431A358BD5021FC3C49A5B5EB9D3EB26A66A9A65301C1D86CFA8036E755B6B`
- rebuilt CSS SHA-256:
  `81F3DC9DBD1BA6290ED6062212ED665561C9E748EB86EEDC7355A5915E7AFCA6`

The build hashes are recorded in `frontend-dist/bundle-manifest.json`; they are
expected to differ from the original 1.30.20 backup because the current source
includes the 1.32.01 health/version diagnostics contract.
