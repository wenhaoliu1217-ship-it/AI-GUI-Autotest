from gui_agent.domain.results import Observation, PageSemanticSummary
from gui_agent.execution.observation import blocking_page_error


def test_visible_rendering_failure_is_a_blocking_application_error() -> None:
    observation = Observation(
        accessibility_summary=(
            '- text: An error occurred while rendering. Rendering has stopped.\n'
            '- paragraph: "TypeError: Cannot read properties of undefined (reading \'length\')"\n'
            '- button "OK"'
        )
    )

    assert blocking_page_error(observation) == (
        "TypeError: Cannot read properties of undefined (reading 'length')"
    )


def test_structured_blocking_error_is_preferred_over_unrelated_page_text() -> None:
    observation = Observation(
        accessibility_summary="- paragraph: TypeError examples in documentation",
        semantic_summary=PageSemanticSummary(
            blocking_errors=["ReferenceError: editorState is not defined"],
            state_signals=["fatal_render_error"],
        ),
    )

    assert blocking_page_error(observation) == (
        "ReferenceError: editorState is not defined"
    )


def test_nonfatal_error_documentation_does_not_block_the_run() -> None:
    observation = Observation(
        accessibility_summary="- heading: How to debug TypeError in JavaScript"
    )

    assert blocking_page_error(observation) is None
