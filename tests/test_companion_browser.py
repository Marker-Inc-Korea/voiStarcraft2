from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from starcraft_commander.companion_ui import render_companion_page


def _chrome_executable() -> str | None:
    configured = os.environ.get("CHROME_BIN", "").strip()
    playwright_cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    cached = sorted(
        playwright_cache.glob(
            "chromium_headless_shell-*/chrome-mac/headless_shell"
        ),
        reverse=True,
    )
    candidates = [
        configured,
        shutil.which("google-chrome-stable"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        *(str(candidate) for candidate in cached),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


class CompactControllerBrowserTest(unittest.TestCase):
    def test_headless_browser_renders_latest_command_without_legacy_ui(self):
        chrome = _chrome_executable()
        if chrome is None:
            self.skipTest("Chrome/Chromium is not installed")
        prelude = r"""
<script>
window.__voiBrowserErrors = [];
window.addEventListener("error", function(event) {
  window.__voiBrowserErrors.push(String(event.message || event.error || "error"));
  document.documentElement.dataset.browserErrors = "true";
});
window.addEventListener("unhandledrejection", function(event) {
  window.__voiBrowserErrors.push(String(event.reason || "rejection"));
  document.documentElement.dataset.browserErrors = "true";
});
window.setInterval = function() { return 1; };
function response(payload) {
  return Promise.resolve({
    ok: true,
    status: 200,
    text: function() { return Promise.resolve(JSON.stringify(payload)); }
  });
}
window.fetch = function(url) {
  var path = String(url || "").split("?")[0];
  if (path === "/api/runtime/status") {
    return response({
      status: "connected",
      runtime_attached: true,
      telemetry_current_for_process: true,
      telemetry_frame: 9731
    });
  }
  if (path === "/api/micromachine/status") {
    return response({
      status: "published",
      consumption_status: "consumed",
      latest_request: {
        update_id: "latest-scv-command",
        consumption_status: "consumed",
        command_queue: {
          command_text: "SCV를 생산한다"
        }
      },
      operations: [{
        active: true,
        operation_id: "older-standing-operation",
        update_id: "older-standing-update",
        command_text: "오래된 상시 작전"
      }]
    });
  }
  return Promise.reject(new Error("unexpected fetch: " + path));
};
</script>
"""
        page = render_companion_page().replace("<script>", prelude + "<script>", 1)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "companion.html"
            fixture.write_text(page, encoding="utf-8")
            result = subprocess.run(
                [
                    chrome,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--dump-dom",
                    fixture.as_uri(),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SCV를 생산한다", result.stdout)
        self.assertIn("정책 적용", result.stdout)
        self.assertIn("SC2 연결됨 · frame 9731", result.stdout)
        self.assertIn(
            '<div id="operation-goal" class="goal">SCV를 생산한다</div>',
            result.stdout,
        )
        self.assertNotIn(
            '<div id="operation-goal" class="goal">오래된 상시 작전</div>',
            result.stdout,
        )
        self.assertNotIn("커맨더 채팅", result.stdout)
        self.assertNotIn('data-browser-errors="true"', result.stdout)
