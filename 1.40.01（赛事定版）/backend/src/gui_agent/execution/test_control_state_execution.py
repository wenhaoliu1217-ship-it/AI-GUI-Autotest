from gui_agent.domain.models import Step
from gui_agent.execution import runner
from gui_agent.security.redaction import Redactor


class FakeInput:
    def __init__(self, value: str = "") -> None:
        self.value = value

    @property
    def first(self):
        return self

    def fill(self, value: str) -> None:
        self.value = value

    def clear(self) -> None:
        self.value = ""

    def input_value(self) -> str:
        return self.value


class FakeSelectedOption:
    def __init__(self, label: str) -> None:
        self.label = label

    @property
    def first(self):
        return self

    def inner_text(self) -> str:
        return self.label


class FakeSelect(FakeInput):
    def __init__(self, internal_value: str, label: str) -> None:
        super().__init__(internal_value)
        self.label = label

    def select_option(self, _value: str) -> None:
        pass

    def locator(self, selector: str) -> FakeSelectedOption:
        assert selector == "option:checked"
        return FakeSelectedOption(self.label)


def _execute(monkeypatch, control: FakeInput, step: Step) -> dict:
    monkeypatch.setattr(runner, "resolve_step_locator", lambda *_args, **_kwargs: control)
    return runner._execute_step(
        object(),
        step,
        "https://ion.cesium.com",
        object(),
        Redactor(),
        timeout_ms=30_000,
    )


def test_fill_returns_control_state_proof(monkeypatch) -> None:
    detail = _execute(
        monkeypatch,
        FakeInput(),
        Step(
            action="fill",
            locator={"role": "searchbox", "name": "Search"},
            value="Google",
        ),
    )

    assert detail["controlState"] == {
        "kind": "value",
        "verified": True,
        "actualLength": 6,
        "expectedLength": 6,
    }


def test_clear_returns_control_state_proof(monkeypatch) -> None:
    detail = _execute(
        monkeypatch,
        FakeInput("Google"),
        Step(action="clear", locator={"role": "searchbox", "name": "Search"}),
    )

    assert detail["controlState"] == {
        "kind": "value",
        "verified": True,
        "actualLength": 0,
        "expectedLength": 0,
    }


def test_select_accepts_exact_visible_label_when_native_value_is_internal_id(monkeypatch) -> None:
    detail = _execute(
        monkeypatch,
        FakeSelect("party-blue-id", "蓝方"),
        Step(
            action="select",
            locator={"role": "combobox", "name": "参与方"},
            value="蓝方",
        ),
    )

    assert detail["controlState"]["verified"] is True
