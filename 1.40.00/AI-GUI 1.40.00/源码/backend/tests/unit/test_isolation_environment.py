from gui_agent.execution.isolation import SHARED_BROWSER_ENV


def test_spawned_runner_retains_shared_browser_contract() -> None:
    assert SHARED_BROWSER_ENV == {"GUI_RUNNER_MODE", "GUI_BROWSER_CDP_URL"}
