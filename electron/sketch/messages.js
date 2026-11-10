"use strict";
// Korean user-facing text for the sketch feature (settings status rows and HUD result lines). Pure strings, no I/O.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiSketchMessages = factory();
})(typeof self !== "undefined" ? self : this, function () {
  function helperMessage(status, fallbackAccelerator) {
    const fb = fallbackAccelerator || "Control+Alt+D";
    switch (status && status.state) {
      case "ok": return { word: "감지 중", text: "Control+Option(⌃⌥)을 누르고 있는 동안 미니맵에 그릴 수 있습니다." };
      case "starting": return { word: "시작 중", text: "키 감지 도우미를 시작하는 중입니다." };
      case "denied": return { word: "권한 필요", text: `키 감지에 '입력 모니터링' 권한이 필요합니다. 시스템 설정 > 개인정보 보호 및 보안 > 입력 모니터링에서 이 앱(또는 chord_helper)을 허용해 주세요. 허용 전에는 단축키 ${fb} 로 그리기를 켜고 끌 수 있습니다.` };
      case "missing": return { word: "미설치", text: `키 감지 도우미가 빌드되지 않았습니다. 'sh electron/native/build.sh' 를 실행해 주세요. 그동안은 단축키 ${fb} 로 그리기를 켜고 끌 수 있습니다.` };
      case "failed": return { word: "중단됨", text: `키 감지 도우미가 반복해서 종료되어 멈췄습니다. 단축키 ${fb} 는 계속 사용할 수 있습니다.` };
      case "stopped": return { word: "꺼짐", text: "키 감지 도우미가 꺼져 있습니다." };
      default: return { word: "알 수 없음", text: "키 감지 상태를 알 수 없습니다." };
    }
  }
  function captureMessage({ permission, enabled }) {
    if (!enabled) return { word: "꺼짐", text: "미니맵 이미지를 보내지 않습니다. 그린 선의 좌표만 전달됩니다." };
    if (permission === "granted") return { word: "사용 가능", text: "그린 선이 포함된 미니맵 이미지를 함께 보냅니다." };
    return { word: "이미지 없음", text: "화면 기록 권한이 없어 이미지 없이 선의 좌표만 전달합니다. 시스템 설정 > 개인정보 보호 및 보안 > 화면 기록에서 이 앱을 허용하면 이미지도 함께 보냅니다." };
  }
  const ACCESS_WARNING = "손쉬운 사용 권한이 없어 그리는 동안 게임 포커스가 잠깐 넘어갈 수 있습니다";
  const ACCESS_HOW = "시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용에서 이 앱(또는 이 앱을 실행한 터미널)을 허용한 뒤 '다시 시도'를 눌러 주세요.";
  // granted: true | false | null (not reported yet)
  function accessibilityMessage(granted) {
    if (granted === false) return { granted: false, word: "권한 필요", text: `${ACCESS_WARNING}.`, how: ACCESS_HOW };
    if (granted === true) return { granted: true, word: "허용됨", text: "그리는 동안 미니맵 안의 클릭만 가로채므로 게임이 포커스를 유지합니다.", how: "" };
    return { granted: null, word: "확인 전", text: "그리기를 처음 시작할 때 확인합니다.", how: ACCESS_HOW };
  }
  function calibrationMessage({ calibrated }) {
    return calibrated
      ? { word: "맞춤", text: "이 모니터와 해상도에 저장된 미니맵 위치를 사용합니다." }
      : { word: "추정값", text: "SC2 기본 화면의 왼쪽 아래 정사각형 미니맵으로 추정한 위치입니다. 실제 미니맵과 어긋나면 '위치 맞추기'로 드래그해서 맞춰 주세요." };
  }
  function resultLine(result) {
    switch (result && result.kind) {
      case "cancel": return "그리기를 취소했습니다. 아무것도 실행하지 않았습니다.";
      case "no-strokes": return "그려진 선이 없어 아무것도 하지 않았습니다.";
      case "failed": return "그림을 전송하지 못했습니다. 아무것도 실행하지 않았습니다.";
      case "sent": return "그린 경로를 해석하는 중입니다…";
      default: return "";
    }
  }
  return { accessibilityMessage, helperMessage, captureMessage, calibrationMessage, resultLine };
});
