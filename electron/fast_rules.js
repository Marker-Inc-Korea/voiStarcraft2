"use strict";
// Deterministic rule-first matcher: a line-by-line port of starcraft_commander/realtime_intent.match_fast_path for the
// (at most five) promoted families. The client runs it the moment the input transcription completes, so retreat / hold /
// stop and the two promoted micro abilities execute without waiting for the model's tool call. The Python function stays
// the authority: the backend re-runs it on every `fast: true` request. electron/fixtures/fast_rules.json pins both
// implementations to the same answers (electron/test/fast_rules.test.js, tests/test_realtime_intent.py).
//
// JS \b is ASCII-only while Python's treats Hangul as word characters, so every word boundary is spelled out (W0/W1).
const W0 = "(?<![0-9A-Za-z_가-힣])";
const W1 = "(?![0-9A-Za-z_가-힣])";
const re = (source, flags = "i") => new RegExp(source, flags);

const PROMOTED_FAMILIES = Object.freeze(["emergency:force_retreat", "emergency:hold_position", "emergency:cancel_attacks", "micro:stimpack", "micro:siege_mode"]);

const NEG = re("지\\s*(?:마|말|않|못)|하지\\s*마|안\\s*(?:해|하|돼)|아니|말고|금지|" + W0 + "(?:don'?t|do\\s+not|never|no\\s+need|without|not)" + W1);
const CONTRA = re("하면서|동시에|하고|그리고|랑|,|" + W0 + "and" + W1);
const ATTACKISH = re("공격|정찰|견제|방어|수비|집결|지어|건설|" + W0 + "(?:attack|scout|harass|defend|build)");
const HOLD = re("(?:멈추고\\s*)?홀드(?:\\s*포지션)?|" + W0 + "hold(?:\\s+position)?" + W1 + "|제자리(?:에서)?|멈춰\\s*서|서\\s*있어");
const STOP = re(
  "(?:공격|러시|러쉬|압박|작전|진격)(?:을|를)?\\s*(?:취소|중지|중단|멈춰|멈추|정지|그만)|" +
  "멈춰|멈추라|정지|그만|스톱|스탑|" + W0 + "(?:stop|halt|freeze)" + W1 + "|" +
  W0 + "(?:cancel|abort)\\s+(?:the\\s+)?(?:attack|rush|operation)" + W1,
);
const RETREAT = re("후퇴|퇴각|철수|물러나|빠져|도망(?!쳐)|" + W0 + "(?:retreat|fall\\s*back|fallback)" + W1);
const EMERGENCY_RULES = [["hold", HOLD], ["stop", STOP], ["retreat", RETREAT]];
const EMERGENCY_FLAGS = { retreat: ["force_retreat", "emergency:force_retreat"], hold: ["hold_position", "emergency:hold_position"], stop: ["cancel_attacks", "emergency:cancel_attacks"] };
const FILLER = re(
  "긴급|즉시|당장|지금|바로|전원|모두|전부|전체|다들|빨리|어서|얼른|제발|뒤로|안전한?\\s*곳으로?|병력|유닛들?|군대|" +
  "하십시[오요]|하시오|합니다|합시다|하세요|해\\s*주세요|해주세요|해줘요?|해라|하라|해요|해봐|해야|해|하자|하|" +
  "가라|가자|시켜라?|라|요|야|가|" +
  W0 + "(?:all|now|please|immediately|everyone|everybody|units?|army|troops|the|to|back|up|right)" + W1,
);
const ABILITY_RULES = [
  ["micro:siege_mode", "unsiege", re("언\\s*시즈|(?:시즈|siege)\\s*(?:모드|mode)?\\s*(?:풀|해제)|unsiege"), ["TERRAN_SIEGETANK"]],
  ["micro:siege_mode", "siege_mode", re("시즈|시[지재]\\s*모드|" + W0 + "siege"), ["TERRAN_SIEGETANK"]],
  ["micro:stimpack", "marine_stimpack", re("(?:마린|해병|marine).{0,3}(?:스팀|stim)"), ["TERRAN_MARINE"]],
  ["micro:stimpack", "marauder_stimpack", re("(?:불곰|marauder).{0,3}(?:스팀|stim)"), ["TERRAN_MARAUDER"]],
  ["micro:stimpack", "stimpack", re("스팀|" + W0 + "(?:stim|steam\\s*p[ae]c?k?)" + W1), []],
];
const PROD_VERB = re("생산|뽑|찍|계속|" + W0 + "(?:keep\\s+making|start\\s+producing|build\\s+more)");
const RESEARCH_WORD = re("연구|업그레이드|" + W0 + "(?:research|upgrade)");
const STRUCT_WORD = re("아카데미|병영|공장|우주공항|보급고|사령부|정제소|" + W0 + "(?:barracks|factory|starport|academy|depot)");
const MICRO_BLOCK = re(
  "클로킹|투명|전술핵|이엠피|야마토|메디박|의료선|지뢰|자동\\s*포탑|저격|간섭|" +
  "후퇴|퇴각|철수|물러나|도망|홀드|제자리|멈|정지|그만|스톱|스탑|취소|중단|중지|일꾼|" +
  W0 + "(?:cloak|decloak|nuke|emp|yamato|medivac|mine|snipe|retreat|hold|stop|halt|cancel|abort|workers?|scv)",
);
const RAW_CONTROL = re(
  "좌표|" + W0 + "coordinates?" + W1 + "|" + W0 + "x\\s*=\\s*\\d|" + W0 + "y\\s*=\\s*\\d|" +
  "\\(\\s*\\d+\\s*[,，]\\s*\\d+\\s*\\)|\\d+\\s*[,，]\\s*\\d+\\s*(?:으로|로)" + W1 + "|" +
  "유닛\\s*(?:태그|id|아이디)|" + W0 + "unit\\s*(?:tag|id)" + W1 + "|태그\\s*\\d|" +
  "클릭|" + W0 + "click" + W1 + "|컨트롤\\s*그룹|" + W0 + "control\\s*group" + W1 + "|" +
  "s2client|python-?sc2|" + W0 + "api" + W1 + "|파이썬|" + W0 + "python" + W1 + "|함수\\s*호출",
);
const INJECTION = re(
  W0 + "(?:ignore|disregard|forget)" + W1 + ".{0,40}" + W0 + "(?:instructions?|rules?|prompt)" + W1 + "|" +
  W0 + "(?:developer|debug|dan)\\s+mode" + W1 + "|" + W0 + "system\\s+prompt" + W1 + "|" + W0 + "jailbreak" + W1 + "|" +
  "(?:이전|위의|앞의|기존)\\s*(?:지시|지침|규칙|명령)(?:은|는|를|을|들)?\\s*(?:모두\\s*|전부\\s*)?(?:무시|잊)|" +
  "(?:규칙|지시|지침)(?:은|는|를|을)?\\s*(?:모두\\s*|전부\\s*)?무시|" +
  "시스템\\s*프롬프트|개발자\\s*모드|제한\\s*해제",
);
const PUNCT = /[.!?~…,，。]+/g;

