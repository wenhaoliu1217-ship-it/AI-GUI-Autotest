"""Small host-platform adapter shared by startup and execution code.

The test plan and Runner stay platform-neutral.  This module contains the
few host decisions that cannot be expressed portably: executable discovery,
per-user data locations, subprocess flags, and Playwright cache locations.
"""

from __future__ import annotations

import os
import platform as stdlib_platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def _is_file(path: Path) -> bool:
    """Return false for inaccessible candidates instead of failing readiness."""
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    """Return false for inaccessible candidates instead of failing readiness."""
    try:
        return path.is_dir()
    except OSError:
        return False


def _system_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unknown"


@dataclass(frozen=True)
class HostPlatform:
    """Detected host properties used by the control service."""

    system: str
    machine: str
    release: str
    python: str

    @property
    def is_windows(self) -> bool:
        return self.system == "windows"

    @property
    def is_unix(self) -> bool:
        return self.system in {"linux", "macos"}

    def docker_candidates(self) -> tuple[Path, ...]:
        """Return conventional Docker CLI locations for this host."""
        if self.is_windows:
            return (
                Path(os.getenv("ProgramFiles", r"C:\\Program Files"))
                / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
                Path(os.getenv("LOCALAPPDATA", ""))
                / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
            )
        if self.system == "macos":
            return (
                Path("/usr/local/bin/docker"),
                Path("/opt/homebrew/bin/docker"),
            )
        if self.system == "linux":
            return (Path("/usr/local/bin/docker"), Path("/usr/bin/docker"))
        return ()

    def data_dir(self, app_name: str = "AI-GUI-Autotest") -> Path:
        """Return a user-writable, OS-appropriate data directory."""
        configured = os.getenv("GUI_AGENT_HOME")
        if configured:
            return Path(configured).expanduser()
        if self.is_windows:
            root = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
            return (Path(root) if root else Path.home() / "AppData" / "Local") / app_name
        if self.system == "macos":
            return Path.home() / "Library" / "Application Support" / app_name
        root = os.getenv("XDG_DATA_HOME")
        return (Path(root).expanduser() if root else Path.home() / ".local" / "share") / app_name

    def playwright_cache_dirs(self) -> tuple[Path, ...]:
        if self.is_windows:
            root = os.getenv("LOCALAPPDATA")
            return (Path(root) / "ms-playwright",) if root else ()
        if self.system == "macos":
            return (Path.home() / "Library" / "Caches" / "ms-playwright",)
        if self.system == "linux":
            return (Path.home() / ".cache" / "ms-playwright",)
        return ()

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "system": self.system,
            "machine": self.machine,
            "release": self.release,
            "python": self.python,
            "isWindows": self.is_windows,
            "isUnix": self.is_unix,
        }


def detect_host_platform() -> HostPlatform:
    return HostPlatform(
        system=_system_name(),
        machine=stdlib_platform.machine() or "unknown",
        release=stdlib_platform.release() or "unknown",
        python=sys.version.split()[0],
    )


def subprocess_creationflags() -> int:
    """Suppress a console window on Windows and remain zero elsewhere."""
    if _system_name() == "windows":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


def resolve_docker_executable() -> str | None:
    host = detect_host_platform()
    configured = os.getenv("GUI_DOCKER_CLI")
    if configured and _is_file(Path(configured)):
        return str(Path(configured))
    discovered = shutil.which("docker")
    if discovered:
        return discovered
    return next(
        (str(candidate) for candidate in host.docker_candidates() if _is_file(candidate)),
        None,
    )


def resolve_playwright_browser_root(project_runtime: Path | None = None) -> Path | None:
    """Find an installed Playwright browser directory without Path("") bugs."""
    configured = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    candidates: list[Path] = []
    if configured and configured != "0":
        candidates.append(Path(configured).expanduser())
    if project_runtime is not None:
        candidates.append(project_runtime)
    candidates.extend(detect_host_platform().playwright_cache_dirs())
    existing = [candidate.resolve() for candidate in candidates if _is_dir(candidate)]
    return next((candidate for candidate in existing if playwright_chromium_ready(candidate)), None) or (
        existing[0] if existing else None
    )


def playwright_chromium_ready(root: Path | None) -> bool:
    return playwright_browser_ready(root, "chromium")


def _system_browser_ready(name: str) -> bool:
    host = detect_host_platform()
    candidates: tuple[Path, ...]
    if name == "edge":
        if host.is_windows:
            candidates = (
                Path(os.getenv("ProgramFiles", r"C:\\Program Files")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            )
        elif host.system == "macos":
            candidates = (Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),)
        else:
            candidates = (Path("/usr/bin/microsoft-edge"), Path("/usr/bin/microsoft-edge-stable"))
    else:
        candidates = ()
    return bool(shutil.which("msedge" if name == "edge" and host.is_windows else "microsoft-edge" if name == "edge" else name)) or any(_is_file(path) for path in candidates)


def playwright_browser_ready(root: Path | None, browser_name: str | None = None) -> bool:
    """Check the selected Playwright browser artifact or system Edge channel."""
    name = (browser_name or os.getenv("GUI_BROWSER", "chromium")).strip().lower()
    name = {"chrome": "chromium", "msedge": "edge"}.get(name, name)
    if name == "edge":
        return _system_browser_ready(name)
    if root is None or not root.is_dir():
        return False
    relative_paths = {
        "chromium": {
            "windows": ("chrome-win/chrome.exe",),
            "linux": ("chrome-linux/chrome",),
            "macos": ("chrome-mac/Chromium.app/Contents/MacOS/Chromium",),
        },
        "firefox": {
            "windows": ("firefox/firefox.exe",),
            "linux": ("firefox/firefox",),
            "macos": ("firefox/Firefox.app/Contents/MacOS/firefox", "firefox/firefox"),
        },
        "webkit": {
            "windows": ("Playwright.exe",),
            "linux": ("pw_run.sh",),
            "macos": ("pw_run.sh", "Playwright.app/Contents/MacOS/Playwright"),
        },
    }.get(name, {})
    if not relative_paths:
        return False
    return any(
        (browser_dir / relative).is_file()
        for browser_dir in root.glob(f"{name}-*")
        for relative in relative_paths.get(detect_host_platform().system, ())
    )
