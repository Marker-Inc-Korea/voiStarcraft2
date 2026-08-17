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

    def test_native_submit_starts_runtime_then_preserves_exact_command(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        page = render_companion_page()
        script_start = page.index('  "use strict";')
        script_end = page.index(
            '  document.getElementById("runtime-start")',
            script_start,
        )
        command_script = page[script_start:script_end]
        harness = r"""
const assert = require("assert");

class FakeElement {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.textContent = "";
    this.value = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
  }
  appendChild(child) {
    this.children.push(child);
    this.scrollHeight = this.children.length;
    return child;
  }
  removeChild(child) {
    this.children.splice(this.children.indexOf(child), 1);
  }
  get firstChild() {
    return this.children[0] || null;
  }
}

const nodes = {
  "caption-list": new FakeElement(),
  "command-feedback": new FakeElement(),
  "command-input": new FakeElement(),
  "operation-composition": new FakeElement(),
  "operation-goal": new FakeElement(),
  "operation-stage": new FakeElement(),
  "runtime-status": new FakeElement()
};
global.document = {
  createElement: function() { return new FakeElement(); },
  getElementById: function(id) { return nodes[id]; }
};
const requestOrder = [];
global.window = {
  location: { search: "" },
  setTimeout: function() { return 1; },
  webkit: {
    messageHandlers: {
      sc2Launch: {
        postMessage: function(payload) {
          requestOrder.push("native");
          window.voiNativeSC2LaunchResolved({
            nonce: payload.nonce,
            accepted: true
          });
        }
      }
    }
  }
};
function response(payload) {
  return Promise.resolve({
    ok: true,
    status: 200,
    text: function() { return Promise.resolve(JSON.stringify(payload)); }
  });
}
global.fetch = function(url, options) {
  const path = String(url || "").split("?")[0];
  if (path === "/api/runtime/start") {
    requestOrder.push("runtime");
    return response({ status: "starting", runtime_attached: true });
  }
  if (path === "/api/micromachine/modulate") {
    requestOrder.push("command");
    global.commandRequest = JSON.parse(options.body);
    return response({
      status: "queued",
      async_publish: true,
      consumption_status: "pending_compile",
      update_id: global.commandRequest.update_id
    });
  }
  return Promise.reject(new Error("unexpected fetch: " + path));
};
"""
        scenario = r"""
(async function() {
  renderRuntime({
    status: "idle",
    runtime_attached: false,
    telemetry_current_for_process: false,
    telemetry_stale_or_detached: true
  });
  await submitCommandWithRuntime("SCV를 생산한다");
  assert.deepStrictEqual(requestOrder, ["native", "runtime", "command"]);
  assert.strictEqual(global.commandRequest.text, "SCV를 생산한다");
  assert.strictEqual(nodes["operation-goal"].textContent, "SCV를 생산한다");
  assert.strictEqual(nodes["operation-stage"].textContent, "명령 해석 중");

  const detached = selectCommand({
    status: "published",
    consumption_status: "detached_telemetry",
    runtime_attached: false,
    telemetry_current_for_process: false,
    telemetry_stale_or_detached: true,
    latest_request: {
      update_id: global.commandRequest.update_id,
      command_text: "SCV를 생산한다",
      consumption_status: "detached_telemetry"
    },
    operations: []
  });
  assert.strictEqual(
    operationStage(detached, {
      runtime_attached: false,
      telemetry_current_for_process: false,
      telemetry_stale_or_detached: true
    }),
    "SC2 실행 대기"
  );
})().catch(function(error) {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(command_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
