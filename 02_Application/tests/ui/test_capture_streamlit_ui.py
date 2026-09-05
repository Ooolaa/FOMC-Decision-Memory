import base64
import unittest
from unittest import mock

from scripts.capture_streamlit_ui import settled_body_text, stable_screenshot_bytes


class FakeClient:
    def __init__(self):
        self.body_values = iter(["transient body", "stable body"])

    def evaluate(self, expression):
        if "document.body" in expression:
            return next(self.body_values)
        return True


class FakeScreenshotClient:
    def __init__(self):
        self.frames = iter([b"frame-a", b"frame-b", b"frame-b"])
        self.command_count = 0

    def command(self, method, params):
        self.command_count += 1
        return {"data": base64.b64encode(next(self.frames)).decode("ascii")}


class CaptureStreamlitUiTests(unittest.TestCase):
    def test_body_hash_input_is_read_after_the_settle_window(self):
        client = FakeClient()

        with mock.patch("scripts.capture_streamlit_ui.time.sleep") as sleep:
            body = settled_body_text(client, "歷史測試結果")

        self.assertEqual(body, "stable body")
        sleep.assert_called_once_with(1.5)

    def test_screenshot_waits_for_two_identical_frames(self):
        client = FakeScreenshotClient()

        with mock.patch("scripts.capture_streamlit_ui.time.sleep"):
            screenshot = stable_screenshot_bytes(client)

        self.assertEqual(screenshot, b"frame-b")
        self.assertEqual(client.command_count, 3)


if __name__ == "__main__":
    unittest.main()
