from gui_agent.planning.cesium_strategy import CesiumDecisionStrategy
from gui_agent.planning.site_strategy import default_site_strategy


def test_generic_target_does_not_load_a_site_decision_strategy() -> None:
    assert default_site_strategy("https://example.test") is None


def test_cesium_target_gets_an_explicit_site_decision_strategy() -> None:
    strategy = default_site_strategy("https://ion.cesium.com")

    assert isinstance(strategy, CesiumDecisionStrategy)
    assert strategy.matches("https://ion.cesium.com/stories") is True
    assert strategy.matches("https://example.test") is False
