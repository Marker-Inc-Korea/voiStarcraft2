# 승인된 Direct SC2 명령 계획과 구현 현황

이 문서는 Codex 세션 `019fb8fc-d26e-73e3-8d8b-6686c71501c6`에서 사용자가 승인한
계획을 기준으로 작성한 구현 대조표다. 이 문서의 목적은 “구현했다”는 표현을
실제 코드·테스트·런타임 증거와 분리해서, 승인된 항목을 하나씩 완료 여부로
보고하는 것이다.

기준 브랜치는 `codex/llm-command-speedup`, 기준 PR은
[#187](https://github.com/Marker-Inc-Korea/voiStarcraft2/pull/187)이다. PR #187은
열려 있지만 현재 GitHub 상태가 `BLOCKED`이고, 실제 StarCraft II Live QA는
완료 증거가 없다.

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

여기서 구현 범위는 raw python-sc2 객체 전체를 무제한으로 노출하는 방식이
아니라, 검증 가능한 semantic capability catalog를 MCP로 공개하는 방식으로
정리됐다. 각 도구는 입력 schema, semantic subject/target, 런타임 필요 여부,
성공·부분 성공·거부 결과를 갖고, 임의의 메서드명이나 raw unit tag를 LLM이
직접 호출하지 않는다.

승인된 capability 목록은 다음이다.

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
적 발견, 후퇴 확인, 능력 사용 완료, 사용자 취소, TTL 만료가 포함된다.

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
| 모든 SC2 기능 | 부분 완료(승인 catalog 기준) | raw python-sc2 전체가 아니라 승인된 bounded semantic catalog를 구현했다. catalog의 모든 이름이 registry에 있는 계약 테스트는 통과하지만, raw API 전체 노출 요구는 안전한 범위 밖이다. |
| semantic 위치와 Map Resolver | 부분 완료 | semantic target alias와 map resolver·placement 검증이 있고 target pin registry를 추가했다. 실제 map 좌표 계산·장애물 검증은 live runtime 증거가 없다. |
| 분대 registry | 부분 완료 | `DirectCommandRegistry`/`SquadDefinition`으로 이름·unit query를 보존하고 unknown squad를 거부한다. 실제 관찰값 기반 tag 선택과 ownership transfer는 runtime 미검증이다. |
| 수리 대상 자동 선택 | 부분 완료 | adapter가 아군 손상 구조물 우선, 손상 유닛 fallback, SCV 선택을 수행한다. 다중 매칭 clarification과 named registry 연동은 미구현이다. |
| 독립 tool 병렬 실행 | 완료(호출 스케줄러 계약) | `call_many_async()`가 독립 호출을 `asyncio.gather`로 병렬 실행하고, 동일 subject/conflict·emergency·다중 action plan은 순차 처리한다. 실제 SC2 동시성은 runtime 미검증이다. |
| 선행조건 순차 실행 | 부분 완료 | ordered plan과 다중 action 순차 실행은 구현했다. “건설 완료 관찰 후 생산”을 자동으로 다음 tool에 재개하는 workflow는 아직 없다. |
| 완료·취소·TTL lifecycle | 부분 완료 | `DirectCommandLifecycle`가 pending/dispatched/active/complete/expired/cancelled/failed 계약 상태와 completion evidence·TTL을 보유하며 MCP/session registry에 연결됐다. executor가 성공 시 active, 거부·예외 시 failed로 전이한다. 실패 상태의 runtime watcher와 자동 polling은 미검증이다. |
| Direct 제어권 반환 | 부분 완료 | terminal lease에서 `control_owned=false`가 되고 session의 `observe_direct_command()`/`cancel_direct_command()`가 ownership 해제를 제공한다. 실제 MicroMachine game-loop 재개 증거는 없다. |
| runtime 미연결 fail-closed | 완료(코드 경계) | Direct executor가 없거나 BotAI가 없으면 `runtime_not_attached`를 반환하고 non-macro는 MicroMachine으로 우회 publish하지 않는다. |
| SC2 상태 관찰 | 부분 완료 | `observe` adapter/state resolver와 executor audit가 있고 lifecycle evidence 입력이 있다. 자동 completion watcher 연결은 미완료다. |
| 카메라 이동 | 부분 완료 | adapter/map resolver/MCP tool은 연결됐다. 실제 SC2 camera API 성공은 live runtime 미검증이다. |
| JEV System 1 라우팅 | 미완료/외부 계약 대기 | #189에 정의 질문과 acceptance criteria만 등록했다. |
| legacy route 격리 | 완료(기본 경계) | `micromachine.operation/ability/emergency` tool은 기본 registry에 없고 `include_legacy_tools=True`에서만 명시적으로 노출된다. 라우터는 non-macro에서 이를 호출하지 않는다. |
| 문서화 | 완료(현재 상태 보고) | 이 문서가 승인 원문 기준, 구조, capability, 위치·분대·수리, 병렬·선행조건, lifecycle, legacy 격리, JEV, PR·Live QA를 항목별로 기록한다. |
| PR 반영 | 부분 완료 | PR #187이 열려 있고 `BLOCKED`다. 구현 커밋 `33f451c`와 최신 상태 보고 커밋을 원격 branch에 push했고, 원격 PR head가 최신 커밋을 가리키는 것을 확인했다. |
| 실제 Live QA | 미완료 | 이전 PR 설명에도 실제 StarCraft II Live QA를 실행하지 않았다고 명시되어 있다. |

## 4. 현재 검증 결과

현재 작업 트리에서 확인한 검증은 다음과 같다.

```text
./.venv/bin/pytest -q tests/test_direct_command_architecture.py
8 passed

python3 -m py_compile starcraft_commander/*.py
통과
```

새 계약 테스트는 Direct-only route, legacy tool opt-in 격리, emergency Stop/Hold/Retreat lowering,
MCP capability catalog와 모든 catalog tool 등록, 독립 호출 병렬 실행,
lifecycle completion/TTL/cancel, named squad·target pin unknown rejection을
검증한다.

전체 저장소 실행도 수행했다.

```text
./.venv/bin/pytest -q
177 failed, 2908 passed, 19 skipped, 54 errors, 7146 subtests passed
```

이 결과에는 이번 변경과 무관한 pre-live binary/fixture 오류도 포함된다.
승인 구조와 직접 충돌하는 legacy 호환성 테스트 묶음은 다음과 같다. 예를 들어
`tests/test_unified_command_router.py`는 operation·ability를
`micromachine.*`와 Direct에 동시에 보내기를 기대하고,
`tests/test_micromachine_live_session.py`는 runtime이 없는 non-macro를
MicroMachine에 publish한 뒤 성공으로 간주한다. 새 계약은 이를 허용하지
않으므로 전체 기존 묶음은 녹색 증거가 아니다. 실패 다수는 이 legacy
expectation과 `runtime_not_attached` fail-closed 계약의 차이다. Stop/Hold
action 집합은 관련 contract test에 반영했다.

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
   registry 초안, lifecycle lease 상태·completion·TTL·cancel, 문서화.
2. **부분 해결**: 실제 named squad의 live unit-tag resolution, target pin의
   live map 좌표·장애물 검증, 수리 모호성 clarification, 건설 완료 관찰 후
   생산 재개, lifecycle 자동 watcher와 MicroMachine game-loop ownership
   재개.
3. **외부 의존으로 미해결**: JEV System 1 계약(#189), 실제 SC2 Live QA,
   GitHub에서 PR #187의 차단 상태 해소.

따라서 이번 문서는 승인 계획을 토씨 하나 빠뜨리지 않고 현재 증거와
대조한 상태 보고서이며, “승인한 계획이 전부 해결됐다”는 보고서는 아니다.
PR 반영은 완료했지만 PR은 여전히 `BLOCKED`이고, Live QA는 현재 완료되지 않았다.
