from collections import deque

from gui_agent.execution.observation import (
    OBSERVATION_EVALUATE_TIMEOUT_MS,
    ObservationCollector,
    _bounded_semantic_components,
)
from gui_agent.security.redaction import Redactor


def test_document_observations_use_a_bounded_locator_evaluation() -> None:
    calls: list[tuple[str, str, int]] = []

    class FakeLocator:
        def __init__(self, selector: str) -> None:
            self.selector = selector

        def evaluate(self, expression: str, *, timeout: int):
            calls.append((self.selector, expression, timeout))
            if "const issues = []" in expression:
                return {"health": {}, "issues": []}
            return ["button | text=Continue"]

    class FakePage:
        @staticmethod
        def locator(selector: str) -> FakeLocator:
            return FakeLocator(selector)

    collector = object.__new__(ObservationCollector)
    collector.page = FakePage()

    assert collector._page_diagnostics() == {"health": {}, "issues": []}
    assert collector._dom_summary() == ["button | text=Continue"]
    assert [call[0] for call in calls] == ["html", "html"]
    assert [call[2] for call in calls] == [
        OBSERVATION_EVALUATE_TIMEOUT_MS,
        OBSERVATION_EVALUATE_TIMEOUT_MS,
    ]


def test_diagnostics_skip_intentionally_disabled_controls() -> None:
    class FakeLocator:
        @staticmethod
        def evaluate(expression: str, *, timeout: int) -> dict:
            assert "nativeDisabled || ariaDisabled || disabledAncestor" in expression
            assert "transientStatusLayer" in expression
            assert "current.getAttribute('aria-hidden') === 'true'" in expression
            assert "current.parentElement" in expression
            assert timeout == OBSERVATION_EVALUATE_TIMEOUT_MS
            return {"health": {}, "issues": []}

    class FakePage:
        @staticmethod
        def locator(selector: str) -> FakeLocator:
            assert selector == "html"
            return FakeLocator()

    collector = object.__new__(ObservationCollector)
    collector.page = FakePage()

    assert collector._page_diagnostics()["issues"] == []


def test_aborted_media_during_navigation_is_not_reported_as_a_failure() -> None:
    class Request:
        url = "http://target/assets/help.mp4"
        method = "GET"
        resource_type = "media"
        failure = "net::ERR_ABORTED"

    collector = object.__new__(ObservationCollector)
    collector.ignore_rules = ()
    collector._failed_requests = deque(maxlen=100)

    collector._on_request_failed(Request())

    assert list(collector._failed_requests) == []


def test_aborted_google_analytics_telemetry_is_not_reported_as_a_failure() -> None:
    class Request:
        url = "https://www.google-analytics.com/g/collect?v=2"
        method = "POST"
        resource_type = "fetch"
        failure = "net::ERR_ABORTED"

    collector = object.__new__(ObservationCollector)
    collector.ignore_rules = ()
    collector._failed_requests = deque(maxlen=100)

    collector._on_request_failed(Request())

    assert list(collector._failed_requests) == []


def test_aborted_business_request_is_still_reported() -> None:
    class Request:
        url = "https://api.example.com/orders/123"
        method = "POST"
        resource_type = "fetch"
        failure = "net::ERR_ABORTED"

    collector = object.__new__(ObservationCollector)
    collector.ignore_rules = ()
    collector._failed_requests = deque(maxlen=100)

    collector._on_request_failed(Request())

    assert len(collector._failed_requests) == 1


