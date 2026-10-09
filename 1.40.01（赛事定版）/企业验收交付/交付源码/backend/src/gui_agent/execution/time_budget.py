"""Runtime budget accounting with an explicit pause for page rendering."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from time import monotonic
from typing import Callable, Iterator


RenderingPauseCallback = Callable[[str, str, float], None]


@dataclass
class RunTimeBudget:
    """Track the execution budget without charging bounded render waits.

    Rendering waits still have their own Playwright timeout. This class only
    prevents that wait from consuming the separate run-wide action budget.
    """

    limit_seconds: float | None
    started_at: float = field(default_factory=lambda: monotonic())
    rendering_pause_callback: RenderingPauseCallback | None = None
    paused_seconds: float = 0.0
    rendering_paused_seconds: float = 0.0
    _pause_depth: int = field(default=0, init=False, repr=False)
    _rendering_depth: int = field(default=0, init=False, repr=False)
    _pause_started_at: float | None = field(default=None, init=False, repr=False)
    _rendering_started_at: float | None = field(default=None, init=False, repr=False)
    _pause_reason: str | None = field(default=None, init=False, repr=False)
    _rendering_reason: str | None = field(default=None, init=False, repr=False)

    @property
    def rendering_wait_active(self) -> bool:
        return self._rendering_depth > 0

    @property
    def rendering_wait_reason(self) -> str | None:
        return self._rendering_reason

    def elapsed_seconds(self) -> float:
        now = monotonic()
        active = now - self._pause_started_at if self._pause_started_at is not None else 0.0
        return max(0.0, now - self.started_at - self.paused_seconds - active)

    def remaining_seconds(self) -> float | None:
        if self.limit_seconds is None:
            return None
        return float(self.limit_seconds) - self.elapsed_seconds()

    def exceeded(self) -> bool:
        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0

    def snapshot(self) -> dict[str, float | bool | str | None]:
        return {
            "runtimeElapsedSeconds": round(self.elapsed_seconds(), 3),
            "excludedWaitSeconds": round(self.paused_seconds, 3),
            "renderingPauseSeconds": round(self.rendering_paused_seconds, 3),
            "renderingWaitActive": self.rendering_wait_active,
            "renderingWaitReason": self.rendering_wait_reason,
            "runtimeLimitSeconds": self.limit_seconds,
        }

    @contextmanager
    def excluded_wait(self, reason: str, *, rendering: bool = False) -> Iterator[None]:
        """Temporarily exclude a bounded wait from the run-wide budget."""
        outer = self._pause_depth == 0
        outer_rendering = rendering and self._rendering_depth == 0
        if outer:
            self._pause_started_at = monotonic()
            self._pause_reason = reason
        self._pause_depth += 1
        if rendering:
            if outer_rendering:
                self._rendering_started_at = (
                    self._pause_started_at if outer else monotonic()
                )
                self._rendering_reason = reason
            self._rendering_depth += 1
            if outer_rendering and self.rendering_pause_callback is not None:
                self.rendering_pause_callback("started", reason, 0.0)
        try:
            yield
        finally:
            finished_at = monotonic() if outer or outer_rendering else None
            if rendering:
                self._rendering_depth -= 1
                if outer_rendering:
                    rendering_duration = max(
                        0.0,
                        (finished_at if finished_at is not None else monotonic())
                        - (
                            self._rendering_started_at
                            if self._rendering_started_at is not None
                            else monotonic()
                        ),
                    )
                    self.rendering_paused_seconds += rendering_duration
            self._pause_depth -= 1
            if outer:
                duration = max(
                    0.0,
                    (finished_at if finished_at is not None else monotonic())
                    - (
                        self._pause_started_at
                        if self._pause_started_at is not None
                        else monotonic()
                    ),
                )
                self.paused_seconds += duration
                self._pause_started_at = None
                self._pause_reason = None
            if outer_rendering:
                if self.rendering_pause_callback is not None:
                    self.rendering_pause_callback(
                        "finished", reason, rendering_duration
                    )
                self._rendering_started_at = None
                self._rendering_reason = None

    def rendering_wait(self, reason: str) -> Iterator[None]:
        """Return a context manager for a page/Canvas/WebGL rendering wait."""
        return self.excluded_wait(reason, rendering=True)
