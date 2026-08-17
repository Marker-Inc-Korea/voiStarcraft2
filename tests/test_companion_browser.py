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
        page_html = render_companion_page().replace(
            "<script>",
            prelude + "<script>",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "companion.html"
            browser_profile = Path(directory) / "chrome-profile"
            fixture.write_text(page_html, encoding="utf-8")
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                process = subprocess.Popen(
                    [
                        chrome,
                        "--headless=new",
                        "--disable-gpu",
                        "--no-sandbox",
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-default-apps",
                        "--disable-sync",
                        "--no-first-run",
                        "--no-default-browser-check",
                        f"--user-data-dir={browser_profile}",
                        "--virtual-time-budget=1000",
                        "--dump-dom",
                        fixture.as_uri(),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    stdout, stderr = process.communicate(timeout=15)
                    returncode = process.returncode
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                    returncode = 0 if "</html>" in stdout else process.returncode
            else:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=chrome,
                        headless=True,
                    )
                    try:
                        browser_page = browser.new_page()
                        browser_page.set_content(
                            page_html,
                            wait_until="domcontentloaded",
                        )
                        browser_page.wait_for_function(
                            """() => (
                              document.getElementById("operation-goal")
                                ?.textContent === "SCV를 생산한다"
                            )""",
                            timeout=10_000,
                        )
                        stdout = browser_page.content()
                        stderr = ""
                        returncode = 0
                    finally:
                        browser.close()

        self.assertEqual(returncode, 0, stderr)
        self.assertIn("</html>", stdout)
        self.assertIn("SCV를 생산한다", stdout)
        self.assertIn("정책 적용", stdout)
        self.assertIn("SC2 연결됨 · frame 9731", stdout)
        self.assertIn(
            '<div id="operation-goal" class="goal">SCV를 생산한다</div>',
            stdout,
        )
        self.assertNotIn(
            '<div id="operation-goal" class="goal">오래된 상시 작전</div>',
            stdout,
        )
        self.assertNotIn("커맨더 채팅", stdout)
        self.assertNotIn('data-browser-errors="true"', stdout)

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
  setTimeout: function(callback) {
    setImmediate(callback);
    return 1;
  },
  clearTimeout: function() {},
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
    return response({
      status: "starting",
      runtime_attached: true,
      telemetry_current_for_process: false
    });
  }
  if (path === "/api/runtime/status") {
    requestOrder.push("runtime-status");
    return response({
      status: "connected",
      runtime_attached: true,
      telemetry_current_for_process: true
    });
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
  assert.deepStrictEqual(
    requestOrder,
    ["native", "runtime", "runtime-status", "runtime-status", "command"]
  );
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

    def test_native_launch_timeout_and_callback_cleanup_pending_requests(self):
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

global.document = {
  createElement: function() { return {}; },
  getElementById: function() {
    return {
      appendChild: function() {},
      removeChild: function() {},
      dataset: {},
      style: {},
      textContent: "",
      value: ""
    };
  }
};
let nextTimerId = 40;
const timers = {};
const clearedTimers = [];
let postedNonce = "";
global.window = {
  location: { search: "" },
  setTimeout: function(callback, delay) {
    nextTimerId += 1;
    timers[nextTimerId] = { callback: callback, delay: delay };
    return nextTimerId;
  },
  clearTimeout: function(timerId) {
    clearedTimers.push(timerId);
    delete timers[timerId];
  },
  webkit: {
    messageHandlers: {
      sc2Launch: {
        postMessage: function(payload) {
          postedNonce = payload.nonce;
        }
      }
    }
  }
};
"""
        scenario = r"""
(async function() {
  const timedOut = requestNativeSC2Launch();
  const timeoutId = Number(Object.keys(timers)[0]);
  assert.ok(Number.isFinite(timers[timeoutId].delay));
  assert.ok(timers[timeoutId].delay > 180000);
  assert.strictEqual(timers[timeoutId].delay, NATIVE_SC2_LAUNCH_TIMEOUT_MS);
  assert.ok(pendingNativeSC2Launches[postedNonce]);
  timers[timeoutId].callback();
  await assert.rejects(timedOut, /timed out after 185 seconds/);
  assert.strictEqual(pendingNativeSC2Launches[postedNonce], undefined);

  const resolved = requestNativeSC2Launch();
  const resolvedNonce = postedNonce;
  const resolvedTimerId = Math.max.apply(
    null,
    Object.keys(timers).map(Number)
  );
  window.voiNativeSC2LaunchResolved({
    nonce: resolvedNonce,
    accepted: true
  });
  assert.strictEqual(await resolved, resolvedNonce);
  assert.strictEqual(pendingNativeSC2Launches[resolvedNonce], undefined);
  assert.ok(clearedTimers.indexOf(resolvedTimerId) !== -1);
  assert.strictEqual(timers[resolvedTimerId], undefined);
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

    def test_runtime_start_rejection_restores_command_without_publishing(self):
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
  }
  appendChild(child) {
    this.children.push(child);
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
let resolveRuntimeStart = null;
global.window = {
  location: { search: "" },
  setTimeout: function() { return 1; },
  clearTimeout: function() {},
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
    return new Promise(function(resolve) {
      resolveRuntimeStart = function() {
        resolve({
          ok: true,
          status: 200,
          text: function() {
            return Promise.resolve(JSON.stringify({
              accepted: false,
              status: "failed",
              error: "runtime rejected"
            }));
          }
        });
      };
    });
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
  nodes["command-input"].value = "  SCV를 생산한다  ";
  const submitPromise = submitCommandWithRuntime(nodes["command-input"].value);
  await Promise.resolve();
  assert.deepStrictEqual(requestOrder, ["native", "runtime"]);
  assert.strictEqual(global.commandRequest, undefined);
  assert.strictEqual(typeof resolveRuntimeStart, "function");
  resolveRuntimeStart();
  await submitPromise;
  assert.strictEqual(nodes["command-input"].value, "  SCV를 생산한다  ");
  assert.ok(
    nodes["caption-list"].children.some(function(node) {
      return node.textContent.indexOf("명령을 전송하지 않았습니다") !== -1;
    })
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

    def test_browser_start_button_explains_native_app_boundary(self):
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
  }
  appendChild(child) {
    this.children.push(child);
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
global.window = {
  location: { search: "" },
  setTimeout: function() { return 1; }
};
global.fetch = function() {
  throw new Error("browser start must not call runtime APIs");
};
"""
        scenario = r"""
(async function() {
  const result = await startRuntime();
  assert.strictEqual(result, null);
  assert.ok(
    nodes["command-feedback"].textContent.indexOf(
      "설치된 voiStarcraft2 앱에서만"
    ) !== -1
  );
  assert.strictEqual(nodes["command-feedback"].style.color, "var(--danger)");
  assert.ok(
    nodes["caption-list"].children.some(function(node) {
      return node.textContent.indexOf("명령 대기열과 실행 상태") !== -1;
    })
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

    def test_failed_command_restores_exact_input_without_overwriting_new_draft(self):
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
  }
  appendChild(child) {
    this.children.push(child);
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
global.window = {
  location: { search: "" },
  setTimeout: function() { return 1; }
};
let rejectRequest = null;
global.fetch = function() {
  return new Promise(function(resolve, reject) {
    rejectRequest = reject;
  });
};
"""
        scenario = r"""
(async function() {
  renderRuntime({
    status: "connected",
    runtime_attached: true,
    telemetry_current_for_process: true
  });

  const exactInput = "  SCV를 생산한다  \n";
  nodes["command-input"].value = exactInput;
  const firstRequest = submitCommandWithRuntime(nodes["command-input"].value);
  await new Promise(function(resolve) { setImmediate(resolve); });
  assert.strictEqual(nodes["command-input"].value, "");
  rejectRequest(new Error("network failure"));
  await firstRequest;
  assert.strictEqual(nodes["command-input"].value, exactInput);

  nodes["command-input"].value = "첫 번째 명령";
  const secondRequest = submitCommandWithRuntime(nodes["command-input"].value);
  await new Promise(function(resolve) { setImmediate(resolve); });
  nodes["command-input"].value = "새로 입력한 초안";
  rejectRequest(new Error("network failure"));
  await secondRequest;
  assert.strictEqual(nodes["command-input"].value, "새로 입력한 초안");
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

    def test_late_poll_responses_cannot_overwrite_newer_runtime_or_command(self):
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
  }
  appendChild(child) {
    this.children.push(child);
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
global.window = {
  location: { search: "" },
  setTimeout: function() { return 1; }
};
const pendingRuntime = [];
const pendingOperation = [];
global.fetch = function(url) {
  const path = String(url || "").split("?")[0];
  return new Promise(function(resolve) {
    const target = path === "/api/runtime/status"
      ? pendingRuntime
      : pendingOperation;
    target.push(function(payload) {
      resolve({
        ok: true,
        status: 200,
        text: function() {
          return Promise.resolve(JSON.stringify(payload));
        }
      });
    });
  });
};
"""
        scenario = r"""
(async function() {
  const runtimeFirst = refreshRuntime();
  const runtimeSecond = refreshRuntime();
  pendingRuntime[1]({
    status: "connected",
    runtime_attached: true,
    telemetry_current_for_process: true,
    telemetry_frame: 500
  });
  await runtimeSecond;
  pendingRuntime[0]({
    status: "idle",
    runtime_attached: false,
    telemetry_current_for_process: false
  });
  await runtimeFirst;
  assert.strictEqual(nodes["runtime-status"].textContent, "SC2 연결됨 · frame 500");

  const operationFirst = refreshOperation();
  const operationSecond = refreshOperation();
  pendingOperation[1]({
    status: "published",
    consumption_status: "consumed",
    latest_request: {
      update_id: "new-command",
      command_text: "SCV를 생산한다",
      consumption_status: "consumed"
    },
    operations: []
  });
  await operationSecond;
  pendingOperation[0]({
    status: "published",
    consumption_status: "consumed",
    latest_request: {
      update_id: "old-command",
      command_text: "오래된 명령",
      consumption_status: "consumed"
    },
    operations: []
  });
  await operationFirst;
  assert.strictEqual(nodes["operation-goal"].textContent, "SCV를 생산한다");
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

    def test_voice_final_segments_preserve_spaces_and_submit_once_per_session(self):
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
    this.listeners = {};
    this.classList = {
      add: function() {},
      remove: function() {}
    };
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    this.children.splice(this.children.indexOf(child), 1);
  }
  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }
  setAttribute() {}
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
  "runtime-status": new FakeElement(),
  "voice-button": new FakeElement()
};
global.document = {
  createElement: function() { return new FakeElement(); },
  getElementById: function(id) { return nodes[id]; }
};
global.window = {
  location: { search: "" },
  setTimeout: function() { return 1; },
  SpeechRecognition: function() {
    this.start = function() {
      if (this.onstart) { this.onstart(); }
    };
    this.stop = function() {
      if (this.onend) { this.onend(); }
    };
    global.voiceRecognitions.push(this);
  }
};
global.voiceRecognitions = [];
"""
        scenario = r"""
(async function() {
  const submitted = [];
  submitCommandWithRuntime = function(text) {
    submitted.push(text);
    return Promise.resolve();
  };
  setupVoice();
  nodes["voice-button"].listeners.click();
  const firstRecognition = voiceRecognitions[0];
  const event = {
    resultIndex: 0,
    results: [
      { 0: { transcript: "마린을 " }, isFinal: true },
      { 0: { transcript: " 생산해" }, isFinal: true }
    ]
  };
  firstRecognition.onresult(event);
  firstRecognition.onresult(event);
  assert.strictEqual(nodes["command-input"].value, "마린을 생산해");
  firstRecognition.onend();
  firstRecognition.onend();
  await Promise.resolve();

  nodes["voice-button"].listeners.click();
  const secondRecognition = voiceRecognitions[1];
  const secondEvent = {
    resultIndex: 0,
    results: [
      { 0: { transcript: "SCV를 " }, isFinal: true },
      { 0: { transcript: " 생산한다" }, isFinal: true }
    ]
  };
  firstRecognition.onresult({
    resultIndex: 0,
    results: [
      { 0: { transcript: "이전 세션" }, isFinal: true }
    ]
  });
  firstRecognition.onend();
  secondRecognition.onresult(secondEvent);
  secondRecognition.onresult(secondEvent);
  secondRecognition.onend();
  secondRecognition.onend();
  await Promise.resolve();
  assert.strictEqual(nodes["command-input"].value, "SCV를 생산한다");
  assert.deepStrictEqual(submitted, ["마린을 생산해", "SCV를 생산한다"]);
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
