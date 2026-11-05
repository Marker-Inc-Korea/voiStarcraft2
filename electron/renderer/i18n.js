"use strict";
// Words only. Korean first with English fallback; a key missing in one language falls back to Korean, then to the key.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiI18n = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const ko = {
    // bar
    command: "명령", retreat: "후퇴", hold: "홀드", stop: "멈춰", ops: "작전", em_sent: "전달", em_failed: "실패", em_nokey: "키불가", settings: "설정", mic: "음성", listening: "듣는 중", ready: "말하세요", connecting: "연결 중", error: "오류",
    // stages / tiers
    stage_heard: "들음", stage_understood: "이해", stage_assigned: "배정", stage_ordered: "명령", stage_effect: "효과",
    tier_macro: "매크로", tier_operation: "작전", tier_micro: "마이크로", tier_emergency: "긴급", tier_unknown: "분류중",
    sim: "시뮬", runtimeUnknown: "미확인", blocked: "막힘", ended: "종료",
    // reasons (one word each)
    r_resource: "자원부족", r_units: "병력부족", r_prereq: "선행필요", r_conflict: "충돌", r_refused: "거절", r_clarify: "확인필요", r_expired: "만료", r_timeout: "시간초과", r_failed: "실패", r_unknown: "막힘",
    // settings: tabs
    tab_glass: "유리", tab_layout: "레이아웃", tab_rainbow: "무지개", tab_voice: "음성", tab_emergency: "긴급", tab_server: "서버", tab_reset: "초기화",
    // glass
    glass_skin: "스킨", skin_cosmic: "우주", skin_terminal: "터미널", glass_slant: "기울기", emergency_retreat: "후퇴 키", emergency_hold: "홀드 키", emergency_stop: "멈춰 키",
    glass_alpha: "투명도", glass_blur: "블러", glass_vibrancy: "네이티브 블러", glass_hue: "색조", glass_saturation: "채도", glass_borderGlow: "테두리 빛", glass_radius: "모서리", glass_stars: "별", glass_starSpeed: "별 속도", glass_nebula: "성운", glass_twinkle: "반짝임", glass_meteors: "유성", glass_textSize: "글자 크기", glass_reduceMotion: "모션 줄임",
    // layout
    layout_dock: "위치", layout_width: "너비", layout_height: "높이", layout_autoHide: "자동 숨김", layout_hideDelay: "숨김 지연", layout_zone: "감지 폭", layout_pinned: "항상 표시", layout_display: "모니터", layout_margin: "여백",
    dock_bottom: "하단", dock_right: "오른쪽", dock_left: "왼쪽", dock_top: "상단", display_cursor: "커서",
    // rainbow
    rainbow_enabled: "사용", rainbow_screen: "화면", screen_current: "현재", screen_all: "전체", rainbow_depth: "깊이", rainbow_intensity: "세기", rainbow_speed: "속도", rainbow_brightness: "밝기", rainbow_thinking: "해석 중도", rainbow_reactive: "음성 반응", preview: "미리보기",
    // voice
    voice_device: "마이크", voice_level: "입력", voice_silence: "침묵", voice_sensitivity: "감도", voice_mode: "방식", voice_shortcut: "단축키", voice_language: "언어", voice_model: "모델", voice_latency: "지연",
    mode_always: "상시", mode_ptt: "누르기", lang_ko: "한국어", lang_en: "English", lang_auto: "자동", recordShortcut: "기록", pressKeys: "키 입력", shortcutUsed: "사용 중", defaultDevice: "기본", off: "꺼짐",
    lat_speech: "말끝→호출", lat_publish: "호출→게시",
    // server
    server_url: "주소", server_status: "상태", connected: "연결됨", disconnected: "끊김", checking: "확인중", apply: "적용",
    // BOARD (tactical board window)
    tab_board: "전술판", board_enabled: "사용", board_anchor: "위치", board_pageSize: "한 쪽 카드", board_width: "너비", board_autoAdvanceSec: "자동 넘김", board_offsetX: "가로 여백", board_offsetY: "세로 여백", board_hideWhenEmpty: "비면 숨김", board_ageOutSec: "종료 후 숨김", board_opacity: "투명도", board_pageUpShortcut: "이전 쪽 키", board_pageDownShortcut: "다음 쪽 키", anchor_upperleft: "좌상단", anchor_left: "왼쪽",
    // reset
    reset_all: "전체", confirm: "확인", on: "켜짐",
  };
  const en = {
    command: "Command", retreat: "Retreat", hold: "Hold", stop: "Stop", ops: "Ops", em_sent: "Sent", em_failed: "Failed", em_nokey: "No key", settings: "Settings", mic: "Voice", listening: "Listening", ready: "Speak", connecting: "Connecting", error: "Error",
    stage_heard: "Heard", stage_understood: "Understood", stage_assigned: "Assigned", stage_ordered: "Ordered", stage_effect: "Effect",
    tier_macro: "Macro", tier_operation: "Operation", tier_micro: "Micro", tier_emergency: "Emergency", tier_unknown: "Sorting",
    sim: "Sim", runtimeUnknown: "Unknown", blocked: "Blocked", ended: "Ended",
    r_resource: "Resources", r_units: "Units", r_prereq: "Prereq", r_conflict: "Conflict", r_refused: "Refused", r_clarify: "Clarify", r_expired: "Expired", r_timeout: "Timeout", r_failed: "Failed", r_unknown: "Blocked",
    tab_glass: "Glass", tab_layout: "Layout", tab_rainbow: "Rainbow", tab_voice: "Voice", tab_emergency: "Emergency", tab_server: "Server", tab_reset: "Reset",
    glass_skin: "Skin", skin_cosmic: "Cosmic", skin_terminal: "Terminal", glass_slant: "Slant", emergency_retreat: "Retreat key", emergency_hold: "Hold key", emergency_stop: "Stop key",
    glass_alpha: "Opacity", glass_blur: "Blur", glass_vibrancy: "Native blur", glass_hue: "Hue", glass_saturation: "Saturation", glass_borderGlow: "Border glow", glass_radius: "Corners", glass_stars: "Stars", glass_starSpeed: "Star speed", glass_nebula: "Nebula", glass_twinkle: "Twinkle", glass_meteors: "Meteors", glass_textSize: "Text size", glass_reduceMotion: "Reduce motion",
    layout_dock: "Position", layout_width: "Width", layout_height: "Height", layout_autoHide: "Auto-hide", layout_hideDelay: "Hide delay", layout_zone: "Edge zone", layout_pinned: "Always visible", layout_display: "Display", layout_margin: "Margin",
    dock_bottom: "Bottom", dock_right: "Right", dock_left: "Left", dock_top: "Top", display_cursor: "Cursor",
    rainbow_enabled: "Enabled", rainbow_screen: "Screen", screen_current: "Current", screen_all: "All", rainbow_depth: "Depth", rainbow_intensity: "Intensity", rainbow_speed: "Speed", rainbow_brightness: "Brightness", rainbow_thinking: "Thinking too", rainbow_reactive: "Voice reactive", preview: "Preview",
    voice_device: "Microphone", voice_level: "Level", voice_silence: "Silence", voice_sensitivity: "Sensitivity", voice_mode: "Mode", voice_shortcut: "Shortcut", voice_language: "Language", voice_model: "Model", voice_latency: "Latency",
    mode_always: "Always", mode_ptt: "Push", lang_ko: "한국어", lang_en: "English", lang_auto: "Auto", recordShortcut: "Record", pressKeys: "Press keys", shortcutUsed: "In use", defaultDevice: "Default", off: "Off",
    lat_speech: "speech→call", lat_publish: "call→post",
    server_url: "URL", server_status: "Status", connected: "Online", disconnected: "Offline", checking: "Checking", apply: "Apply",
    // BOARD (tactical board window)
    tab_board: "Board", board_enabled: "Enabled", board_anchor: "Position", board_pageSize: "Cards per page", board_width: "Width", board_autoAdvanceSec: "Auto page", board_offsetX: "Left offset", board_offsetY: "Top offset", board_hideWhenEmpty: "Hide if empty", board_ageOutSec: "Hide after end", board_opacity: "Opacity", board_pageUpShortcut: "Prev page key", board_pageDownShortcut: "Next page key", anchor_upperleft: "Upper-left", anchor_left: "Left",
    reset_all: "All", confirm: "Confirm", on: "On",
  };
  const tables = { ko, en };

  // Backend reasons are long technical strings; the bar shows exactly one word and keeps the raw text as the hover title.
  const REASON_RULES = [
    [/clarif|확인|모호/i, "r_clarify"],
    [/conflict|direct_control|lease|already owned|충돌/i, "r_conflict"],
    [/mineral|gas|resource|supply|afford|자원|광물|가스|인구/i, "r_resource"],
    [/composition|shortfall|no_idle|idle_unit|insufficient|unit|army|squad|병력|유닛|부족/i, "r_units"],
    [/prereq|tech|requires|missing_|선행|테크/i, "r_prereq"],
    [/refus|reject|unsupported|policy|지원하지|거절|정책/i, "r_refused"],
    [/expire|만료/i, "r_expired"],
    [/timeout|timed_out|시간/i, "r_timeout"],
    [/fail|error|send|전송|실패|오류/i, "r_failed"],
  ];
  function reasonKey(reason) {
    const raw = String(reason || "");
    for (const [pattern, key] of REASON_RULES) if (pattern.test(raw)) return key;
    return "r_unknown";
  }

  function create(lang = "ko") {
    let current = lang === "en" ? "en" : "ko";
    return {
      get lang() { return current; },
      set(next) { current = next === "en" ? "en" : "ko"; },
      t(key, vars) {
        let value = tables[current][key] ?? ko[key] ?? key;
        if (vars) for (const [name, replacement] of Object.entries(vars)) value = value.replaceAll(`{${name}}`, String(replacement));
        return value;
      },
      reason(reason) { return this.t(reasonKey(reason)); },
    };
  }
  return { create, tables, reasonKey };
});
