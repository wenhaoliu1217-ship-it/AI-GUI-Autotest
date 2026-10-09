from __future__ import annotations

from gui_agent.execution.grounding import (
    ground_click_target,
    narrow_to_unique_visible_click_target,
)


class _Property:
    def __init__(self, *, element=None, value=None) -> None:
        self._element = element
        self._value = value

    def as_element(self):
        return self._element

    def json_value(self):
        return self._value


class _Bundle:
    def __init__(self, target, evidence) -> None:
        self.target = target
        self.evidence = evidence

    def get_property(self, name: str):
        if name == "target":
            return _Property(element=self.target)
        return _Property(value=self.evidence)


class _Locator:
    def __init__(self, target, evidence) -> None:
        self.target = target
        self.evidence = evidence
        self.expression = ""

    def evaluate_handle(self, expression: str):
        self.expression = expression
        return _Bundle(self.target, self.evidence)


def test_current_hit_target_proxy_is_returned_for_execution() -> None:
    pointer_target = object()
    locator = _Locator(
        pointer_target,
        {"accepted": True, "mode": "current_hit_target_proxy"},
    )

    grounded = ground_click_target(locator)

    assert grounded.target is pointer_target
    assert grounded.evidence["mode"] == "current_hit_target_proxy"
    assert "elementFromPoint" in locator.expression


def test_unrelated_occluder_is_not_accepted() -> None:
    grounded = ground_click_target(
        _Locator(None, {"accepted": False, "mode": "unrelated_occluder"})
    )

    assert grounded.target is None
    assert grounded.evidence["accepted"] is False


class _Candidate:
    def __init__(self, visible: bool, box: dict | None) -> None:
        self.visible = visible
        self.box = box

    def bounding_box(self):
        return self.box

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return True

    def count(self):
        return 1


class _Candidates:
    def __init__(self, items) -> None:
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index: int):
        return self.items[index]


def test_duplicate_semantic_options_use_the_only_visible_geometry() -> None:
    hidden = _Candidate(False, None)
    visible = _Candidate(True, {"x": 10, "y": 10, "width": 120, "height": 30})

    resolution = narrow_to_unique_visible_click_target(
        _Candidates([hidden, visible])
    )

    assert resolution.locator is visible
    assert resolution.evidence == {
        "mode": "unique_visible_geometry",
        "candidateCount": 2,
        "selectedIndex": 1,
    }