function normalize(text) { return String(text).split(/\s+/).filter(Boolean).join(" "); }

function matchEmergency(bare) {
  let remaining = bare;
  const actions = [];
  for (const [action, pattern] of EMERGENCY_RULES) {
    if (pattern.test(remaining)) { actions.push(action); remaining = remaining.replace(new RegExp(pattern.source, "gi"), " "); }
  }
  if (actions.length !== 1) return null;
  if (remaining.replace(new RegExp(FILLER.source, "gi"), " ").trim()) return null;
  const [flag, family] = EMERGENCY_FLAGS[actions[0]];
  return { family, action: actions[0], flag, ability: "" };
}

function matchMicro(text) {
  if (RESEARCH_WORD.test(text) || STRUCT_WORD.test(text) || ATTACKISH.test(text) || PROD_VERB.test(text) || CONTRA.test(text) || MICRO_BLOCK.test(text)) return null;
  const hits = ABILITY_RULES.filter(([, , pattern]) => pattern.test(text));
  if (!hits.length) return null;
  const [family, ability, , units] = hits[0];
  if (hits.some(([other]) => other !== family)) return null;
  return { family, action: "", flag: "", ability, unit_classes: units.slice() };
}

// -> null | { family, action ("retreat"|"hold"|"stop"|""), flag, ability, unit_classes? }
function matchFastRule(transcript) {
  if (typeof transcript !== "string") return null;
  const text = normalize(transcript);
  if (!text || [...text].length > 80 || text.includes("?") || text.includes("？")) return null;
  if (NEG.test(text) || RAW_CONTROL.test(text) || INJECTION.test(text)) return null;
  const bare = normalize(text.replace(PUNCT, " "));
  if (!bare) return null;
  const hit = matchEmergency(bare) || matchMicro(text);
  return hit && PROMOTED_FAMILIES.includes(hit.family) ? hit : null;
}
module.exports = { matchFastRule, PROMOTED_FAMILIES };
