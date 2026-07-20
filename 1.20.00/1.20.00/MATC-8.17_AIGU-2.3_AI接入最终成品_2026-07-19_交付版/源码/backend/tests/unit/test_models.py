import pytest
from pydantic import ValidationError

from gui_agent.domain.models import ActionType, Locator, Step


def test_locator_requires_strategy() -> None:
    with pytest.raises(ValidationError):
        Locator()


def test_fill_requires_value() -> None:
    with pytest.raises(ValidationError):
        Step(action=ActionType.FILL, locator=Locator(label="用户名"))


def test_secret_and_plain_value_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        Step(
            action=ActionType.FILL,
            locator=Locator(label="用户名"),
            value="admin",
            value_from_secret="ADMIN_USERNAME",
        )