def test_semantic_summary_is_bounded_value_safe_and_signed() -> None:
    class FakeLocator:
        @staticmethod
        def evaluate(expression: str, *, timeout: int) -> dict:
            assert "const normalize" in expression
            assert "el.value" not in expression
            assert "valueStateOf" in expression
            assert "blockingControls" in expression
            assert "document.querySelectorAll('option')" in expression
            assert "const generalResourceNames" in expression
            assert "mineScenarioList" in expression
            assert "data-resource-name" in expression
            assert "const seenRuntimeIds = new Set()" in expression
            assert "const visibleOptions = uniqueOptions" in expression
            assert "visibleOptions()" not in expression
            assert "const dialogCandidates" in expression
            assert "[role=\"dialog\"],.ant-modal" in expression
            assert "const dialogScore" in expression
            assert timeout == OBSERVATION_EVALUATE_TIMEOUT_MS
            return {
                "route": "/#/wizard",
                "heading": "Create agent",
                "headings": ["Create agent"],
                "regions": [],
                "dialogs": [{"role": "", "name": "Create agent", "text": "Step 1"}],
                "controls": [{
                    "role": "textbox", "name": "Agent keyword", "testId": "", "href": "",
                    "disabled": False, "required": True, "valueState": "empty", "invalid": True,
                    "validationMessage": "Required", "disabledReason": "", "selected": False, "checked": False,
                }],
                "forms": [{"name": "", "controls": 2, "submitButtons": 0}],
                "resourceNames": ["test_A", "test_Z", "test_AA"],
                "stateSignals": ["modal_visible"],
            }

    class FakePage:
        @staticmethod
        def locator(selector: str) -> FakeLocator:
            assert selector == "html"
            return FakeLocator()

    collector = object.__new__(ObservationCollector)
    collector.page = FakePage()
    collector.redactor = Redactor()

    summary = collector._semantic_summary()

    assert summary is not None
    assert summary["page_key"] == "/#/wizard|Create agent"
    assert summary["resource_names"] == ["test_A", "test_Z", "test_AA"]
    assert summary["state_signals"] == ["modal_visible"]
    assert summary["controls"][0]["valueState"] == "empty"
    assert summary["controls"][0]["required"] is True
    assert len(summary["signature"]) == 16


def test_repeated_portal_options_are_owned_by_only_the_expanded_component() -> None:
    options = [
        {"runtimeId": f"option-{index}", "text": f"Option {index}", "selected": False, "disabled": False}
        for index in range(40)
    ]
    options[-1]["text"] = "C(communication)"
    components = []
    for index in range(40):
        components.append({
            "runtimeId": f"trigger-{index}",
            "kind": "cascader",
            "expanded": index == 35,
            "visibleOptions": list(options),
            "visibleOptionGroups": [{"groupIndex": 2, "options": list(options)}],
            "optionCount": len(options),
        })
    components.append(dict(components[0]))

    bounded = _bounded_semantic_components(components)

    assert len(bounded) == 30
    assert len({item["runtimeId"] for item in bounded}) == 30
    owners = [item for item in bounded if item["visibleOptionGroups"]]
    assert len(owners) == 1
    assert owners[0]["runtimeId"] == "trigger-35"
    assert owners[0]["visibleOptionGroups"][0]["options"][-1]["text"] == "C(communication)"
    assert sum(len(item["visibleOptions"]) for item in bounded) <= 120


def test_canvas_signature_hashes_only_the_visible_canvas() -> None:
    class CanvasLocator:
        @staticmethod
        def count() -> int:
            return 1

        @property
        def first(self):
            return self

        @staticmethod
        def screenshot(*, animations: str, timeout: int) -> bytes:
            assert animations == "disabled"
            assert timeout == OBSERVATION_EVALUATE_TIMEOUT_MS
            return b"bounded-canvas-evidence"

    class Page:
        @staticmethod
        def locator(selector: str) -> CanvasLocator:
            assert selector == "canvas:visible"
            return CanvasLocator()

    collector = object.__new__(ObservationCollector)
    collector.page = Page()

    assert collector._canvas_signature() == "d1873d2540f1de70"


def test_cesium_canvas_signature_reuses_semantic_pixel_facts() -> None:
    class Page:
        url = "https://ion.cesium.com/stories/editor"

        @staticmethod
        def locator(_selector: str):
            raise AssertionError("Cesium semantic facts should avoid Canvas locator calls")

    collector = object.__new__(ObservationCollector)
    collector.page = Page()

    signature = collector._canvas_signature(canvas_facts={
        "count": 1,
        "surfaces": [{"width": 936, "height": 918, "webgl": True, "nonEmptyPixels": True}],
        "contextLost": False,
    })

    assert len(signature) == 16
