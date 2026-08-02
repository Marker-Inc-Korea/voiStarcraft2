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
  document.documentElement.setAttribute("data-qa-js-errors", "true");
  function recordJsError(error) {
    window.__voiceQa.jsErrors = window.__voiceQa.jsErrors || [];
    window.__voiceQa.jsErrors.push(String(
      error && error.message || error || "unknown browser error"
    ));
    document.documentElement.setAttribute("data-qa-js-errors", "false");
  }
  window.addEventListener("error", function(event) {
    recordJsError(event && (event.error || event.message));
  });
  window.addEventListener("unhandledrejection", function(event) {
    recordJsError(event && event.reason);
  });

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
    applyLanguage("zh");
    var finalResultLocale = currentLang;
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
      var expectedStages = ["Interpret", "Assign", "Submit", "Observe"];
      var expectedActions = [
        "view",
        "revise",
        "reinforce",
        "retarget",
        "cancel"
      ];
      var expectedActionLabels = [
        "View",
        "Revise",
        "Reinforce",
        "Retarget",
        "Cancel operation"
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
      mark("final-non-korean",
        finalResultLocale === "zh" &&
        window.__voiceQa.modulateRequests === 1);
      mark("plan-identity",
        captions.indexOf("browser-recon#3") !== -1 &&
        captions.indexOf("browser-assault#7") !== -1);
      mark("truthful-plan",
        (
          captions.indexOf("계획 확인") !== -1 ||
          captions.indexOf("Plan confirmed") !== -1
        ) &&
        captions.indexOf("이동 시작") === -1 &&
        captions.indexOf("Movement started") === -1);
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
  document.documentElement.setAttribute("data-qa-js-errors", "true");
  function recordJsError(error) {
    window.__transferQa.jsErrors = window.__transferQa.jsErrors || [];
    window.__transferQa.jsErrors.push(String(
      error && error.message || error || "unknown browser error"
    ));
    document.documentElement.setAttribute("data-qa-js-errors", "false");
  }
  window.addEventListener("error", function(event) {
    recordJsError(event && (event.error || event.message));
  });
  window.addEventListener("unhandledrejection", function(event) {
    recordJsError(event && event.reason);
  });

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
      return new Promise(function(resolve) {
        window.setTimeout(function() {
          response({
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
          }, 202).then(resolve);
        }, 40);
      });
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
        session_epoch: "9007199254740991",
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
        session_epoch: "9007199254740991",
        game_frame: 140
      },
      battlefield_projection_fingerprint: "c".repeat(64),
      battlefield_overview: {
        schema_version: 2,
        authority: "micromachine_cpp",
        identity: {
          session_epoch: "9007199254740991",
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
    var localizedInFlightStatus = false;
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
      applyLanguage("zh");
      localizedInFlightStatus =
        document.getElementById("micromachine-status").textContent ===
          "正在原子化重新验证权威转移身份。";
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
        body.session_epoch === "9007199254740991" &&
        body.projection_frame === 140 &&
        body.projection_fingerprint === "c".repeat(64));
      mark("allowlist", forbiddenFields.every(function (field) {
        return body[field] === undefined;
      }));
      mark("opaque-dom", opaqueDom);
      mark("llm-bypassed", window.__transferQa.modulationRequests === 0);
      mark("locale-inflight-status", localizedInFlightStatus);
      var currentDestinationButton = card.querySelector(
        '[data-contextual-choice-id="' + choiceId + '"]'
      );
      mark("locale-inflight-release",
        currentLang === "zh" &&
        Boolean(currentDestinationButton) &&
        currentDestinationButton !== destinationButton &&
        currentDestinationButton.getAttribute("aria-disabled") === "false" &&
        contextualTransferChoiceRecords[choiceId].inFlight === false);
      mark("original-ux",
        document.querySelectorAll("[data-operation-lane]").length === 4 &&
        card.querySelectorAll(".operation-stage").length === 4 &&
        card.querySelectorAll("[data-operation-action]").length === 5);

      contextualTransferChoiceRecords = {};
      contextualTransferChoiceOrder = [];
      for (var evictionIndex = 0; evictionIndex < 256; evictionIndex += 1) {
        var evictionChoiceId = "voi-ctx-choice-eviction-" + evictionIndex;
        contextualTransferChoiceRecords[evictionChoiceId] = {
          payload: { choice_id: evictionChoiceId },
          inFlight: evictionIndex !== 1,
          promise: evictionIndex !== 1 ? {} : null
        };
        contextualTransferChoiceOrder.push(evictionChoiceId);
      }
      var newEvictionChoiceId = "voi-ctx-choice-eviction-new";
      var rememberedEvictionChoice = rememberContextualTransferChoice({
        choice_id: newEvictionChoiceId
      });
      mark("cache-oldest-eligible-evicted",
        rememberedEvictionChoice === true &&
        Boolean(contextualTransferChoiceRecords[
          "voi-ctx-choice-eviction-0"
        ]) &&
        contextualTransferChoiceRecords[
          "voi-ctx-choice-eviction-1"
        ] === undefined &&
        Boolean(contextualTransferChoiceRecords[newEvictionChoiceId]) &&
        contextualTransferChoiceOrder[0] ===
          "voi-ctx-choice-eviction-0" &&
        contextualTransferChoiceOrder[
          contextualTransferChoiceOrder.length - 1
        ] === newEvictionChoiceId &&
        Object.keys(contextualTransferChoiceRecords).length === 256 &&
        contextualTransferChoiceOrder.length === 256);

      contextualTransferChoiceRecords = {};
      contextualTransferChoiceOrder = [];
      for (var index = 0; index < 256; index += 1) {
        var inFlightChoiceId = "voi-ctx-choice-inflight-" + index;
        contextualTransferChoiceRecords[inFlightChoiceId] = {
          payload: { choice_id: inFlightChoiceId },
          inFlight: true,
          promise: {}
        };
        contextualTransferChoiceOrder.push(inFlightChoiceId);
      }
      var capacitySourceProjection = projection(
        "capacity-source",
        11,
        4,
        2
      );
      var capacityDestinationProjection = projection(
        "capacity-destination",
        13,
        3,
        1
      );
      var capacityOperation = JSON.parse(JSON.stringify(sourceOperation));
      capacityOperation.operation_id = "capacity-source";
      capacityOperation.operation_generation = 11;
      capacityOperation.update_id = "browser-capacity-update";
      capacityOperation.battlefield_operation = capacitySourceProjection;
      capacityOperation.update.update_id = "browser-capacity-update";
      capacityOperation.update.vector.operation_id = "capacity-source";
      capacityOperation.intervention.command_execution.command_id =
        "browser-capacity-update";
      capacityOperation.intervention.command_execution.operation_id =
        "capacity-source";
      capacityOperation.intervention.command_execution.operation_generation =
        11;
      renderOperationConsole({
        status: "published",
        blackboard_scope_id: "browser-transfer-scope",
        battlefield_projection_identity: {
          session_epoch: "9007199254740991",
          game_frame: 141
        },
        battlefield_projection_fingerprint: "d".repeat(64),
        battlefield_overview: {
          authority: "micromachine_cpp",
          identity: {
            session_epoch: "9007199254740991",
            game_frame: 141
          },
          operation_ownership: [
            capacitySourceProjection,
            capacityDestinationProjection
          ],
          transfer_availability: {
            atomic_revalidation_required: true,
            entries: [{
              source_owner_id: "capacity-source",
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
                source_owner_id: "capacity-source",
                counterpart_operation_id: "capacity-destination",
                requested_source_generation: 11,
                requested_counterpart_generation: 13,
                source_active: true,
                destination_active: true,
                ownership_integrity: true,
                operation_assignments_match: true,
                squad_assignments_match: true,
                action_assignments_match: true,
                role_assignments_match: true,
                atomic_revalidation_ready: true
              }
            }]
          }
        },
        operations: [capacityOperation]
      });
      var capacityRecord = operationRecords[
        operationRecordKey("browser-transfer-scope", "capacity-source")
      ];
      var uniqueChoiceOrder = new Set(contextualTransferChoiceOrder);
      mark("cache-bounded",
        Object.keys(contextualTransferChoiceRecords).length === 256 &&
        contextualTransferChoiceOrder.length === 256 &&
        uniqueChoiceOrder.size === 256);
      mark("cache-fail-closed",
        Boolean(capacityRecord) &&
        capacityRecord.node.querySelectorAll(
          "[data-contextual-choice-id]"
        ).length === 0);
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


