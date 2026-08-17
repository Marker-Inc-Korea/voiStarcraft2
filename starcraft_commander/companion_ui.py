"""Compact same-origin command companion for the local SC2 cockpit."""

from __future__ import annotations

import json


_COMPANION_PAGE_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>voiStarcraft2 전술 명령창</title>
  <style>
    :root {
      color-scheme: dark;
      --ink: #eef8f6;
      --muted: #91a9a8;
      --panel: rgba(5, 18, 24, 0.92);
      --line: rgba(102, 231, 219, 0.24);
      --cyan: #65f3df;
      --amber: #ffbd57;
      --danger: #ff6b5f;
      --ok: #7ae582;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 88% 8%, rgba(255, 189, 87, 0.16), transparent 31%),
        linear-gradient(145deg, #07171d 0%, #0a2228 47%, #071319 100%);
      font-family: "Avenir Next Condensed", "Arial Narrow", sans-serif;
      letter-spacing: 0.01em;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: 0.18;
      background-image:
        linear-gradient(rgba(101, 243, 223, 0.08) 1px, transparent 1px),
        linear-gradient(90deg, rgba(101, 243, 223, 0.08) 1px, transparent 1px);
      background-size: 28px 28px;
      mask-image: linear-gradient(to bottom, black, transparent 70%);
    }
    button, input { font: inherit; }
    button { cursor: pointer; }
    .shell {
      position: relative;
      min-height: 100vh;
      padding: 16px;
      display: grid;
      grid-template-rows: auto auto minmax(150px, 1fr) auto;
      gap: 12px;
    }
    .topbar {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }
    .eyebrow {
      margin: 0 0 3px;
      color: var(--cyan);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }
    h1 {
      margin: 0;
      font-family: "Futura", "Avenir Next Condensed", sans-serif;
      font-size: clamp(22px, 6vw, 31px);
      letter-spacing: -0.045em;
    }
    .status-pill {
      flex: none;
      max-width: 180px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--amber);
      background: rgba(2, 10, 14, 0.74);
      font-size: 12px;
      font-weight: 800;
      text-align: center;
    }
    .status-pill[data-state="connected"] {
      color: var(--ok);
      border-color: rgba(122, 229, 130, 0.42);
    }
    .status-pill[data-state="failed"] {
      color: var(--danger);
      border-color: rgba(255, 107, 95, 0.48);
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
      box-shadow: 0 18px 45px rgba(0, 0, 0, 0.28);
      backdrop-filter: blur(14px);
    }
    .operation {
      padding: 14px;
      display: grid;
      gap: 10px;
    }
    .operation-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .label {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    .stage {
      color: var(--cyan);
      font-size: 12px;
      font-weight: 800;
    }
    .goal {
      min-height: 44px;
      font-size: 17px;
      font-weight: 750;
      line-height: 1.35;
      word-break: keep-all;
    }
    .composition {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
    }
    .runtime-actions {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
    }
    .runtime-actions button {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 11px;
      color: var(--ink);
      background: rgba(12, 39, 45, 0.88);
      font-weight: 800;
    }
    .captions {
      min-height: 0;
      padding: 12px;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 8px;
    }
    .captions ol {
      min-height: 0;
      margin: 0;
      padding: 0;
      overflow: auto;
      list-style: none;
      display: grid;
      align-content: end;
      gap: 7px;
    }
    .captions li {
      padding: 9px 10px;
      border-left: 3px solid var(--cyan);
      border-radius: 0 9px 9px 0;
      color: #d8e9e6;
      background: rgba(15, 48, 55, 0.68);
      font-size: 13px;
      line-height: 1.35;
    }
    .captions li.warning { border-left-color: var(--amber); }
    .captions li.danger { border-left-color: var(--danger); }
    .command-dock {
      position: sticky;
      bottom: 0;
      padding: 11px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 44px 64px;
      gap: 8px;
    }
    #command-input {
      min-width: 0;
      min-height: 46px;
      border: 1px solid rgba(101, 243, 223, 0.32);
      border-radius: 12px;
      padding: 0 12px;
      color: #071319;
      background: #eaf6f3;
      outline: none;
    }
    #command-input:focus {
      box-shadow: 0 0 0 3px rgba(101, 243, 223, 0.18);
    }
    .icon-button, .send-button {
      min-height: 46px;
      border: 0;
      border-radius: 12px;
      font-weight: 900;
    }
    .icon-button {
      color: var(--cyan);
      background: rgba(15, 49, 57, 0.95);
    }
    .icon-button.recording {
      color: #081316;
      background: var(--danger);
      animation: pulse 1s infinite alternate;
    }
    .send-button {
      color: #071319;
      background: linear-gradient(135deg, var(--cyan), #7bb8ff);
    }
    .retreat-button {
      grid-column: 1 / -1;
      min-height: 39px;
      border: 1px solid rgba(255, 107, 95, 0.55);
      border-radius: 11px;
      color: #ffd8d3;
      background: rgba(97, 24, 24, 0.72);
      font-weight: 900;
    }
    .command-feedback {
      grid-column: 1 / -1;
      min-height: 17px;
      margin: 0;
      color: var(--muted);
      font-size: 12px;
    }
    @keyframes pulse {
      from { transform: scale(0.96); }
      to { transform: scale(1); }
    }
    @media (max-width: 430px) {
      .shell { padding: 10px; }
      .topbar { align-items: center; }
      .status-pill { max-width: 135px; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">SC2 tactical companion</p>
        <h1>전술 명령창</h1>
      </div>
      <div id="runtime-status" class="status-pill" data-state="idle">SC2 대기</div>
    </header>

    <section class="panel operation" aria-labelledby="operation-label">
      <div class="operation-head">
        <span id="operation-label" class="label">현재 명령</span>
        <span id="operation-stage" class="stage">명령 대기</span>
      </div>
      <div id="operation-goal" class="goal">게임을 시작하고 명령을 입력하세요.</div>
      <div id="operation-composition" class="composition">실행 대상과 증거가 여기에 표시됩니다.</div>
      <div class="runtime-actions">
        <button id="runtime-start" type="button">SC2 / MicroMachine 시작</button>
        <button id="runtime-refresh" type="button" title="상태 새로고침">↻</button>
      </div>
    </section>

    <section class="panel captions" aria-labelledby="caption-label">
      <span id="caption-label" class="label">전술 자막</span>
      <ol id="caption-list" aria-live="polite"></ol>
    </section>

    <form id="command-form" class="panel command-dock">
      <input id="command-input" type="text" autocomplete="off" autofocus
             placeholder="예: 마린 6기, 탱크 2기, 바이킹 2기로 적 본진 공격">
      <button id="voice-button" class="icon-button" type="button"
              title="음성 명령" aria-label="음성 명령" aria-pressed="false">◉</button>
      <button class="send-button" type="submit">전송</button>
      <button id="retreat-button" class="retreat-button" type="button">긴급 전군 후퇴</button>
      <p id="command-feedback" class="command-feedback">MyProxy 명령 경로 준비 중...</p>
    </form>
  </main>
  <script>
  "use strict";
  var DEFAULT_BLACKBOARD_DIR = __BLACKBOARD_JSON__;
  var search = new URLSearchParams(window.location.search);
  var token = search.get("token") || "";
  var blackboardDir = search.get("blackboard_dir") || DEFAULT_BLACKBOARD_DIR;
  var enemyDifficulty = Number(search.get("enemy_difficulty") || 10);
  var submitSequence = 0;
  var lastRuntimeSignature = "";
  var lastOperationSignature = "";
  var lastSubmittedUpdateId = "";
  var pendingCommand = null;
  var captionKeys = {};
  var recognition = null;
  var recording = false;

  function endpoint(path, values) {
    var query = new URLSearchParams(values || {});
    if (token) { query.set("token", token); }
    var suffix = query.toString();
    return path + (suffix ? "?" + suffix : "");
  }

  function parseJsonResponse(response) {
    return response.text().then(function(text) {
      var data = text ? JSON.parse(text) : {};
      if (!response.ok) {
        throw new Error(data.error || "HTTP " + response.status);
      }
      return data;
    });
  }

  function appendCaption(text, tone, key) {
    var normalized = String(text || "").trim();
    var dedupeKey = String(key || normalized);
    if (!normalized || captionKeys[dedupeKey]) { return; }
    captionKeys[dedupeKey] = true;
    var list = document.getElementById("caption-list");
    var row = document.createElement("li");
    row.className = tone || "";
    row.textContent = normalized;
    list.appendChild(row);
    while (list.children.length > 7) {
      list.removeChild(list.firstChild);
    }
    list.scrollTop = list.scrollHeight;
  }

  function setFeedback(text, failed) {
    var node = document.getElementById("command-feedback");
    node.textContent = text;
    node.style.color = failed ? "var(--danger)" : "var(--muted)";
  }

  function runtimeLabel(status) {
    var state = String(status.status || "idle");
    if (
      status.runtime_attached === true &&
      status.telemetry_current_for_process === true
    ) {
      return "SC2 연결됨 · frame " + String(status.telemetry_frame || 0);
    }
    if (state === "starting" || state === "running") {
      return "SC2 시작 중";
    }
    if (state === "failed" || state === "blocked") {
      return "실행 실패";
    }
    if (status.telemetry_stale_or_detached) {
      return "SC2 미연결";
    }
    return "SC2 대기";
  }

  function renderRuntime(status) {
    var node = document.getElementById("runtime-status");
    var label = runtimeLabel(status || {});
    var connected = status &&
      status.runtime_attached === true &&
      status.telemetry_current_for_process === true;
    var failed = status &&
      (status.status === "failed" || status.status === "blocked");
    node.textContent = label;
    node.dataset.state = connected ? "connected" : (failed ? "failed" : "idle");
    var signature = [
      status.status,
      connected,
      status.telemetry_frame,
      status.error || status.last_line || ""
    ].join("|");
    if (signature !== lastRuntimeSignature) {
      lastRuntimeSignature = signature;
      if (connected) {
        appendCaption("SC2와 MicroMachine 연결을 확인했습니다.", "", "runtime-connected");
      } else if (failed) {
        appendCaption(
          "런타임 시작 실패: " + String(status.error || status.last_line || "원인 미상"),
          "danger",
          signature
        );
      } else if (status.status === "starting" || status.status === "running") {
        appendCaption("SC2 / MicroMachine을 시작하고 있습니다.", "warning", "runtime-starting");
      }
    }
  }

  function unitName(value) {
    var names = {
      TERRAN_SCV: "SCV",
      TERRAN_MARINE: "마린",
      TERRAN_MARAUDER: "불곰",
      TERRAN_REAPER: "사신",
      TERRAN_GHOST: "유령",
      TERRAN_HELLION: "화염차",
      TERRAN_WIDOWMINE: "땅거미 지뢰",
      TERRAN_SIEGETANK: "공성전차",
      TERRAN_CYCLONE: "사이클론",
      TERRAN_THOR: "토르",
      TERRAN_VIKINGFIGHTER: "바이킹",
      TERRAN_MEDIVAC: "의료선",
      TERRAN_LIBERATOR: "해방선",
      TERRAN_BANSHEE: "밴시",
      TERRAN_BATTLECRUISER: "전투순양함"
    };
    return names[value] || String(value || "").replace(/^TERRAN_/, "");
  }

  function operationComposition(operation) {
    var edit = operation.operation_edit || {};
    var update = operation.update || {};
    var vector = update.vector || {};
    var nestedOperations = Array.isArray(vector.operations) ? vector.operations : [];
    var operationId = String(operation.operation_id || "");
    var generation = Number(operation.generation || operation.requested_generation || 0);
    var nestedOperation = nestedOperations.find(function(item) {
      if (!item || String(item.operation_id || "") !== operationId) {
        return false;
      }
      var nestedGeneration = Number(item.generation || item.requested_generation || 0);
      return !generation || !nestedGeneration || nestedGeneration === generation;
    }) || nestedOperations[0] || {};
    var candidates = [
      edit.after_composition,
      nestedOperation.composition_requirements,
      vector.composition_requirements
    ];
    var values = candidates.find(function(candidate) {
      return Array.isArray(candidate) && candidate.length > 0;
    }) || [];
    if (!Array.isArray(values) || !values.length) {
      var productionPlan = vector.production_plan || {};
      var tacticalTask = vector.tactical_task || {};
      var productionTargets = Array.isArray(productionPlan.targets)
        ? productionPlan.targets
        : [];
      var tacticalTargets = Array.isArray(tacticalTask.production_targets)
        ? tacticalTask.production_targets
        : [];
      var targets = productionTargets.concat(tacticalTargets).filter(function(value, index, all) {
        return value && all.indexOf(value) === index;
      });
      if (targets.length) {
        return "실행 대상 · " + targets.map(unitName).join(" · ");
      }
      return "실행 증거 확인 중";
    }
    return values.map(function(item) {
      return unitName(item.unit_type) + " " + String(item.count || 0) + "기";
    }).join(" · ");
  }

  function operationGoal(operation) {
    var update = operation.update || {};
    var vector = update.vector || {};
    var intervention = operation.intervention || {};
    var latestRequest = operation.latest_request || {};
    var latestQueue = latestRequest.command_queue || {};
    var compileResult = operation.compile_result || {};
    var compileQueue = compileResult.command_queue || {};
    return String(
      operation.command_text ||
      latestRequest.command_text ||
      latestQueue.command_text ||
      compileResult.command_text ||
      compileQueue.command_text ||
      vector.goal ||
      intervention.goal ||
      "명령 내용 확인 중"
    );
  }

  function operationStage(operation) {
    var intervention = operation.intervention || {};
    var execution = intervention.command_execution || {};
    var requestIdentity = commandIdentity(operation);
    var executionOwnerIdentity = String(
      operation.operation_console_execution_owner_update_id ||
      execution.update_id ||
      execution.policy_update_id ||
      ""
    );
    var executionMatchesRequest = (
      !requestIdentity ||
      !executionOwnerIdentity ||
      requestIdentity === executionOwnerIdentity
    );
    var state = executionMatchesRequest ? String(execution.state || "") : "";
    var disposition = String(operation.disposition || "");
    var consumption = String(operation.consumption_status || "");
    var transport = String(operation.transport_status || operation.status || "");
    if (state === "effect_observed" || disposition === "completed") {
      return "효과 확인";
    }
    if (state === "action_issued") { return "SC2 실행"; }
    if (state === "assigned" || state === "assignment_ready") {
      return "병력 배정";
    }
    if (state === "blocked" || disposition === "blocked") { return "차단"; }
    if (state === "cancelled" || disposition === "cancelled") { return "취소"; }
    if (state === "superseded" || disposition === "superseded") { return "교체"; }
    if (consumption === "consumed") { return "정책 적용"; }
    if (consumption === "pending_telemetry") { return "실행 확인 중"; }
    if (consumption === "detached_telemetry") { return "연결 확인 필요"; }
    if (consumption === "pending_compile" || transport === "queued") {
      return "명령 해석 중";
    }
    if (state === "published" || transport === "published") {
      return "명령 전달";
    }
    return "명령 추적";
  }

  function selectOperation(data) {
    var operations = Array.isArray(data.operations) ? data.operations : [];
    return operations.find(function(item) {
      return item && item.active === true;
    }) || operations[0] || null;
  }

  function commandIdentity(value) {
    if (!value) { return ""; }
    var update = value.update || {};
    var intervention = value.intervention || {};
    return String(
      value.update_id ||
      update.update_id ||
      value.policy_update_id ||
      value.active_update_id ||
      intervention.latest_update_id ||
      value.operation_id ||
      ""
    );
  }

  function selectCommand(data) {
    var update = data.update || {};
    var vector = update.vector || {};
    var latestRequest = data.latest_request || {};
    var latestQueue = latestRequest.command_queue || {};
    var compileResult = data.compile_result || {};
    var compileQueue = compileResult.command_queue || {};
    var intervention = data.intervention || {};
    var updateId = String(
      latestRequest.update_id ||
      update.update_id ||
      intervention.latest_update_id ||
      ""
    );
    var commandText = String(
      latestRequest.command_text ||
      latestQueue.command_text ||
      compileResult.command_text ||
      compileQueue.command_text ||
      vector.goal ||
      intervention.goal ||
      ""
    );
    var operations = Array.isArray(data.operations) ? data.operations : [];
    var matchingOperation = updateId ? operations.find(function(item) {
      return commandIdentity(item) === updateId;
    }) : null;
    if (matchingOperation) {
      return Object.assign({}, matchingOperation, {
        command_text: matchingOperation.command_text || commandText,
        latest_request: matchingOperation.latest_request || latestRequest,
        consumption_status: (
          matchingOperation.consumption_status ||
          data.consumption_status ||
          latestRequest.consumption_status ||
          ""
        ),
        transport_status: (
          matchingOperation.transport_status ||
          data.status ||
          ""
        )
      });
    }
    if (!updateId && !commandText) { return selectOperation(data); }
    return {
      operation_id: updateId,
      update_id: updateId,
      command_text: commandText,
      update: update,
      latest_request: latestRequest,
      intervention: intervention,
      consumption_status: (
        data.consumption_status ||
        latestRequest.consumption_status ||
        ""
      ),
      transport_status: data.status || "",
      disposition: data.disposition || "",
      active: data.status === "published"
    };
  }

  function renderOperation(data) {
    data = data || {};
    var operation = selectCommand(data);
    if (
      pendingCommand &&
      commandIdentity(operation) !== pendingCommand.update_id
    ) {
      data = pendingCommand.payload;
      operation = selectCommand(data);
    }
    var goalNode = document.getElementById("operation-goal");
    var stageNode = document.getElementById("operation-stage");
    var compositionNode = document.getElementById("operation-composition");
    if (!operation) {
      goalNode.textContent = "명령을 입력하면 해석 및 실행 상태가 표시됩니다.";
      stageNode.textContent = "명령 대기";
      compositionNode.textContent = "실행 대상과 증거가 여기에 표시됩니다.";
      return;
    }
    var goal = operationGoal(operation);
    var stage = operationStage(operation);
    var composition = operationComposition(operation);
    var selectedUpdateId = commandIdentity(operation);
    if (
      pendingCommand &&
      selectedUpdateId === pendingCommand.update_id &&
      stage !== "명령 해석 중"
    ) {
      pendingCommand = null;
    }
    goalNode.textContent = goal;
    stageNode.textContent = stage;
    compositionNode.textContent = composition;
    var signature = [
      selectedUpdateId,
      operation.operation_id || "",
      operation.operation_generation || "",
      operation.requested_operation_generation || "",
      operation.operation_console_execution_owner_update_id || "",
      stage,
      operation.consumption_status || "",
      data.runtime_attached === true
    ].join("|");
    if (signature !== lastOperationSignature) {
      lastOperationSignature = signature;
      var runtimeConnected = data.runtime_attached === true &&
        data.telemetry_current_for_process === true;
      var tone = stage === "차단" ? "danger" : (runtimeConnected ? "" : "warning");
      var suffix = runtimeConnected
        ? ""
        : " 현재 SC2 런타임 연결 증거는 아직 없습니다.";
      appendCaption(
        stage + ": " + goal + " · " + composition + suffix,
        tone,
        signature
      );
      if (stage === "효과 확인" || stage === "SC2 실행") {
        setFeedback("명령이 SC2 런타임에 적용되었습니다.", false);
      } else if (stage === "차단") {
        setFeedback("명령 실행이 차단되었습니다. 전술 자막을 확인하세요.", true);
      } else if (lastSubmittedUpdateId === selectedUpdateId) {
        setFeedback("명령을 전달했고 실제 실행 증거를 확인하고 있습니다.", false);
      }
    }
  }

  function refreshRuntime() {
    return fetch(endpoint("/api/runtime/status", {
      mode: "micromachine",
      blackboard_dir: blackboardDir
    })).then(parseJsonResponse).then(renderRuntime).catch(function(error) {
      renderRuntime({ status: "failed", error: error.message });
    });
  }

  function refreshOperation() {
    return fetch(endpoint("/api/micromachine/status", {
      blackboard_dir: blackboardDir
    })).then(parseJsonResponse).then(renderOperation).catch(function(error) {
      setFeedback("작전 상태 확인 실패: " + error.message, true);
    });
  }

  function refreshAll() {
    refreshRuntime();
    refreshOperation();
  }

  var pendingNativeSC2Launches = {};

  window.voiNativeSC2LaunchResolved = function(result) {
    var nonce = String(result && result.nonce || "");
    var pending = pendingNativeSC2Launches[nonce];
    if (!pending) { return; }
    delete pendingNativeSC2Launches[nonce];
    if (result && result.accepted === true) {
      pending.resolve(nonce);
      return;
    }
    pending.reject(new Error(
      String(result && result.error || "SC2 visible launch was not verified.")
    ));
  };

  function requestNativeSC2Launch() {
    var bridge = window.webkit && window.webkit.messageHandlers &&
      window.webkit.messageHandlers.sc2Launch;
    if (!bridge || typeof bridge.postMessage !== "function") {
      return Promise.reject(new Error(
        "SC2는 설치된 voiStarcraft2 앱의 네이티브 실행 버튼에서만 시작할 수 있습니다."
      ));
    }
    var nonce = (
      Date.now().toString(36) + "-" +
      Math.random().toString(36).slice(2) + "-" +
      Math.random().toString(36).slice(2)
    );
    return new Promise(function(resolve, reject) {
      pendingNativeSC2Launches[nonce] = {
        resolve: resolve,
        reject: reject
      };
      bridge.postMessage({ nonce: nonce });
    });
  }

  function startRuntimeWithNonce(nonce) {
    return fetch(endpoint("/api/runtime/start"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "micromachine",
        blackboard_dir: blackboardDir,
        sc2_launch_nonce: nonce,
        enemy_difficulty: (
          Number.isInteger(enemyDifficulty) &&
          enemyDifficulty >= 1 &&
          enemyDifficulty <= 10
        ) ? enemyDifficulty : 10
      })
    }).then(parseJsonResponse).then(function(status) {
      renderRuntime(status);
      setFeedback("시작 요청을 보냈습니다. 연결될 때까지 상태를 추적합니다.", false);
      window.setTimeout(refreshAll, 700);
    }).catch(function(error) {
      setFeedback("시작 실패: " + error.message, true);
      appendCaption("시작 실패: " + error.message, "danger");
    });
  }

  function startRuntime() {
    setFeedback("StarCraft II 실제 창과 렌더링을 확인하는 중입니다.", false);
    return requestNativeSC2Launch()
      .then(startRuntimeWithNonce)
      .catch(function(error) {
        setFeedback("시작 실패: " + error.message, true);
        appendCaption("시작 실패: " + error.message, "danger");
      });
  }

  function responseLanguage(text) {
    if (/[가-힣]/.test(text)) { return "ko"; }
    if (/[\\u4e00-\\u9fff]/.test(text)) { return "zh"; }
    return "en";
  }

  function submitCommand(text) {
    var cleaned = String(text || "").trim();
    if (!cleaned) { return Promise.resolve(); }
    submitSequence += 1;
    var submissionSequence = submitSequence;
    var updateId = "voi-companion-" + Date.now() + "-" + submitSequence;
    lastSubmittedUpdateId = updateId;
    var pendingPayload = {
      status: "queued",
      latest_request: {
        update_id: updateId,
        command_text: cleaned,
        consumption_status: "pending_compile"
      },
      operations: []
    };
    pendingCommand = {
      sequence: submissionSequence,
      update_id: updateId,
      command_text: cleaned,
      payload: pendingPayload
    };
    renderOperation(pendingPayload);
    document.getElementById("command-input").value = "";
    setFeedback("MyProxy가 명령을 해석하고 있습니다...", false);
    return fetch(endpoint("/api/micromachine/modulate"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: cleaned,
        blackboard_dir: blackboardDir,
        ui_language: "ko",
        response_language: responseLanguage(cleaned),
        async_publish: true,
        update_id: updateId,
        operation_id: updateId,
        operation_generation: 1
      })
    }).then(parseJsonResponse).then(function(data) {
      if (submissionSequence !== submitSequence) { return data; }
      lastSubmittedUpdateId = String(data.update_id || updateId);
      var acceptedPayload = {
        status: data.status || "queued",
        consumption_status: data.consumption_status || "pending_compile",
        latest_request: {
          update_id: lastSubmittedUpdateId,
          command_text: cleaned,
          consumption_status: data.consumption_status || "pending_compile"
        },
        operations: []
      };
      pendingCommand = {
        sequence: submissionSequence,
        update_id: lastSubmittedUpdateId,
        command_text: cleaned,
        payload: acceptedPayload
      };
      renderOperation(acceptedPayload);
      setFeedback(
        data.async_publish
          ? "명령 접수 완료. 작전 상태를 계속 추적합니다."
          : "명령 처리 완료.",
        false
      );
      appendCaption("명령 접수: " + cleaned, "", updateId);
      window.setTimeout(refreshOperation, 500);
    }).catch(function(error) {
      if (submissionSequence !== submitSequence) { return; }
      pendingCommand = null;
      setFeedback("명령 실패: " + error.message, true);
      appendCaption("명령 실패: " + error.message, "danger");
      window.setTimeout(refreshOperation, 500);
    });
  }

  function setupVoice() {
    var VoiceRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    var button = document.getElementById("voice-button");
    if (!VoiceRecognition) {
      button.addEventListener("click", function() {
        setFeedback("이 브라우저는 음성 입력을 지원하지 않습니다.", true);
      });
      return;
    }
    recognition = new VoiceRecognition();
    recognition.lang = "ko-KR";
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.onstart = function() {
      recording = true;
      button.classList.add("recording");
      button.setAttribute("aria-pressed", "true");
      setFeedback("듣고 있습니다. 명령을 말하세요.", false);
    };
    recognition.onresult = function(event) {
      var finalText = "";
      var interimText = "";
      for (var index = event.resultIndex; index < event.results.length; index += 1) {
        var transcript = event.results[index][0].transcript;
        if (event.results[index].isFinal) {
          finalText += transcript;
        } else {
          interimText += transcript;
        }
      }
      document.getElementById("command-input").value = finalText || interimText;
      if (finalText.trim()) { submitCommand(finalText); }
    };
    recognition.onerror = function(event) {
      setFeedback("음성 입력 실패: " + String(event.error || "unknown"), true);
    };
    recognition.onend = function() {
      recording = false;
      button.classList.remove("recording");
      button.setAttribute("aria-pressed", "false");
    };
    button.addEventListener("click", function() {
      if (recording) {
        recognition.stop();
      } else {
        recognition.start();
      }
    });
  }

  document.getElementById("runtime-start").addEventListener("click", startRuntime);
  document.getElementById("runtime-refresh").addEventListener("click", refreshAll);
  document.getElementById("retreat-button").addEventListener("click", function() {
    submitCommand("긴급 전군 즉시 후퇴해");
  });
  document.getElementById("command-form").addEventListener("submit", function(event) {
    event.preventDefault();
    submitCommand(document.getElementById("command-input").value);
  });

  setupVoice();
  appendCaption("전술 명령창이 준비되었습니다.", "", "companion-ready");
  refreshAll();
  window.setInterval(refreshRuntime, 1200);
  window.setInterval(refreshOperation, 2400);
  </script>
</body>
</html>
"""


def render_companion_page(micromachine_blackboard_dir: str = "") -> str:
    """Render the compact companion without exposing private configuration."""

    blackboard_json = json.dumps(
        str(micromachine_blackboard_dir or ""),
        ensure_ascii=True,
    ).replace("<", "\\u003c")
    return _COMPANION_PAGE_TEMPLATE.replace(
        "__BLACKBOARD_JSON__",
        blackboard_json,
    )
