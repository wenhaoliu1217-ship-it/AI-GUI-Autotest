from gui_agent.security.screenshot_privacy import screenshot_privacy_masks


def test_privacy_masks_self_expire_without_synchronous_frame_cleanup() -> None:
    calls: list[tuple[str, str, object, int]] = []

    class FakeFrame:
        class FakeLocator:
            def __init__(self, selector: str) -> None:
                self.selector = selector

            def evaluate(self, script: str, argument: object, *, timeout: int) -> dict:
                calls.append((self.selector, script, argument, timeout))
                return {"maskedCount": 1, "invalidSelectors": []}

        @staticmethod
        def locator(selector: str) -> "FakeFrame.FakeLocator":
            return FakeFrame.FakeLocator(selector)

    class FakePage:
        frames = [FakeFrame()]

    with screenshot_privacy_masks(FakePage(), ()) as evidence:
        assert evidence["masked_count"] == 1

    assert len(calls) == 1
    assert calls[0][0] == "html"
    assert "setTimeout" in calls[0][1]
    assert calls[0][3] == 500
