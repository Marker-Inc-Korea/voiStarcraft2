const test = require("node:test");
const assert = require("node:assert/strict");
const Msg = require("../sketch/messages.js");

test("helper status maps to a Korean explanation + a one-word state, and says what to do", () => {
  const denied = Msg.helperMessage({ state: "denied", reason: "input-monitoring" }, "Control+Alt+D");
  assert.equal(denied.word, "권한 필요");
  assert.match(denied.text, /입력 모니터링/); assert.match(denied.text, /Control\+Alt\+D/); assert.match(denied.text, /시스템 설정/);
  assert.equal(Msg.helperMessage({ state: "ok" }, "x").word, "감지 중");
  assert.equal(Msg.helperMessage({ state: "missing" }, "x").word, "미설치");
  assert.match(Msg.helperMessage({ state: "missing" }, "x").text, /native\/build\.sh/);
  assert.equal(Msg.helperMessage({ state: "failed", reason: "crash-loop" }, "x").word, "중단됨");
  assert.equal(Msg.helperMessage({ state: "starting" }, "x").word, "시작 중");
  assert.equal(Msg.helperMessage({ state: "weird" }, "x").word, "알 수 없음");
});
test("screen capture status says that no image is sent when it is not permitted", () => {
  const no = Msg.captureMessage({ permission: "denied", enabled: true });
  assert.equal(no.word, "이미지 없음"); assert.match(no.text, /화면 기록/); assert.match(no.text, /이미지 없이/);
  assert.equal(Msg.captureMessage({ permission: "granted", enabled: true }).word, "사용 가능");
  assert.equal(Msg.captureMessage({ permission: "granted", enabled: false }).word, "꺼짐");
  assert.equal(Msg.captureMessage({ permission: "not-determined", enabled: true }).word, "이미지 없음");
});
test("calibration status distinguishes the documented default guess from a fitted rect", () => {
  assert.match(Msg.calibrationMessage({ calibrated: false }).text, /추정/);
  assert.equal(Msg.calibrationMessage({ calibrated: false }).word, "추정값");
  assert.equal(Msg.calibrationMessage({ calibrated: true }).word, "맞춤");
});
test("HUD result lines are short Korean sentences for each outcome", () => {
  assert.match(Msg.resultLine({ kind: "cancel", reason: "key" }), /취소/);
  assert.match(Msg.resultLine({ kind: "failed", error: "Commander HTTP 500" }), /전송하지 못/);
  assert.match(Msg.resultLine({ kind: "failed", error: "Commander HTTP 500" }), /아무것도 실행하지 않/);
  assert.match(Msg.resultLine({ kind: "no-strokes" }), /선이 없/);
  assert.match(Msg.resultLine({ kind: "sent" }), /해석/);
});