def _battlefield_overview_browser_fixture_page() -> str:
    prelude = r"""
<script>
(function () {
  window.__overviewQa = { fetchCount: 0 };
  document.documentElement.setAttribute("data-qa-js-errors", "true");
  function recordJsError(error) {
    window.__overviewQa.jsErrors = window.__overviewQa.jsErrors || [];
    window.__overviewQa.jsErrors.push(String(
      error && error.message || error || "unknown browser error"
    ));
    document.documentElement.setAttribute("data-qa-js-errors", "false");
  }
  window.addEventListener("error", function(event) {
    recordJsError(event && (event.error || event.message));
  });
  window.addEventListener("unhandledrejection", function(event) {
    recordJsError(event && event.reason);
  });
  function response(payload) {
    return Promise.resolve({
      ok: true,
      status: 200,
      statusText: "",
      json: function () { return Promise.resolve(payload); },
      text: function () { return Promise.resolve(JSON.stringify(payload)); }
    });
  }
  window.fetch = function (url) {
    window.__overviewQa.fetchCount += 1;
    var route = String(url || "").split("?")[0];
    if (route === "/api/llm") {
      return response({ configured: true, provider: "openai", model: "test" });
    }
    if (route === "/api/history") {
      return response({ events: [], latest: 0 });
    }
    if (route === "/api/state") {
      return response({ available: false, standing_orders: [] });
    }
    if (route === "/api/runtime/status") {
      return response({ running: false, status: "idle" });
    }
    if (route === "/api/micromachine/status") {
      return response({
        status: "idle",
        blackboard_scope_id: "browser-overview-scope",
        operations: []
      });
    }
    return response({});
  };
  class FakeEventSource {
    addEventListener() {}
    close() {}
  }
  window.EventSource = FakeEventSource;
})();
</script>
"""
    scenario = r"""
<script>
(function () {
  function mark(name, pass) {
    document.body.setAttribute("data-qa-" + name, pass ? "true" : "false");
  }

  function projection(updateId, operationId, generation, ownerCount) {
    return {
      identity: {
        update_id: updateId,
        scope: "operation:" + operationId,
        session_epoch: 1700000000000,
        operation_id: operationId,
        generation: generation,
        stage: "queued_or_assigned",
        game_frame: 480
      },
      operation_id: operationId,
      generation: generation,
      operation_route: {
        requested_route_type: "direct",
        applied_route_type: "direct",
        location_intent: "home",
        target_type: "base_defense",
        resolved_target_label: "home",
        target_x: 44,
        target_y: 20,
        target_evidence: "semantic_anchor"
      },
      operation_lifetime: {
        mode: "standing",
        completion_state: "active",
        completion_conditions: ["cancelled_by_user"],
        duration_seconds: 0,
        issued_at_frame: 400,
        deadline_frame: 0,
        standing: true,
        completed: false,
        completion_reason: "",
        completed_frame: 0
      },
      operation_ownership: {
        owner_count: ownerCount,
        integrity_status: "valid"
      },
      operation_launch_policy: {
        min_units: 2,
        max_units: 4,
        allow_partial_requested: true,
        strict_scope: true,
        partial_launch_allowed: true,
        partial_launch_safe: true,
        launch_count: ownerCount,
        missing_count: Math.max(0, 4 - ownerCount),
        decision: ownerCount >= 4 ? "launch" : "wait",
        blocker: ownerCount >= 4 ? "" : "missing_addon",
        recommended_choices: [],
        safety_evidence: {
          evaluated_at_frame: 480,
          protected_defense_minimum_respected: true,
          source_operation_minimum_respected: true,
          transfer_admission: "accepted",
          emergency_preemption: "none"
        }
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

  function operation(updateId, operationId, generation, mission, ownerCount) {
    var canonical = projection(
      updateId,
      operationId,
      generation,
      ownerCount
    );
    return {
      operation_id: operationId,
      operation_generation: generation,
      requested_operation_generation: generation,
      update_id: updateId,
      operation_console_execution_owner_update_id: updateId,
      operation_console_execution_owner_vector: {
        operation_id: operationId,
        generation: generation,
        tactical_task: { task_type: "defend_with_units" }
      },
      command_text: "Defend " + operationId,
      mission: mission,
      transport_status: "published",
      consumption_status: "consumed",
      telemetry_frame: 480,
      telemetry_current: true,
      disposition: "active",
      operation_convergence: {
        target_count: 4,
        represented_count: ownerCount,
        missing_count: Math.max(0, 4 - ownerCount),
        blocker: ownerCount >= 4 ? "" : "missing_addon",
        requirements: [{
          unit_type: "TERRAN_MARAUDER",
          canonical_family: "marauder",
          role: "defender",
          target_count: 4,
          assigned_count: ownerCount,
          represented_count: ownerCount,
          completed_count: Math.max(0, ownerCount - 1),
          in_progress_count: ownerCount < 4 ? 1 : 0,
          queued_count: 0,
          missing_count: Math.max(0, 4 - ownerCount),
          production_blocker: ownerCount < 4 ? "missing_addon" : "ready",
          prerequisites: ["TERRAN_BARRACKS", "BARRACKS_TECHLAB"],
          missing_prerequisites: ownerCount < 4
            ? ["BARRACKS_TECHLAB"]
            : [],
          prerequisite_integrity_status: "valid",
          prerequisite_integrity_blockers: []
        }],
        prerequisite_integrity_status: "valid",
        prerequisite_integrity_blockers: []
      },
      battlefield_projection_join: {
        status: "matched",
        reason: "",
        update_id: updateId,
        scope: "operation:" + operationId,
        session_epoch: "1700000000000",
        operation_id: operationId,
        generation: generation
      },
      battlefield_operation: canonical,
      semantic_timeline: [],
      update: {
        update_id: updateId,
        vector: {
          goal: "Defend " + operationId,
          operation_id: operationId,
          generation: generation,
          tactical_task: { task_type: "defend_with_units" }
        }
      },
      intervention: {
        telemetry_frame: 480,
        command_execution: {
          command_id: updateId,
          operation_id: operationId,
          operation_generation: generation,
          state: "queued_or_assigned",
          completed: false,
          failed: false,
          expired: false,
          stages: [
            { name: "parsed", ok: true },
            { name: "consumed_by_manager", ok: true },
            { name: "queued_or_assigned", ok: true }
          ]
        }
      }
    };
  }

  window.setTimeout(async function () {
    var destinationOperationId =
      "destination-" + "xxxxxxxx-".repeat(12) + "xxxxxxxx";
    var source = operation(
      "browser-overview-source",
      "hold-main",
      2,
      "defense",
      3
    );
    source.battlefield_operation.operation_launch_policy
      .recommended_choices = [
        "launch_partial",
        "wait_for_full_force"
      ];
    source.semantic_timeline = Array.from(
      { length: 12 },
      function(_, index) {
        return {
          timeline_seq: index + 1,
          kind: index % 2 ? "assigned" : "movement_observed",
          summary: "canonical timeline event " + (index + 1),
          game_frame: index === 0 ? -1 : 460 + index,
          technical: {
            operation_id: "hold-main",
            generation: 2,
            sequence: index + 1
          }
        };
      }
    );
    var destination = operation(
      "browser-overview-destination",
      destinationOperationId,
      5,
      "defense",
      2
    );
    var overview = {
      schema_version: 2,
      authority: "micromachine_cpp",
      identity: {
        update_id: "browser-overview",
        scope: "battlefield",
        session_epoch: 1700000000000,
        generation: 9,
        stage: "observed",
        game_frame: 480
      },
      eligible_combat_count: 8,
      explicit_operation_owned_count: 5,
      autonomous_owned_count: 2,
      unassigned_count: 1,
      duplicate_owner_count: 0,
      operation_ownership: [
        source.battlefield_operation,
        destination.battlefield_operation
      ],
      autonomous_ownership: [{
        owner_id: "squad:Base Defense 44 20",
        owner_count: 2,
        composition: [{
          family: "marine",
          role: "base_defender",
          count: 1,
          ground_capable_count: 1,
          air_capable_count: 1
        }, {
          family: "viking",
          role: "base_defender",
          count: 1,
          ground_capable_count: 1,
          air_capable_count: 1
        }],
        integrity_status: "valid"
      }],
      bases: [{
        base_id: "base:44:20",
        semantic_anchor: "self_main",
        base_readiness: {
          readiness_state: "ready",
          reason: "capability_aware_minimum_satisfied",
          ground_threat: 2,
          air_threat: 1,
          observed_enemy_strength: 3,
          last_evidence_frame: 479,
          evidence_class: "observed_enemy_units",
          assigned_defender_count: 5,
          ground_capable_defender_count: 4,
          air_capable_defender_count: 2,
          required_defender_count: 5,
          required_ground_defender_count: 2,
          required_air_defender_count: 1,
          protected_minimum: [{
            family: "marine",
            role: "defender",
            count: 2
          }]
        }
      }],
      transfer_availability: {
        evaluated_at_frame: 480,
        atomic_revalidation_required: true,
        entries: [{
          source_owner_id: "hold-main",
          source_owner_count: 3,
          protected_minimum: 2,
          transferable_count: 1,
          transfer_safe: true,
          atomic_runtime_blocker: "",
          recommended_resolution_choices: ["transfer_available_units"],
          safety_evidence: {
            evaluated_at_frame: 480,
            protected_minimum_respected: true,
            atomic_revalidation_required: true
          },
          atomic_revalidation_inputs: {
            requested: true,
            requested_count: 1,
            source_owner_id: "hold-main",
            counterpart_operation_id: destinationOperationId,
            requested_generation: 2,
            counterpart_generation: 5,
            requested_source_generation: 3,
            requested_counterpart_generation: 6,
            source_active: true,
            destination_active: true,
            ownership_integrity: true,
            operation_assignments_match: true,
            squad_assignments_match: true,
            action_assignments_match: true,
            role_assignments_match: true,
            atomic_revalidation_ready: true
          }
        }]
      }
    };
    var payload = {
      status: "published",
      blackboard_scope_id: "browser-overview-scope",
      battlefield_projection_identity: {
        session_epoch: 1700000000000,
        game_frame: 480
      },
      battlefield_projection_fingerprint: "e".repeat(64),
      battlefield_overview: overview,
      battlefield_projection_integrity: {
        status: "valid",
        blocker_count: 0
      },
      operations: [source, destination]
    };
    safeRenderMicroMachineStatus(
      payload,
      { suppressPlanAnnouncements: true }
    );
    [
      "battlefield-operation-disclosure",
      "battlefield-base-disclosure",
      "battlefield-transfer-disclosure",
      "battlefield-raw-disclosure"
    ].forEach(function(id) {
      var disclosure = document.getElementById(id);
      if (disclosure) { disclosure.open = true; }
    });

    var sourceCard = document.querySelector(
      '[data-operation-id="hold-main"]'
    );
    var transferButton = sourceCard && sourceCard.querySelector(
      "[data-contextual-choice-id]"
    );
    var transferChoiceId = transferButton && transferButton.getAttribute(
      "data-contextual-choice-id"
    );
    var disabledResolution = sourceCard && sourceCard.querySelector(
      '[aria-disabled="true"]'
    );
    var disabledResolutionKey = disabledResolution && String(
      disabledResolution.getAttribute("data-operation-resolution") ||
      disabledResolution.getAttribute("data-contextual-choice-id") ||
      ""
    );
    var disabledReasonId = disabledResolution && String(
      disabledResolution.getAttribute("aria-describedby") || ""
    );
    var pendingId = appendPendingCommand(
      "locale cycle pending command"
    );
    var pendingNode = pendingAggregateNode;
    var timelineDisclosure = document.querySelector(
      ".operation-timeline-item details"
    );
    if (timelineDisclosure) { timelineDisclosure.open = true; }
    var board = document.getElementById("operation-list");
    var timeline = document.getElementById("operation-timeline");
    var statePanel = document.getElementById("state-panel");
    board.style.height = "180px";
    board.style.overflow = "auto";
    timeline.style.height = "140px";
    timeline.style.overflow = "auto";
    statePanel.style.height = "180px";
    statePanel.style.overflow = "auto";
    if (disabledResolution) {
      disabledResolution.focus({ preventScroll: true });
    }
    board.scrollTop = Math.min(
      37,
      Math.max(0, board.scrollHeight - board.clientHeight)
    );
    timeline.scrollTop = Math.min(
      29,
      Math.max(0, timeline.scrollHeight - timeline.clientHeight)
    );
    statePanel.scrollTop = Math.min(
      43,
      Math.max(0, statePanel.scrollHeight - statePanel.clientHeight)
    );
    var scrollBefore = {
      board: board.scrollTop,
      timeline: timeline.scrollTop,
      statePanel: statePanel.scrollTop
    };
    var sourceRecord = operationRecords[
      operationRecordKey("browser-overview-scope", "hold-main")
    ];
    var sourceNode = sourceRecord && sourceRecord.node;
    var sourceDomId = sourceNode && sourceNode.id;
    var sourceLane = sourceNode && sourceNode.parentNode;
    var sourceGeneration = sourceRecord &&
      sourceRecord.operationGeneration;
    var selectedBefore = selectedOperationKey;
    var announcementOrdinal =
      activeCommandConsoleRecord.announcementOrdinal;
    var captionsBefore = tacticalRadio.captions.length;
    var localeResults = {};
    var localeTransferFits = {};
    function settleLocale() {
      return new Promise(function(resolve) {
        window.setTimeout(resolve, 80);
      });
    }
    function inspectLocale(lang, laneTitle, stage, action, overviewText) {
      var card = sourceRecord.node;
      var focused = document.activeElement;
      var resolution = disabledResolutionKey && card.querySelector(
        '[data-operation-resolution="' + disabledResolutionKey + '"],' +
        '[data-contextual-choice-id="' + disabledResolutionKey + '"]'
      );
      var firstTimelineDisclosure = document.querySelector(
        ".operation-timeline-item details"
      );
      var currentTransferButton = transferChoiceId && card.querySelector(
        '[data-contextual-choice-id="' + transferChoiceId + '"]'
      );
      var currentCardRect = card.getBoundingClientRect();
      var currentTransferRect = currentTransferButton &&
        currentTransferButton.getBoundingClientRect();
      var expectedResolution = operationResolutionChoices(
        sourceRecord.data
      ).find(function(choice) {
        return String(choice.choiceId || choice.action || "") ===
          disabledResolutionKey;
      });
      var resolutionReason = resolution &&
        resolution.getAttribute("aria-describedby") &&
        document.getElementById(
          resolution.getAttribute("aria-describedby")
        );
      var timelineItem = document.querySelector(
        ".operation-timeline-item"
      );
      var timelineKind = timelineItem &&
        timelineItem.querySelector(".operation-timeline-kind");
      var timelineSummary = timelineItem &&
        timelineItem.querySelector(".operation-timeline-summary");
      var timelineTechnical = timelineItem &&
        timelineItem.querySelector("pre");
      var expectedTimelineKind = operationTimelineKindLabel(
        source.semantic_timeline[0].kind
      );
      var laneTitles = operationLaneDefinitions().map(function(definition) {
        return definition[1];
      });
      var renderedLaneTitles = Array.from(
        document.querySelectorAll(".operation-lane-title")
      ).map(function(node) { return node.textContent; });
      var expectedStages = [
        commandUiText("해석", "Interpret", "解析"),
        commandUiText("배정", "Assign", "分配"),
        commandUiText("제출", "Submit", "提交"),
        commandUiText("관측", "Observe", "观察")
      ];
      var renderedStages = Array.from(
        card.querySelectorAll(".operation-stage")
      ).map(function(node) { return node.textContent; });
      var expectedActions = [
        commandUiText("대표 보기", "View", "查看"),
        commandUiText("수정", "Revise", "修改"),
        commandUiText("증원", "Reinforce", "增援"),
        commandUiText("목표 변경", "Retarget", "变更目标"),
        commandUiText("작전 취소", "Cancel operation", "取消作战")
      ];
      var renderedActions = Array.from(
        card.querySelectorAll(
          ".operation-card-actions [data-operation-action]"
        )
      ).map(function(node) { return node.textContent; });
      localeTransferFits[lang] = Boolean(
        currentTransferButton &&
        currentTransferButton.textContent.indexOf(
          destinationOperationId
        ) >= 0 &&
        currentTransferButton.scrollWidth <=
          currentTransferButton.clientWidth + 1 &&
        currentTransferRect.left >= currentCardRect.left - 1 &&
        currentTransferRect.right <= currentCardRect.right + 1
      );
      var checks = {
        lang: document.documentElement.lang === lang,
        lane: document.getElementById("operation-lane-planning-title")
          .textContent === laneTitle,
        stage: card.querySelector(".operation-stage").textContent === stage,
        action: card.querySelector('[data-operation-action="view"]')
          .textContent === action,
        fourLanes: JSON.stringify(renderedLaneTitles) ===
          JSON.stringify(laneTitles),
        fourStages: JSON.stringify(renderedStages) ===
          JSON.stringify(expectedStages),
        fiveActions: JSON.stringify(renderedActions) ===
          JSON.stringify(expectedActions),
        overview: document.getElementById("battlefield-control-summary")
          .textContent.indexOf(overviewText) === 0,
        node: sourceRecord.node === sourceNode,
        domId: sourceRecord.node.id === sourceDomId,
        laneIdentity: sourceRecord.node.parentNode === sourceLane,
        generation: sourceRecord.operationGeneration === sourceGeneration,
        selection: selectedOperationKey === selectedBefore,
        pendingNode: pendingAggregateNode === pendingNode,
        pendingCount: pendingCommandCount() === 1,
        pendingDom: document.querySelectorAll(
          "#pending-aggregate"
        ).length === 1,
        resolution: Boolean(resolution),
        resolutionDisabled: Boolean(
          resolution &&
          resolution.getAttribute("aria-disabled") === "true"
        ),
        resolutionDescription: Boolean(
          resolution &&
          resolution.getAttribute("aria-describedby") === disabledReasonId
        ),
        resolutionLabel: Boolean(
          resolution &&
          expectedResolution &&
          resolution.textContent === expectedResolution.label &&
          resolution.getAttribute("aria-label").indexOf(
            expectedResolution.label + ":"
          ) === 0
        ),
        resolutionReason: Boolean(
          expectedResolution &&
          resolutionReason &&
          resolutionReason.textContent === expectedResolution.reason
        ),
        focus: focused === resolution,
        timelineKind: Boolean(
          timelineKind &&
          timelineKind.textContent === expectedTimelineKind
        ),
        timelineSummary: Boolean(
          timelineSummary &&
          timelineSummary.textContent ===
            expectedTimelineKind + " · hold-main#2" &&
          timelineSummary.textContent.indexOf(
            "canonical timeline event"
          ) < 0
        ),
        timelineTechnical: Boolean(
          timelineTechnical &&
          timelineTechnical.textContent.indexOf(
            '"canonical_summary": "canonical timeline event 1"'
          ) >= 0
        ),
        timelineDisclosure: Boolean(
          firstTimelineDisclosure && firstTimelineDisclosure.open === true
        ),
        operationDisclosure: document.getElementById(
          "battlefield-operation-disclosure"
        ).open,
        baseDisclosure: document.getElementById(
          "battlefield-base-disclosure"
        ).open,
        transferDisclosure: document.getElementById(
          "battlefield-transfer-disclosure"
        ).open,
        rawDisclosure: document.getElementById(
          "battlefield-raw-disclosure"
        ).open,
        boardScroll: board.scrollTop === scrollBefore.board,
        timelineScroll: timeline.scrollTop === scrollBefore.timeline,
        statePanelScroll:
          statePanel.scrollTop === scrollBefore.statePanel,
        transferCurrent: Boolean(currentTransferButton),
        transferOverflow: localeTransferFits[lang]
      };
      localeResults[lang] = Object.keys(checks).every(function(key) {
        return checks[key];
      });
      document.body.setAttribute(
        "data-qa-locale-" + lang + "-failures",
        Object.keys(checks).filter(function(key) {
          return !checks[key];
        }).join(",") || "none"
      );
      document.body.setAttribute(
        "data-qa-locale-" + lang + "-scroll",
        [
          scrollBefore.board,
          board.scrollTop,
          scrollBefore.timeline,
          timeline.scrollTop,
          scrollBefore.statePanel,
          statePanel.scrollTop
        ].join(",")
      );
    }
    var rapidLiveRegionValues = Array.from(
      document.querySelectorAll("[aria-live]")
    ).map(function(node) {
      return {
        node: node,
        value: node.getAttribute("aria-live")
      };
    });
    applyLanguage("en");
    applyLanguage("zh");
    await settleLocale();
    var rapidLiveRegionRestore = rapidLiveRegionValues.every(
      function(entry) {
        return entry.node.getAttribute("aria-live") === entry.value;
      }
    ) && Array.from(
      document.querySelectorAll(".operation-card-state")
    ).every(function(node) {
      return node.getAttribute("aria-live") === "polite";
    });
    applyLanguage("ko");
    await settleLocale();
    document.querySelector('[data-lang-button="en"]').click();
    await settleLocale();
    inspectLocale(
      "en",
      "Planning",
      "Interpret",
      "View",
      "Eligible "
    );
    var fetchesBeforeSameLanguage =
      window.__overviewQa.fetchCount;
    var surfacesBeforeSameLanguage = JSON.stringify({
      cardFingerprint: sourceNode.getAttribute(
        "data-operation-card-fingerprint"
      ),
      cardText: sourceNode.textContent,
      laneTitles: Array.from(
        document.querySelectorAll(".operation-lane-title")
      ).map(function(node) { return node.textContent; }),
      stages: Array.from(
        sourceNode.querySelectorAll(".operation-stage")
      ).map(function(node) { return node.textContent; }),
      actions: Array.from(
        sourceNode.querySelectorAll("[data-operation-action]")
      ).map(function(node) { return node.textContent; }),
      timelineFingerprint: timeline.getAttribute(
        "data-operation-timeline-fingerprint"
      ),
      timelineText: timeline.textContent,
      overviewText: document.getElementById(
        "battlefield-control-overview"
      ).textContent,
      resolutionText: disabledResolutionKey && sourceNode.querySelector(
        '[data-operation-resolution="' + disabledResolutionKey + '"],' +
        '[data-contextual-choice-id="' + disabledResolutionKey + '"]'
      ).textContent
    });
    document.querySelector('[data-lang-button="en"]').click();
    await settleLocale();
    var sameLanguageNoop =
      window.__overviewQa.fetchCount === fetchesBeforeSameLanguage &&
      JSON.stringify({
        cardFingerprint: sourceNode.getAttribute(
          "data-operation-card-fingerprint"
        ),
        cardText: sourceNode.textContent,
        laneTitles: Array.from(
          document.querySelectorAll(".operation-lane-title")
        ).map(function(node) { return node.textContent; }),
        stages: Array.from(
          sourceNode.querySelectorAll(".operation-stage")
        ).map(function(node) { return node.textContent; }),
        actions: Array.from(
          sourceNode.querySelectorAll("[data-operation-action]")
        ).map(function(node) { return node.textContent; }),
        timelineFingerprint: timeline.getAttribute(
          "data-operation-timeline-fingerprint"
        ),
        timelineText: timeline.textContent,
        overviewText: document.getElementById(
          "battlefield-control-overview"
        ).textContent,
        resolutionText: disabledResolutionKey && sourceNode.querySelector(
          '[data-operation-resolution="' + disabledResolutionKey + '"],' +
          '[data-contextual-choice-id="' + disabledResolutionKey + '"]'
        ).textContent
      }) === surfacesBeforeSameLanguage &&
      sourceRecord.node === sourceNode &&
      document.activeElement.getAttribute(
        "data-operation-resolution"
      ) === disabledResolutionKey;
    document.querySelector('[data-lang-button="zh"]').click();
    await settleLocale();
    inspectLocale(
      "zh",
      "解析/编组",
      "解析",
      "查看",
      "可战斗 "
    );
    document.querySelector('[data-lang-button="ko"]').click();
    await settleLocale();
    inspectLocale(
      "ko",
      "해석/편성",
      "해석",
      "대표 보기",
      "전투 가능 "
    );
    var fallbackValue = I18N.en.operationConsoleTitle;
    delete I18N.en.operationConsoleTitle;
    document.querySelector('[data-lang-button="en"]').click();
    await settleLocale();
    var fallbackConsistent =
      document.getElementById("operation-console-title").textContent ===
        I18N.ko.operationConsoleTitle &&
      commandUiText("한국어 fallback", undefined, undefined) ===
        "한국어 fallback";
    I18N.en.operationConsoleTitle = fallbackValue;
    document.querySelector('[data-lang-button="ko"]').click();
    await settleLocale();
    removePendingById(pendingId);
    var operationText = document.getElementById(
      "battlefield-operation-details"
    ).textContent;
    var baseText = document.getElementById(
      "battlefield-base-details"
    ).textContent;
    var transferText = document.getElementById(
      "battlefield-transfer-details"
    ).textContent;
    var overviewNode = document.getElementById(
      "battlefield-control-overview"
    );
    var allIds = Array.from(document.querySelectorAll("[id]"))
      .map(function(node) { return node.id; });
    mark("complete", true);
    mark("operation-detail",
      operationText.indexOf("operation:hold-main") >= 0 &&
      operationText.indexOf("MARAUDER/defender") >= 0 &&
      operationText.indexOf("BARRACKS_TECHLAB") >= 0 &&
      operationText.indexOf("보호 minimum 충족") >= 0 &&
      operationText.indexOf("source minimum 충족") >= 0);
    mark("base-detail",
      baseText.indexOf("observed_enemy_units") >= 0 &&
      baseText.indexOf("squad:Base Defense 44 20 2") >= 0 &&
      baseText.indexOf("hold-main#2") >= 0 &&
      baseText.indexOf("MARAUDER/defender 3/4") >= 0 &&
      baseText.indexOf("보호 minimum 준수 충족") >= 0 &&
      baseText.indexOf("marine/base_defender 1") >= 0 &&
      baseText.indexOf("viking/base_defender 1") >= 0 &&
      baseText.indexOf("family evidence missing") < 0);
    mark("transfer-detail",
      transferText.indexOf("hold-main#2") >= 0 &&
      transferText.indexOf(destinationOperationId + "#5") >= 0 &&
      transferText.indexOf("atomic=충족") >= 0);
    mark("original-ux",
      document.querySelectorAll("[data-operation-lane]").length === 4 &&
      sourceCard &&
      sourceCard.querySelectorAll(".operation-stage").length === 4 &&
      sourceCard.querySelectorAll(
        ".operation-card-actions [data-operation-action]"
      ).length === 5);
    mark("accessibility",
      document.querySelectorAll(
        ".battlefield-detail-disclosure > summary"
      ).length === 4 &&
      document.getElementById("battlefield-integrity-alert")
        .getAttribute("role") === "status" &&
      document.getElementById("battlefield-operation-details")
        .getAttribute("role") === "list");
    mark("unique-ids", allIds.length === new Set(allIds).size);
    mark("single-pending",
      document.querySelectorAll(".message-pending").length <= 1);
    mark("locale-cycle",
      localeResults.en && localeResults.zh && localeResults.ko);
    mark("locale-noop", sameLanguageNoop);
    mark("locale-fallback", fallbackConsistent);
    mark("locale-live-regions",
      rapidLiveRegionRestore &&
      activeCommandConsoleRecord.announcementOrdinal ===
        announcementOrdinal &&
      tacticalRadio.captions.length === captionsBefore &&
      Array.from(
        document.querySelectorAll(".operation-card-state")
      ).every(function(node) {
        return node.getAttribute("aria-live") === "polite";
      }) &&
      document.getElementById("operation-summary")
        .getAttribute("aria-live") === "polite");
    mark("scroll-nonzero",
      scrollBefore.board > 0 &&
      scrollBefore.timeline > 0 &&
      scrollBefore.statePanel > 0);
    transferButton = sourceCard && sourceCard.querySelector(
      "[data-contextual-choice-id]"
    );
    var overviewRect = overviewNode.getBoundingClientRect();
    var sourceCardRect = sourceCard.getBoundingClientRect();
    var transferButtonRect = transferButton &&
      transferButton.getBoundingClientRect();
    var detailRowsFit = Array.from(
      overviewNode.querySelectorAll(".battlefield-detail-row")
    ).every(function(row) {
      return row.scrollWidth <= row.clientWidth + 1;
    });
    var isMobileViewport = window.innerWidth <= 620;
    var transferButtonFits = Boolean(
      destinationOperationId.length === 128 &&
      transferButton &&
      transferButton.textContent.indexOf(destinationOperationId) >= 0 &&
      transferButton.scrollWidth <= transferButton.clientWidth + 1 &&
      transferButtonRect.left >= sourceCardRect.left - 1 &&
      transferButtonRect.right <= sourceCardRect.right + 1
    );
    var transferCardFits =
      sourceCard.scrollWidth <= sourceCard.clientWidth + 1;
    var documentFits =
      document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1 &&
      document.body.scrollWidth <= document.body.clientWidth + 1;
    var viewportFits =
      document.documentElement.scrollWidth <= window.innerWidth + 1 &&
      sourceCardRect.left >= -1 &&
      sourceCardRect.right <= window.innerWidth + 1 &&
      transferButtonRect &&
      transferButtonRect.left >= -1 &&
      transferButtonRect.right <= window.innerWidth + 1;
    mark("transfer-button-overflow",
      !isMobileViewport || transferButtonFits);
    mark("transfer-locale-overflow",
      localeTransferFits.en &&
      localeTransferFits.zh &&
      localeTransferFits.ko);
    mark("transfer-card-overflow",
      !isMobileViewport || transferCardFits);
    mark("transfer-document-overflow",
      !isMobileViewport || documentFits);
    mark("transfer-viewport-overflow",
      !isMobileViewport || viewportFits);
    mark("layout",
      overviewRect.left >= -1 &&
      overviewRect.right <= window.innerWidth + 1 &&
      overviewRect.width <= window.innerWidth + 1 &&
      detailRowsFit);
    var screenshotLocale = new URLSearchParams(
      window.location.search
    ).get("locale");
    if (["ko", "en", "zh"].indexOf(screenshotLocale) >= 0) {
      applyLanguage(screenshotLocale);
      await settleLocale();
      document.body.setAttribute(
        "data-qa-screenshot-locale",
        currentLang
      );
    }
  }, 120);
})();
</script>
"""
    page = render_web_gui_page(
        micromachine_blackboard_dir="/tmp/browser-overview-blackboard"
    )
    page = page.replace("<script>", prelude + "\n<script>", 1)
    return page.replace("</body>", scenario + "\n</body>", 1)


