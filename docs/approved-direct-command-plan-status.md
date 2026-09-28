# 승인된 Direct SC2 명령 계획과 구현 현황

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

세션에서 JEV의 의미·endpoint·프로토콜이 저장소에 정의되어 있지 않다는
사실을 확인했고, 임의의 계약을 구현하지 않는 별도 이슈로 분리했다.

- 이슈: [#189](https://github.com/Marker-Inc-Korea/voiStarcraft2/issues/189)
- 요구사항: transport, request/response schema, timeout, retry, failure semantics,
  eligible command classes, audit/UI payload, fake endpoint 테스트
- 현재 상태: 계약 질문과 acceptance criteria가 등록됐고 구현은 시작하지 않았다.

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

- 목표 도착: 관찰 위치와 목적지 거리 검사. 실제 명령 대상 tag 고정·Live QA 없음.
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
strict lookup이 있다. 컨트롤러 클릭 → 게임 좌표 변환 → 유효성 검사 → pin
저장 → 명령 실행의 사용자 경로는 미완료다. §2.3의 모든 자연어 위치·상대
위치 예제가 실제 지도에서 정확히 해석되는지, 장애물·위협을 피하는지는 미검증이다.
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

- 모든 SC2 API의 MCP tool calling 및 micro 직접 사용: **미완료**. 현재 18개
  semantic capability만 공개한다. 전체 API inventory·지원/미지원 분류·구현이 필요하다.
- 해당 계획 문서화: **완료(원문 및 현황 문서)**. 위 요구를 18개로 축소 승인받은
  것으로 취급하지 않는다. 기존 구현 작업은 진행됐으나 전체 구현 완료가 아니다.
- goal 생성 및 구현 진행: **goal은 active**, 구현 일부 진행. 현재 goal 조회로
  active 상태를 확인했으며, 전체 계획이 해결되지 않았으므로 complete로 바꾸지 않았다.
- System 1 → JEV: **이슈 작성은 완료**, [#189](https://github.com/Marker-Inc-Korea/voiStarcraft2/issues/189)는 OPEN.
  **통합 구현은 미완료/계약 대기**. endpoint·schema·timeout/retry 및 fake endpoint 테스트가 없다.
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
| LLM이 `macro / operation / micro / emergency`를 분류 | 부분 완료 | `PolicyModulationVector`, provider DSL, `unified_command_router.py`에 계층 분류가 있고 operation/micro/emergency를 deterministic하게 lower한다. 실제 LLM·runtime 전체 경로 증거는 아직 없다. |
| `macro`만 MicroMachine bias | 완료(라우터 기준) | `route_policy_vector()`의 macro 경로는 `micromachine.policy`만 생성한다. 실제 MicroMachine 소비는 별도 런타임 telemetry가 필요하다. |
| `operation` Direct-only | 완료(라우터 기준) / 외부 런타임 미검증 | `route_policy_vector()`가 `sc2.direct.execute`만 생성하며 `micromachine.operation`을 생성하지 않는다. `tests/test_direct_command_architecture.py::test_non_macro_route_is_direct_only`가 이를 고정한다. |
| `micro` Direct-only | 완료(라우터 기준) / 외부 런타임 미검증 | ability가 `sc2.direct.execute` 계획으로만 내려가고 live session은 non-macro를 publish하지 않는다. runtime이 없으면 `DIRECT_FAILED/runtime_not_attached`로 fail-closed한다. |
| `emergency` Direct-only | 완료(라우터 기준) / 외부 런타임 미검증 | Stop/Hold/Retreat 계획이 Direct action으로 lower되고 MicroMachine emergency publish가 없다. emergency TTL은 60초 계약 안에서 검증된다. |
| semantic MCP tool registry | 완료(계약 범위) | `tools/list`, `tools/call`, `voiStarcraft2/tools/call_many`, capability list, registry list, lifecycle, 직접 semantic action tool이 registry와 MCP server에 등록되어 있다. |
| `move_camera` 개별 MCP tool | 완료(계약 범위) | `sc2.direct.move_camera`가 catalog·registry·adapter·executor mapping에 존재한다. 실제 카메라 이동은 SC2 runtime 필요. |
| Stop/Hold/Retreat direct tool | 완료(계약 범위) / runtime 미검증 | `stop_group`, `hold_position`, 명시적 `sc2.direct.retreat` capability와 MCP tool을 제공한다. Retreat는 adapter 의미상 self-main 이동으로 lower되며 emergency 계획은 Stop/Hold/Move를 사용한다. |
| 모든 SC2 기능을 MCP tool calling으로 노출 | **미완료** | 현재 18개 bounded semantic capability의 이름·schema·registry·MCP 호출과 fake adapter 계약은 구현했다. 이것은 사용자 승인 범위를 축소한 것이 아니며, raw `python-sc2`/s2client 전체 기능을 노출한 것은 아니다. |
| semantic 위치와 Map Resolver | 부분 완료 | semantic target alias와 map resolver·placement 검증이 있고 target pin registry/strict pin lookup을 추가했다. 실제 map 좌표 계산·장애물 검증은 live runtime 증거가 없고, 컨트롤러에서 사용자가 찍는 target-pin UX도 아직 없다. |
| 분대 registry | 부분 완료 | `DirectCommandRegistry`/`SquadDefinition`으로 이름·unit query를 보존하고 configured registry에서 unknown named squad를 거부한다. 실제 관찰값 기반 tag 선택과 ownership transfer는 runtime 미검증이다. |
| 수리 대상 자동 선택 | 부분 완료 | adapter의 손상 구조물 우선·명시적 유닛명 fallback·worker 선택, 모호성 거부 및 live-session clarification 전파를 구현했다. 실제 UI 재질문/답변 대상 선택, 공간적 이름 매칭, 기계/생체 구분·heal 경로는 미완료다. |
| 독립 tool 병렬 실행 | 완료(호출 스케줄러 계약) | `call_many_async()`가 독립 호출을 `asyncio.gather`로 병렬 실행하고, 동일 subject/conflict·emergency·다중 action plan은 순차 처리한다. 실제 SC2 동시성은 runtime 미검증이다. |
| 선행조건 순차 실행 | 부분 완료 | ordered plan과 다중 action 순차 실행, runtime executor dependent workflow가 구현됐다. `create_command_tool_registry(direct_executor=...)` 경로의 lifecycle 공유와 build→completion evidence→train 지연 dispatch, child producer concrete tag rebinding을 계약 테스트로 고정했다. 실제 SC2 건설 완료 관찰은 없다. |
| 완료·취소·TTL lifecycle | 부분 완료(코드+fake contract) | `DirectCommandLifecycle`가 pending/dispatched/active/complete/expired/cancelled/failed 계약 상태와 cumulative completion evidence·TTL을 보유하며 MCP/session/웹 브리지/`SC2RuntimeExecutor` lifetime registry에 연결됐다. lease가 원래 action context와 baseline을 보존하고, adapter evidence watcher가 목표 도착·건설·유닛 수·적 발견·적 구조물 파괴·후퇴·runtime 능력 확인을 공급한다. `active_leases()`/`observe_all()`이 여러 lease를 frame tick으로 관찰하고 terminal lease를 재관찰하지 않는다. live demo `on_step`은 TTL/evidence tick과 dependent workflow drain을 호출하지만 실제 Live QA는 없다. |
| Direct 제어권 반환 후 MicroMachine 재개 | 부분 완료 | terminal lease에서 `control_owned=false`가 되고 session/웹 브리지의 `observe_direct_command()`/`observe_direct_commands()`/`tick_direct_commands()`/`cancel_direct_command()`와 executor release callback seam이 ownership 해제를 제공한다. dependent workflow 마지막 단계 뒤 release를 보장하는 fake contract도 있다. 실제 MicroMachine game-loop가 자동으로 재개되는 연결과 Live QA 증거는 없다. |
| runtime 미연결 fail-closed | 완료(코드 경계) | Direct executor가 없거나 BotAI가 없으면 `runtime_not_attached`를 반환하고 non-macro는 MicroMachine으로 우회 publish하지 않는다. |
| SC2 상태 관찰 | 부분 완료 | `observe`/state resolver, 조건별 adapter evidence, batch observation 및 demo `on_step` 연결이 있다. 실제 게임의 모든 조건을 관찰하는 통합과 Live QA는 완료되지 않았다. |
| 카메라 이동 | 부분 완료 | adapter/map resolver/MCP tool은 연결됐다. 실제 SC2 camera API 성공은 live runtime 미검증이다. |
| JEV System 1 라우팅 | 미완료/외부 계약 대기 | #189에 정의 질문과 acceptance criteria만 등록했다. |
| 기본 MCP의 legacy tactical route 제거 | **완료(코드 경계)** | `include_legacy_tools` opt-in과 `micromachine.operation/ability/emergency` registry entries를 제거했다. 기본 registry와 MCP discovery에는 `micromachine.policy`만 남는다. 저장소 전체의 legacy 문서·테스트 제거까지 완료한 것은 아니다. |
| 문서화 | 완료(현재 상태 보고) | 이 문서가 승인 원문 기준, 구조, capability, 위치·분대·수리, 병렬·선행조건, lifecycle, legacy 격리, JEV, PR·Live QA를 항목별로 기록한다. |
| PR 반영 | 부분 완료 | 검증 대상 실행 코드 commit은 `2c6a841`다(이후 문서 갱신 commit은 별도). PR #187은 병합하지 않았다. 최신 확인 `ci` run `36403412568`은 Python 3.10/3.11/3.12 모두 `173 failed, 2967 passed, 68 skipped, 7164 subtests passed`로 실패했고, Python 3.12에는 warning 1개가 있었다. `pre-live-provenance` run `36403410273`의 `pre-live-producer-isolation`은 trusted MicroMachine CTest `0/10`으로 실패했다. `final-pre-live` run `36403410317`의 success는 `event-admission`·`ready-to-merge`만 실행되고 후속 job이 skipped된 결과로, 전체 검증 완료를 뜻하지 않는다. |
| 실제 Live QA | 미완료 | 이전 PR 설명에도 실제 StarCraft II Live QA를 실행하지 않았다고 명시되어 있다. |

## 4. 현재 검증 결과

실행 코드 `2c6a841` 기준으로 재실행한 로컬 집중 검증은 다음과 같다. 명령마다
선택한 테스트 집합이 다르므로 수치를 합산하지 않는다.

```text
./.venv/bin/pytest -q \
  tests/test_direct_command_architecture.py \
  tests/test_unified_command_router.py \
  tests/test_sc2_executor.py \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_llm_interpreter.py \
  tests/test_architecture_docs.py
314 passed, 1 skipped, 439 subtests passed

./.venv/bin/pytest -q tests/test_python_sc2_adapter_contract.py
89 passed, 1 skipped, 74 subtests passed

./.venv/bin/python -S -m unittest -q tests.test_python_sc2_adapter_contract
90 tests, OK (python-sc2 미설치 격리 경로)

./.venv/bin/python -m py_compile starcraft_commander/*.py
통과

git diff --check
통과
```

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
145 failed, 111 passed, 1 skipped, 89 subtests passed
```

live-session만 실행한 결과:

```text
./.venv/bin/pytest -q --tb=no -rN tests/test_micromachine_live_session.py
145 failed, 40 passed, 25 subtests passed
```

실패 수에는 unittest subtest 실패도 포함된다. 103개 수집 테스트와 145 failed는
서로 다른 집계 단위이므로 모순이 아니다. 실패 예시는 runtime이 없는
non-macro를 성공/publish로 기대하는 기존 계약과 `runtime_not_attached`의 충돌이다.
모든 실패를 건별 분류하거나 이번 변경과 무관하다고 입증한 것은 아니다.
승인된 Direct-only 경로를 되돌리지 않고 기존 테스트·호출부를 이행해야 한다.

이전 문서의 정정 사항:

- 위 6개 focused 파일 명령에 적혀 있던 `380 passed, 1 skipped, 575 subtests passed`와
  중간 기록의 `295 passed, 1 skipped, 423 subtests passed`는 현재 HEAD에서 재현되지
  않았다. 최신 `2c6a841` 재실행의 실제 출력은 `314 passed, 1 skipped, 439 subtests
  passed`다.
- 인계 요약의 `145 failed, 421 passed, 1 skipped, 600 subtests passed`는 전체 저장소
  실행 결과가 아니다. 이를 `pytest -q` 전체 실행 밑에 적은 것은 잘못이므로 제거했다.
- 이전 문서에 있었던 전체 로컬 결과
  `173 failed, 2940 passed, 19 skipped, 54 errors, 7148 subtests passed`는 과거 기록이다.
  이번 보고 단계에서 로컬 전체 suite를 다시 실행한 결과로 제시하지 않는다.
- `tests/test_unified_command_router.py`가 이중 publish를 기대한다는 이전 설명도
  오래된 정보다. 현재 해당 파일은 Direct-only를 기대하고 위 focused 실행에 통과한다.

PR #187 실행 코드 `2c6a841`의 hosted 전체 suite는 문서 갱신 commit `4c00c98`에서
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

## 5. 최종 판정

승인된 계획 중 코드·계약 테스트로 해결된 항목과 아직 해결되지 않은
항목을 구분하면 다음과 같다.

1. **코드로 해결됨**: macro-only MicroMachine route, operation/micro/emergency
   Direct-only route, bounded semantic MCP catalog(현재 18개 semantic capability),
   move camera,
   Stop/Hold/Retreat, fail-closed runtime, independent-call parallel scheduler,
   same-subject/emergency/ordered-plan serialization, named squad·target pin
   registry 초안, builder/producer/research/warp concrete tag binding과 lease
   conflict/release contract, lifecycle lease 상태·completion·TTL·cancel, active
   lease batch observation callback seam, runtime lifecycle 공유, dependent
   workflow child producer rebinding seam, 현재 상태 문서화.
2. **부분 해결**: 실제 named squad의 live unit-tag resolution, target pin의
   live map 좌표·장애물 검증, 수리 모호성 prompt의 사용자 UI 연결, 건설 완료 관찰 후
   생산 재개(현재 fake contract 및 current-observation tag rebinding은 있음),
   실제 BotAI evidence watcher와 MicroMachine game-loop ownership 재개.
3. **명시적으로 미완료 또는 외부 의존**: raw SC2 API 전체 tool화, 사용자용 target-pin UI, 모든 완료조건의 실게임 관찰 검증과 실제
   MicroMachine game-loop 복귀, JEV System 1 계약(#189), 실제 SC2 Live QA,
   GitHub에서 PR #187의 차단 상태 해소.

원문 부록은 승인 관련 메시지 8개의 전문을 정확히 보존한다. 본 문서는
두 계획의 15개 번호 및 추가 승인 요구를 이행 증거와 대조한 보고서이며,
“승인한 계획이 전부 해결됐다”는 보고서는 아니다.
현재 구현 보강은 `43a834b`, `838dd59`, `090a51c`, `d3f4abf`, `2c6a841`이며 이 문서의
실행 코드 evidence snapshot은 `2c6a841`다. 이 상태 보고는 최신
Hosted 결과를 반영한다. `unit-contracts` 3개와
`pre-live-producer-isolation`은 실패했으므로
PR은 여전히 `BLOCKED`이며 green/merge가 아니다. PR merge는 하지 않았고, Live QA도
완료되지 않았다. goal도 active로 유지했다.
