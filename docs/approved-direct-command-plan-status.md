# 승인된 Direct SC2 명령 계획과 구현 현황

이 문서는 Codex 세션 `019fb8fc-d26e-73e3-8d8b-6686c71501c6`에서 사용자가 승인한
계획을 기준으로 작성한 구현 대조표다. 이 문서의 목적은 “구현했다”는 표현을
실제 코드·테스트·런타임 증거와 분리해서, 승인된 항목을 하나씩 완료 여부로
보고하는 것이다.

기준 브랜치는 `codex/llm-command-speedup`, 기준 PR은
[#187](https://github.com/Marker-Inc-Korea/voiStarcraft2/pull/187)이다. 현재
실행 코드 기준 커밋은 `ef53ed2`이며, 이번 보강(종료 callback seam,
dependent workflow, `enemy_destroyed` evidence, registry lifecycle 공유,
compact prompt 축소)은 그 커밋에 포함됐다. 이후 상태 문서 커밋들은
hosted/전체 suite 결과와 이 상태 문서를 갱신했으며 실행 코드는 바꾸지
않았다. GitHub에서 확인한 PR #187은
`OPEN / BLOCKED`이며, 최신 완료 hosted 결과(문서-only 상태 커밋
`59580ce` 기준)는 `ci` run `36346988710`, `final-pre-live` run
`36346986307`, `pre-live-provenance` run `36346986309`이다. `ci`의
`unit-contracts (3.10)`, `(3.11)`, `(3.12)`은 모두 실패했고,
`pre-live-provenance`의 `pre-live-producer-isolation`도 실패했다.
`event-admission`, `pre-live-build`, `ready-to-merge`는 성공했지만,
필수 검사가 실패했으므로 CI green이나 merge를 주장하지 않는다. 실제
StarCraft II Live QA는 완료 증거가 없다.

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
사용자가 승인한 “sc2api에 있는 모든 기능” 요구를 12개로 축소한 승인이
아니다. 각 도구는 입력 schema, semantic subject/target, 런타임 필요 여부,
성공·부분 성공·거부 결과를 갖고, 임의의 메서드명이나 raw unit tag를 LLM이
직접 호출하지 않는다.

현재 등록된 1차 capability 목록은 다음이다. 이 목록 밖의 SC2 API 기능은
아직 미완료로 판정한다.

- 일꾼 자원 배정
- 구조물 건설
- 유닛 생산
- 분대 이동
- 분대 공격 이동
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

아래 판정은 [원문 부록](approved-direct-command-plan-verbatim.md)의 assistant
계획 두 개를 번호 그대로 나눠 적은 것이다. 각 항목의 “완료”는 저장소 코드와
집중 테스트가 증명하는 범위만 뜻하며, 실제 SC2 게임 동작까지 자동으로
의미하지 않는다.

### 첫 번째 계획(원문 199849)의 7개 항목

| 번호 | 승인 계획 항목 | 판정 | 현재 증거 / 남은 일 |
| --- | --- | --- | --- |
| 1 | `operation / micro / emergency`에서 MicroMachine publish 제거 | 완료(코드 경계) | non-macro route는 `sc2.direct.execute`만 만든다. live session도 non-macro를 publish하지 않는다. |
| 2 | 이동·공격·정찰·방어·재집결·건설·생산·일꾼·수리·능력·카메라·게임상태·Stop/Hold/Retreat를 Direct MCP tool로 등록 | 부분 완료 | semantic registry와 MCP tool/adapter 매핑은 있다. top-level tactical task와 `building_tasks` lowering도 추가했다. raw python-sc2 전체 API와 명시적 repair/worker 자연어 필드의 전체 연결은 미완료다. |
| 3 | 모든 Direct 명령에 `command_id`, 대상 분대, 발행 프레임, 만료 프레임, 완료조건 부착 | 부분 완료 | lifecycle lease가 command/frame/TTL/conditions와 `owned_subjects`를 보유한다. 실제 unit tag로 확정되는 것은 아니다. |
| 4 | 게임 루프가 매 프레임 목표 도착·건설·파괴·능력·후퇴·TTL을 확인 | 부분 완료(코드+fake contract) | `SC2RuntimeExecutor`가 match-lifetime Direct lifecycle owner가 되고 `tick_direct_commands()`를 제공하며, live demo `BotAI.on_step`이 이를 매 frame 호출한다. `PythonSC2BotAdapter.direct_command_evidence()`가 baseline과 현재 관찰값으로 목표 도착·건설 시작/완료·유닛 수·적 발견·적 구조물 파괴·후퇴를 보수적으로 공급하고, runtime이 노출한 능력 확인도 받는다. 실제 SC2 Live QA와 파괴/능력의 게임별 관찰 증거는 아직 없다. |
| 5 | 완료·취소·만료 시 Direct 제어권 해제 후 MicroMachine 재개 | 부분 완료 | terminal state에서 `control_owned=false`, release callback과 ownership registry가 있고, `SC2RuntimeExecutor`가 명시 callback 또는 adapter/BotAI의 `resume_micromachine(lease)` seam을 호출한다. dependent workflow 중간에는 재개를 보류하고 마지막 단계 뒤 한 번만 release한다. 실제 game-loop가 MicroMachine owner를 다시 호출하는 연결·Live QA는 없다. |
| 6 | 독립 Direct 병렬, 동일 분대 충돌·emergency 순차 | 완료(스케줄러 계약) | `call_many_async`가 independent gather를 사용하며 emergency/복합 plan을 순차화한다. embedded plan subject와 lifecycle subject overlap을 deterministic하게 거부한다. 실제 SC2 동시성은 Live QA 대상이다. |
| 7 | runtime 미연결은 `runtime_not_attached`로 fail-closed | 완료(코드 경계) | executor/BotAI 부재 시 Direct 실패를 반환하고 MicroMachine으로 우회 publish하지 않는다. |

### 두 번째 계획(원문 199881)의 8개 항목

| 번호 | 승인 계획 항목 | 판정 | 현재 증거 / 남은 일 |
| --- | --- | --- | --- |
| 1 | 명시적 1회 행동은 Direct, 지속 운영방침은 Macro bias | 부분 완료 | macro-only route와 direct tactical lowering이 있다. 모든 자연어 명령의 LLM 전체 경로 증거는 없다. |
| 2 | `move_camera`, `stop`, `hold`, `retreat` 추가 | 완료(계약 범위) / runtime 미검증 | catalog·MCP registry·adapter contract에 등록됐고 emergency lowering이 whole-group actions를 만든다. |
| 3 | 자연어 squad registry | 부분 완료 | named squad와 unknown rejection, unit query 보존이 있다. live observation의 안정적 tag selection과 mixed squad ownership은 미완료다. |
| 4 | semantic 위치·relative anchor·target pin 통합 | 부분 완료 | map resolver/placement metadata와 target-pin registry가 있다. controller click UI, 좌표 변환 저장, live obstacle/threat validation 증거는 없다. |
| 5 | repair target 자동 선택·모호하면 재질문 | 부분 완료 | 손상 구조물 우선·유닛 fallback·SCV 선택 adapter가 있다. 다중 매칭 clarification UI/재질문은 없다. “마린 한 기 치료해”는 별도 heal capability가 필요하다. |
| 6 | lifecycle `pending → dispatched → active → terminal` | 부분 완료(코드+fake contract) | state transition, cumulative evidence/TTL/cancel/fail, subject lease와 release callback, action context·baseline 보존, active lease snapshot·batch observation callback seam이 있다. BotAI adapter가 조건별 evidence를 공급하고, registry가 runtime executor의 lifecycle을 공유한다. 실제 게임 관찰은 없다. |
| 7 | Direct 종료 후 MicroMachine 기본 AI 복귀 | 부분 완료 | lease terminal ownership 해제는 코드로 명시됐지만 game-loop 재개 증거는 없다. |
| 8 | 독립 병렬, 선행조건 순차(건설→완성 관찰→생산) | 부분 완료(코드+fake contract) | 병렬/충돌/ordered plan 순차 실행과 `SC2RuntimeExecutor.register_dependent_plans()`/`drain_completed_workflows()`가 있다. registry 실제 경로도 runtime executor lifecycle을 공유해 `building_completed` 관찰 뒤 train을 지연 dispatch하며, 중간 MicroMachine resume을 보류한다. 실제 SC2 건설 완료 관찰은 Live QA가 필요하다. |

### 추가 승인: “모든 SC2 API tool calling”

| 항목 | 판정 | 현재 증거 / 남은 일 |
| --- | --- | --- |
| SC2 API의 모든 기능을 micro에서 MCP tool calling | **미완료** | 현재는 검증 가능한 semantic catalog만 노출한다. 전체 python-sc2/s2client API coverage를 증명하지 않으며, action inventory와 unsupported API 분류·추가 구현이 필요하다. |
| System 1 → JEV issue routing | **미완료/외부 계약 대기** | #189에 요구사항을 기록했지만 endpoint 계약과 fake endpoint 통합은 없다. |

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
| 모든 SC2 기능을 MCP tool calling으로 노출 | 부분 완료(현재 catalog 기준) | 현재 bounded semantic catalog의 이름·schema·registry·MCP 호출은 구현했다. 이것은 사용자 승인 범위를 축소한 것이 아니며, raw `python-sc2`/s2client 전체 기능을 노출한 것은 아니므로 “SC2 API 모두” 요구는 미완료다. |
| semantic 위치와 Map Resolver | 부분 완료 | semantic target alias와 map resolver·placement 검증이 있고 target pin registry/strict pin lookup을 추가했다. 실제 map 좌표 계산·장애물 검증은 live runtime 증거가 없고, 컨트롤러에서 사용자가 찍는 target-pin UX도 아직 없다. |
| 분대 registry | 부분 완료 | `DirectCommandRegistry`/`SquadDefinition`으로 이름·unit query를 보존하고 configured registry에서 unknown named squad를 거부한다. 실제 관찰값 기반 tag 선택과 ownership transfer는 runtime 미검증이다. |
| 수리 대상 자동 선택 | 부분 완료 | adapter가 아군 손상 구조물 우선, 손상 유닛 fallback, SCV 선택을 수행한다. 다중 매칭 clarification과 named registry 연동은 미구현이다. |
| 독립 tool 병렬 실행 | 완료(호출 스케줄러 계약) | `call_many_async()`가 독립 호출을 `asyncio.gather`로 병렬 실행하고, 동일 subject/conflict·emergency·다중 action plan은 순차 처리한다. 실제 SC2 동시성은 runtime 미검증이다. |
| 선행조건 순차 실행 | 부분 완료 | ordered plan과 다중 action 순차 실행, runtime executor dependent workflow가 구현됐다. `create_command_tool_registry(direct_executor=...)` 경로의 lifecycle 공유와 build→completion evidence→train 지연 dispatch를 계약 테스트로 고정했다. 실제 SC2 건설 완료 관찰은 없다. |
| 완료·취소·TTL lifecycle | 부분 완료(코드+fake contract) | `DirectCommandLifecycle`가 pending/dispatched/active/complete/expired/cancelled/failed 계약 상태와 cumulative completion evidence·TTL을 보유하며 MCP/session/웹 브리지/`SC2RuntimeExecutor` lifetime registry에 연결됐다. lease가 원래 action context와 baseline을 보존하고, adapter evidence watcher가 목표 도착·건설·유닛 수·적 발견·적 구조물 파괴·후퇴·runtime 능력 확인을 공급한다. `active_leases()`/`observe_all()`이 여러 lease를 frame tick으로 관찰하고 terminal lease를 재관찰하지 않는다. live demo `on_step`은 TTL/evidence tick과 dependent workflow drain을 호출하지만 실제 Live QA는 없다. |
| Direct 제어권 반환 후 MicroMachine 재개 | 부분 완료 | terminal lease에서 `control_owned=false`가 되고 session/웹 브리지의 `observe_direct_command()`/`observe_direct_commands()`/`tick_direct_commands()`/`cancel_direct_command()`와 executor release callback seam이 ownership 해제를 제공한다. dependent workflow 마지막 단계 뒤 release를 보장하는 fake contract도 있다. 실제 MicroMachine game-loop가 자동으로 재개되는 연결과 Live QA 증거는 없다. |
| runtime 미연결 fail-closed | 완료(코드 경계) | Direct executor가 없거나 BotAI가 없으면 `runtime_not_attached`를 반환하고 non-macro는 MicroMachine으로 우회 publish하지 않는다. |
| SC2 상태 관찰 | 부분 완료 | `observe` adapter/state resolver와 executor audit가 있고 lifecycle evidence 입력 및 batch observation seam이 있다. 실제 BotAI 조건별 evidence 수집·completion watcher 연결은 미완료다. |
| 카메라 이동 | 부분 완료 | adapter/map resolver/MCP tool은 연결됐다. 실제 SC2 camera API 성공은 live runtime 미검증이다. |
| JEV System 1 라우팅 | 미완료/외부 계약 대기 | #189에 정의 질문과 acceptance criteria만 등록했다. |
| legacy route 완전 제거 | **완료(코드 경계)** | `include_legacy_tools` opt-in과 `micromachine.operation/ability/emergency` registry entries를 제거했다. 기본 registry와 MCP discovery에는 `micromachine.policy`만 남는다. |
| 문서화 | 완료(현재 상태 보고) | 이 문서가 승인 원문 기준, 구조, capability, 위치·분대·수리, 병렬·선행조건, lifecycle, legacy 격리, JEV, PR·Live QA를 항목별로 기록한다. |
| PR 반영 | 부분 완료 | PR #187이 열려 있고 `BLOCKED`다. 최신 완료 hosted `ci` run `36346988710`에서 `unit-contracts (3.10)`, `(3.11)`, `(3.12)`이 모두 실패했고, `pre-live-provenance` run `36346986309`의 `pre-live-producer-isolation`도 실패했다. `event-admission`, `pre-live-build`, `ready-to-merge`는 성공했다. hosted CI green/merge를 주장할 수 없다. |
| 실제 Live QA | 미완료 | 이전 PR 설명에도 실제 StarCraft II Live QA를 실행하지 않았다고 명시되어 있다. |

## 4. 현재 검증 결과

현재 작업 트리에서 확인한 검증은 다음과 같다.

```text
./.venv/bin/pytest -q \
  tests/test_direct_command_architecture.py \
  tests/test_unified_command_router.py \
  tests/test_sc2_executor.py \
  tests/test_python_sc2_adapter_contract.py \
  tests/test_llm_interpreter.py \
  tests/test_architecture_docs.py
380 passed, 1 skipped, 575 subtests passed

python3 -m py_compile starcraft_commander/*.py
통과
```

새 계약 테스트는 Direct-only route, 기본 legacy tool 비노출, emergency Stop/Hold/Retreat lowering,
MCP capability catalog와 모든 catalog tool 등록, 독립 호출 병렬 실행,
lifecycle completion/TTL/cancel, named squad·target pin unknown rejection,
event-loop 내부 async direct executor dispatch, subject ownership/conflict,
top-level task/build lowering, adapter evidence와 runtime evidence 연결,
Direct release callback, registry lifecycle 공유, build completion 후 dependent train,
중간 MicroMachine resume 보류, `enemy_destroyed` evidence를 검증한다.

웹 브리지의 game-loop seam과 요청 간 Direct ownership 보존을 별도로 확인했다.

```text
./.venv/bin/pytest -q tests/test_web_gui.py \\
  -k 'bridge_ticks_shared_direct_lifecycle_for_game_loop or modulation_requests_share_bridge_direct_ownership_registry'
2 passed, 542 deselected
```

전체 저장소 실행도 수행했다.

```text
./.venv/bin/pytest -q
173 failed, 2940 passed, 19 skipped, 54 errors, 7148 subtests passed
```

전체 실패 결과는 승인된 Direct-only·`runtime_not_attached` 계약과 충돌하는
기존 MicroMachine 경로 기대, pre-live journey/verifier fixture 문제 등이 함께
포함되어 있어 PR green 증거가 아니다. 따라서 전체 suite를 통과했다고 보고하지
않으며, 승인 구조를 직접 검증하는 위 집중 묶음을 별도 기준으로 유지한다.

PR #187의 최신 완료 hosted `unit-contracts` run도 확인했다.

```text
head: 59580ce
ci run: 36346988710
unit-contracts (3.10), (3.11), (3.12): 모두 실패
3.10 요약: 173 failed, 2945 passed, 68 skipped, 7148 subtests passed
주요 원인: 승인된 runtime_not_attached fail-closed와 충돌하는 기존
MicroMachine 성공 기대 및 legacy 웹 UI publish 기대
```

최신 완료 hosted `pre-live-provenance` run도 확인했다.

```text
run: 36346986309
pre-live-build: success
pre-live-producer-isolation: failure
trusted verifier CTest: passed=0, total=10, failures=10, returncode=0
```

hosted `unit-contracts` 실패에는 승인 구조와 충돌하는 기존 MicroMachine
성공 기대와 `runtime_not_attached` fail-closed 차이가 포함되어 있다. 별도
`pre-live-producer-isolation` job은 trusted verifier fixture에서 CTest가
`0/10`으로 기록되어 실패했다. 현재 head에서도 이 job은 실패 상태이며,
따라서 PR은 CI green이나 merge 상태가 아니다.

이 결과에는 이번 변경과 무관한 pre-live binary/fixture 오류도 포함된다.
승인 구조와 직접 충돌하는 legacy 호환성 테스트 묶음은 다음과 같다. 예를 들어
`tests/test_unified_command_router.py`는 operation·ability를
`micromachine.*`와 Direct에 동시에 보내기를 기대하고,
`tests/test_micromachine_live_session.py`는 runtime이 없는 non-macro를
MicroMachine에 publish한 뒤 성공으로 간주한다. 새 계약은 이를 허용하지
않으므로 전체 기존 묶음은 녹색 증거가 아니다. 실패 다수는 이 legacy
expectation과 `runtime_not_attached` fail-closed 계약의 차이다. Stop/Hold
action 집합은 관련 contract test에 반영했다.

`python3 -m py_compile starcraft_commander/*.py`와 `git diff --check`도 통과했다.
실제 StarCraft II BotAI가 이 환경에 연결되지 않았으므로 action issuance,
map collision, camera, lifecycle observation, MicroMachine 재개를 증명하는
Live QA는 수행하지 못했다.

## 5. 최종 판정

승인된 계획 중 코드·계약 테스트로 해결된 항목과 아직 해결되지 않은
항목을 구분하면 다음과 같다.

1. **코드로 해결됨**: macro-only MicroMachine route, operation/micro/emergency
   Direct-only route, bounded semantic MCP catalog, move camera,
   Stop/Hold/Retreat, fail-closed runtime, independent-call parallel scheduler,
   same-subject/emergency/ordered-plan serialization, named squad·target pin
   registry 초안, lifecycle lease 상태·completion·TTL·cancel, active lease batch observation callback seam, runtime lifecycle 공유, dependent workflow dispatch seam, 현재 상태 문서화.
2. **부분 해결**: 실제 named squad의 live unit-tag resolution, target pin의
   live map 좌표·장애물 검증, 수리 모호성 clarification, 건설 완료 관찰 후
   생산 재개, 실제 BotAI evidence watcher와 MicroMachine game-loop ownership
   재개.
3. **명시적으로 미완료 또는 외부 의존**: raw SC2 API 전체 tool화, 사용자용 target-pin UI, 실제 BotAI 자동 lifecycle watcher와 실제
   MicroMachine game-loop 복귀, JEV System 1 계약(#189), 실제 SC2 Live QA,
   GitHub에서 PR #187의 차단 상태 해소.

따라서 이번 문서는 승인 계획을 토씨 하나 빠뜨리지 않고 현재 증거와
대조한 상태 보고서이며, “승인한 계획이 전부 해결됐다”는 보고서는 아니다.
현재 구현 보강은 집중 테스트·문서 대조 후 `ef53ed2`로 커밋·푸시했고,
`59580ce`를 마지막 완료 hosted snapshot을 기록한 상태 문서 커밋으로
푸시했다. 후속 문서 정정 커밋의 hosted 검사는 별도로 진행 중이다. 원격
최신 완료 hosted 결과에서도 `unit-contracts` 3개와 `pre-live-producer-isolation`이
실패했으므로 PR은 여전히 `BLOCKED`이며 green/merge가 아니다. PR merge는
하지 않았고, Live QA도 완료되지 않았다.
