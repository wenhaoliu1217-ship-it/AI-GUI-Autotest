from gui_agent.benchmarks.cesium_ion.policy import validate_cesium_step
from gui_agent.domain.models import EffectLevel, Step


def test_policy_handles_a_valid_string_effect_level_at_the_boundary() -> None:
    step = Step(
        action="navigate",
        target="/assets",
        effect_kind="browse_search_filter_sort",
        effect_level=EffectLevel.READ_ONLY,
    )
    step.effect_level = "read_only"  # type: ignore[assignment]

    validate_cesium_step(step, 1, [])
