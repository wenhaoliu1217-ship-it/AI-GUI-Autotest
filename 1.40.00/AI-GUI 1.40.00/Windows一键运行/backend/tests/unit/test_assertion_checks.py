from gui_agent.assertions.checks import check_assertion
from gui_agent.domain.models import Assertion, AssertionType, Locator


class _Match:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    def is_visible(self) -> bool:
        return self._visible


class _Matches:
    def __init__(self, values: list[bool]) -> None:
        self._values = values

    def count(self) -> int:
        return len(self._values)

    def nth(self, index: int) -> _Match:
        return _Match(self._values[index])


class _Page:
    def __init__(self, values: list[bool]) -> None:
        self._values = values

    def get_by_text(self, _text: str, *, exact: bool = False) -> _Matches:
        return _Matches(self._values)


def test_visible_assertion_passes_when_first_match_is_hidden_but_later_match_is_visible() -> None:
    assertion = Assertion(type=AssertionType.VISIBLE, locator=Locator(text="京东"))
    outcome = check_assertion(_Page([False, True, True]), assertion)  # type: ignore[arg-type]

    assert outcome.passed is True
    assert outcome.actual == "matches=3; visible=2"


def test_visible_assertion_fails_when_all_matches_are_hidden() -> None:
    assertion = Assertion(type=AssertionType.VISIBLE, locator=Locator(text="京东"))
    outcome = check_assertion(_Page([False, False]), assertion)  # type: ignore[arg-type]

    assert outcome.passed is False
    assert outcome.actual == "matches=2; visible=0"
