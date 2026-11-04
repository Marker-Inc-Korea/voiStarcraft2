"use strict";
// Korean-first labels with English fallback. Keys missing in a language fall back to Korean, then to the key.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiI18n = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const ko = {
    brandSub: "음성 지휘", chipConnecting: "연결 확인 중", chipBackendOk: "백엔드 연결됨", chipBackendDown: "백엔드 연결 끊김",
    pinHint: "항상 표시 (고정)", dockSwitch: "도킹 위치: 하단 ↔ 오른쪽",
    emptyTitle: "말하거나 입력하면 여기에 실행 단계가 표시됩니다",
    emptySub: "들음 → 이해 → 배정 → 명령 → 효과 확인. 백엔드가 보고한 단계만 표시합니다.",
    opsTitle: "작전 · 분대", opsEmpty: "활성 작전이 없습니다", eventsTitle: "최근 이벤트", eventsEmpty: "수신한 이벤트가 없습니다",
    orbLabel: "음성 명령 켜기/끄기", orbOn: "음성 끄기", orbOff: "음성 켜기",
    voiceIdle: "음성 꺼짐", voiceReady: "대기 중 · 말씀하세요", voiceConnecting: "연결 중", voiceListening: "듣는 중", voiceThinking: "해석 중", voiceActing: "전송 중", voiceError: "오류",
    transcriptPlaceholder: "마이크를 켜면 말하는 즉시 받아 적습니다",
    transcriptListening: "듣고 있습니다…", transcriptReady: "말씀하세요 — 키를 누를 필요 없습니다",
    inputPlaceholder: "또는 직접 입력 — 예: 마린 두 기는 정찰, 네 기는 오른쪽 길로 공격",
    send: "전송", retreat: "긴급 후퇴", retreatHint: "긴급 후퇴 명령을 즉시 전송",
    stage_heard: "들음", stage_understood: "이해", stage_assigned: "배정", stage_ordered: "명령", stage_effect: "효과 확인",
    tier_macro: "매크로", tier_operation: "작전", tier_micro: "마이크로", tier_emergency: "긴급",
    tier_unknown: "분류 대기",
    stageBlocked: "막힘", stageEnded: "종료", stageActive: "진행 중", stageDone: "완료", stageTodo: "대기",
    effectObserved: "효과 확인됨", effectNotObserved: "효과 미확인",
    blockedBecause: "막힌 이유", reasonUnknown: "백엔드가 이유를 보고하지 않았습니다",
    consumption: "소비 상태", pending_compile: "컴파일 대기", pending_consumption: "게임 소비 대기", pending_telemetry: "텔레메트리 대기", consumed: "게임이 소비함", not_published: "게시되지 않음", detached_telemetry: "텔레메트리 분리됨",
    disp_active: "활성", disp_pending: "대기", disp_blocked: "막힘", disp_expired: "만료", disp_superseded: "대체됨", disp_completed: "완료",
    mission_scouting: "정찰", mission_attack: "공격", mission_defense: "방어", mission_emergency: "긴급", mission_production: "생산", mission_operation: "작전",
    latency: "지연", latSpeech: "말 끝→도구호출", latPublish: "도구호출→게시",
    voiceSource: "음성", textSource: "입력", you: "나",
    parallel: "병렬 작전", parallelN: "병렬 {n}개",
    noOps: "백엔드가 아직 작전을 보고하지 않았습니다", queued: "백엔드 접수됨 · 해석 대기",
    sendFailed: "전송 실패", noCommand: "명령으로 인식되지 않았습니다", retreatSent: "긴급 후퇴 전송",
    runtimeLabel: "SC2", runtimeUnknown: "SC2 —", runtimeDisabled: "SC2 비활성",
    micDenied: "마이크 권한이 필요합니다", framesFmt: "프레임 {n}",
    queueAction: "큐 동작", layerAction: "레이어", sources: "출처",
    newer: "새 명령", seconds: "초", ms: "ms",
    count: "수량", route: "경로", target: "목표",
    glassOpacity: "유리 불투명도", glassBlur: "블러", vibrancy: "네이티브 블러", vibrancyHint: "macOS 배경 블러 (끄면 순수 투명 유리)",
    stageSim: "시뮬레이션", runtime_dry_run_fake_bot: "시뮬레이션(가짜 봇)", runtime_unknown: "실행 대상 미확인", runtime_live: "실제 실행", runtime_micromachine: "MicroMachine",
    simNote: "가짜 봇이 실행함 — 실제 SC2 명령/효과가 아닙니다", unknownNote: "백엔드가 실행 대상을 보고하지 않아 주문/효과를 확인할 수 없습니다",
  };
  const en = {
    brandSub: "Voice Command", chipConnecting: "Connecting", chipBackendOk: "Backend connected", chipBackendDown: "Backend offline",
    pinHint: "Always visible (pin)", dockSwitch: "Dock position: bottom ↔ right",
    emptyTitle: "Speak or type — execution stages appear here",
    emptySub: "Heard → Understood → Assigned → Ordered → Effect observed. Only stages the backend reports are shown.",
    opsTitle: "Operations · Squads", opsEmpty: "No active operations", eventsTitle: "Recent events", eventsEmpty: "No events received",
    orbLabel: "Toggle voice command", orbOn: "Stop voice", orbOff: "Start voice",
    voiceIdle: "Voice off", voiceReady: "Ready · speak any time", voiceConnecting: "Connecting", voiceListening: "Listening", voiceThinking: "Thinking", voiceActing: "Sending", voiceError: "Error",
    transcriptPlaceholder: "Turn on the mic and speech is transcribed live",
    transcriptListening: "Listening…", transcriptReady: "Just speak — no key to press",
    inputPlaceholder: "Or type — e.g. send two marines to scout and four to attack by the right path",
    send: "Send", retreat: "Emergency retreat", retreatHint: "Send an emergency retreat order now",
    stage_heard: "Heard", stage_understood: "Understood", stage_assigned: "Assigned", stage_ordered: "Ordered", stage_effect: "Effect observed",
    tier_macro: "Macro", tier_operation: "Operation", tier_micro: "Micro", tier_emergency: "Emergency", tier_unknown: "Classifying",
    stageBlocked: "Blocked", stageEnded: "Ended", stageActive: "In progress", stageDone: "Done", stageTodo: "Pending",
    effectObserved: "Effect observed", effectNotObserved: "No effect observed yet",
    blockedBecause: "Blocked because", reasonUnknown: "Backend gave no reason",
    consumption: "Consumption", pending_compile: "Awaiting compile", pending_consumption: "Awaiting game", pending_telemetry: "Awaiting telemetry", consumed: "Consumed by game", not_published: "Not published", detached_telemetry: "Telemetry detached",
    disp_active: "Active", disp_pending: "Pending", disp_blocked: "Blocked", disp_expired: "Expired", disp_superseded: "Superseded", disp_completed: "Completed",
    mission_scouting: "Scouting", mission_attack: "Attack", mission_defense: "Defense", mission_emergency: "Emergency", mission_production: "Production", mission_operation: "Operation",
    latency: "Latency", latSpeech: "speech end→tool", latPublish: "tool→publish",
    voiceSource: "Voice", textSource: "Typed", you: "You",
    parallel: "Parallel operations", parallelN: "{n} in parallel",
    noOps: "Backend has not reported operations yet", queued: "Accepted by backend · awaiting interpretation",
    sendFailed: "Send failed", noCommand: "Not recognised as a command", retreatSent: "Retreat sent",
    runtimeLabel: "SC2", runtimeUnknown: "SC2 —", runtimeDisabled: "SC2 disabled",
    micDenied: "Microphone permission required", framesFmt: "frame {n}",
    queueAction: "Queue action", layerAction: "Layer", sources: "Source",
    newer: "New command", seconds: "s", ms: "ms",
    count: "Count", route: "Route", target: "Target",
    glassOpacity: "Glass opacity", glassBlur: "Blur", vibrancy: "Native blur", vibrancyHint: "macOS behind-window blur (off = pure see-through glass)",
    stageSim: "Simulated", runtime_dry_run_fake_bot: "Simulation (fake bot)", runtime_unknown: "Runtime unknown", runtime_live: "Live execution", runtime_micromachine: "MicroMachine",
    simNote: "Executed by the scripted fake bot — not a real SC2 order or effect", unknownNote: "Backend did not report the execution target, so order/effect cannot be confirmed",
  };
  const tables = { ko, en };
  function create(lang = "ko") {
    let current = lang === "en" ? "en" : "ko";
    const api = {
      get lang() { return current; },
      set(next) { current = next === "en" ? "en" : "ko"; },
      t(key, vars) {
        let value = tables[current][key] ?? ko[key] ?? key;
        if (vars) for (const [name, replacement] of Object.entries(vars)) value = value.replaceAll(`{${name}}`, String(replacement));
        return value;
      },
    };
    return api;
  }
  return { create, tables };
});
