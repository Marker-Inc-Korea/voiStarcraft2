# 승인된 Direct SC2 명령 계획과 구현 현황

## 최신 재개 상태 — 2026-10-16

아래 날짜별 기록은 과거 증거이며, 현재 완료 판정은 이 요약을 우선한다.
원문 계획은 `approved-direct-command-plan-verbatim.md`에 그대로 보존한다.

- PR #187: 아직 main에 머지되지 않았다. head `0584f46`의 hosted 재실행에서
  Python 3.10/3.11/3.12 unit-contracts는 모두 통과했다. 후보/신뢰 검증기
  producer-isolation은 CTest 4.4.3에서 exact 10/10 증거 판정 실패 상태다.
  배포 검증은 기존 QA 헤더/스크립트 네 개의 명시적 허용 목록 누락을 발견했다.
- Electron #190: `electron/`에 Main/Preload/Renderer 셸을 추가했다.
  loopback-only 연결, 입력 allowlist, sender 검증, sandbox/context isolation,
  CSP, TypeScript API 선언 및 bounded history 조회를 제공한다.
  Node 테스트 4개 통과. 실제 Electron UI 실행·기존 UI parity·실게임 E2E는 미검증이다.
- C++ #192: 네이티브 Direct lease 이벤트 소비 helper와 Python executor의
  opt-in 이벤트 writer를 추가했다. runtime epoch 고정, 중복/오래된 이벤트 거부,
  구분자 주입 거부를 테스트했다. **upstream 게임 루프 연결은 아직 없다.**
  따라서 C++ MicroMachine 자율제어 재개 완료를 의미하지 않는다.
- JEV #189: 비객체 응답을 typed `invalid`로 거부하고 protocol version,
  reason type, 유한 timeout을 검증한다. 실제 외부 endpoint/auth는 미설정이다.
- 위치/분대/수리 #194: target-pin registry 값을 caller metadata가 위조하지
  못하도록 보강했다. 실제 map/buildability·repair 재선택·squad observation은 미검증이다.
- API #195: 기존 bounded catalog와 raw API 거부를 유지한다. `alert`,
  `chat_send`, Client/protocol API 및 모든 ability 실게임 검증은 완료되지 않았다.
- 재검증: Direct/JEV/executor/bridge/API 집중 테스트
  `145 passed, 127 subtests passed`; Electron Node tests `4 passed`.
  이는 계약/단위 테스트 증거이며 실게임 증거가 아니다.
- Goal: 기존 thread Goal은 `blocked`다. 확장 목표를 새 Goal로 생성하려 했으나
  `cannot create a new goal because this thread has an unfinished goal`로 거부됐다.
  완료되지 않은 기존 Goal을 완료로 바꾸거나 Goal DB를 직접 수정하지 않는다.

확장 목표의 추적 원장은 issue #196이며 #188–#195의 완료 조건을 포함한다.
최종 종료 조건은 필수 CI 통과 후 main 머지, Electron parity, 실제 SC2
명령/건설/생산/카메라/TTL/취소/실패/lease 해제/C++ 재개 증거,
실제 JEV 운영 계약, API 범위별 구현 또는 명시적 미지원 근거다.
계약 테스트 통과를 이 종료 조건의 대체물로 사용하지 않는다.

이 문서는 Codex 세션 `019fb8fc-d26e-73e3-8d8b-6686c71501c6`에서 사용자가 승인한
계획을 기준으로 작성한 구현 대조표다. 이 문서의 목적은 “구현했다”는 표현을
실제 코드·테스트·런타임 증거와 분리해서, 승인된 항목을 하나씩 완료 여부로
보고하는 것이다.

