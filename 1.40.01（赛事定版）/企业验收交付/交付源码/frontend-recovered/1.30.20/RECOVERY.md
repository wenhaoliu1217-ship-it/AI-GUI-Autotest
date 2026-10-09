# 1.30.20 Frontend Recovery Record

## Provenance

Recovered on 2026-08-19 from the local source backup:

```text
C:\Users\14054\Documents\Codex\2026-07-22\ni-xian\1.30.20-work\源码
```

The source version remains `1.30.20`; it was not relabeled as 1.32.01.

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

This source predates the current 1.32.01 compiled UI. Its output must not be
copied to `../../frontend-dist` or a desktop release without an explicit port,
API contract review, browser parity run and target-site regression.

## Verification performed on 2026-08-19

- locked install: `npm ci` succeeded
- TypeScript: `npm run lint` succeeded
- unit tests: 19 of 19 passed across 2 suites
- production build: `npm run build` succeeded
- rebuilt JavaScript SHA-256:
  `E8431A358BD5021FC3C49A5B5EB9D3EB26A66A9A65301C1D86CFA8036E755B6B`
- rebuilt CSS SHA-256:
  `81F3DC9DBD1BA6290ED6062212ED665561C9E748EB86EEDC7355A5915E7AFCA6`

Both build hashes exactly match the original 1.30.20 backup build.