class WebGuiRealBrowserTest(unittest.TestCase):
    def test_battlefield_overview_detail_in_real_chrome_desktop_and_mobile(
        self,
    ) -> None:
        chrome = _chrome_executable()
        if chrome is None:
            self.skipTest("Chrome/Chromium is not installed")

        page = _battlefield_overview_browser_fixture_page()
        expected_markers = (
            "js-errors",
            "complete",
            "operation-detail",
            "base-detail",
            "transfer-detail",
            "original-ux",
            "accessibility",
            "unique-ids",
            "single-pending",
            "locale-cycle",
            "locale-noop",
            "locale-fallback",
            "locale-live-regions",
            "scroll-nonzero",
            "transfer-button-overflow",
            "transfer-locale-overflow",
            "transfer-card-overflow",
            "transfer-document-overflow",
            "transfer-viewport-overflow",
            "layout",
        )

        class FixtureHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.split("?", 1)[0] not in {"/", "/index.html"}:
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
            name="battlefield-overview-browser-fixture",
            daemon=True,
        )
        thread.start()

        def stop_fixture_server() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop_fixture_server)
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = pathlib.Path(temporary_directory)
            for width, height in ((1440, 1100), (390, 844)):
                with self.subTest(viewport=f"{width}x{height}"):
                    profile = temporary_root / f"profile-dump-{width}"
                    common = [
                        chrome,
                        "--headless=new",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--force-device-scale-factor=1",
                        f"--window-size={width},{height}",
                        "--virtual-time-budget=1800",
                    ]
                    result = subprocess.run(
                        [
                            *common,
                            f"--user-data-dir={profile}",
                            "--dump-dom",
                            f"http://127.0.0.1:{server.server_port}/",
                        ],
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

                    for locale in ("ko", "en", "zh"):
                        screenshot = (
                            temporary_root /
                            (
                                "battlefield-overview-"
                                f"{locale}-{width}x{height}.png"
                            )
                        )
                        screenshot_result = subprocess.run(
                            [
                                *common,
                                (
                                    "--user-data-dir="
                                    f"{temporary_root / f'profile-shot-{locale}-{width}'}"
                                ),
                                f"--screenshot={screenshot}",
                                (
                                    f"http://127.0.0.1:{server.server_port}/"
                                    f"?locale={locale}"
                                ),
                            ],
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        self.assertEqual(
                            screenshot_result.returncode,
                            0,
                            textwrap.shorten(
                                screenshot_result.stderr
                                or screenshot_result.stdout,
                                width=2000,
                                placeholder="...",
                            ),
                        )
                        png = screenshot.read_bytes()
                        self.assertGreater(len(png), 1000)
                        self.assertEqual(b"\x89PNG\r\n\x1a\n", png[:8])
                        self.assertEqual(
                            width,
                            int.from_bytes(png[16:20], "big"),
                        )
                        self.assertEqual(
                            height,
                            int.from_bytes(png[20:24], "big"),
                        )

    def test_voice_tactical_loop_in_real_chrome_desktop_and_mobile(self) -> None:
        chrome = _chrome_executable()
        if chrome is None:
            self.skipTest("Chrome/Chromium is not installed")

        page = _browser_fixture_page()
        expected_markers = (
            "js-errors",
            "complete",
            "single-node",
            "exactly-once",
            "final-non-korean",
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
            "js-errors",
            "complete",
            "exactly-once",
            "typed-endpoint",
            "destination",
            "identity",
            "allowlist",
            "opaque-dom",
            "llm-bypassed",
            "locale-inflight-status",
            "locale-inflight-release",
            "original-ux",
            "cache-oldest-eligible-evicted",
            "cache-bounded",
            "cache-fail-closed",
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
