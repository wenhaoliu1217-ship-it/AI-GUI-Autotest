from gui_agent.execution.time_budget import RunTimeBudget


def test_rendering_wait_is_excluded_from_run_budget(monkeypatch) -> None:
    clock = iter([0.0, 2.0, 12.0, 12.0, 12.0, 12.0, 12.0])
    monkeypatch.setattr("gui_agent.execution.time_budget.monotonic", lambda: next(clock))
    budget = RunTimeBudget(limit_seconds=5)

    with budget.rendering_wait("3d_scene_loading"):
        assert budget.exceeded() is False

    assert budget.elapsed_seconds() == 2.0
    assert budget.exceeded() is False
    assert budget.paused_seconds == 10.0
    assert budget.rendering_paused_seconds == 10.0


def test_regular_wait_remains_in_run_budget(monkeypatch) -> None:
    clock = iter([0.0, 2.0, 6.0, 6.0, 6.0, 6.0, 6.0])
    monkeypatch.setattr("gui_agent.execution.time_budget.monotonic", lambda: next(clock))
    budget = RunTimeBudget(limit_seconds=5)

    with budget.excluded_wait("user_clarification"):
        assert budget.exceeded() is False

    assert budget.elapsed_seconds() == 2.0
    assert budget.exceeded() is False
    assert budget.paused_seconds == 4.0
    assert budget.rendering_paused_seconds == 0.0
    snapshot = budget.snapshot()
    assert snapshot["excludedWaitSeconds"] == 4.0
    assert snapshot["renderingPauseSeconds"] == 0.0


def test_rendering_pause_callback_receives_bounded_evidence(monkeypatch) -> None:
    clock = iter([0.0, 1.0, 4.0, 4.0, 4.0])
    monkeypatch.setattr("gui_agent.execution.time_budget.monotonic", lambda: next(clock))
    events: list[tuple[str, str, float]] = []
    budget = RunTimeBudget(10, rendering_pause_callback=lambda *item: events.append(item))

    with budget.rendering_wait("canvas_settlement"):
        pass

    assert events == [
        ("started", "canvas_settlement", 0.0),
        ("finished", "canvas_settlement", 3.0),
    ]


def test_rendering_callback_observes_active_then_finished_state(monkeypatch) -> None:
    clock = iter([0.0, 1.0, 1.0, 4.0, 4.0])
    monkeypatch.setattr("gui_agent.execution.time_budget.monotonic", lambda: next(clock))
    states: list[tuple[str, bool, str | None, float]] = []
    budget = RunTimeBudget(10)
    budget.rendering_pause_callback = lambda phase, _reason, _duration: states.append(
        (
            phase,
            budget.rendering_wait_active,
            budget.rendering_wait_reason,
            budget.snapshot()["excludedWaitSeconds"],
        )
    )

    with budget.rendering_wait("webgl_scene_loading"):
        pass

    assert states == [
        ("started", True, "webgl_scene_loading", 0.0),
        ("finished", False, "webgl_scene_loading", 3.0),
    ]
