from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from starcraft_commander.web_gui import render_web_gui_page


def _chrome_executable() -> str | None:
    configured = os.environ.get("CHROME_BIN", "").strip()
    playwright_cache = pathlib.Path.home() / "Library" / "Caches" / "ms-playwright"
    cached_headless_shells = sorted(
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
        shutil.which("chromium-browser"),
        *(str(candidate) for candidate in cached_headless_shells),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).is_file():
            return str(candidate)
    return None


def _browser_fixture_page() -> str:
    prelude = r"""
<script>
(function () {
  window.__voiceQa = {
    modulateRequests: 0,
    spoken: [],
    cancelCount: 0
  };

  function response(payload, status) {
    var responseStatus = status || 200;
    var serialized = JSON.stringify(payload);
    return Promise.resolve({
      ok: responseStatus >= 200 && responseStatus < 300,
      status: responseStatus,
      statusText: "",
      json: function () { return Promise.resolve(payload); },
      text: function () { return Promise.resolve(serialized); }
    });
  }

  function operation(operationId, generation, count, task, target, route) {
    return {
      operation_id: operationId,
      operation_generation: generation,
      update: {
        update_id: "browser-voice-plan",
        vector: {
          operation_id: operationId,
          generation: generation,
          composition_requirements: [
            { unit_type: "TERRAN_MARINE", count: count }
          ],
          tactical_task: { task_type: task },
          route_intent: {
            route_type: route,
            target_intent: target
          },
          lifetime: { mode: "until_completed" }
        }
      }
    };
  }

  window.fetch = function (url) {
    var route = String(url || "").split("?")[0];
    if (route === "/api/micromachine/modulate") {
      window.__voiceQa.modulateRequests += 1;
      var operations = [
        operation(
          "browser-recon",
          3,
          2,
          "scout_with_units",
          "enemy_main",
          "direct"
        ),
        operation(
          "browser-assault",
          7,
          4,
          "pressure_with_main_army",
          "enemy_natural",
          "flank_right"
        )
      ];
      window.setTimeout(function () {
        safeRenderMicroMachineStatus({
          ok: true,
          accepted: true,
          status: "published",
          update_id: "browser-voice-plan",
          blackboard_scope_id: "browser-voice-scope",
          compile_result: {
            status: "compiled",
            update_id: "browser-voice-plan",
            blackboard_scope_id: "browser-voice-scope",
            vector: {
              goal: "parallel browser voice operation",
              operations: operations.map(function (item) {
                return item.update.vector;
              })
            }
          },
          operations: operations
        });
      }, 10);
      return response({
        ok: true,
        accepted: true,
        queued: true,
        async_publish: true,
        status: "queued",
        update_id: "browser-voice-plan",
        blackboard_scope_id: "browser-voice-scope",
        consumption_status: "pending_compile"
      }, 202);
    }
    if (route === "/api/llm") {
      return response({
        configured: true,
        provider: "myproxy",
        model: "gpt-5.6-sol",
        reasoning_effort: "low"
      });
    }
    if (route === "/api/history") {
      return response({ events: [], latest: 0 });
    }
    if (route === "/api/state") {
      return response({
        available: true,
        game_time_seconds: 0,
        standing_orders: []
      });
    }
    if (route === "/api/runtime/status") {
      return response({ running: false, status: "idle" });
    }
    if (route === "/api/micromachine/status") {
      return response({
        status: "idle",
        blackboard_dir: "/tmp/browser-voice-blackboard",
        blackboard_scope_id: "browser-voice-scope",
        operations: []
      });
    }
    return response({});
  };

  class FakeEventSource {
    constructor() {
      this.listeners = {};
    }
    addEventListener(name, handler) {
      this.listeners[name] = handler;
    }
    close() {}
  }

  class FakeSpeechRecognition {
    constructor() {
      window.__voiceQa.recognition = this;
      this.interimResults = false;
      this.continuous = false;
      this.lang = "";
    }
    start() {
      if (this.onstart) { this.onstart(); }
    }
    stop() {
      if (this.onend) { this.onend(); }
    }
  }

  class FakeSpeechSynthesisUtterance {
    constructor(text) {
      this.text = text;
      this.lang = "";
      this.onend = null;
      this.onerror = null;
    }
  }

  Object.defineProperty(window, "EventSource", {
    configurable: true,
    value: FakeEventSource
  });
  Object.defineProperty(window, "SpeechRecognition", {
    configurable: true,
    value: FakeSpeechRecognition
  });
  Object.defineProperty(window, "webkitSpeechRecognition", {
    configurable: true,
    value: FakeSpeechRecognition
  });
  Object.defineProperty(window, "SpeechSynthesisUtterance", {
    configurable: true,
    value: FakeSpeechSynthesisUtterance
  });
  Object.defineProperty(window, "speechSynthesis", {
    configurable: true,
    value: {
      speak: function (utterance) {
        window.__voiceQa.spoken.push(utterance.text);
        window.setTimeout(function () {
          if (utterance.onend) { utterance.onend(); }
        }, 0);
      },
      cancel: function () {
        window.__voiceQa.cancelCount += 1;
      }
    }
  });
  window.setInterval = function () { return 0; };
  window.clearInterval = function () {};
})();
</script>
"""
    scenario = r"""
<script>
(function () {
  function result(text, final) {
    var item = [{ transcript: text }];
    item.isFinal = final;
    return item;
  }

  function mark(name, value) {
    document.documentElement.setAttribute(
      "data-qa-" + name,
      value ? "true" : "false"
    );
  }

  window.setTimeout(function () {
    var recognition = window.__voiceQa.recognition;
    document.getElementById("voice-button").click();
    var session = activeVoiceSession;
    var stableNode = session.node;
    recognition.onresult({
      resultIndex: 0,
      results: [result("마린 두 기로", false)]
    });
    var interimSharedNode = activeVoiceSession.node === stableNode &&
      stableNode.textContent.indexOf("마린 두 기로") !== -1;
    recognition.onend();
    recognition.onresult({
      resultIndex: 0,
      results: [
        result(
          "마린 두 기로 정찰하고 네 기로 우회 공격해",
          true
        )
      ]
    });

    window.setTimeout(function () {
      var captions = document.getElementById(
        "tactical-radio-captions"
      ).textContent;
      var spokenBeforeMute = window.__voiceQa.spoken.length;
      document.getElementById("tactical-radio-mute").click();
      queueTacticalRadioCallout({
        priority: 1,
        caption: "muted browser caption",
        speech: "must not play",
        dedupeKey: "muted-browser-caption",
        createdAt: Date.now()
      });
      var mutedCaptions = document.getElementById(
        "tactical-radio-captions"
      ).textContent;
      applyLanguage("en");
      var localizedAccessibility =
        document.getElementById("tactical-radio-captions")
          .getAttribute("aria-label") === "Tactical radio captions" &&
        document.getElementById("voice-button")
          .getAttribute("aria-label") === "Voice input" &&
        document.getElementById("voice-button")
          .getAttribute("title") === "Voice input";
      var radio = document.getElementById("tactical-radio");
      var commandPanel = document.getElementById("command-panel");
      var radioRect = radio.getBoundingClientRect();
      var panelRect = commandPanel.getBoundingClientRect();
      var noHorizontalOverflow =
        document.documentElement.scrollWidth <= window.innerWidth + 1;
      var radioInsidePanel =
        radioRect.left >= panelRect.left - 1 &&
        radioRect.right <= panelRect.right + 1;

      mark("complete", true);
      mark("single-node", interimSharedNode &&
        stableNode === session.node &&
        stableNode.parentNode === document.getElementById("log"));
      mark("exactly-once", window.__voiceQa.modulateRequests === 1);
      mark("plan-identity",
        captions.indexOf("browser-recon#3") !== -1 &&
        captions.indexOf("browser-assault#7") !== -1);
      mark("truthful-plan",
        captions.indexOf("계획 확인") !== -1 &&
        captions.indexOf("이동 시작") === -1);
      mark("speech-bounded",
        window.__voiceQa.spoken.length > 0 &&
        window.__voiceQa.spoken.every(function (text) {
          return text.length <= 180;
        }));
      mark("mute-caption",
        mutedCaptions.indexOf("muted browser caption") !== -1 &&
        window.__voiceQa.spoken.length === spokenBeforeMute);
      mark("accessibility",
        document.getElementById("tactical-radio-status")
          .getAttribute("role") === "status" &&
        document.getElementById("tactical-radio-captions")
          .getAttribute("role") === "log" &&
        document.getElementById("tactical-radio-mute")
          .getAttribute("aria-pressed") === "true" &&
        localizedAccessibility);
      mark("original-ux",
        document.querySelectorAll("[data-operation-lane]").length === 4 &&
        Boolean(document.getElementById("command-form")) &&
        Boolean(document.getElementById("voice-button")) &&
        radio.parentNode === commandPanel);
      mark("layout", noHorizontalOverflow && radioInsidePanel);
    }, 120);
  }, 40);
})();
</script>
"""
    page = render_web_gui_page(
        micromachine_blackboard_dir="/tmp/browser-voice-blackboard"
    )
    page = page.replace("<script>", prelude + "\n<script>", 1)
    return page.replace("</body>", scenario + "\n</body>", 1)


