import unittest
from unittest.mock import patch

from gui_agent.domain.models import Step
from gui_agent.execution import runner
from gui_agent.planning.visual_adapter import VisualSuggestion
from gui_agent.security.redaction import Redactor


class _Locator:
    @property
    def first(self):
        return self

    def bounding_box(self):
        return {"x": 100, "y": 50, "width": 800, "height": 600}


class _Mouse:
    def __init__(self):
        self.calls = []

    def click(self, x, y):
        self.calls.append(("click", round(x), round(y)))

    def dblclick(self, x, y, delay=0):
        self.calls.append(("dblclick", round(x), round(y), delay))

    def move(self, x, y, **kwargs):
        self.calls.append(("move", round(x), round(y), kwargs))

    def down(self):
        self.calls.append(("down",))

    def up(self):
        self.calls.append(("up",))

    def wheel(self, x, y):
        self.calls.append(("wheel", x, y))


class _Keyboard:
    def press(self, key):
        pass


class _Page:
    viewport_size = {"width": 1200, "height": 800}

    def __init__(self):
        self.mouse = _Mouse()
        self.keyboard = _Keyboard()

    def wait_for_timeout(self, _milliseconds):
        pass


class VisualGestureRegressionTests(unittest.TestCase):
    def test_visual_adapter_accepts_one_complete_multi_point_gesture(self):
        suggestion = VisualSuggestion(
            target="四点折线距离测量",
            action="draw_polygon",
            x_ratio=0.2,
            y_ratio=0.3,
            points=[
                {"x_ratio": 0.2, "y_ratio": 0.3},
                {"x_ratio": 0.4, "y_ratio": 0.25},
                {"x_ratio": 0.6, "y_ratio": 0.45},
                {"x_ratio": 0.72, "y_ratio": 0.62},
            ],
            expected_change="显示距离标注",
            confidence=0.95,
            rationale="主画布和测量路径清晰可见",
        )
        self.assertEqual(len(suggestion.points), 4)

    def test_runner_dispatches_polygon_as_one_bounded_step(self):
        page = _Page()
        step = Step(
            action="visual_draw_polygon",
            execution_mode="visual",
            stability_level="C",
            stability_reason="latest screenshot grounding",
            visual_target="四点折线距离测量",
            canvas_region_locator={"css": "canvas"},
            visual_points=[
                {"xRatio": 0.2, "yRatio": 0.3},
                {"xRatio": 0.4, "yRatio": 0.25},
                {"xRatio": 0.6, "yRatio": 0.45},
                {"xRatio": 0.72, "yRatio": 0.62},
            ],
            gesture_finish="double_click",
        )
        with patch.object(runner, "resolve_locator", return_value=_Locator()):
            detail = runner._execute_step(
                page, step, "https://ion.cesium.com", object(), Redactor()
            )
        self.assertEqual(detail["visualPointCount"], 4)
        self.assertEqual([call[0] for call in page.mouse.calls], [
            "click", "click", "click", "dblclick"
        ])


if __name__ == "__main__":
    unittest.main()
