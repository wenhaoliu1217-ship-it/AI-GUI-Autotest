# Platform support

The control service now keeps host-specific discovery behind
`gui_agent.platform_support`. Test planning, evidence, completion gates, and
site capability packs remain shared code and must not branch by operating
system.

## Support tiers

| Tier | Host | Browser | Status |
| --- | --- | --- | --- |
| Tier 1 | Windows 10/11 + Docker Desktop | bundled Chromium | Certified |
| Tier 2 | Ubuntu 22.04/24.04 + Docker Engine | Playwright Chromium | Adapter implemented; acceptance pending |
| Tier 3 | macOS 13+ Intel/Apple Silicon + Docker Desktop | Playwright Chromium | Adapter implemented; acceptance pending |
| Tier 4 | Any host | Firefox/WebKit | Not certified |

An implemented adapter is not a certification. A platform becomes certified
only after the full acceptance matrix passes on real hardware or a matching CI
runner.

## Portable entry points

Windows keeps its existing entry point:

```powershell
./start.ps1
```

Linux and macOS use:

```sh
./start.sh
# or, for a background service:
./start.sh --detached --port 8080
./stop.sh
```

The Unix entry point expects Python 3 with `backend/requirements-runtime.txt`
and `backend/requirements-platform.txt` installed, plus a Playwright Chromium
installation. Set `GUI_AGENT_PYTHON` when the desired interpreter is not
`python3` or `python`. Docker must be available on `PATH`, or `GUI_DOCKER_CLI`
must name the executable.

Both launch paths anchor frontend, data, artifact, and browser paths to this
checkout. They do not write runtime state relative to the shell's current
directory.

Before an acceptance run, use the platform preflight command:

```sh
./tools/platform-preflight.sh --require-docker --pretty
```

On Windows the equivalent is:

```powershell
./tools/platform-preflight.ps1 -RequireDocker -Pretty
```

It returns machine-readable JSON and exits non-zero when a required
prerequisite is missing. The command is diagnostic only and does not start or
stop Docker or the development service.

Chromium remains the default browser. Set `GUI_BROWSER` to `chromium`, `edge`,
`firefox`, or `webkit` to select another Playwright browser. The current
Docker Runner image certifies Chromium only; other browsers require process
mode or a separately built Runner image and a matching acceptance run.

## Acceptance matrix

The executable matrix and CI workflow are documented in
[`platform-acceptance-matrix.md`](platform-acceptance-matrix.md) and
`.github/workflows/platform-acceptance.yml`. A CI green result is evidence for
that runner only; it does not silently certify a different host, browser, GPU,
or credential backend.

Each platform certification must cover:

1. Health and API contract verification.
2. Docker Runner startup and cleanup.
3. Login-state save and restore using the platform credential backend.
4. Form, dialog, select, upload, and download actions.
5. Dynamic-page re-observation and recovery.
6. Canvas/WebGL pixel evidence and nonblank screenshots.
7. Complete audit artifacts and accurate failure classification.

On Windows the encrypted store uses DPAPI. On Linux, `keyring` must resolve to
Secret Service (for example, `gnome-keyring` or KDE Wallet); on macOS it uses
the system Keychain backend. A plaintext or fail-safe keyring backend is
rejected. These adapters are implemented in the source tree, but each host
must still pass a real save/restart/restore acceptance run before certification.
