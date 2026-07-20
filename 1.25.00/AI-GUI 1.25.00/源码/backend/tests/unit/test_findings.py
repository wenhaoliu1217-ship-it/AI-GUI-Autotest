from gui_agent.domain.results import AssertionResult, Status
from gui_agent.execution.findings import build_findings


def test_visibility_finding_uses_description_instead_of_none_string() -> None:
    findings = build_findings([], [AssertionResult(
        index=1,
        type="visible",
        description="客户管理标题可见",
        detail="visible",
        status=Status.FAILED,
        expected_summary="None",
        actual_summary="visible=False",
    )], [])

    assert findings[0].expected_result == "客户管理标题可见"
