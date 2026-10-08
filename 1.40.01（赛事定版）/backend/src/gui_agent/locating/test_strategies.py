from types import SimpleNamespace

from gui_agent.domain.models import Locator, LocatorScope
import pytest

from gui_agent.locating.strategies import LocatorError, _resolve_scope_context, resolve_locator


class FakeLocator:
    def __init__(self, name: str, count: int = 0) -> None:
        self.name = name
        self._count = count

    def count(self) -> int:
        return self._count

    def filter(self, **_kwargs):
        return self

    def locator(self, selector: str):
        assert selector == "xpath=ancestor::*[.//button][1]"
        return FakeLocator("modal-container", 1)


class FakePage:
    def get_by_role(self, role: str, **_kwargs):
        if role == "dialog":
            return FakeLocator("unnamed-dialog", 0)
        if role == "heading":
            return FakeLocator("dialog-heading", 1)
        raise AssertionError(role)

    @staticmethod
    def get_by_text(*_args, **_kwargs):
        return FakeLocator("text", 0)

    @staticmethod
    def locator(_selector: str):
        return FakeLocator("css-modal", 0)


def test_dialog_scope_recovers_from_visible_heading() -> None:
    scope = SimpleNamespace(
        kind="dialog",
        identity="Create agent",
        locator=SimpleNamespace(),
    )

    result = _resolve_scope_context(FakePage(), scope)

    assert result.name == "modal-container"
    assert result.count() == 1


def test_runtime_id_ignores_stale_dialog_scope() -> None:
    class RuntimePage:
        def __init__(self) -> None:
            self.selector = ""

        def locator(self, selector: str):
            self.selector = selector
            return FakeLocator("runtime", 1)

    page = RuntimePage()
    locator = Locator(
        runtime_id="ai_42",
        scope=LocatorScope(
            kind="dialog",
            identity="Create model",
            locator=Locator(role="dialog", name="Create model"),
        ),
    )

    resolved = resolve_locator(page, locator)

    assert resolved.name == "runtime"
    assert page.selector == '[data-ai-gui-runtime-id="ai_42"]'


def test_exact_card_identity_never_falls_back_to_partial_name() -> None:
    class PartialOnlyPage:
        @staticmethod
        def get_by_text(_text: str, *, exact: bool):
            return FakeLocator("partial" if not exact else "exact", 1 if not exact else 0)

        @staticmethod
        def locator(_selector: str):
            return FakeLocator("cards", 0)

    scope = LocatorScope(
        kind="card",
        identity="test_D",
        locator=Locator(text="test_D", exact=True),
    )

    with pytest.raises(LocatorError):
        _resolve_scope_context(PartialOnlyPage(), scope)


class StrategyLocator:
    def __init__(self, name: str, count: int) -> None:
        self.name = name
        self._count = count

    def count(self):
        return self._count

    def filter(self, **_kwargs):
        return self

    def get_by_role(self, role: str, **kwargs):
        name = kwargs.get("name")
        if role == "combobox" and name:
            return StrategyLocator("wrong-exact-name", 0)
        if role == "combobox":
            return StrategyLocator("unique-scoped-combobox", 1)
        return StrategyLocator("role", 0)

    def get_by_label(self, label: str, **_kwargs):
        return StrategyLocator("label-participant", 1 if label == "Participant" else 0)

    def get_by_placeholder(self, *_args, **_kwargs):
        return StrategyLocator("placeholder", 0)

    def get_by_test_id(self, *_args, **_kwargs):
        return StrategyLocator("test-id", 0)

    def get_by_text(self, *_args, **_kwargs):
        return StrategyLocator("text", 0)

    def locator(self, *_args, **_kwargs):
        return StrategyLocator("css", 0)


def test_failed_role_name_falls_back_to_current_label() -> None:
    page = StrategyLocator("page", 1)

    resolved = resolve_locator(
        page,
        Locator(role="combobox", name="wrong options concatenated", label="Participant"),
    )

    assert resolved.name == "label-participant"


def test_label_falls_back_to_unique_accessible_textbox_when_no_native_label() -> None:
    """ARIA-labelled fields remain locatable when a library omits <label for>."""

    class LabelOnlyPage(StrategyLocator):
        def get_by_label(self, _label: str, **_kwargs):
            return StrategyLocator("missing-native-label", 0)

        def get_by_role(self, role: str, **kwargs):
            if role == "textbox" and kwargs.get("name") == "搜索想定名称":
                return StrategyLocator("aria-textbox", 1)
            return StrategyLocator(role, 0)

    resolved = resolve_locator(LabelOnlyPage("page", 1), Locator(label="搜索想定名称"))

    assert resolved.name == "aria-textbox"


def test_scoped_locator_can_fall_back_to_unique_current_role() -> None:
    page = StrategyLocator("page", 1)
    scope = LocatorScope(
        kind="dialog",
        identity="Create agent",
        locator=Locator(role="dialog"),
    )

    resolved = resolve_locator(
        page,
        Locator(role="combobox", name="wrong options concatenated", scope=scope),
    )

    assert resolved.name == "unique-scoped-combobox"


def test_locator_uses_the_only_visible_duplicate_after_rerender() -> None:
    class DuplicateLocator:
        def __init__(self, name: str, visible: list[bool], index: int | None = None) -> None:
            self.name = name
            self.visible = visible
            self.index = index

        def count(self) -> int:
            return len(self.visible) if self.index is None else 1

        def nth(self, index: int):
            return DuplicateLocator(self.name, self.visible, index)

        def is_visible(self) -> bool:
            assert self.index is not None
            return self.visible[self.index]

    class DuplicatePage:
        def get_by_role(self, role: str, **_kwargs):
            assert role == "button"
            return DuplicateLocator("save", [False, True])

    resolved = resolve_locator(
        DuplicatePage(),
        Locator(role="button", name="保存"),
    )

    assert resolved.count() == 1
    assert resolved.is_visible() is True
