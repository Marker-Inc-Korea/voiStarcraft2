from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
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
          battlefield_projection_identity: {
            session_epoch: "browser-voice-epoch"
          },
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

  function isRendered(node) {
    if (!node) { return false; }
    var style = window.getComputedStyle(node);
    var rect = node.getBoundingClientRect();
    return style.display !== "none" &&
      style.visibility !== "hidden" &&
      rect.width > 0 &&
      rect.height > 0;
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
      var cards = Array.from(
        document.querySelectorAll(".operation-card")
      );
      var expectedStages = ["해석", "배정", "제출", "관측"];
      var expectedActions = [
        "view",
        "revise",
        "reinforce",
        "retarget",
        "cancel"
      ];
      var expectedActionLabels = [
        "대표 보기",
        "수정",
        "증원",
        "목표 변경",
        "작전 취소"
      ];
      var fourStages = cards.length === 2 && cards.every(function (card) {
        var stageLine = card.querySelector(".operation-stage-line");
        var stages = Array.from(
          card.querySelectorAll(".operation-stage")
        );
        return stageLine &&
          stageLine.getAttribute("role") === "list" &&
          stages.length === 4 &&
          stages.every(function (stage, index) {
            return stage.textContent === expectedStages[index] &&
              stage.getAttribute("role") === "listitem" &&
              ["step", "false"].indexOf(
                stage.getAttribute("aria-current")
              ) !== -1;
          });
      });
      var fiveActions = cards.length === 2 && cards.every(function (card) {
        var actions = Array.from(
          card.querySelectorAll("[data-operation-action]")
        );
        return actions.length === 5 &&
          actions.every(function (action, index) {
            return action.getAttribute("data-operation-action") ===
                expectedActions[index] &&
              action.textContent === expectedActionLabels[index];
          });
      });
      var ids = {};
      var uniqueIds = Array.from(
        document.querySelectorAll("[id]")
      ).every(function (node) {
        var id = String(node.id || "");
        if (!id || ids[id]) { return false; }
        ids[id] = true;
        return true;
      });
      var cardAccessibility =
        cards.length === 2 &&
        cards.every(function (card) {
          var labelledBy = card.getAttribute("aria-labelledby");
          var stages = Array.from(
            card.querySelectorAll(".operation-stage")
          );
          var actions = Array.from(
            card.querySelectorAll("[data-operation-action]")
          );
          return card.getAttribute("role") === "listitem" &&
            Boolean(labelledBy) &&
            Boolean(document.getElementById(labelledBy)) &&
            isRendered(card) &&
            stages.every(isRendered) &&
            actions.every(function (action) {
              return action.tagName === "BUTTON" &&
                Boolean(action.getAttribute("aria-label")) &&
                isRendered(action);
            });
        });
      var focusTarget = cards[0] && cards[0].querySelector(
        '[data-operation-action="retarget"]'
      );
      var focusContinuity = false;
      if (focusTarget) {
        var focusKey = focusTarget.getAttribute("data-operation-key");
        var focusRecord = operationRecords[focusKey];
        var cardBefore = focusRecord && focusRecord.node;
        var fingerprintBefore = cardBefore && cardBefore.getAttribute(
          "data-operation-card-fingerprint"
        );
        focusTarget.focus();
        if (focusRecord) {
          focusRecord.telemetryFrame =
            Number(focusRecord.telemetryFrame || 0) + 1;
          focusRecord.data = Object.assign({}, focusRecord.data, {
            browser_focus_revision:
              Number(focusRecord.data.browser_focus_revision || 0) + 1
          });
          renderOperationRecords();
          var focusedAfter = document.activeElement;
          var cardAfter = focusRecord.node;
          focusContinuity =
            cardAfter === cardBefore &&
            cardAfter.getAttribute(
              "data-operation-card-fingerprint"
            ) !== fingerprintBefore &&
            focusedAfter !== focusTarget &&
            focusedAfter.getAttribute("data-operation-key") === focusKey &&
            focusedAfter.getAttribute("data-operation-action") ===
              "retarget" &&
            operationNodeContains(cardAfter, focusedAfter);
        }
      }

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
      mark("four-stages", fourStages);
      mark("five-actions", fiveActions);
      mark("unique-ids", uniqueIds);
      mark("card-accessibility", cardAccessibility);
      mark("focus-continuity", focusContinuity);
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


def _contextual_transfer_browser_fixture_page() -> str:
    prelude = r"""
<script>
(function () {
  window.__transferQa = {
    contextualRequests: [],
    modulationRequests: 0
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

  window.fetch = function (url, options) {
    var route = String(url || "").split("?")[0];
    if (route === "/api/micromachine/contextual-transfer") {
      window.__transferQa.contextualRequests.push({
        url: String(url || ""),
        options: options || {}
      });
      var body = JSON.parse(String(options && options.body || "{}"));
      return response({
        ok: true,
        accepted: true,
        status: "published",
        result_id: "browser-transfer-result",
        contextual_transfer: {
          choice_id: body.choice_id,
          request_id: body.request_id,
          action: body.action,
          source_operation_id: body.source_operation_id,
          destination_operation_id: body.destination_operation_id,
          requested_count: body.requested_count,
          stage: "published"
        }
      }, 202);
    }
    if (route === "/api/micromachine/modulate") {
      window.__transferQa.modulationRequests += 1;
      return response({ error: "natural-language path must not run" }, 500);
    }
    if (route === "/api/llm") {
      return response({ configured: false, provider: "openai", model: "" });
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
        blackboard_dir: "/tmp/browser-transfer-blackboard",
        blackboard_scope_id: "browser-transfer-scope",
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

  Object.defineProperty(window, "EventSource", {
    configurable: true,
    value: FakeEventSource
  });
  window.setInterval = function () { return 0; };
  window.clearInterval = function () {};
})();
</script>
"""
    scenario = r"""
<script>
(function () {
  function mark(name, value) {
    document.documentElement.setAttribute(
      "data-qa-" + name,
      value ? "true" : "false"
    );
  }

  function projection(operationId, generation, ownerCount, minimum) {
    return {
      identity: {
        update_id: "browser-transfer-update",
        scope: "operation:" + operationId,
        session_epoch: 1700000000000,
        operation_id: operationId,
        generation: generation,
        stage: "assigned",
        game_frame: 140
      },
      operation_id: operationId,
      generation: generation,
      operation_route: {
        requested_route_type: "direct",
        applied_route_type: "direct",
        location_intent: "enemy_natural",
        target_type: "enemy_expansion",
        resolved_target_label: "enemy natural",
        target_x: 120,
        target_y: 44,
        target_evidence: "observed_enemy_structure"
      },
      operation_lifetime: {
        mode: "until_completed",
        completion_state: "active",
        completion_conditions: ["target_reached"],
        duration_seconds: 300,
        issued_at_frame: 100,
        deadline_frame: 4600,
        standing: false,
        completed: false,
        completion_reason: "",
        completed_frame: 0
      },
      operation_ownership: {
        owner_count: ownerCount,
        integrity_status: "valid"
      },
      operation_launch_policy: {
        min_units: minimum,
        max_units: ownerCount,
        allow_partial_requested: false,
        strict_scope: true,
        partial_launch_allowed: false,
        partial_launch_safe: false,
        launch_count: ownerCount,
        missing_count: 0,
        decision: "launch",
        blocker: "",
        recommended_choices: [],
        safety_evidence: {}
      },
      operation_completion: {
        movement_observed: false,
        engagement_observed: false,
        target_reached: false,
        terminal: false,
        state: "active",
        reason: "",
        frame: 0,
        generation: generation
      }
    };
  }

  function transferEntry(destinationId, destinationGeneration) {
    return {
      source_owner_id: "source-alpha",
      source_owner_count: 4,
      protected_minimum: 2,
      transferable_count: 2,
      transfer_safe: true,
      atomic_runtime_blocker: "",
      recommended_resolution_choices: ["transfer_two_units"],
      safety_evidence: {
        protected_minimum_respected: true,
        atomic_revalidation_required: true
      },
      atomic_revalidation_inputs: {
        source_owner_id: "source-alpha",
        counterpart_operation_id: destinationId,
        requested_source_generation: 5,
        requested_counterpart_generation: destinationGeneration,
        source_active: true,
        destination_active: true,
        ownership_integrity: true,
        operation_assignments_match: true,
        squad_assignments_match: true,
        action_assignments_match: true,
        role_assignments_match: true,
        atomic_revalidation_ready: true
      }
    };
  }

  window.setTimeout(function () {
    var sourceProjection = projection("source-alpha", 5, 4, 2);
    var destinationBravo = projection("destination-bravo", 3, 4, 1);
    var destinationCharlie = projection("destination-charlie", 7, 3, 1);
    var sourceOperation = {
      operation_id: "source-alpha",
      operation_generation: 5,
      update_id: "browser-transfer-update",
      command_text: "병력 이관 source",
      mission: "attack",
      transport_status: "published",
      consumption_status: "consumed",
      telemetry_frame: 140,
      disposition: "active",
      operation_convergence: {
        target_count: 4,
        represented_count: 4,
        missing_count: 0,
        blocker: "",
        requirements: []
      },
      battlefield_operation: sourceProjection,
      semantic_timeline: [],
      update: {
        update_id: "browser-transfer-update",
        vector: {
          goal: "병력 이관 source",
          operation_id: "source-alpha"
        }
      },
      intervention: {
        telemetry_frame: 140,
        command_execution: {
          command_id: "browser-transfer-update",
          operation_id: "source-alpha",
          operation_generation: 5,
          state: "queued_or_assigned",
          completed: false,
          failed: false,
          expired: false,
          stages: [
            { name: "parsed", ok: true, manager: "CommandGateway" },
            { name: "reduced", ok: true, manager: "PolicyReducer" },
            {
              name: "consumed_by_manager",
              ok: true,
              manager: "CombatCommander"
            },
            {
              name: "queued_or_assigned",
              ok: true,
              manager: "CombatCommander",
              evidence: { assigned_unit_count: 4 }
            }
          ]
        }
      }
    };
    renderOperationConsole({
      status: "published",
      blackboard_scope_id: "browser-transfer-scope",
      battlefield_projection_identity: {
        session_epoch: 1700000000000,
        game_frame: 140
      },
      battlefield_projection_fingerprint: "c".repeat(64),
      battlefield_overview: {
        schema_version: 2,
        authority: "micromachine_cpp",
        identity: {
          session_epoch: 1700000000000,
          game_frame: 140
        },
        operation_ownership: [
          sourceProjection,
          destinationBravo,
          destinationCharlie
        ],
        transfer_availability: {
          atomic_revalidation_required: true,
          entries: [
            transferEntry("destination-bravo", 3),
            transferEntry("destination-charlie", 7)
          ]
        }
      },
      operations: [sourceOperation]
    });

    var card = document.querySelector(".operation-card");
    var transferButtons = Array.from(
      card.querySelectorAll("[data-contextual-choice-id]")
    );
    var destinationButton = transferButtons.find(function (button) {
      return button.textContent.indexOf("destination-charlie") !== -1;
    });
    var choiceId = destinationButton &&
      destinationButton.getAttribute("data-contextual-choice-id");
    var opaqueDom = Boolean(
      destinationButton &&
      choiceId &&
      choiceId.indexOf("voi-ctx-choice-") === 0 &&
      !destinationButton.hasAttribute("data-source-operation-id") &&
      !destinationButton.hasAttribute("data-destination-operation-id") &&
      !destinationButton.hasAttribute("data-source-generation") &&
      !destinationButton.hasAttribute("data-destination-generation") &&
      !destinationButton.hasAttribute("data-unit-tags")
    );
    if (destinationButton) {
      destinationButton.click();
      destinationButton.click();
    }

    window.setTimeout(function () {
      var requests = window.__transferQa.contextualRequests;
      var typedRequest = requests[0] || {};
      var body = JSON.parse(String(
        typedRequest.options && typedRequest.options.body || "{}"
      ));
      var forbiddenFields = [
        "text",
        "provider_output",
        "unit_tag",
        "unit_tags",
        "selected_unit_tags",
        "frame_script",
        "keyboard"
      ];
      mark("complete", true);
      mark("exactly-once", requests.length === 1);
      mark("typed-endpoint",
        String(typedRequest.url || "") ===
          "/api/micromachine/contextual-transfer");
      mark("destination",
        body.source_operation_id === "source-alpha" &&
        body.destination_operation_id === "destination-charlie");
      mark("identity",
        body.choice_id === choiceId &&
        String(body.request_id || "").indexOf("voi-ctx-request-") === 0 &&
        body.source_generation === 5 &&
        body.destination_generation === 7 &&
        body.requested_count === 2 &&
        body.protected_minimum === 2 &&
        body.source_minimum === 2 &&
        body.blackboard_scope_id === "browser-transfer-scope" &&
        body.session_epoch === 1700000000000 &&
        body.projection_frame === 140 &&
        body.projection_fingerprint === "c".repeat(64));
      mark("allowlist", forbiddenFields.every(function (field) {
        return body[field] === undefined;
      }));
      mark("opaque-dom", opaqueDom);
      mark("llm-bypassed", window.__transferQa.modulationRequests === 0);
      mark("original-ux",
        document.querySelectorAll("[data-operation-lane]").length === 4 &&
        card.querySelectorAll(".operation-stage").length === 4 &&
        card.querySelectorAll("[data-operation-action]").length === 5);
    }, 80);
  }, 80);
})();
</script>
"""
    page = render_web_gui_page(
        micromachine_blackboard_dir="/tmp/browser-transfer-blackboard"
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
            "four-stages",
            "five-actions",
            "unique-ids",
            "card-accessibility",
            "focus-continuity",
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

    def test_contextual_transfer_click_in_real_chrome_on_localhost(self) -> None:
        chrome = _chrome_executable()
        if chrome is None:
            self.skipTest("Chrome/Chromium is not installed")

        page = _contextual_transfer_browser_fixture_page()
        expected_markers = (
            "complete",
            "exactly-once",
            "typed-endpoint",
            "destination",
            "identity",
            "allowlist",
            "opaque-dom",
            "llm-bypassed",
            "original-ux",
        )

        class FixtureHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path not in {"/", "/index.html"}:
                    self.send_error(404)
                    return
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(
            target=server.serve_forever,
            name="contextual-transfer-browser-fixture",
            daemon=True,
        )
        thread.start()

        def stop_fixture_server() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop_fixture_server)
        with tempfile.TemporaryDirectory() as temporary_directory:
            profile = pathlib.Path(temporary_directory) / "profile"
            command = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--window-size=1440,1100",
                "--virtual-time-budget=1800",
                "--dump-dom",
                f"http://127.0.0.1:{server.server_port}/",
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
            self.skipTest("The local macOS sandbox blocks headless Chrome")
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