class WebGuiRealBrowserTest(unittest.TestCase):
    def test_voice_tactical_loop_in_real_chrome_desktop_and_mobile(self) -> None:
        chrome = _chrome_executable()
        if chrome is None:
            self.skipTest("Chrome/Chromium is not installed")

        page = _browser_fixture_page()
        expected_markers = (
            "complete",
            "single-node",
            "exactly-once",
            "plan-identity",
            "truthful-plan",
            "speech-bounded",
            "mute-caption",
            "accessibility",
            "original-ux",
            "layout",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = pathlib.Path(temporary_directory)
            page_path = temporary_root / "voice-tactical-loop.html"
            page_path.write_text(page, encoding="utf-8")
            for width, height in ((1440, 1100), (390, 844)):
                with self.subTest(viewport=f"{width}x{height}"):
                    profile = temporary_root / f"profile-{width}"
                    command = [
                        chrome,
                        "--headless=new",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--no-default-browser-check",
                        f"--user-data-dir={profile}",
                        f"--window-size={width},{height}",
                        "--virtual-time-budget=1500",
                        "--dump-dom",
                        page_path.resolve().as_uri(),
                    ]
                    result = subprocess.run(
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if (
                        sys.platform == "darwin"
                        and result.returncode != 0
                        and not result.stdout
                        and (
                            "sandbox_parameters_mac.mm" in result.stderr
                            or result.returncode in (-6, -5, 134)
                        )
                    ):
                        self.skipTest(
                            "The local macOS sandbox blocks headless Chrome"
                        )
                    self.assertEqual(
                        result.returncode,
                        0,
                        textwrap.shorten(
                            result.stderr or result.stdout,
                            width=2000,
                            placeholder="...",
                        ),
                    )
                    for marker in expected_markers:
                        self.assertIn(
                            f'data-qa-{marker}="true"',
                            result.stdout,
                            marker,
                        )


if __name__ == "__main__":
    unittest.main()