기준 브랜치는 `codex/llm-command-speedup`, 기준 PR은
[#187](https://github.com/Marker-Inc-Korea/voiStarcraft2/pull/187)이다. 현재
이 문서가 기록하는 실행 코드 evidence snapshot은 `2c6a841`다. 문서 갱신 커밋은
이 snapshot 이후 별도로 기록된다. `2c6a841`은 builder·producer·researcher·warp structure의 concrete
tag ownership을 adapter dispatch와 dependent workflow child lease까지 확장한 코드
커밋이다. 앞선 코드
구현 커밋 `43a834b`·`838dd59`에는 수리 대상 모호성 거부, live-session
clarification prompt 전파, semantic Direct capability 확장, live unit-tag pin이
포함돼 있다. 2026-09-28 KST 확인 시 PR #187은 `OPEN / BLOCKED`다.

2026-09-29 현재 원격 재확인 기준 PR #187의 head는 `5934022`
(`docs: record latest hosted verification`)이며 상태는 여전히 `OPEN / BLOCKED`다.
최신 check run `36404286959`의 `unit-contracts` Python 3.10/3.11/3.12는 모두
실패했고, `pre-live-producer-isolation`도 실패했다. `pre-live-build`,
`event-admission`, `ready-to-merge`는 성공했지만 deterministic journeys,
browser accessibility, provenance, distribution 등은 skipped이므로 전체 CI green이나
merge 가능 상태가 아니다.

이 Hosted run은 원격 head `5934022`에서 실행됐다. 현재 working tree의
Direct-only 경계·typed projection fixture·chat fake-runtime 보강은 아직
커밋/푸시하지 않았으므로, Hosted 실패 수치를 현재 working tree의 최신 집중
회귀 결과와 혼동하지 않는다. 커밋·push·merge는 사용자 지시가 없어 수행하지
않았다.

실행 코드 `2c6a841` 기준 최신 Hosted `ci` 결과는 run `36403412568`다.
문서 갱신 commit `4c00c98`에서 확인한 최신 pre-live 결과는
`pre-live-provenance` run `36403410273`, `final-pre-live` run `36403410317`이다.
`36403410273`은
`pre-live-build`가 실행 중/후속 상태가 결정되지 않은 시점에
`pre-live-producer-isolation`이 실패했으며, 실패 로그의 trusted MicroMachine
CTest는 `passed=0, total=10, failures=10`이었다. `36403410317`의
`final-pre-live`는 `event-admission`과 `ready-to-merge`만 실제 실행되어
success였고 build identity, provenance, deterministic journeys, browser
accessibility, distribution 등 후속 job은 skipped였다. 따라서 전체 CI
green·merge 가능·실게임 검증을 주장하지 않는다. 실제 StarCraft II Live QA는
완료 증거가 없다.

2026-09-30 text/live lifecycle 보강: `SC2CommandSession`에 명시적
`direct_lifecycle` seam을 연결하면 MCP와 동일하게 concrete unit tag를 lease
admission 전에 고정하고 `pending -> dispatched -> active`를 기록한다. 실행
실패는 `failed`로 해제되고, `on_step`에서 공유 executor의
`tick_direct_commands()`가 completion/TTL을 관찰해 제어권을 반환한다. 실제
`demo_sc2.run_live()`는 이 shared lifecycle을 주입하며, dry-run 및 기존
커스텀 테스트 session은 opt-in seam을 사용해 legacy executor double을
변경하지 않는다. 증거: `tests/test_live_pipeline.py::LivePipelineIntegrationTest::test_explicit_text_session_uses_shared_direct_lifecycle`,
`174 passed, 3 skipped` demo/live pipeline suite.

TUI `thread/goal/set failed in TUI` 재검증(읽기 전용): `goals_1.sqlite`
`PRAGMA integrity_check=ok`, 현재 thread goal은 `active`, 공식
`thread/goal/get` RPC는 정상 응답했고 새 `goal/set`/ENOSPC 오류는 재현되지
않았다. 과거 `No space left on device (os error 28)`는 공용 저장 공간 부족의
정황이지만, 해당 로그는 별도 Product Lemon thread의 rollout 저장 실패다.
이 SC2 thread의 실패한 goal RPC와 직접 연결된 증거는 없어 원인을 확정하거나
영구 해결을 주장하지 않는다. Codex session metadata, DB, lock, process는
직접 수정하지 않았다. 공식 계약 출처:
[OpenAI App Server — Manage a thread goal](https://learn.chatgpt.com/docs/app-server#manage-a-thread-goal).

원문 보존 검증: 원문 부록의 8개 메시지를 원본 JSONL의 해당 메시지 본문과
문자열 전체 비교하여 모두 일치함을 확인했다(공백·줄바꿈·오타 포함).
계획 메시지 199849의 SHA-256은
`4d9c34e16359823719f37860c1905b403435f3e1277dc92bd7acdbc76b51f5ae`,
199881은 `94b61e4a49ff75fac68be103693c020a1fdce746f932e5dfc136a4400fb92977`이다.
원본 세션은 읽기만 했으며 수정하지 않았다. 이 보고서는 세션 전체의 모든
주제가 아니라 최종 승인된 Direct 계획의 이행 여부를 다룬다.

## 1. 사용자가 승인한 최종 구조

세션에서 확정된 구조는 다음과 같다.

```text
사용자 명령
  -> LLM/System 1이 command_layer와 대상·위치·분대·완료조건을 해석
      ├─ macro
      │   -> MicroMachine bias/정책 조절만 publish
      │
      └─ operation / micro / emergency
          -> MicroMachine 명령 무시
          -> Direct SC2 MCP tool 호출
          -> 상태 관찰
          -> 완료 / 취소 / TTL 만료
          -> Direct 제어권 해제
          -> 기본 MicroMachine 자율제어 재개
```

사용자가 명시한 구분은 다음과 같다.

| 명령 | 승인된 처리 |
| --- | --- |
| `macro` | MicroMachine의 의사결정·가중치·운영방침에 bias를 준다. |
| `operation` | 분대 정찰·공격·방어·재집결을 Direct SC2로 실행한다. |
| `micro` | Stim·Siege·Unsiege·EMP·전술핵·Yamato 등 능력을 Direct SC2로 실행한다. |
| `emergency` | Stop·Hold·Retreat 등 긴급 명령을 Direct SC2로 즉시 실행한다. |

따라서 `operation / micro / emergency`를 MicroMachine bias로 다시 변환하거나,
Direct 실행과 MicroMachine publish를 동시에 하는 것은 승인된 최종 구조가 아니다.

## 2. 승인된 세부 요구사항 원문 기준

### 2.1 Direct와 Macro의 판단 기준

사용자는 한 번 실행할 명시적 행동은 Direct로, 지속적인 운영방침은 Macro로
구분하기로 했다.

- “팩토리 지어” → Direct `build_structure`
- “SCV 3기를 가스에 붙여” → Direct `assign_workers`
- “마린 6기를 적 앞마당으로 보내” → Direct `move_group` 또는 `attack_move`
- “앞마당 입구의 벙커 수리해” → Direct `repair`
- “팩토리 중심으로 운영하고 탱크를 우선 생산해” → Macro → MicroMachine bias
- “계속 마린을 생산해” → Macro → MicroMachine bias

### 2.2 SC2 기능의 MCP tool calling

사용자는 “SC2 API에 있는 모든 기능들을 tool calling할 수 있도록 MCP로
만들어두고, micro 명령에서 바로 쓸 수 있도록” 요청했다.

현재 구현은 raw python-sc2 객체 전체를 무제한으로 노출하지 않고, 검증 가능한
semantic capability catalog를 MCP로 공개한다. 이것은 구현 안전장치이지,
사용자가 승인한 “sc2api에 있는 모든 기능” 요구를 현재의 bounded catalog로 축소한 승인이
아니다. 각 도구는 입력 schema, semantic subject/target, 런타임 필요 여부,
성공·부분 성공·거부 결과를 갖고, 임의의 메서드명이나 raw unit tag를 LLM이
직접 호출하지 않는다.

현재 등록된 semantic capability 목록은 다음이다. 이 목록 밖의 SC2 API 기능은
아직 미완료로 판정한다.

- 일꾼 자원 배정
- 명시적 자원 채취
- 구조물 건설
- 유닛 생산
- 업그레이드 연구
- 워프인 생산
- 분대 이동
- 분대 공격 이동
- context-aware Smart 명령
- 분대 순찰
- 자원 반납
- 수리
- 유닛 능력 사용
- 게임 상태 관찰
- 카메라 이동
- Stop
- Hold position
- 능력 가용성 조회(query ability)
- 유닛 계산·술어 조회(query unit)
- 게임 계산·비용·진행률 조회(query game)
- 지도 지형·시야·경로·확장 위치 조회(query map)
- 건설 위치 검증(query placement)
- 명시적 가스 건설(build gas)
- 일꾼 재배치(distribute workers)
- 검증된 확장(expand now)
- Retreat

### 2.3 위치 지정

사용자가 좌표나 내부 unit tag를 입력하지 않아도 되도록 semantic 위치를
사용한다.

지원해야 하는 표현은 본진, 본진 입구, 앞마당, 앞마당 입구, 3멀티, 미네랄
라인, 적 본진, 적 앞마당, 적 입구, 적 미네랄 라인, 마지막으로 본 적 위치,
정찰 위치 등이다.

상대적 건설 위치는 다음 의미 구조로 해석한다.

```json
{
  "anchor": "self_ramp",
  "relation": "near",
  "direction": "left",
  "distance": 3
}
```

Map Resolver가 실제 좌표를 계산하고, SC2 런타임이 장애물·건설 가능 여부·적
위협을 검증한다. 자연어로 충분하지 않은 경우에는 컨트롤러의 target pin을
사용할 수 있어야 하며, 게임 좌표 변환·검증·저장 후 Direct 명령에 사용한다.

### 2.4 분대와 수리 대상

사용자는 `1분대`, `방어 분대`, `정찰 분대`, `주력 분대` 같은 이름으로
명령하고, 내부 unit tag를 알 필요가 없다. Direct executor가 현재 관찰값과
squad registry를 이용해 실제 유닛을 선택한다.

수리는 다음 순서로 대상을 찾는다.

1. 아군 소유인지 확인한다.
2. 손상 여부를 확인한다.
3. 사용자가 말한 이름과 매칭한다.
4. 손상된 구조물을 먼저 찾는다.
5. 없으면 손상된 아군 유닛을 찾는다.
6. 수리 가능한 SCV를 선택한다.
7. 여러 대상이 모호하면 임의 선택하지 않고 다시 묻는다.

### 2.5 병렬 실행과 선행조건

독립적인 Direct tool은 한 번의 tool-calling 요청에서 병렬 실행한다. 같은
분대에 충돌하는 명령, emergency, 또는 선행조건이 있는 명령은 순차 실행한다.

예를 들어 SCV 가스 배정과 정찰 분대 이동은 병렬 실행할 수 있지만, “팩토리
짓고 탱크 생산해”는 다음 순서여야 한다.

```text
팩토리 건설 -> 팩토리 완성 관찰 -> 탱크 생산
```

### 2.6 완료·취소·TTL과 제어권 반환

Direct 명령은 다음 수명 상태를 가져야 한다.

```text
pending -> dispatched -> active -> completed / expired / cancelled / failed
```

완료조건에는 유닛 수 충족, 건설 시작, 건설 완료, 명령 발행, 목표 도착,
적 발견, 적 구조물 파괴 확인, 후퇴 확인, 능력 사용 완료, 사용자 취소,
TTL 만료가 포함된다. `enemy_destroyed`는 명시적 runtime receipt 또는
완전한 시야에서 baseline 대비 visible enemy structure 감소가 확인될 때만
참으로 기록하며, fog/불완전 관찰에서는 추측하지 않는다.

Direct 명령이 완료·취소·실패·TTL 만료되면 해당 분대의 Direct 제어권을
해제하고 기본 MicroMachine 자율제어가 재개되어야 한다. 런타임이 없을 때는
MicroMachine으로 몰래 대체하지 않고 `runtime_not_attached`로 실패해야 한다.

### 2.7 System 1 → JEV

외부 endpoint·인증·transport를 저장소가 임의로 가정하지 않도록, JEV를
주입 가능한 authorization boundary로 구현했다. deterministic
`route_policy_vector()`가 항상 먼저 bounded local route를 만들고, JEV는
eligible layer에서 그 route를 `allow`/`deny`만 결정한다. JEV 응답으로 raw
SC2 method, tool call, plan, policy vector를 공급할 수 없으며, timeout·retry와
unavailable/invalid 응답은 fail-closed다. endpoint가 없거나 layer가 대상이
아니면 기존 local route를 보존한다.

- 계약 구현: `starcraft_commander/jev_router.py`
- public API: `JEVRouter`, `JEVRouteRequest`, `JEVRouteDecision`,
  `route_policy_vector_with_jev`
- fake endpoint 계약 테스트: `tests/test_jev_router.py` (10 passed)
- 이슈: [#189](https://github.com/Marker-Inc-Korea/voiStarcraft2/issues/189)
- 남은 외부 의존: 실제 JEV transport/auth/request schema와 endpoint 운영 계약,
  그리고 실제 SC2 Live QA는 #189 합의 및 환경 없이는 완료로 주장하지 않는다.

## 2.8 승인된 두 assistant 계획의 번호별 대조

아래 인용은 [원문 부록](approved-direct-command-plan-verbatim.md)의 번호와
문구를 유지한다. 각 항목의 “완료(코드/계약)”는 해당 테스트가 증명하는 범위만
뜻한다. 실제 SC2 실행이나 전체 승인 계획 완료를 뜻하지 않는다. 계획 본문,
예시, 표와 설명 전체는 원문 부록에 생략 없이 보존했다.

### 첫 번째 계획(원문 199849)의 7개 항목

#### A1 — 완료(코드 경계)

> 1. 라우터에서 `operation / micro / emergency`의 `micromachine.*` 호출을 제거합니다.

`route_policy_vector()`의 non-macro route는 `sc2.direct.execute`만 만들고
live session도 non-macro를 blackboard에 publish하지 않는다.
증거: `test_non_macro_route_is_direct_only`,
`test_legacy_micromachine_tactical_tools_are_removed`.

#### A2 — 부분 완료

> 2. 모든 Direct SC2 기능을 MCP tool로 등록합니다.
>    - 이동, 공격, 정찰, 방어, 재집결
>    - 건설, 생산, 일꾼 배정, 수리
>    - 능력 사용
>    - 카메라 이동
>    - 게임 상태 관찰
>    - Stop/Hold/Retreat

- 이동·공격·순찰: `move_group`/`attack_move`/`patrol` 등록. 정찰·방어·재집결은 tactical task lowering으로 연결되며 별도 전 기능 도구가 아니다.
- 건설·생산·일꾼 배정·수리: 각 semantic tool과 adapter 등록. repair/worker 자연어 요청부터 실행까지의 전체 연결은 미완료.
- 명시적 자원 채취·자원 반납: `gather_resource`/`return_resource`와 adapter의 `Unit.gather`/`Unit.return_resource` 호출을 등록했다. 실제 게임 검증은 미완료.
- 업그레이드 연구·워프인: `research_upgrade`/`warp_in`과 adapter의 `Unit.research`/`Unit.warp_in` 호출을 등록했다. 업그레이드·프로토스 구조물의 실제 런타임 검증은 미완료.
- 능력 사용: `execute_ability` 등록. 모든 ability의 target/caster/효과 확인을 입증한 것은 아니다.
- 카메라 이동: `move_camera` 등록 및 fake runtime 호출 검증. 실제 카메라 검증 없음.
- 게임 상태 관찰: `observe`/state resolver 등록. 실제 게임 관찰 QA 없음.
- Stop/Hold/Retreat: `stop_group`/`hold_position`/`retreat` 등록. 실제 긴급 제어 QA 없음.

현재 provider/MicroMachine 정책 DSL은 25개 Direct task 값(24개 non-ability
semantic task와 `execute_ability`)을 허용한다. fake compact provider의
응답을 compiler → `PolicyModulationVector` → `route_policy_vector`까지 통과시키는
계약 검증이 이 25개를 다룬다. 이는 MCP 직접 호출과 provider 자연어 경계가
연결됐다는 뜻이지, raw SC2 API 전체 노출이나 실제 runtime·Live QA가 완료됐다는
뜻은 아니다.

증거: `test_mcp_capability_catalog_and_direct_tools_are_complete`,
`test_top_level_operation_tasks_and_one_off_build_lower_to_direct_actions`,
adapter contract tests. 테스트명의 “complete”는 현재 catalog 안의 등록 누락이
없다는 뜻이지 SC2 API 전체 지원이라는 뜻이 아니다.

#### A3 — 부분 완료

> 3. 각 Direct 명령에 `command_id`, 대상 분대, 발행 프레임, 만료 프레임, 완료조건을 붙입니다.

`DirectCommandLease`에 명령 ID·frame·TTL·완료조건·`owned_subjects`가 있고, live
python-sc2 Unit/structure에 tag가 있으면 dispatch 전에 현재 선택된
`owned_unit_tags`를 고정한다. 이제 builder·train producer·researcher·warp
structure도 같은 concrete tag authority를 사용하며, dependent workflow의
child step은 완료 관찰 뒤 현재 producer observation에서 다시 bind한다. 같은
tag의 중복 lease는 거부하고, 종료 후에만 재점유할 수 있다. tag가 없는
offline fake는 semantic subject ownership으로만 동작한다. 실제 Live unit tag
ownership은 게임에서 아직 검증하지 않았다.
증거: `test_direct_lifecycle_records_subject_ownership_and_rejects_overlap`,
`test_direct_lifecycle_reserves_concrete_unit_tags_and_releases_them`,
`test_bound_group_tracks_original_tags_across_observation_reordering`,
`RegistryConcreteOwnershipIntegrationTest`.

#### A4 — 부분 완료(코드+fake contract)

> 4. 게임 루프가 매 프레임 명령 상태를 확인합니다.
>    - 목표 도착
>    - 구조물 완성
>    - 적 생산시설 파괴
>    - 능력 사용 확인
>    - 후퇴 완료
>    - TTL 만료

`SC2RuntimeExecutor.tick_direct_commands()`와 demo `BotAI.on_step` 연결이 있다.
이것은 `on_step` 호출마다의 tick이며 실제 모든 게임 프레임 관찰을 검증하지 않았다.

- 목표 도착: 관찰 위치와 목적지 거리 검사. concrete command-target tag pinning은
  adapter/lease 계약으로 구현됐지만 실제 게임 관찰과 Live QA는 없다.
- 구조물 완성: baseline 대비 건설 상태/수량 관찰. fake contract 통과, 실제 게임 미검증.
- 적 생산시설 파괴: `enemy_destroyed` receipt 또는 완전한 시야의 구조물 감소 관찰. 특정 생산시설 파괴에 대한 전체 게임 증거 없음.
- 능력 사용 확인: runtime이 제공한 증거를 받는 경로. 모든 능력의 자동 효과 관찰기 아님.
- 후퇴 완료: 본진 도착 관찰 경로. 실제 후퇴 QA 없음.
- TTL 만료: lifecycle tick의 frame 기준 만료 및 release 계약 테스트 통과.

증거: adapter의 `direct_command_evidence()`, executor의
`test_runtime_executor_supplies_adapter_evidence_to_lifecycle`,
`test_runtime_executor_ticks_shared_direct_lifecycle`.

#### A5 — 부분 완료

> 5. 완료·취소·만료되면 Direct 명령의 제어권을 해제하고 MicroMachine이 기본 자율제어를 재개합니다.

terminal lease의 `control_owned=false`, release callback, ownership registry가 있다.
`resume_micromachine(lease)`는 host callback에 위임하며 callback이 없으면 no-op이다.
따라서 실제 MicroMachine 자동 재개 연결은 미완료이고 Live QA도 없다.
증거: `test_terminal_direct_release_calls_host_micromachine_resume_seam`.

#### A6 — 완료(스케줄러 계약), 실게임 미검증

> 6. 서로 독립적인 tool은 병렬 실행하고, 같은 분대에 충돌하는 명령이나 emergency는 순차 실행합니다.

`call_many_async()`의 독립 gather, emergency/복합 plan 직렬화와 subject 충돌
검사가 있다. 실행 시 소유권이 충돌하면 거부할 수 있다. 실제 mixed squad의
unit-tag 중복 제어까지 해결한 것은 아니다.
증거: `test_call_many_async_runs_independent_tools_in_parallel`,
`test_call_many_async_serializes_emergency_after_independent_batch`,
`test_call_many_async_detects_embedded_plan_subject_conflicts`.

#### A7 — 완료(코드 경계)

> 7. Direct SC2 런타임이 없으면 MicroMachine으로 몰래 대체하지 않고 `runtime_not_attached`로 실패시켜야 합니다.

executor/BotAI가 없으면 Direct 실패를 반환하며 non-macro를 MicroMachine으로
우회 publish하지 않는다. 증거:
`test_registry_supports_ordered_call_many_and_runtime_not_attached`,
`test_lifecycle_execute_without_bound_bot_returns_structured_failure`.

### 두 번째 계획(원문 199881)의 8개 항목

#### B1 — 완료(코드 경계)

> 1. `operation / micro / emergency`에서 MicroMachine publish 제거

A1과 같은 구현·증거다. 종전 표의 “명시적 1회 행동은 Direct, 지속 운영방침은
Macro bias”는 계획 본문의 기준이지만 번호 1의 원문은 아니므로 정정했다.
그 자연어 구분의 전체 LLM 실행 경로 검증은 별도로 부분 완료다.

#### B2 — 완료(도구 계약), 실게임 미검증

> 2. Direct MCP 도구에 `move_camera`, `stop`, `hold`, `retreat` 추가

실제 도구 이름은 `sc2.direct.move_camera`, `sc2.direct.stop_group`,
`sc2.direct.hold_position`, `sc2.direct.retreat`다. catalog/registry/adapter
연결과 emergency lowering 테스트가 있다. Retreat는 `self_main` 이동으로
내려가며 실제 안전한 후퇴나 카메라 이동을 검증한 것은 아니다.

#### B3 — 부분 완료

> 3. 자연어용 squad registry 추가

`DirectCommandRegistry`/`SquadDefinition`은 이름과 unit query를 보존하고,
registry가 설정된 경로에서는 unknown named squad를 거부한다. live adapter는
dispatch 시 semantic selection을 현재 unit tag 집합으로 pin하고 해당 tag가
아닌 새 관찰 unit을 완료 판정에 사용하지 않는다.
`1분대`, `방어 분대`, `정찰 분대`, `주력 분대`를 안정적인 실제 tag 집합에
연결하고 혼합 분대의 소유권을 이전하는 기능은 미완료다.
증거: `test_named_squad_and_target_pin_registry_rejects_unknown_squads`,
`test_mcp_named_squad_resolution_is_strict_when_registry_is_configured`.

#### B4 — 부분 완료

> 4. semantic 위치와 target pin을 통합한 위치 resolver 추가

semantic map resolver, relative placement metadata, target-pin registry와
strict lookup이 있다. 이제 compact companion controller가 bounded semantic
target 선택과 `POST /api/direct/registry/target-pin` 저장 경로를 제공하고,
HTTP 경계에서 별칭을 canonical target으로 바꾸며 좌표/unit tag 메타데이터를
거부한다. 다만 실제 지도 클릭 좌표를 semantic target으로 변환하는 live map
resolver와 게임 내 유효성·장애물·위협 검증은 아직 미완료다. §2.3의 모든
자연어 위치·상대 위치 예제가 실제 지도에서 정확히 해석되는지도 미검증이다.
증거: `test_mcp_target_pin_is_resolved_before_direct_dispatch`와 map/placement 계약.

#### B5 — 부분 완료

> 5. `repair` 대상 선택과 모호성 확인 강화

아군 collection의 손상 대상을 이름으로 매칭하고 구조물 우선·명시적 유닛명
fallback·worker 선택을 수행한다. 여러 대상이면 임의 선택 없이
`ambiguous_repair_target`, 후보와 한국어 질문을 반환한다. `6520abe`에서
live session의 `CLARIFICATION_REQUIRED`와 `compile_result.clarification_prompt`까지
전파했다. 단, session 테스트는 route 응답을 대체한 경계 테스트이며
LLM → 실제 수리 → UI 재질문 → 답변 대상 선택의 end-to-end 증거는 아니다.
공간적 “앞 벙커” 매칭, 선택 답변의 대상 재연결, 기계/생체 수리 가능성 구분과
“마린 한 기 치료해”의 heal 경로도 남아 있다.
증거: `test_ambiguous_repair_target_requires_clarification`,
`test_direct_runtime_clarification_becomes_live_session_status`.

#### B6 — 부분 완료(코드+fake contract)

> 6. 명령 lifecycle 추가

원문의 상태 순서:

```text
pending
  -> dispatched
  -> active
  -> completed / expired / cancelled / failed
```

상태 전이, cumulative evidence, TTL/cancel/fail, subject lease, baseline,
batch observation, runtime executor와 registry의 lifecycle 공유가 있다.
실제 게임의 모든 완료조건 관찰은 A4의 미완료 범위가 남는다.
증거: `test_direct_lifecycle_releases_on_completion_expiry_and_cancel`,
`test_observe_all_ticks_multiple_leases_and_skips_terminal_leases`.

#### B7 — 부분 완료

> 7. Direct 명령이 완료되거나 만료되면 제어권을 해제하고 MicroMachine 기본 AI로 복귀

A5와 같은 판정이다. lease 해제와 callback 테스트는 있으나 실제 MicroMachine
owner가 해당 유닛의 자율제어를 다시 수행하는 연결·Live QA는 없다.

#### B8 — 부분 완료(코드+fake contract)

> 8. 독립 명령은 병렬 실행하되, 선행조건이 있는 명령은 순차 실행

독립 호출 병렬/충돌 직렬화, `register_dependent_plans()`와
`drain_completed_workflows()`가 있다. registry와 runtime의 lifecycle을 공유하고
`building_completed` 관찰 뒤 train을 지연 dispatch하며 중간 resume callback을
보류한다. child workflow도 현재 observation의 concrete producer tag를 다시
bind하고 lease에 기록한다. “SCV로 팩토리 짓고 탱크 생산해”의 실제 SC2
건설·완공·생산은 미검증이다.
“SCV는 가스에 보내고 정찰 분대는 적 앞마당으로 보내”의 scheduler 계약은 있으나
자연어부터 실게임 동시 동작까지 입증한 것은 아니다.
증거: `test_registry_shares_runtime_executor_lifecycle_for_dependent_workflow`,
`test_dependent_workflow_waits_for_parent_completion_before_training`.

### 추가 승인: “모든 SC2 API tool calling”

원문 199888:

> 오케이 지금 계획에 하나더 추가해서 sc2 api에있는 모든 기능들 tool calling할수있도록 mcp로 만들어두고, micro 명령에서 바로 쓸 수있도록 만드는 계획까지 추가해서 문서화 시키고,  goal 만들어서 구현진행하자.

- 모든 SC2 API의 MCP tool calling 및 micro 직접 사용: **미완료**. 현재 26개
  bounded semantic capability(명시적 `retreat`를 `move_group`으로 낮추는 alias 포함)를
  공개한다. 전체 API inventory·지원/미지원 분류·구현과 실제 runtime 검증이 필요하다.
- 해당 계획 문서화: **완료(원문 및 현황 문서)**. 위 요구를 현재 bounded catalog로
  축소 승인받은 것으로 취급하지 않는다. 기존 구현 작업은 진행됐으나 전체 구현
  완료가 아니다.
- goal 생성 및 구현 진행: **goal은 active**, 구현 일부 진행. 최신 `get_goal`이
  active를 반환했고 앞선 `thread/goal/set` 재시도도 성공했지만, Live QA·외부 JEV endpoint 운영 계약·
  전체 raw API 노출 등 외부/추가 작업이 남아 있어 complete로 바꾸지 않았다.
- System 1 → JEV: **bounded 계약 구현 및 fake 테스트 완료**, [#189](https://github.com/Marker-Inc-Korea/voiStarcraft2/issues/189)는 OPEN.
  실제 endpoint·transport/auth/schema 운영 계약과 Live QA는 외부 의존으로 남아 있다.
- PR 작성 및 보고: **PR 작성 완료**, #187은 OPEN/BLOCKED. merge하지 않았다.
- legacy 제거: **기본 MCP tactical route 제거 완료**, 저장소의 모든 과거 문서·테스트·
  MicroMachine 코드를 전부 제거한 것은 아니다. 기존 실패 테스트의 승인 계약 이행은 남아 있다.
- Live QA: **미완료**. 원문 198202에서 사용자가 직접 수행하겠다고 했으며,
  이 보고서는 실제 게임 검증을 대신하지 않는다.

## 3. 항목별 구현 대조 보고

상태의 의미는 다음과 같다.

- **완료**: 코드와 해당 계약 테스트가 있다.
- **부분 완료**: 일부 경로만 구현됐거나 통합·런타임 증거가 없다.
- **미완료**: 승인된 동작과 코드가 일치하지 않는다.
- **외부 의존**: 코드만으로 완료를 주장할 수 없고 실제 런타임·JEV·Live QA가 필요하다.

| 승인 항목 | 현재 상태 | 확인된 증거와 남은 문제 |
| --- | --- | --- |
| LLM이 `macro / operation / micro / emergency`를 분류 | 부분 완료 | `PolicyModulationVector`, provider DSL, `unified_command_router.py`에 계층 분류가 있고 operation/micro/emergency를 deterministic하게 lower한다. 25개 Direct task 값(24개 non-ability + `execute_ability`)은 fake compact provider → compiler → router 계약으로 검증됐지만, 실제 LLM·runtime 전체 경로와 Live QA 증거는 아직 없다. |
| `macro`만 MicroMachine bias | 완료(라우터 기준) | `route_policy_vector()`의 macro 경로는 `micromachine.policy`만 생성한다. 실제 MicroMachine 소비는 별도 런타임 telemetry가 필요하다. |
| `operation` Direct-only | 완료(라우터 기준) / 외부 런타임 미검증 | `route_policy_vector()`가 `sc2.direct.execute`만 생성하며 `micromachine.operation`을 생성하지 않는다. `tests/test_direct_command_architecture.py::test_non_macro_route_is_direct_only`가 이를 고정한다. |
| `micro` Direct-only | 완료(라우터 기준) / 외부 런타임 미검증 | ability가 `sc2.direct.execute` 계획으로만 내려가고 live session은 non-macro를 publish하지 않는다. runtime이 없으면 `DIRECT_FAILED/runtime_not_attached`로 fail-closed한다. |
| `emergency` Direct-only | 완료(라우터 기준) / 외부 런타임 미검증 | Stop/Hold/Retreat 계획이 Direct action으로 lower되고 MicroMachine emergency publish가 없다. emergency TTL은 60초 계약 안에서 검증된다. |
| semantic MCP tool registry | 완료(계약 범위) | `tools/list`, `tools/call`, `voiStarcraft2/tools/call_many`, capability list, registry list, lifecycle, 직접 semantic action tool이 registry와 MCP server에 등록되어 있다. |
| `move_camera` 개별 MCP tool | 완료(계약 범위) | `sc2.direct.move_camera`가 catalog·registry·adapter·executor mapping에 존재한다. 실제 카메라 이동은 SC2 runtime 필요. |
| Stop/Hold/Retreat direct tool | 완료(계약 범위) / runtime 미검증 | `stop_group`, `hold_position`, 명시적 `sc2.direct.retreat` capability와 MCP tool을 제공한다. Retreat는 adapter 의미상 self-main 이동으로 lower되며 emergency 계획은 Stop/Hold/Move를 사용한다. |
| 모든 SC2 기능을 MCP tool calling으로 노출 | **미완료** | 현재 26개 bounded semantic capability(명시적 `retreat` alias와 `query_map` 포함)의 이름·schema·registry·MCP 호출과 fake adapter 계약은 구현했다. `query_ability`, `query_placement`, `query_unit`, `query_game`, `query_map`은 읽기 전용 capability이며, query는 finite allowlist·semantic subject/target resolution·fail-closed 결과 검사를 적용한다. `build_gas`·`distribute_workers`·`expand_now`는 명시적 semantic/범위/소유권 검사를 거친다. 이것은 raw `python-sc2`/s2client 전체 기능이나 실제 Live QA를 완료했다는 뜻이 아니다. |
| semantic 위치와 Map Resolver | 부분 완료 | semantic target alias/map resolver·placement 검증, strict target-pin lookup, companion controller의 semantic pin 저장 UI 및 `POST /api/direct/registry/target-pin`을 구현했다. HTTP 경계는 raw 좌표/unit tag를 거부한다. 실제 map 클릭→좌표 변환·장애물 검증과 live runtime 증거는 없다. |
| 분대 registry | 부분 완료 | `DirectCommandRegistry`/`SquadDefinition`으로 이름·unit query를 보존하고 configured registry에서 unknown named squad를 거부한다. 실제 관찰값 기반 tag 선택과 ownership transfer는 runtime 미검증이다. |
| 수리 대상 자동 선택 | 부분 완료 | adapter의 손상 구조물 우선·명시적 유닛명 fallback·worker 선택, 모호성 거부 및 live-session clarification 전파를 구현했다. companion UI가 이제 동일 update의 semantic clarification prompt를 `확인 필요` 상태·자막·채팅·입력 피드백으로 표시하고 안전한 답변 입력에 focus한다. 실제 UI→답변→재선택 E2E, 공간적 이름 매칭, 기계/생체 구분·heal 경로는 Live QA/추가 구현이 필요하다. |
| 독립 tool 병렬 실행 | 완료(호출 스케줄러 계약) | `call_many_async()`가 독립 호출을 `asyncio.gather`로 병렬 실행하고, 동일 subject/conflict·emergency·다중 action plan은 순차 처리한다. 실제 SC2 동시성은 runtime 미검증이다. |
| 선행조건 순차 실행 | 부분 완료 | ordered plan과 다중 action 순차 실행, runtime executor dependent workflow가 구현됐다. `create_command_tool_registry(direct_executor=...)` 경로의 lifecycle 공유와 build→completion evidence→train 지연 dispatch, child producer concrete tag rebinding을 계약 테스트로 고정했다. 실제 SC2 건설 완료 관찰은 없다. |
| 완료·취소·TTL lifecycle | 부분 완료(코드+fake contract) | `DirectCommandLifecycle`가 pending/dispatched/active/completed/expired/cancelled/failed 계약 상태와 cumulative completion evidence·TTL을 보유하며 MCP/session/웹 브리지/`SC2RuntimeExecutor` lifetime registry에 연결됐다. lease가 원래 action context와 baseline을 보존하고, adapter evidence watcher가 목표 도착·건설·유닛 수·적 발견·적 구조물 파괴·후퇴·runtime 능력 확인을 공급한다. `active_leases()`/`observe_all()`이 여러 lease를 frame tick으로 관찰하고 terminal lease를 재관찰하지 않는다. live demo `on_step`은 TTL/evidence tick과 dependent workflow drain을 호출하지만 실제 Live QA는 없다. |
| Direct 제어권 반환 후 MicroMachine 재개 | 부분 완료 | terminal lease에서 `control_owned=false`가 되고 session/웹 브리지의 `observe_direct_command()`/`observe_direct_commands()`/`tick_direct_commands()`/`cancel_direct_command()`와 executor release callback seam이 ownership 해제를 제공한다. dependent workflow 마지막 단계 뒤 release를 보장하는 fake contract도 있다. 실제 MicroMachine game-loop가 자동으로 재개되는 연결과 Live QA 증거는 없다. |
| runtime 미연결 fail-closed | 완료(코드 경계) | Direct executor가 없거나 BotAI가 없으면 `runtime_not_attached`를 반환하고 non-macro는 MicroMachine으로 우회 publish하지 않는다. |
| SC2 상태 관찰 | 부분 완료 | `observe`/state resolver, 조건별 adapter evidence, batch observation 및 demo `on_step` 연결이 있다. 실제 게임의 모든 조건을 관찰하는 통합과 Live QA는 완료되지 않았다. |
| 카메라 이동 | 부분 완료 | adapter/map resolver/MCP tool은 연결됐다. 실제 SC2 camera API 성공은 live runtime 미검증이다. |
| JEV System 1 라우팅 | 계약 구현 완료 / 외부 endpoint 대기 | deterministic local route를 권위로 두는 `allow`/`deny` authorization boundary, sync/async timeout·retry·fail-closed, eligible layer gate, unsafe response rejection, fake endpoint 10개 테스트를 구현했다. 실제 JEV transport/auth/schema 운영 계약과 Live QA는 #189 대기다. |
| 기본 MCP의 legacy tactical route 제거 | **완료(코드 경계)** | `include_legacy_tools` opt-in과 `micromachine.operation/ability/emergency` registry entries를 제거했다. 기본 registry와 MCP discovery에는 `micromachine.policy`만 남는다. 저장소 전체의 legacy 문서·테스트 제거까지 완료한 것은 아니다. |
| 문서화 | 완료(현재 상태 보고) | 이 문서가 승인 원문 기준, 구조, capability, 위치·분대·수리, 병렬·선행조건, lifecycle, legacy 격리, JEV, PR·Live QA를 항목별로 기록한다. |
| PR 반영 | 부분 완료 | 검증 대상 실행 코드 commit은 `2c6a841`다(이후 문서 갱신 commit은 별도). PR #187은 병합하지 않았다. `ci` run `36403412568`과 `pre-live-provenance` run `36403410273`은 historical hosted snapshots이며 Python 3.10/3.11/3.12 contract 및 pre-live producer isolation 실패를 기록한다. 더 최근 hosted run `36404286959`도 `unit-contracts`와 `pre-live-producer-isolation`이 실패했으므로 전체 검증 완료·merge 가능 상태가 아니다. |
| 실제 Live QA | 미완료 | 이전 PR 설명에도 실제 StarCraft II Live QA를 실행하지 않았다고 명시되어 있다. |

## 4. 현재 검증 결과

### 2026-09-29 working-tree continuation

- Research Harness/Deep Research 산출물은 이 SC2 저장소에서 제거했다. `integrations/research_harness/`, `landing/research/`, Research 테스트·최종목표 문서, landing의 Research 섹션/script 및 생성된 Research bytecode가 남아 있지 않다.
- `product_lemon`은 `/Users/jinminseong/Desktop/product_lemon`의 별도 Git 저장소다. 이 저장소의 코드·산출물과 섞인 흔적은 없으며, 그 저장소의 변경·세션 메타데이터는 수정하지 않았다.
- TUI 오류 `thread/goal/set failed`는 2026-09-29 04:09–04:14 UTC(13:09–13:14 KST)의 전역 `No space left on device` 로그와 시간상 일치해 유력 원인으로 기록한다. 목표 DB와 프로젝트가 있는 볼륨의 현재 여유 공간은 약 39GiB이고 `goals_1.sqlite`·`state_5.sqlite` 무결성은 `ok`였다. 이는 영구적인 ENOSPC 해결을 증명하지 않으며, 대상 thread는 기존 TUI/app-server writer가 보유 중이므로 lock·DB·프로세스를 건드리지 않았고 중복 `/goal resume`/`codex queue`도 실행하지 않았다. 현재 persisted goal은 `active`이며 token/time 카운터가 갱신 중이다.
- 2026-09-30 continuation에서 같은 볼륨의 여유 공간은 `35GiB`로 재확인됐고, 저장소 변경은 여전히 voiStarcraft2 working tree 안에만 있다. goal 상태는 `get_goal`로 `active`를 재확인했으며, 보호된 세션/DB/lock 파일과 app-server writer는 수정·종료하지 않았다.
- 2026-09-30 TUI 전담 재검증에서도 저장소에는 `thread/goal/set` 구현이 없고, 오류 문구는 설치된 TUI/app-server가 생성하는 것으로 확인했다. 공식 OpenAI App Server 문서의 `thread/goal/set|get|clear` 계약과 대조한 결과, 대상 goal은 `active`, objective 길이는 424자, `goals_1.sqlite` `integrity_check=ok`였다. 최근 실제 ERROR/WARN 범위에는 `thread/goal/set failed`/`Failed to update thread goal`이 0건이며, 남은 문자열은 DEBUG 문서 diff 인용과 과거 `apply_patch verification failed` stderr 인용뿐이다. 과거 ENOSPC는 별도 `product_lemon` thread의 rollout persistence 실패로 확인되며 이 SC2 thread와 직접 연결된 RPC 오류 증거는 없다. 보호된 전역 state를 삭제·정리하거나 writer를 종료하지 않았고, voiStarcraft2 코드나 전역 Codex 설정에는 수정 대상이 없다.
- 최신 read-only recheck(2026-09-30 05:35:40 KST)에서도 `thread/goal/get`은
  `active`를 반환했고 objective SHA-256은 동일했다. `tokensUsed=50,147,669`,
  `timeUsedSeconds=98,733`, `tokenBudget=null`이며, 약 `35.9GiB` 여유 공간이
  남아 있다. 최근 app-server 로그에는 `thread/goal/set failed`,
  `Failed to update thread goal`, ENOSPC, DB-lock 오류가 없었고
`goals_1.sqlite`·`state_5.sqlite`의 `quick_check`도 `ok`였다.
- 2026-09-30 09:06 KST 최종 read-only 재확인에서도 `goals_1.sqlite`의
  `PRAGMA integrity_check`는 `ok`, goal은 `active`, 디스크 여유는 약 `36GiB`였다.
  managed app-server와 기존 TUI writer는 정상 실행 중이며, 최신 daemon 오류 검색에서
  `thread/goal/set`, `Failed to update thread goal`, `No space left on device`,
  `StorageFull` 신규 항목은 모두 `0건`이었다. 이 확인에서도 세션·DB·lock·프로세스는
  수정·삭제·signal하지 않았다.
- 이 작업 직후의 `get_goal`도 동일 thread에서 `status=active`를 반환했다
  (`tokensUsed=51,387,658`, `timeUsedSeconds=99,483`). objective를 바꾸거나
  goal을 complete/paused로 전환하지 않았다.
- 2026-09-30 현재 턴의 `get_goal`도 동일 thread에서 `status=active`를 반환했다
  (`tokensUsed=67,719,503`, `timeUsedSeconds=109,660`, `tokenBudget=null`).
  objective·상태를 덮어쓰지 않았고, `/goal resume`를 중복 호출하거나 goal을
  complete/paused로 바꾸지 않았다.
- 2026-09-29 재검증에서 `lsof`는 managed app-server PID `70913`의 정상적인 DB 핸들을 보여주고, 기존 TUI writer PID `54434/54435`도 유지 중이다. writer 충돌을 일으킬 수 있는 병렬 `codex resume`는 실행하지 않았다.
- 공식 App Server UNIX WebSocket transport에서 `initialize` → `thread/goal/get` → `thread/goal/set` → `thread/goal/get`을 재시도한 결과, `thread/goal/set` payload `{"threadId":"01a0de40-4b10-7cf0-beae-685b24b4c4ef","status":"active"}`에 오류가 없었고 `thread/goal/updated` 이벤트도 수신했다. 직후 read-back status는 `active`였고 objective SHA-256은 `4c88606b8aa80e8975083211cb117a66a122acc98c4719d608bf3834d0d02b25`로 before/after가 같았으며, `goals_1.sqlite` read-only integrity check도 `ok`였다. 이 단일 재시도에서는 `thread/goal/set failed`가 재현되지 않았고 goal persistence가 읽기·쓰기 가능한 증거를 얻었지만, 이것만으로 영구적인 오류 해결이나 향후 모든 writer 시도의 성공을 주장하지 않는다.
- 2026-09-30 추가 재시도에서는 `initialize` → `thread/goal/get` → `thread/goal/set(status=active, objective 생략)` → `thread/goal/get`을 같은 managed app-server UNIX WebSocket으로 수행했다. `thread/goal/set`은 성공했고 `thread/goal/updated`를 수신했으며, read-back은 `status=active`, 동일 objective, `tokenBudget=null`이었다. usage history도 보존되어 `tokensUsed=54,119,459`, `timeUsedSeconds=100,173`으로 읽혔다. 이는 사용자가 요청한 `/goal resume`를 목표 본문을 덮어쓰지 않고 적용한 현재 증거다. 과거 ENOSPC의 영구 재발 방지까지 증명하는 것은 아니다.
- scope-only operation의 self-side 위치 alias(`our home`, `우리 본진`, `base`, `self_main`, `우리 앞마당`, `self_ramp`)를 검증 경계에서 canonicalize하고, Direct lowering이 self target을 `move_group`으로 처리하도록 보강했다. 공격 이동으로 잘못 해석하지 않는 회귀 테스트를 추가했다.
- 집중 Direct suite(이전 snapshot)는 `352 passed, 1 skipped, 456 subtests passed`였고, 이번 bounded capability 확장 후 adapter/router/executor 집중 검증은 `225 passed, 1 skipped, 382 subtests passed`였다. 최신 Direct/Web GUI/demo 회귀 집합은 `483 passed, 3 skipped, 447 subtests passed`다. dry-run `마린 6기 입구로 보내`는 `[executed]`를 확인했다.
- 문서 계약과 unified router까지 포함한 최신 architecture/Web/demo 회귀 집합은 `500 passed, 3 skipped, 573 subtests passed`다. Direct repair tickets now preserve a named `tactical_task.target` through provider normalization and router lowering.
- 정책·LLM·provider·Direct architecture 집중 집합은 `707 passed, 3 skipped, 729 subtests passed`로 통과했고, target-field 추가 후 재검증한 핵심 집합은 `315 passed, 1 skipped, 320 subtests passed`였다.
- `tests/test_micromachine_live_session.py` 현재 검증은 `105 passed, 115 subtests passed`다. Direct-only live-session 테스트는 production 기본값(`publish_non_macro_projection=False`)으로 실행하고, reducer/standing-order 상호작용 31개는 명시적 typed projection fixture로 분리했다. 따라서 runtime 미연결 시 `runtime_not_attached`로 fail-closed하는 승인 구조와 내부 projection 계약을 동시에 검증하며, legacy stale failure를 현재 결과로 보고하지 않는다.
- chat modulation의 non-macro 사례와 표준 Terran scouting 사례는 명시적 Direct fake executor 또는 `runtime_not_attached` assertion으로 이행했다. typed contextual-transfer는 권위 projection을 재검증한 뒤에만 사용하는 별도 `publish_non_macro_projection` seam으로 기존 MicroMachine projection/replay 계약을 유지하며, 일반 사용자/LLM live-session 경로의 기본값은 계속 `false`다. contextual-transfer suite는 `16 passed, 26 subtests passed`다. 전체 pytest 재실행에서도 Direct-only·projection 분리가 함께 통과했으며, production fallback은 복구하지 않았다.
- Direct/router/adapter/UI/architecture 회귀 묶음의 `595 passed, 1 skipped, 944 subtests passed`는 직전 historical snapshot이다. 이는 `tests/test_architecture_docs.py`, `tests/test_micromachine_chat_modulation.py`, contextual transfer, live pipeline, Direct architecture, python-sc2 adapter contract, compact boundary, web GUI를 포함한다.
- 위 회귀 묶음에 현재 live-session fixture 이관을 포함해 다시 실행한 combined focused suite는 `700 passed, 1 skipped, 1059 subtests passed`였다. Ruff(변경 파일), `compileall`, `git diff --check`도 통과했다. 이는 focused evidence이며 전체 pytest green을 의미하지 않는다.
- 전체 working-tree pytest의 과거 완료 실행 `53 failed, 3094 passed, 20 skipped, 7429 subtests passed`는 historical snapshot이다. 현재 working tree는 live-session fixture 이관과 문서 보정 후 focused suites를 재검증했으며, 전체 pytest 통합 결과는 아직 새로 산출하지 않았으므로 전체 green으로 일반화하지 않는다.
- 이후 현재 working tree에서 전체 `pytest -q --tb=no --reruns 1`를 끝까지 완료한 최신 authoritative 결과는 `3150 passed, 20 skipped, 7443 subtests passed`(858.17초)다. 이는 JEV bounded contract/public export와 코드·fixture·문서 보정 이후의 저장소 전체 테스트 결과이며, 실제 StarCraft II Live QA·raw python-sc2 전체 API·외부 JEV endpoint 운영 계약 완료를 의미하지 않는다.

### 2026-09-30 continuation

Current working-tree recheck after the bounded JEV/public-export changes and
the Direct-only fixture migration:

```text
python3 -m pytest -q tests/test_micromachine_live_session.py \
  tests/test_web_gui.py tests/test_micromachine_pre_live_journeys.py --tb=no
437 passed, 3 skipped, 412 subtests passed in 154.37s
```

The hosted `unit-contracts` failures listed above are therefore historical
results from PR head `5934022`, not a current working-tree failure. The local
run still does not prove a hosted green check or real SC2 Live QA; the
uncommitted working-tree changes have not been pushed.

The same focused contract set was repeated five times after the JEV transport
failure-closed hardening; each run passed `393 passed, 443 subtests passed`.

typed contextual-transfer 재검증에서는 일반 자연어 non-macro 라우팅을 경유하던
초기 fixture를 직접 compile/publish하는 projection fixture로 분리했다.
서버가 identity·generation·count·protected minimum을 검증한 typed endpoint만
명시적 projection seam을 사용하며, 일반 user/LLM non-macro의 Direct-only 기본값은
변경하지 않았다. 이 seam은 MCP discovery나 사용자 입력으로 노출되지 않는다.

```text
pytest -q tests/test_contextual_transfer.py
16 passed, 26 subtests passed

pytest -q tests/test_direct_command_architecture.py tests/test_sc2_executor.py \
  tests/test_contextual_transfer.py tests/test_micromachine_chat_modulation.py \
  tests/test_live_pipeline.py tests/test_web_gui.py
530 passed, 789 subtests passed
```

아래는 fixture 이행 전 historical 기록이다. 전체 `pytest -q`는 stale live-session 및 pre-live fixture 충돌을 재확인한 뒤
중단했다. 중단 시점 결과는 `128 failed, 1803 passed, 7 skipped, 54 errors,
5577 subtests passed`이며 전체 suite 완료 결과로 사용하지 않는다. compileall,
이번 변경 파일의 Ruff, `git diff --check`는 통과했다. 저장소 전체 Ruff에는
당시 `starcraft_commander/__init__.py`의 re-export 관련 F401 9건이 남아 있었다.
현재는 해당 경고를 보정했고 Ruff 검증이 통과한다.

pre-live/native journey fixture는 사용자 입력 경로와 분리된 typed projection seam을
명시적으로 사용하도록 정합화했다. ordinary live-session 호출부에는 이 seam을
주입하지 않았다. 재검증 결과는 다음과 같다.

```text
pytest -q tests/test_micromachine_pre_live_journeys.py
64 passed, 3 skipped, 96 subtests passed
```

2026-09-30 현재 턴의 동일 working tree 재검증 결과도 기록한다. 이 수치는
실행 코드 회귀의 현재 증거이며, 전체 pytest green이나 실제 Live QA를 뜻하지 않는다.

```text
pytest -q tests/test_contextual_transfer.py
16 passed, 26 subtests passed

pytest -q tests/test_micromachine_chat_modulation.py
11 passed

pytest -q tests/test_live_pipeline.py tests/test_demo_sc2.py
176 passed, 3 skipped, 501 subtests passed

pytest -q tests/test_direct_command_architecture.py tests/test_unified_command_router.py \
  tests/test_sc2_executor.py tests/test_python_sc2_adapter_contract.py \
  tests/test_llm_interpreter.py tests/test_policy_modulation.py \
  tests/test_policy_modulation_provider.py tests/test_compact_live_command_boundary.py \
  tests/test_web_gui.py
698 passed, 1 skipped, 739 subtests passed
```

정적 검증도 같은 턴에 `python3 -m compileall -q starcraft_commander tests`,
변경 대상 Ruff, `git diff --check` 순서로 통과했다. 일반
`tests/test_micromachine_live_session.py`는 runtime 없는 operation/micro/emergency
legacy 기대와 현재 Direct-only·`runtime_not_attached` 계약이 충돌하므로 실패를
숨기기 위한 production fallback은 복구하지 않았다. 당시 이 테스트 이행은
별도 작업이었으며 위 historical 회귀 수치에는 포함하지 않았다.

위 `128 failed, 3079 passed, 20 skipped, 7369 subtests passed`는
live-session fixture 정합화 전 완료 실행의 historical snapshot이다.
이후 전체 `pytest -q --tb=no`를 재실행한 결과는 `1 failed, 3141 passed,
20 skipped, 7443 subtests passed`(737.55초)였다. 유일한 실패는
`tests/test_companion_browser.py::CompactControllerBrowserTest::test_headless_browser_renders_latest_command_without_legacy_ui`
였고, 동일 테스트 5회 및 companion browser 전체 3회 단독 재실행은 모두
통과했다(`8 passed`씩). 따라서 transient headless-browser flake로 기록하며
코드 회귀로 과장하지 않는다. UI 변경과 신규 회귀는 별도 최신 검증으로 확인했다.
동일 전체 suite를 `pytest -q --tb=no --reruns 1`로 재실행했을 때는 첫 실패가
재시도에서 통과해 `3142 passed, 20 skipped, 7443 subtests passed`(833.09초)가
되었다. `pytest-rerunfailures`가 재시도 성공을 별도 pass로 세므로 고유 테스트
수는 3141이며, 최종 판정은 flaky 재시도 성공을 명시한 것으로 한다.

Companion repair clarification 보강을 working tree에 추가했다. semantic
clarification prompt를 `확인 필요` 상태·자막·채팅·피드백으로 표시하고 답변 입력에
focus하며, update ID가 다른 stale prompt는 무시하고 raw unit tag는 노출하지 않는다.
증거:

```text
pytest -q tests/test_companion_direct_feedback.py tests/test_companion_browser.py
9 passed

pytest -q tests/test_companion_direct_feedback.py tests/test_companion_browser.py \
  tests/test_web_gui.py -k companion
14 passed, 263 deselected, 28 subtests passed

pytest -q tests/test_companion_browser.py  # 3회 반복
8 passed (각 실행)

ruff check starcraft_commander/companion_ui.py \
  tests/test_companion_direct_feedback.py
python3 -m compileall -q starcraft_commander tests
git diff --check
```

이는 실제 SC2 runtime에서 clarification 답변을 받아 대상을 재선택하는 Live E2E
증거가 아니며, 실제 Live QA·raw python-sc2 전체 API·외부 JEV endpoint 운영 계약은 여전히 미완료다.
과거 실패는 직접 runtime을 주입하지 않은 기존 operation/micro/emergency 테스트가
blackboard publish 성공을 기대하던 stale 계약 때문이었다.
이 테스트들을 통과시키기 위해 일반 user/LLM non-macro를 projection seam으로
우회하지 않았고, production의 Direct-only·`runtime_not_attached` 경계를 유지했다.

compact provider 경계를 fake provider로 재검증했다. `execute_ability`를
제외한 22개 bounded Direct task와 별도 micro ability 경로가 모두
compiler → `PolicyModulationVector` → `route_policy_vector` →
`sc2.direct.execute`까지 도달한다. repair의 named target과 worker/resource
count도 보존된다. Direct 티켓은 `standing_order`나 operation edit와
혼합되면 거부되며, runtime이 없을 때는 여전히 MicroMachine으로 fallback하지
않는다.

```text
./.venv/bin/pytest -q \
  tests/test_compact_live_command_boundary.py \
  tests/test_llm_interpreter.py::LLMCommandInterpreterResolveTest::test_compact_policy_contract_is_materially_smaller_than_full_dsl
11 passed, 62 subtests passed
```

프롬프트는 `intermediate` operation 계층과 “For macro and micro, set
operation_action=null” 규칙을 명시한다. `query_unit`의 finite enum을 추가한
현재 compact contract는 15,725자이며, 회귀 제한은 16,000자다. 전체 DSL의
39,848자보다 작지만 기존 15,000자 제한을 유지했다고 주장하지 않는다.

최신 승인 구조 집중 회귀 집합은 다음과 같다.

```text
./.venv/bin/pytest -q \
  tests/test_architecture_docs.py \
  tests/test_direct_command_architecture.py \
  tests/test_unified_command_router.py \
  tests/test_sc2_executor.py \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_web_gui.py \
  tests/test_demo_sc2.py \
  tests/test_llm_interpreter.py \
  tests/test_policy_modulation.py \
  tests/test_policy_modulation_provider.py \
  tests/test_compact_live_command_boundary.py
```

이후 schema 설명 압축 회귀를 포함해 다시 실행한 최신 분할 결과는
`277 passed, 1 skipped, 399 subtests` + `152 passed, 266 subtests` +
`299 passed, 3 skipped, 254 subtests`이며, 합산하지 않고 명령별 증거로 보존한다.

같은 파일을 한 번에 실행한 최신 통합 결과는 `735 passed, 4 skipped, 919
subtests passed`다. 이는 현재 working tree의 query metadata 보강과 compact schema
설명 압축을 포함한 결과이며, 실제 SC2 Live QA를 의미하지 않는다.

정적 검증도 현재 working tree에서 다시 통과했다.

```text
ruff check <modified source/test files>       All checks passed!
git diff --check                              통과
./.venv/bin/python -m compileall -q starcraft_commander  통과
```

`query_unit` 및 `query_game` continuation의 adapter/router/provider 회귀도 별도로 통과했다.

```text
python3 -m pytest -q \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_direct_command_architecture.py \
  tests/test_compact_live_command_boundary.py \
  tests/test_llm_interpreter.py \
  tests/test_policy_modulation.py \
  tests/test_policy_modulation_provider.py \
  tests/test_sc2_executor.py \
  tests/test_architecture_docs.py
424 passed, 1 skipped, 665 subtests passed
```

`query_unit`은 `calculate_speed`, 거리/범위/방향, ability/buff predicate,
damage/DPS의 bounded read-only enum만 허용하며, query-only Direct plan에는
ownership lease를 만들지 않는다. `query_unit` task는 빈 query를 거부하며,
`query_ability` 응답도 semantic unit type과 가용성만 반환하고 raw unit tag는
provider/MCP 응답으로 반출하지 않는다.

`query_game`은 `can_afford`, cost/value/supply, pending count, structure/tech
progress, `worker_en_route_to_build`만 허용한다. `already_pending_upgrade`는
semantic `UpgradeId`로, 나머지는 `UnitTypeId`로 해석하며 Cost 객체도 JSON-safe
구조로 변환한다. query-only Direct plan은 `query_game`을 포함해 ownership
lease를 만들지 않는다.

이번 text/live lifecycle continuation까지 포함한 현재 집중 회귀는 다음과 같다.

```text
python3 -m pytest -q \
  tests/test_architecture_docs.py \
  tests/test_direct_command_architecture.py \
  tests/test_unified_command_router.py \
  tests/test_sc2_executor.py \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_web_gui.py \
  tests/test_demo_sc2.py \
  tests/test_live_pipeline.py \
  tests/test_llm_interpreter.py \
  tests/test_policy_modulation.py \
  tests/test_policy_modulation_provider.py \
  tests/test_compact_live_command_boundary.py
883 passed, 4 skipped, 1367 subtests passed
```

정적 검증: 변경 source/test 대상 Ruff 통과, `compileall` 통과,
`git diff --check` 통과. 저장소 전체 Ruff는 기존 unrelated F401 12건(그중
`tests/test_unified_command_router.py`의 기존 unused import 포함)이 있어 전체
green이라고 주장하지 않는다.

전체 `pytest -q --maxfail=30` 재검증은 `1604 passed, 4 skipped` 후
`44 failed`에서 중단됐다. 실패 대부분은 `MicroMachineLiveTextSession`에
`direct_executor`를 주입하지 않은 legacy 테스트가 operation/micro/emergency를
기존 MicroMachine publish 성공으로 기대하는 충돌이다. 현재 승인 구조의
runtime 미연결 fail-closed 계약(`runtime_not_attached`)과 직접 모순되므로,
이 결과를 전체 green 증거로 사용하지 않는다. 해당 경로의 승인 구조 집중
검증은 별도 suite에서 계속 통과한다.

이 continuation 직후 전체 오프라인 회귀도 다시 확인했다. 결과는
`160 failed, 2997 passed, 20 skipped, 54 errors, 7235 subtests passed`였다.
실패/오류는 승인된 Direct runtime 미연결 시 `runtime_not_attached`로
fail-closed하는 현재 계약과, 여전히 MicroMachine publish 성공을 기대하는
legacy live-session/pre-live 테스트가 충돌한 것이다. 신규 Direct/query
집중 suite의 `731 passed, 3 skipped` 결과와 별개로 기록하며, 이 환경에서
실제 StarCraft II Live QA를 완료했다고 주장하지 않는다.

실행 코드 `2c6a841`과 현재 working tree의 Direct scope 보강을 기준으로 재실행한 로컬 집중 검증은 다음과 같다. 명령마다
선택한 테스트 집합이 다르므로 수치를 합산하지 않는다.

이 working tree continuation에서는 `query_ability`, `query_placement`, `query_unit`, `query_game`, `build_gas`,
`distribute_workers`, `expand_now` capability 및 관련 adapter/executor/MCP 계약이
추가되었다. 아래 수치는 이 uncommitted
working tree를 포함한 재실행 결과이며, 이를 `2c6a841`에 이미 포함된 hosted
검증 결과로 소급하지 않는다.

```text
./.venv/bin/pytest -q \
  tests/test_direct_command_architecture.py \
  tests/test_unified_command_router.py \
  tests/test_sc2_executor.py \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_llm_interpreter.py \
  tests/test_architecture_docs.py
325 passed, 1 skipped, 449 subtests passed

./.venv/bin/pytest -q tests/test_python_sc2_adapter_contract.py
95 passed, 1 skipped, 79 subtests passed

./.venv/bin/python -S -m unittest -q tests.test_python_sc2_adapter_contract
96 tests, OK (python-sc2 미설치 격리 경로)

./.venv/bin/python -m py_compile starcraft_commander/*.py
통과

git diff --check
통과
```

이번 continuation에서 Web GUI와 demo fixture까지 포함한 현재 집중 회귀 집합도
재실행했다.

```text
./.venv/bin/pytest -q \
  tests/test_direct_command_architecture.py \
  tests/test_sc2_executor.py \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_web_gui.py \
  tests/test_demo_sc2.py
483 passed, 3 skipped, 447 subtests passed
```

현재 working-tree 승인 집중 회귀(legacy live-session/contextual-transfer 제외)는
다음과 같다.

```text
./.venv/bin/pytest -q --disable-warnings --tb=short \
  tests/test_architecture_docs.py \
  tests/test_direct_command_architecture.py \
  tests/test_unified_command_router.py \
  tests/test_sc2_executor.py \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_web_gui.py \
  tests/test_demo_sc2.py \
  tests/test_live_pipeline.py \
  tests/test_llm_interpreter.py \
  tests/test_policy_modulation.py \
  tests/test_policy_modulation_provider.py \
  tests/test_compact_live_command_boundary.py \
  tests/test_micromachine_chat_modulation.py
895 passed, 3 skipped, 1367 subtests passed
```

정적 검증도 변경 대상에 대해 `ruff check`, `compileall`, `git diff --check`가
통과했다. 이 결과는 실제 SC2 Live QA나 hosted CI green을 의미하지 않는다.

새 계약 테스트는 Direct-only route, 기본 legacy tool 비노출, emergency Stop/Hold/Retreat lowering,
MCP capability catalog와 모든 catalog tool 등록, 독립 호출 병렬 실행,
concrete unit-tag ownership binding and conflict rejection for group/builder/
producer/research/warp actions, lifecycle
completion/TTL/cancel, named squad·target pin unknown rejection,
event-loop 내부 async direct executor dispatch, subject ownership/conflict,
top-level task/build lowering, adapter evidence와 runtime evidence 연결,
Direct release callback, registry lifecycle 공유, build completion 후 dependent train,
dependent child producer tag rebinding,
중간 MicroMachine resume 보류, `enemy_destroyed` evidence를 검증한다.
이번 확장 계약은 `gather_resource`, `research_upgrade`, `warp_in`, `patrol`,
`return_resource`의 adapter 호출과 MCP 등록을 추가로 검증한다.

웹 브리지의 game-loop seam과 요청 간 Direct ownership 보존을 별도로 확인했다.

```text
./.venv/bin/pytest -q tests/test_web_gui.py \
  -k 'bridge_ticks_shared_direct_lifecycle_for_game_loop or modulation_requests_share_bridge_direct_ownership_registry'
2 passed, 261 deselected
```

수리 모호성 및 clarification 경계 테스트:

```text
./.venv/bin/pytest -q \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_micromachine_live_session.py \
  -k 'ambiguous_repair_target or clarification'
4 passed, 171 deselected
```

adapter와 live-session 파일 전체를 함께 실행한 결과:

```text
./.venv/bin/pytest -q --tb=no -rN \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_micromachine_live_session.py
144 failed, 137 passed, 1 skipped, 104 subtests passed
```

저장소 전체 pytest를 2026-09-29 continuation에서 다시 실행한 결과는 다음과 같다.
실패가 승인된 Direct-only 경계를 되돌려야 한다는 뜻은 아니다. 아래 실패 집합에는
runtime이 연결되지 않은 환경에서 예전 MicroMachine publish 성공을 요구하는 legacy
live-session 계약, 별도 pre-live/native artifact 계약, 그리고 fake demo fixture 기대가
함께 포함되어 있다. 전체 실패를 이번 변경의 회귀로 단정하지 않으며, 승인된 Direct
집중 suite의 결과와 분리해 보고한다.

```text
python3 -m pytest -q --tb=no -rN  # fixture 정합화 전 continuation 실행
162 failed, 2978 passed, 20 skipped, 54 errors, 7170 subtests passed in 783.91s (0:13:03)
```

수집된 실패 키의 파일별 분포는 `test_micromachine_live_session.py` 63건,
`test_micromachine_pre_live_journeys.py` 54건, `test_contextual_transfer.py` 14건,
`test_micromachine_chat_modulation.py` 2건, 그리고 당시 실행 중이던
`test_unified_command_router.py`의 1건이었다.
대표 실패를 재현하면 live-session/contextual-transfer/chat/pre-live 경로는
런타임이 연결되지 않은데도 예전 MicroMachine publish 또는 native replay 성공을
기대해 `runtime_not_attached`/`direct_failed`가 된다. router 파일은 이후 현재
Direct-only assertion으로 정리됐고 현재 전체 파일은 `7 passed`다. 당시 demo fixture
변경에 맞지 않던 Siege Tank 관찰 기대는 정합화했으며 현재 `test_demo_sc2.py`는
`32 passed, 2 skipped`다. 이 분류는
승인된 Direct-only fail-closed 경계를 유지한 상태에서 확인했으며, 실제 SC2 Live QA나
JEV 계약 구현의 증거로 간주하지 않는다.

live-session만 실행한 결과 (이전 working-tree snapshot; historical):

```text
./.venv/bin/pytest -q --tb=no -rN tests/test_micromachine_live_session.py
144 failed, 42 passed, 25 subtests passed
```

현재 working tree의 `tests/test_web_gui.py` 전체 실행도 별도로 확인한 historical
snapshot이다.

```text
./.venv/bin/pytest -q --tb=no -rN tests/test_web_gui.py
265 passed, 194 subtests passed
```

Web GUI 전체 계약은 Direct-only emergency epoch/receipt/history 마이그레이션까지
반영해 통과했다. stale normal 요청은 per-request history에는 보존되지만 emergency
결과를 authoritative latest에서 덮어쓰지 않으며, Direct runtime이 없는 경우에도
fail-closed 계약은 유지된다.

실패 수에는 unittest subtest 실패도 포함된다. 104개 수집 테스트와 144 failed는
서로 다른 집계 단위이므로 모순이 아니다. 실패 예시는 runtime이 없는
non-macro를 성공/publish로 기대하는 기존 계약과 `runtime_not_attached`의 충돌이다.
이 수치는 현재 결과가 아니라 당시 fixture 정합화 전의 historical evidence다.
현재 결과는 위의 `105 passed, 115 subtests passed`로 재검증했다.
승인된 Direct-only 경로를 되돌리지 않고 기존 테스트·호출부를 이행해야 한다.

이전 문서의 정정 사항:

- 위 6개 focused 파일 명령에 적혀 있던 `380 passed, 1 skipped, 575 subtests passed`와
  중간 기록의 `295 passed, 1 skipped, 423 subtests passed`는 현재 HEAD에서 재현되지
  않았다. 현재 working tree의 이전 재실행 출력은 `317 passed, 1 skipped, 441 subtests
  passed`였고, `query_placement` 및 narrator 계약을 포함한 최신 재실행은
  `352 passed, 1 skipped, 456 subtests passed`다.
- 인계 요약의 `145 failed, 421 passed, 1 skipped, 600 subtests passed`는 전체 저장소
  실행 결과가 아니다. 이를 `pytest -q` 전체 실행 밑에 적은 것은 잘못이므로 제거했다.
- 이전 문서에 있었던 전체 로컬 결과
  `173 failed, 2940 passed, 19 skipped, 54 errors, 7148 subtests passed`는 과거 기록이다.
  이번 보고 단계에서 로컬 전체 suite를 다시 실행한 결과로 제시하지 않는다.
- `tests/test_unified_command_router.py`가 이중 publish를 기대한다는 이전 설명도
  오래된 정보다. 현재 해당 파일은 Direct-only를 기대하고 위 focused 실행에 통과한다.

PR #187 실행 코드 `2c6a841`의 hosted 전체 suite는 historical snapshot이다. 문서 갱신 commit `4c00c98`에서
확인한 최신 run `36403412568`이 완료됐고, Python 3.10/3.11/3.12 모두 실패했다.
세 버전 모두 `173 failed, 2967 passed, 68 skipped, 7164 subtests passed`였고
Python 3.12에는 warning 1개가 있었다. 실패는 주로 runtime이 연결되지 않은
live-session/web GUI 테스트가 기존처럼 MicroMachine publish 성공을 기대해
`runtime_not_attached`/`direct_failed`가 된 것이다. 새 concrete ownership 테스트는 설치된
python-sc2의 `Point2` 표현과 미설치 환경의 tuple 표현을 타입이 아닌 좌표값으로
검증하도록 수정했다. 이 수정은 `2c6a841` Hosted run에 반영됐고 focused contract는
통과했지만 전체 legacy/live-session suite의 기존 기대 충돌은 남아 있다.

```text
head: 2c6a841 (검증 대상 실행 코드; PR 문서 HEAD는 4c00c98)
ci run: 36403412568
unit-contracts (3.10): failure — 173 failed, 2967 passed, 68 skipped, 7164 subtests passed
unit-contracts (3.11): failure — 173 failed, 2967 passed, 68 skipped, 7164 subtests passed
unit-contracts (3.12): failure — 173 failed, 2967 passed, 68 skipped, 1 warning, 7164 subtests passed
```

최신 확인 기준 `pre-live-provenance` run `36403410273`은
`pre-live-producer-isolation: failure`였고, 실패 로그는 trusted MicroMachine
CTest `passed=0, total=10, failures=10` 및 `ctest did not report an exact 10/10
pass`를 기록했다. `final-pre-live` run `36403410317`은 `event-admission`과
`ready-to-merge`가 success였지만 deterministic journeys, browser accessibility,
build identity, provenance, distribution 등의 후속 job은 skipped였다.

문서 snapshot/PR HEAD `25e9ef9`에 대한 후속 pre-live 결과도 확인했다.

```text
pre-live-provenance run: 36369654688
pre-live-build: success
pre-live-producer-isolation: failure
pre-live-provenance dependent job: skipped

final-pre-live run: 36369654697
event-admission: success
ready-to-merge: success
후속 build/provenance/deterministic/browser/distribution jobs: skipped
```

이전 기준 snapshot의 hosted 전체 suite 출력도 변경 이력으로 보존한다.

```text
head: 64d41a7
ci run: 36351195349
unit-contracts (3.10): failure
173 failed, 2948 passed, 68 skipped, 7148 subtests passed in 305.50s (0:05:05)
unit-contracts (3.11): failure
173 failed, 2948 passed, 68 skipped, 7148 subtests passed in 213.99s (0:03:33)
unit-contracts (3.12): failure
173 failed, 2948 passed, 68 skipped, 1 warning, 7148 subtests passed in 277.18s (0:04:37)
```

같은 snapshot의 hosted `pre-live-provenance` 상태:

```text
run: 36351193813
pre-live-build: success
pre-live-producer-isolation: failure
pre-live-provenance dependent job: skipped
```

직전 실행 코드 커밋 `6520abe`의 `pre-live-provenance` run `36350509288` 실패 로그에는
trusted verifier fixture의 `passed=0, total=10, failures=10, returncode=0`과
`ctest did not report an exact 10/10 pass`가 기록돼 있었다. 이는 verifier의
증거 거부 기록이지 실제 게임 테스트 10개가 수행됐다는 증거가 아니다.

`final-pre-live` run `36351193937`은 success지만 `event-admission`과
`ready-to-merge` 외 다수 job(예: deterministic journeys, browser accessibility,
build identity)은 skipped다. 이름이 `ready-to-merge`인 job 하나의 성공으로
전체 CI green·실게임 검증·병합 가능을 주장하지 않는다.

실제 StarCraft II BotAI가 이 환경에 연결되지 않았으므로 action issuance,
map collision, camera, lifecycle observation, MicroMachine 재개를 증명하는
Live QA는 수행하지 못했다.

Direct lease가 MicroMachine blackboard에 publish되지 않는 동안에도 후속 provider
요청이 현재 semantic lease의 상태·소유 subject·완료조건을 읽을 수 있도록
`active_direct_commands` 문맥을 추가했다. 이 문맥에서는 concrete unit tag,
baseline, adapter 내부 binding metadata를 제거한다. 첫 Direct 실행 후 두 번째
독립 Direct 요청에 active lease가 전달되고 두 요청 모두 blackboard를 쓰지 않는
fake-runtime 회귀가 통과했다.

```text
./.venv/bin/pytest -q \
  tests/test_micromachine_live_session.py \
  -k 'explicit_direct_executor_without_publish or follow_up_provider_context_includes_active_direct_lease'
2 passed, 103 deselected
```

## 5. 최종 판정

### 2026-09-30 map query 및 TUI 재확인 (working tree)

`sc2.direct.query_map`을 추가해 현재 bounded catalog는 26개 capability,
provider Direct task는 25개다(`retreat`는 `move_group` alias).
허용 조회는 `get_next_expansion`, `get_terrain_height`,
`get_terrain_z_height`, `has_creep`, `in_map_bounds`, `in_pathing_grid`,
`in_placement_grid`, `is_visible`이다. 지도 전체 조회인 `get_next_expansion`은
MCP subject/target 없이 호출할 수 있고 내부 subject는 `map`이다.
다른 조회는 semantic target 또는 location_intent가 필요하다.

구현·검증한 경계:

- compact/live/full LLM schema와 provider/compiler/router/adapter/executor,
  narrator에 연결했다. 일반 DSL에도 누락됐던 game/map query enum을 반영했다.
- 실제 Point2처럼 tuple을 상속하는 좌표도 `{ "x": 42.5, "y": 17.25 }`로
  직렬화한다. async 반환, `None`(가능한 확장 없음), non-finite 결과를 검증했다.
- unsupported query, missing/unresolvable target은 실패하고 주문은 발행하지 않는다.
- MCP 및 text/live query-only plan은 ownership lease를 만들지 않는다.
  MCP lifecycle 분류에서 누락됐던 `QUERY_MAP`을 수정하고 회귀 테스트를 추가했다.
- compact prompt+schema는 16,995자, full DSL은 41,251자다. 기존 17,000자
  상한과 full DSL의 절반 미만 조건을 유지했다.

최종 집중 검증(전체 suite와 별도, 수치를 합산하지 않음):

```text
python3 -m pytest -q tests/test_python_sc2_adapter_contract.py \
  tests/test_direct_command_architecture.py tests/test_unified_command_router.py \
  tests/test_compact_live_command_boundary.py tests/test_policy_modulation.py \
  tests/test_policy_modulation_provider.py tests/test_sc2_executor.py \
  tests/test_live_pipeline.py tests/test_llm_interpreter.py tests/test_architecture_docs.py
598 passed, 1 skipped, 1128 subtests passed
```

문서 누락을 보정한 최종 전체 회귀 결과:

```text
python3 -m pytest -q
3165 passed, 20 skipped, 7458 subtests passed in 778.97s (0:12:58)
```

직전 전체 실행의 `1 failed, 3163 passed, 20 skipped, 7450 subtests passed`는
수정 전 `contracts.md`에서 backtick 표기된 `query_map` 항목이 빠져 실패한
historical snapshot이다. 표 항목을 추가하고 architecture 문서 suite가
`10 passed, 129 subtests passed`인 것을 확인한 뒤 위 전체 suite를 다시 실행했다.
최종 결과는 현재 uncommitted working tree에 대한 증거이며 원격 PR/Hosted
결과로 소급하지 않는다.

후속 demo lifecycle 보정: `CommanderLiveBot.on_step()`은 callback counter가
아니라 `self.state.game_loop`를 Direct lifecycle과 dependent workflow의
`current_frame`으로 전달하고, state가 없는 fake에서는 기존 `iteration`을
fallback으로 사용한다. `tests/test_demo_sc2.py`는 `game_loop=448` 전달을
검증하며 해당 suite는 `31 passed, 3 skipped, 53 subtests passed`다.

변경 대상 Ruff 및 `compileall`, `git diff --check`는 통과했다. 실제 SC2 Live QA,
전체 raw API tool화, JEV 외부 endpoint, PR hosted 검증 완료를 뜻하지 않는다.

TUI 병렬 재점검(2026-09-30 18:27 KST): 기존 성공한 `thread/goal/set` 재시도와
`thread/goal/updated` 증거는 보존했고 추가 set/resume는 실행하지 않았다.
현재 goal은 `active`(objective 424자), DB read-only integrity check는 `ok`다.
9/30 00:00 UTC 이후 실제 ERROR/WARN에서 goal RPC 실패·ENOSPC/StorageFull은
각각 0건, 17시 이후 확인한 정상 updated 이벤트는 22건이었다.
디스크 여유는 약 19 GiB(96% 사용)다. 현재 재현되지 않으므로 영구 수정 완료로
판정하지 않으며, 세션·DB·전역 설정·다른 프로젝트·프로세스는 수정하지 않았다.
공식 계약은 [Manage a thread goal](https://learn.chatgpt.com/docs/app-server#manage-a-thread-goal)
에서 재확인했다. 보호 데이터 정리나 세션 재생성은 수행하지 않았다.

### 2026-09-30 Direct release listener hardening

Direct terminal release의 embedding 경계를 다시 감사해 다음을 보강했다.

- `DirectReleaseListeners`가 executor lifecycle wrapper 뒤에서 보존된 callback과
  명시적 host callback을 bound-method identity까지 비교해 정확히 한 번만 호출한다.
- sync callback은 기존처럼 즉시 실행하고, running event loop의 async callback은
  executor/session이 소유한 task로 보관한다. `drain_direct_release_callbacks()`와
  executor `close()`가 종료 전에 drain하며 callback 예외는 `lifecycle_errors`에
  기록하고 game-loop로 전파하지 않는다.
- 별도 lifecycle을 executor에 bind할 때 active lease/deferred workflow/pending
  callback이 있으면 재바인딩을 거부한다. session game-loop tick은 공유 executor의
  workflow-aware tick을 사용하므로 `build -> completion -> train` 중 parent release가
  child 완료 전에 MicroMachine으로 누출되지 않는다.
- Python completion condition(`building_completed`, `unit_count_reached`,
  `enemy_destroyed`)과 native `voi_operation_completion.hpp` helper의 allowlist를
  일치시켰다. 이 helper는 여전히 Python Direct lease ID를 실제 C++ MicroMachine이
  소비하는 bridge가 아니며, 실제 game-loop 자동 재개/Live QA 완료를 뜻하지 않는다.

회귀 증거:

```text
python3 -m pytest -q tests/test_direct_release_callbacks.py
7 passed

python3 -m pytest -q tests/test_direct_release_callbacks.py \
  tests/test_sc2_executor.py tests/test_direct_command_architecture.py \
  tests/test_micromachine_live_session.py
210 passed, 229 subtests passed

c++ -std=c++17 -Wall -Wextra -pedantic -I integrations/micromachine \
  integrations/micromachine/tests/operation_completion_test.cpp
pass

python3 -m compileall -q starcraft_commander tests; git diff --check
pass
```

listener hardening 및 session workflow tick 보강 후 전체 회귀도 완료했다.

```text
python3 -m pytest -q
3178 passed, 20 skipped, 7458 subtests passed in 870.40s (0:14:30)
```

이 수치는 현재 uncommitted working tree의 최신 전체 회귀 증거이며 원격 PR/Hosted
결과로 소급하지 않는다. 실제 SC2 Live QA·raw python-sc2 전체 API·외부 JEV
endpoint·C++ Direct lease 소비는 여전히 외부 의존/미완료다.

### 2026-09-30 game-level read-only query continuation

`sc2.direct.query_game`를 추가해 BotAI의 게임 수준 읽기 전용 계산을 semantic
MCP/tool calling과 micro Direct route에서 사용할 수 있게 했다. 허용 enum은
`can_afford`, `calculate_cost`, `calculate_unit_value`, `calculate_supply_cost`,
`can_feed`, `already_pending`, `already_pending_upgrade`,
`structure_type_build_progress`, `tech_requirement_progress`,
`worker_en_route_to_build`이며, 임의 메서드명·raw ID·order surface는 받지 않는다.
`already_pending_upgrade`만 `UpgradeId`로, 나머지는 `UnitTypeId`로 해석한다.
Cost 객체는 JSON-safe 구조로 변환하고 예외·미해결 subject·non-finite 결과는
fail-closed한다. query-only plan은 ownership lease를 만들지 않으며 capability는
parallel-safe로 분류된다. provider/compact/LLM schema, router, executor,
narrator, live read-only gate, MCP registry와 문서를 모두 갱신했다.
Upgrade query의 semantic 토큰은 router에서 보존하며, unit-only game query에
`subject_kind=upgrade`가 섞이면 adapter가 `invalid_subject_kind`로 거부한다.

이번 continuation의 검증:

```text
python3 -m pytest -q
3158 passed, 20 skipped, 7447 subtests passed

ruff check <modified source/test files>       All checks passed!
python3 -m compileall -q starcraft_commander tests
git diff --check                              통과
```

MCP discovery/call 경계도 별도로 재검증했다. `tools/list`가
`sc2.direct.query_game`와 10개 allowlist query enum을 광고하고,
`tools/call`이 semantic subject/query를 `SC2ActionType.QUERY_GAME`으로
lower하며, query-only 호출에는 Direct lifecycle lease를 만들지 않는지
`tests/test_unified_command_router.py::test_mcp_query_game_is_discoverable_and_calls_read_only_action`
이 고정한다. 업그레이드 subject 보존 회귀까지 포함한 MCP/router 집중 회귀는
`42 passed`였다.

`sc2.direct.query_map`은 8개 allowlist map query enum과 map-global
`get_next_expansion`을 MCP discovery/call schema에 추가했다. map-global 조회는
내부 `map` subject를 사용하고, terrain/pathing/visibility/creep 계열은 semantic
target을 map resolver로 해석한다. query-only map plan은 lifecycle lease를 만들지
않으며 Point2 결과는 JSON-safe 좌표 object로 반환된다. adapter/provider/router
집중 회귀는 이번 continuation에서 통과했다.

TUI `Failed to update thread goal: thread/goal/set failed`는 이 저장소 코드의
RPC가 아니라 Codex app-server/TUI 경계다. 공식 OpenAI 문서의 `thread/goal/set`
계약은 성공 시 `thread/goal/updated`를 발생시키며, read-only 점검에서 대상
goal은 `active`, objective 길이는 424자, `goals_1.sqlite` integrity check는
`ok`였다. 최근 실제 ERROR/WARN에는 해당 실패가 없었고, 보호된 세션/DB/전역
설정/프로세스는 변경하지 않았다. 따라서 현재 재현 가능한 저장소 수정점은
없으며, 실제 오류가 다시 발생하면 app-server request/response와 correlation
id가 필요하다. 근거: [Codex App Server 공식 문서](https://learn.chatgpt.com/docs/app-server#api-overview).

### 2026-09-30 expansion dispatch correction

검증된 확장 위치를 전달한 뒤에도 python-sc2 `BotAI.expand_now()`가 내부에서
임의 worker를 선택하고 `BotAI.build()`의 반환값을 버리는 문제가 발견됐다.
Direct 확장은 이제 `bind_direct_action()`에서 예약한 양의 worker tag를
필수로 하고, `build_structure`의 pinned `Unit.build(COMMANDCENTER, point)`
경로로만 실행한다. no-arg `expand_now` fallback과 TypeError 후 재호출은
제거했다. 잘못된 UnitTypeId, 누락·소실된 worker, `False` 또는 `None`인
건설 결과는 `SC2ActionReport(applied=false, issued_count=0)`로 거부된다.
텍스트/live pipeline의 `SC2RuntimeExecutor.execute()`와
`execute_plan()`도 실행 직전에 같은 binding을 적용하므로 MCP를 거치지 않는
호출이 worker ownership을 우회하지 않는다.
실패·중복 부작용 경로와 검증 위치 보존은 adapter 계약 테스트로 고정했지만,
실제 StarCraft II 게임에서의 건설 완료 관찰은 여전히 Live QA 대기다.

```text
python3 -m pytest -q tests/test_python_sc2_adapter_contract.py tests/test_demo_sc2.py
133 passed, 4 skipped, 133 subtests passed

python3 -m pytest -q tests/test_direct_command_architecture.py \
  tests/test_unified_command_router.py tests/test_sc2_executor.py \
  tests/test_policy_modulation_provider.py tests/test_compact_live_command_boundary.py \
  tests/test_architecture_docs.py
174 passed, 414 subtests passed

최신 동일 집중 명령(실행 직전 executor binding 회귀 포함)은
`739 passed, 4 skipped, 919 subtests passed`로 통과했다. Ruff, compileall,
`git diff --check`도 통과했다.
```

승인된 계획 중 코드·계약 테스트로 해결된 항목과 아직 해결되지 않은
항목을 구분하면 다음과 같다.

1. **코드로 해결됨**: macro-only MicroMachine route, operation/micro/emergency
   Direct-only route, bounded semantic MCP catalog(현재 26개 semantic capability,
   명시적 `retreat` alias와 `query_map` 포함),
   move camera,
   Stop/Hold/Retreat, query/read-only ability·placement, bounded build-gas/
   worker-distribution/expansion tools, fail-closed runtime, independent-call parallel scheduler,
   same-subject/emergency/ordered-plan serialization, named squad·target pin
   registry 초안, builder/producer/research/warp concrete tag binding과 lease
   conflict/release contract, lifecycle lease 상태·completion·TTL·cancel, active
   lease batch observation callback seam, runtime lifecycle 공유, active Direct
   lease의 안전한 후속 provider context, dependent workflow child producer
   rebinding seam, 현재 상태 문서화.
2. **부분 해결**: 실제 named squad의 live unit-tag resolution, target pin의
   live map 좌표·장애물 검증, 수리 clarification 답변→대상 재선택(현재 companion
   prompt 표시·입력 focus·stale update 격리는 구현), 건설 완료 관찰 후
   생산 재개(현재 fake contract 및 current-observation tag rebinding은 있음),
   실제 BotAI evidence watcher와 MicroMachine game-loop ownership 재개.
3. **명시적으로 미완료 또는 외부 의존**: raw SC2 API 전체 tool화, map-click 기반 target-pin 좌표 변환, 모든 완료조건의 실게임 관찰 검증과 실제
   MicroMachine game-loop 복귀, 실제 JEV endpoint 운영 계약(#189), 실제 SC2 Live QA,
   GitHub에서 PR #187의 차단 상태 해소.

원문 부록은 승인 관련 메시지 8개의 전문을 정확히 보존한다. 본 문서는
두 계획의 15개 번호 및 추가 승인 요구를 이행 증거와 대조한 보고서이며,
“승인한 계획이 전부 해결됐다”는 보고서는 아니다.
현재 구현 보강은 `43a834b`, `838dd59`, `090a51c`, `d3f4abf`, `2c6a841`이며 이 문서의
실행 코드 evidence snapshot은 `2c6a841`다. 이 상태 보고는 최신
Hosted 결과를 반영한다. `unit-contracts` 3개와
`pre-live-producer-isolation`은 실패했으므로
PR은 여전히 `BLOCKED`이며 green/merge가 아니다. PR merge는 하지 않았고, Live QA도
완료되지 않았다. 최신 `get_goal`은 `active`를 반환했고, 앞선 `thread/goal/set`
재시도는 성공했지만 goal 도구에는 별도 `resume` 상태 전이가 없다. 따라서 상태를
임의로 변경하지 않았으며, 디스크 여유·단일 writer·RPC 성공을 관찰 증거로만
기록한다. 중복 resume·stale lock 삭제·프로세스 종료는 하지 않았다.

### 2026-10-01 최신 working-tree 재검증

직전 Direct release listener 보강 이후 현재 working tree에서 집중 회귀를
다시 실행했다.

```text
./.venv/bin/pytest -q \
  tests/test_direct_release_callbacks.py tests/test_sc2_executor.py \
  tests/test_direct_command_architecture.py tests/test_micromachine_live_session.py
210 passed, 229 subtests passed
```

전체 회귀도 다시 실행해 다음 결과를 얻었다.

```text
./.venv/bin/pytest -q
3181 passed, 19 skipped, 7458 subtests passed in 807.04s (0:13:27)
```

변경된 Python 파일 대상 Ruff, `python3 -m compileall -q starcraft_commander tests`,
C++ completion helper, 그리고 `git diff --check`도 통과한 상태다. 저장소
전체 Ruff에는 기존 unrelated fixture의 F401 두 건이 남아 있다. 이 검증은 여전히
uncommitted working tree의 fake/contract 증거이며 실제 SC2 Live QA, C++ Direct lease 소비,
raw python-sc2 전체 API tool화, 외부 JEV endpoint 운영 계약을 증명하지 않는다.

TUI goal 오류는 2026-10-01 read-only 재확인에서도 저장소 코드의 실패로
재현되지 않았다. `goals_1.sqlite`의 `PRAGMA integrity_check`는 `ok`, 대상
thread goal은 `active`, objective 길이는 424자다. 현재 app-server stderr에는
새 `thread/goal/set` 실패·`ENOSPC`·`StorageFull` 항목이 없고, app-server
프로세스는 실행 중이다. 다만 파일시스템 여유가 약 8.2 GiB(98% 사용)로
낮아 재발 위험은 남아 있다. 보호된 세션/goal DB/lock/프로세스와 다른
프로젝트는 수정하거나 정리하지 않았다. 따라서 TUI 문제를 영구 해결했다고
주장하지 않고, 재발 시 app-server RPC request/response와 correlation id를
수집해야 저장소 밖의 원인을 확정할 수 있다.

현재 저장소 untracked 항목 중 `landing/`은 voiStarcraft 랜딩 페이지이고,
`output/`·`tmp/`는 기존 사용자 산출물이다. Product Lemon 파일은 이
working tree에서 발견되지 않았으며, 사용자 요청 없이 해당 산출물을 삭제하지
않았다.

### 2026-10-16 legacy runtime removal and follow-up tracks

The browser server no longer exposes or launches the legacy `legacy_commander`
python-sc2 compatibility mode. Runtime selection is now single-mode
(`micromachine`); legacy live status/start requests return `410 Gone`, and the
old auto-launch option is removed. The approved Direct SC2 path remains behind
the semantic router/executor; this removal does not delete historical words in
archived documents or unrelated test fixture data.

Follow-up issues opened:

- #190 Electron desktop shell migration.
- #191 real SC2 Live QA and MicroMachine resume evidence.
- #192 C++ game-loop consumption of Direct lease release callbacks.
- #193 hosted CI blocker resolution for PR #187.
- #194 live squad/target-pin/repair clarification resolution.
- #195 bounded SC2 API coverage gaps and explicit raw-API dispositions.

Validation after removal: `586 passed, 3 skipped, 451 subtests passed` in the
focused web/demo/Direct/adapter/session/JEV suites. Full-suite revalidation and
hosted checks are separate; no merge or live-QA completion is implied here.

### 2026-10-02 raw API disposition audit

`starcraft_commander.sc2_api_inventory`와 `sc2.direct.api_inventory`는 설치된
`python-sc2`의 공개 `Unit`/`BotAI` callable을 자동 분류한다. 현재 virtualenv
에서는 Unit의 14개 명령 메서드가 semantic action으로, 10개 계산/술어 메서드가
bounded query로 분류된다. BotAI의 52개 callable은 semantic mapping 28개,
runtime callback/helper 20개, raw rejection 2개, 미구현 2개로 나뉜다.
semantic mapping은 API 기능 전체가 아니라 현재 adapter의 제한된 구현에
연결된다는 뜻이다. `do`와 `synchronous_do`는 raw order 우회 위험 때문에
거부되며, `alert`와 `chat_send`는 bounded schema가 없어 **미구현**이다.
새 dependency에서 분류되지 않은 callable이 생기면 `bot_unclassified` 또는
`unit_unclassified`가 남고 `classification_complete`가 false가 된다.
이 진단은 execution gate나 전체 API 완료 판정이 아니다. properties,
Client/protocol API, ability target/caster matrix와 Live QA는 감사/구현 범위가
남아 있으며 원래 승인 요구에서 제외하지 않는다.
