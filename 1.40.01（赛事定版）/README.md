# AI-GUI Autotest

This directory is the single source of truth for the unified 1.32.01 project.
The backend source, frontend bundle, Python runtime, Playwright browsers,
Runner image archive, project data, and launch scripts are all kept here.
Desktop folders such as `1.32.01` and `1.32.02` are historical packages and
must not be used as a runtime fallback.

The fixed canonical path on this local deployment is
`C:\Users\zzzzl\Desktop\京彩OPC\AI-GUI-Autotest\赛事截止最终版_20260902`. Its baseline
is the final Wednesday L2 run `20260819-182235-4bfa522b`. All later bug fixes
are applied directly in this tree. Routine work must not create, assemble, or
launch another project copy.

## Layout

```text
backend/          Python source and benchmark definitions
frontend-dist/    The exact frontend bundle used by this project
frontend/         Canonical React/Vite 1.32.01 source and locked build
frontend-recovered/ Versioned historical frontend source recovered from local backups
runtime/          Bundled Python, Playwright browsers, and Runner image
packaging/        Startup scripts and runtime manifest template
tools/            Development, release, and integrity tooling
docs/             Architecture and release rules
.local/           Local-only runtime data, ignored by Git
```

The bundled runtime is part of this project. The launcher never falls back to a
desktop package or another checkout.

## Development run

For the only supported interactive experience, double-click:

```text
双击启动AI测试.bat
```

The launcher validates the runtime, starts the current backend source, waits for
the health endpoint, and opens the web UI automatically. Keep the launcher
window open; pressing Enter or closing the window stops its service. Use
`双击关闭AI测试.bat` to stop a detached service recorded by this source tree.
It attempts to prepare the Docker Runner before startup. If Docker is not
available, the web UI still opens with a warning; real isolated test execution
remains unavailable until Docker is ready. Use `-RequireDocker` for strict
delivery validation.

The launcher rejects external runtime paths and never reads a desktop package.
It also verifies every declared frontend asset hash and requires the prebuilt
bundle's `apiContractVersion` to match the backend before starting. The current
backend and generated frontend bundle are both `1.32.01`; a missing or
incompatible contract is a startup failure, not a warning that can be ignored.

The command-line form is:

```powershell
./tools/Start-Dev.ps1
```

After startup, `/api/health` distinguishes the HTTP service from the actual
isolated Runner. The UI shows Docker CLI path, Engine readiness, Runner image,
bundled Python and Playwright browser status. A healthy web page therefore no
longer implies that a real test can run; the run button is disabled with the
reported reason until the Runner is ready.

For repeatable development verification (without changing the shipped
runtime), use:

```powershell
./tools/Test-All.ps1
```

This uses development Python for pytest, runs the frontend typecheck and
Vitest suite, then performs strict Docker Runner startup. Test temporary files
are kept below `.local/pytest-suite` so Windows Temp permissions cannot make a
healthy codebase appear broken. The production runtime intentionally does not
include pytest; install `backend/requirements-dev.txt` only for development.

It serves the checked-in `frontend-dist` and stores local artifacts under
`.local`. It also writes server logs and a verified PID record there. Do not
point it at a release's `data` or `artifacts` directories.

For a background development service, use `-Detached`; stop it with:

```powershell
./tools/Stop-Dev.ps1
```

The historical desktop package was assembled into this root once on Wednesday.
Do not run the assembly tool again during ordinary development. All development
and bug fixes happen only in this root.

## Publishing a release

```powershell
./tools/New-Release.ps1 `
  -Version '1.32.02' `
  -RuntimePackage 'C:\path\to\1.32.01' `
  -OutputRoot 'C:\path\to\release-output'
./tools/Verify-Release.ps1 -ReleaseRoot 'C:\path\to\release-output\1.32.02'
```

The publisher refuses to overwrite an existing release. This preserves the
ability to reproduce and compare every shipped version.

## Version and frontend policy

The desktop delivery contained only a compiled frontend bundle. The canonical
repository now has the reviewed React/TypeScript source under `frontend/`, with
the 1.32.01 health/version diagnostics changes and a locked build. The older
`frontend-recovered/1.30.20` tree remains an auditable recovery baseline. See
`docs/frontend-source-status.md`.

`unified-package.json` records the one-time assembly source and confirms that
external runtime fallback is disabled. The launcher serves only the generated
`frontend-dist/` output from this source tree. Do not patch minified JavaScript
in place; rebuild `frontend/` and publish the verified output instead.

## Platform support

The currently verified delivery target is **Windows 10/11 + Docker Desktop +
bundled Chromium**. The service may start on another operating system when its
Python and Playwright prerequisites are installed, but Linux, macOS, Dockerless
isolation, Firefox, WebKit, and the private 3D target have not been certified
by this package. They require separate platform adapters and acceptance runs;
the current Windows package is not universal support.

The host adapter and Unix launch entry points are now available for Linux and
macOS bring-up. They intentionally do not claim certification: encrypted login
state is still Windows DPAPI-only and each host must pass the acceptance matrix
before release. See `docs/platform-support.md` for the current tier matrix and
portable startup requirements.
