"""Capture the frozen FOMC product pages through Chromium CDP."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path
from urllib.request import urlopen

from websocket import create_connection


PAGES = (
    ("下次會議預測", "next_meeting_forecast.png"),
    ("決策重播", "decision_replay.png"),
    ("歷史測試結果", "historical_results.png"),
)


class CdpClient:
    def __init__(self, websocket_url: str) -> None:
        self._ws = create_connection(websocket_url, timeout=30, suppress_origin=True)
        self._next_id = 1

    def close(self) -> None:
        self._ws.close()

    def command(self, method: str, params: dict | None = None) -> dict:
        request_id = self._next_id
        self._next_id += 1
        self._ws.send(
            json.dumps({"id": request_id, "method": method, "params": params or {}})
        )
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    def evaluate(self, expression: str) -> object:
        result = self.command(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return result["result"].get("value")


def chromium_page(debug_port: int, expected_title: str) -> dict:
    with urlopen(f"http://127.0.0.1:{debug_port}/json", timeout=10) as response:
        targets = json.load(response)
    for target in targets:
        if target.get("type") == "page" and target.get("title") == expected_title:
            return target
    pages = [target for target in targets if target.get("type") == "page"]
    if len(pages) == 1:
        return pages[0]
    available = [target.get("title") for target in pages]
    raise RuntimeError(f"Browser page {expected_title!r} not found; available={available}")


def wait_for_text(client: CdpClient, expected: str, timeout_seconds: float = 30) -> str:
    deadline = time.monotonic() + timeout_seconds
    latest = ""
    while time.monotonic() < deadline:
        latest = str(client.evaluate("document.body ? document.body.innerText : ''") or "")
        ready = client.evaluate(
            """
            (() => {
              const title = document.querySelector('[data-testid="stMain"] h1')?.innerText.trim();
              const stale = document.querySelector('[data-stale="true"]');
              const running = document.querySelector('[data-testid="stStatusWidget"]');
              return title === %s && !stale && !running;
            })()
            """
            % json.dumps(expected)
        )
        if ready:
            return latest
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {expected!r}; last body={latest[:500]!r}")


def settled_body_text(client: CdpClient, expected: str) -> str:
    wait_for_text(client, expected)
    time.sleep(1.5)
    return str(client.evaluate("document.body ? document.body.innerText : ''") or "")


def select_page(client: CdpClient, title: str) -> None:
    script = json.dumps(title)
    clicked = client.evaluate(
        """
        (() => {
          const title = %s;
          const sidebar = document.querySelector('[data-testid="stSidebar"]');
          if (!sidebar) return false;
          const textNode = [...sidebar.querySelectorAll('*')]
            .find((node) => node.children.length === 0 && node.textContent.trim() === title);
          if (!textNode) return false;
          const radio = textNode.closest('label, [role="radio"]') || textNode;
          const input = radio.matches('input') ? radio : radio.querySelector('input');
          if (input?.checked || radio.getAttribute('aria-checked') === 'true') return true;
          radio.click();
          return true;
        })()
        """
        % script
    )
    if not clicked:
        options = client.evaluate(
            "document.querySelector('[data-testid=\"stSidebar\"]')?.innerText || ''"
        )
        raise RuntimeError(f"Could not select {title!r}; options={options}")


def select_member(client: CdpClient, display_name: str) -> None:
    opened = client.evaluate(
        """
        (() => {
          const controls = [...document.querySelectorAll('[role="combobox"]')];
          const control = controls.find((candidate) =>
            (candidate.getAttribute('aria-label') || '').includes('選擇有投票權委員')
          ) || (controls.length === 1 ? controls[0] : null);
          if (!control) return false;
          control.click();
          return true;
        })()
        """
    )
    if not opened:
        raise RuntimeError("Could not open the member selectbox")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        clicked = client.evaluate(
            """
            (() => {
              const displayName = %s;
              const option = [...document.querySelectorAll('[role="option"]')]
                .find((node) => node.innerText.trim() === displayName);
              if (!option) return false;
              option.click();
              return true;
            })()
            """
            % json.dumps(display_name)
        )
        if clicked:
            return
        time.sleep(0.25)
    raise RuntimeError(f"Could not select member {display_name!r}")


def wait_for_body_contains(
    client: CdpClient, expected: str, timeout_seconds: float = 30
) -> str:
    deadline = time.monotonic() + timeout_seconds
    latest = ""
    while time.monotonic() < deadline:
        latest = str(client.evaluate("document.body ? document.body.innerText : ''") or "")
        stale = client.evaluate("Boolean(document.querySelector('[data-stale=\"true\"]'))")
        if expected in latest and not stale:
            return latest
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {expected!r}; last body={latest[:500]!r}")


def stable_screenshot_bytes(
    client: CdpClient,
    *,
    max_attempts: int = 12,
    interval_seconds: float = 0.5,
) -> bytes:
    previous: bytes | None = None
    for _ in range(max_attempts):
        result = client.command(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": True, "fromSurface": True},
        )
        current = base64.b64decode(result["data"])
        if current == previous:
            return current
        previous = current
        time.sleep(interval_seconds)
    raise RuntimeError("Screenshot did not stabilize within the capture window")


def capture(client: CdpClient, destination: Path) -> None:
    client.evaluate(
        """
        (() => {
          if (document.activeElement instanceof HTMLElement) {
            document.activeElement.blur();
          }
          let captureStyle = document.getElementById('codex-capture-stability');
          if (!captureStyle) {
            captureStyle = document.createElement('style');
            captureStyle.id = 'codex-capture-stability';
            captureStyle.textContent =
              '*, *::before, *::after { ' +
              'animation: none !important; transition: none !important; ' +
              'caret-color: transparent !important; }';
            document.head.appendChild(captureStyle);
          }
          window.scrollTo(0, 0);
          document
            .querySelectorAll(
              '[data-testid="stMain"], [data-testid="stMain"] *, ' +
              '[data-testid="stSidebar"], [data-testid="stSidebar"] *'
            )
            .forEach((node) => { if (node.scrollTop) node.scrollTop = 0; });
        })()
        """
    )
    time.sleep(0.25)
    destination.write_bytes(stable_screenshot_bytes(client))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, default=9225)
    parser.add_argument("--page-title", default="聯準會決策預測實驗室")
    parser.add_argument("--url")
    parser.add_argument("--filename-prefix", default="")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = chromium_page(args.debug_port, args.page_title)
    client = CdpClient(target["webSocketDebuggerUrl"])
    try:
        client.command("Page.enable")
        client.command("Runtime.enable")
        if args.url:
            client.command("Page.navigate", {"url": args.url})
            wait_for_text(client, "下次會議預測")
        client.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 1100, "deviceScaleFactor": 1, "mobile": False},
        )
        select_page(client, "下次會議預測")
        wait_for_text(client, "下次會議預測")
        wait_for_body_contains(client, "2026/9/15–16")
        results = []
        for title, filename in PAGES:
            select_page(client, title)
            if title == "下次會議預測":
                select_member(client, "Christopher J. Waller")
                wait_for_body_contains(client, "官方公開發言 1")
            body = settled_body_text(client, title)
            output_name = f"{args.filename_prefix}{filename}"
            capture(client, args.output_dir / output_name)
            results.append(
                {
                    "page": title,
                    "screenshot": output_name,
                    "body_text_chars": len(body),
                    "body_text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                }
            )
        serialized = json.dumps(results, ensure_ascii=False, indent=2) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(serialized, encoding="utf-8")
        print(serialized, end="")
    finally:
        client.close()


if __name__ == "__main__":
    main()
