# 승인된 Direct SC2 계획 원문 부록

출처: Codex 세션 `019fb8fc-d26e-73e3-8d8b-6686c71501c6`의
`rollout-2026-08-01T01-23-24-019fb8fc-d26e-73e3-8d8b-6686c71501c6.jsonl`.

아래는 최종 Direct SC2 계획의 사용자 요구·승인과, 그 승인의 대상이 된
두 assistant 계획을 JSONL 메시지 본문에서 그대로 추출한 것이다.
오타·공백·줄바꿈·Markdown을 교정하지 않았다. timestamp는 원본 UTC다.
전체 세션의 모든 주제를 복제한 문서가 아니라 최종 승인 계획의 원문 부록이다.
이전 세션 파일은 읽기만 했다.

상태 판정: [승인 계획 대조 보고](approved-direct-command-plan-status.md).
12개 semantic capability로 제한하는 후속 assistant 설명은 사용자 승인
이후에 나온 구현 선택이며, 사용자 요구인 “모든 기능”을 대체하는 승인이 아니다.

## 원문 198202 — user — 2026-09-26T05:06:42.852Z

출처 JSONL 198202행. 아래 코드 블록 내부가 해당 메시지 전문이다.

````text
사용자가 명령을 입력하면 llm이 해석후에 Micro 명령으로 갈지 macro로 갈지 에 대한 routing을 수행하고, micro명령으로 분류되면 지금 만든것과 더불어 바로 Sc2 api Direct로 하는것도 추가해서 지금 만든것과 api direct들을 tool calling처럼 사용할수있도록 Mcp로 만들어놓고, 여러개를 사용할수도 있게끔하고, 그것이 아니라면 Macro로 가고, Macro에는 주로 micromachine의 Weight조절로 진행해. 그리고 나머지는 모두 MIcro로 하고 그안에      ├─ operation(intermediate): 분대 정찰·공격·방
        어·재집결
        ├─ micro: Stim·Siege 등 능력 사용
        └─ emergency: 긴급 후퇴·전체 중지 이것들을 전부 넣고, Sc2 direct로도 넣어서 tool calling처럼 만들자. 그리고 헷갈리지 않게 legacy들은 모두 제거하고. 그렇게해서 새로운 Goal을 만들어서 개발 진행하고 PR 만들어서 나에게 보고해. 직접 내가 live qa해볼게
````

## 원문 199537 — user — 2026-09-26T07:55:45.435Z

출처 JSONL 199537행. 아래 코드 블록 내부가 해당 메시지 전문이다.

````text
오케이 이슈로 라우팅하는 system 1은 jev로 라우팅할수있도록 이슈로 파고, sc2기능을  operation / micro / emergency 여기에 넣어놨어? 바로바로 실행할수있도록? sc2api 모두 되어야해. sc2api 넣은것들이랑 게임조작에 사용할수있는것들은 뭐가있는지도 알려줘
````

## 원문 199721 — user — 2026-09-26T08:12:10.267Z

출처 JSONL 199721행. 아래 코드 블록 내부가 해당 메시지 전문이다.

````text
잘이해가 가지않아. micro에느 ㄴ모든 sc2api 가 되는걸 모두 연결했나? 그리고 툴콜링처럼 사용할수있게되고, 마이크로 머신을 무시하고 바로 할수있나? 그리고 툴콜링처럼 한번에 여러개의 명령을 병렬로 수행하고, 내가 명령한것에 대한 유통기한이 지나면 micromachine이 조작하는건가? 전혀 이해가 가지않아. 그리고 모두 sc2api에 기능에는 어떤것들이 있지? 그리고 macro에만 micromachine의 의사결정으로만하고 micro나 operation, emergency는 micromachine의 명령을 무시하고 바로 다이랙트로 작동시켜야하는데. 그리고 카메라 이동과 게임상태 관찰은 어떻게 하는거고, 수리같은건 어떤 객체에 해야하는지는 어떻게 알아? 그리고 micro 명령을 하면 다시 micromachine이 조작하기까지 어떤 과정이 있지? micro 명령에 대한 사용자 조작 임무 수행 완수조건이 따로 있는거야?
````

## 원문 199842 — user — 2026-09-26T14:42:30.394Z

출처 JSONL 199842행. 아래 코드 블록 내부가 해당 메시지 전문이다.

````text
오케이 그럼 내 요구사항을 만족시키기 위해서는 어떻게 해야하지? 그리고그리고 macro외의 명령들은 direct로 가는게 아니라 마찬가지로 micromachine에 bias를 주는거야?
````

## 원문 199849 — assistant — 2026-09-26T14:43:00.948Z

출처 JSONL 199849행. 아래 코드 블록 내부가 해당 메시지 전문이다.

````text
**결론**

사용자가 요구한 구조에서는 **`macro`만 MicroMachine bias**를 사용합니다.

| 명령 계층 | 처리 방식 |
|---|---|
| `macro` | MicroMachine에 bias/정책 조절만 전달 |
| `operation` | Direct SC2로 분대 이동·공격·방어·정찰·재집결 |
| `micro` | Direct SC2로 Stim·Siege·Unsiege·EMP 등 능력 실행 |
| `emergency` | Direct SC2로 Stop·Hold·Retreat 등 즉시 실행 |

즉, `operation / micro / emergency` 명령을 MicroMachine bias로 변환하면 안 됩니다. 현재 코드는 이 부분이 잘못되어 있어 수정이 필요합니다.

**필요한 구현**

```text
사용자 명령
  -> LLM이 macro / operation / micro / emergency로 분류
      ├─ macro
      │   -> MicroMachine bias
      │
      └─ operation / micro / emergency
          -> Direct SC2 MCP tool
          -> 명령 실행
          -> 상태 관찰
          -> 완료 / 취소 / TTL 만료
          -> 직접 제어권 해제
          -> 기본 MicroMachine 자율제어 재개
```

구체적으로는 다음이 필요합니다.

1. 라우터에서 `operation / micro / emergency`의 `micromachine.*` 호출을 제거합니다.
2. 모든 Direct SC2 기능을 MCP tool로 등록합니다.
   - 이동, 공격, 정찰, 방어, 재집결
   - 건설, 생산, 일꾼 배정, 수리
   - 능력 사용
   - 카메라 이동
   - 게임 상태 관찰
   - Stop/Hold/Retreat
3. 각 Direct 명령에 `command_id`, 대상 분대, 발행 프레임, 만료 프레임, 완료조건을 붙입니다.
4. 게임 루프가 매 프레임 명령 상태를 확인합니다.
   - 목표 도착
   - 구조물 완성
   - 적 생산시설 파괴
   - 능력 사용 확인
   - 후퇴 완료
   - TTL 만료
5. 완료·취소·만료되면 Direct 명령의 제어권을 해제하고 MicroMachine이 기본 자율제어를 재개합니다.
6. 서로 독립적인 tool은 병렬 실행하고, 같은 분대에 충돌하는 명령이나 emergency는 순차 실행합니다.
7. Direct SC2 런타임이 없으면 MicroMachine으로 몰래 대체하지 않고 `runtime_not_attached`로 실패시켜야 합니다.

**예시**

- “마린 계속 생산해”  
  → `macro` → MicroMachine의 생산 bias

- “1분대는 적 앞마당 공격”  
  → `operation` → Direct `attack_move`

- “탱크 2기 공성모드”  
  → `micro` → Direct `execute_ability`

- “전 병력 즉시 후퇴”  
  → `emergency` → Direct `stop` 후 `move_group`

- 공격 명령이 목표 도착으로 완료되거나 TTL이 끝남  
  → Direct 제어권 해제 → MicroMachine 기본 전투 AI 재개

현재 구현은 이와 달리 `operation / micro / emergency`도 MicroMachine publish가 먼저 발생하고 Direct 실행이 추가되는 구조입니다. 따라서 사용자의 요구를 만족하려면 **Direct 명령 전용 실행 경로와 명령 lifecycle/제어권 반환 시스템을 추가해야 합니다.**
````

## 원문 199856 — user — 2026-09-26T14:48:21.167Z

출처 JSONL 199856행. 아래 코드 블록 내부가 해당 메시지 전문이다.

````text
그럼 수리나 건물짓기, 혹은 유닛 특정위치로 가게 하기는 어떻게 지시하는거야? 그 위치는 어떻게 특정하지? 사용자입장에서는? 지금 너가 제시한 방향성 아주 동의해. 내 질문에 대한 해답과 해결책을 더불어서 지금 말한거 기반으로 다시 어떻게 할건지 나에게 보고해.
````

## 원문 199881 — assistant — 2026-09-26T14:49:57.343Z

출처 JSONL 199881행. 아래 코드 블록 내부가 해당 메시지 전문이다.

````text
**핵심 기준**

사용자가 직접 행동을 지시하면 `direct SC2`, 장기적인 운영방침을 지시하면 `macro → MicroMachine bias`로 구분합니다.

| 사용자 명령 | 라우팅 |
|---|---|
| “팩토리 지어” | Direct `build_structure` |
| “SCV 3기를 가스에 붙여” | Direct `assign_workers` |
| “마린 6기를 적 앞마당으로 보내” | Direct `move_group` 또는 `attack_move` |
| “앞마당 입구의 벙커 수리해” | Direct `repair` |
| “팩토리 중심으로 운영하고 탱크를 우선 생산해” | Macro → MicroMachine bias |
| “계속 마린을 생산해” | Macro → MicroMachine bias |

즉, **명시적인 1회성 행동은 Direct**, **지속적인 운영전략은 Macro**입니다.

**위치 지정 방식**

사용자는 좌표를 입력할 필요가 없습니다.

1. **의미 기반 위치**

```text
본진
본진 입구
앞마당
앞마당 입구
3멀티
미네랄 라인
적 본진
적 앞마당
적 입구
적 미네랄 라인
마지막으로 본 적 위치
정찰 위치
```

예:

```text
"마린 6기를 적 앞마당으로 보내"
"본진 입구에 벙커 지어"
"앞마당 가스 옆에 정제소 지어"
```

2. **상대 위치**

```text
"본진 입구 왼쪽에 보급고 지어"
"앞마당 입구 가까이에 벙커 지어"
"적 입구에서 떨어진 곳에 탱크 배치해"
```

내부적으로는 다음 형태로 변환합니다.

```json
{
  "anchor": "self_ramp",
  "relation": "near",
  "direction": "left",
  "distance": 3
}
```

그 후 Map Resolver가 실제 좌표를 계산하고, SC2 API가 건설 가능 여부와 장애물을 확인합니다. 안전한 위치를 찾지 못하면 실행하지 않고 이유를 반환합니다.

3. **컨트롤러로 위치 지정**

자연어로 애매한 위치를 지정하기 어려우면 컨트롤러에서 `위치 지정`을 누르고 게임 화면이나 미니맵에서 지점을 선택합니다.

단, LLM에 raw 화면 좌표를 직접 전달하지 않고 다음처럼 처리합니다.

```text
컨트롤러 클릭
  -> 게임 좌표 변환
  -> 유효성 검사
  -> target_pin 저장
  -> Direct SC2 명령에 사용
```

현재 코드는 semantic 위치와 상대적 건설 anchor는 지원하지만, 사용자용 `target pin` UX는 추가 구현 대상입니다.

**수리 대상 지정**

사용자는 유닛 ID나 내부 tag를 알 필요가 없습니다.

```text
"앞 벙커 수리해"
"손상된 건물 수리해"
"마린 한 기 치료해"
```

SC2 상태를 관찰해서 다음 순서로 찾습니다.

1. 아군 소유 여부 확인
2. 손상 여부 확인
3. 사용자가 말한 이름과 매칭
4. 구조물을 먼저 검색
5. 대상이 없으면 유닛 검색
6. 수리 가능한 SCV를 선택
7. 수리 명령 실행

여러 대상이 동시에 매칭되면 자동으로 임의 선택하지 않고 “어느 벙커인지”를 다시 묻습니다.

**분대 지정**

사용자가 `1분대`, `방어 분대`, `정찰 분대`, `주력 분대`라고 말할 수 있도록 안정적인 분대 registry를 둡니다.

```text
1분대 = Marine 6기
방어 분대 = Marine 4기 + Tank 2기
정찰 분대 = Reaper 1기
```

사용자는 내부 SC2 unit tag를 보지 않습니다. Direct executor가 현재 관찰 결과와 squad registry를 이용해 실제 유닛을 선택합니다.

**최종 구현 방향**

```text
사용자 명령
  -> LLM/System 1이 계층과 대상 해석
  -> 위치/분대/대상 검증
      ├─ Macro
      │   -> MicroMachine bias만 publish
      │
      └─ 명시적 Direct 명령
          -> SC2 MCP tool 호출
          -> 게임 상태 관찰
          -> 완료조건 확인
          -> TTL/취소/실패 처리
          -> Direct 제어권 해제
          -> 기본 MicroMachine 자율제어 재개
```

추가할 핵심 기능은 다음입니다.

1. `operation / micro / emergency`에서 MicroMachine publish 제거
2. Direct MCP 도구에 `move_camera`, `stop`, `hold`, `retreat` 추가
3. 자연어용 squad registry 추가
4. semantic 위치와 target pin을 통합한 위치 resolver 추가
5. `repair` 대상 선택과 모호성 확인 강화
6. 명령 lifecycle 추가

```text
pending
  -> dispatched
  -> active
  -> completed / expired / cancelled / failed
```

7. Direct 명령이 완료되거나 만료되면 제어권을 해제하고 MicroMachine 기본 AI로 복귀
8. 독립 명령은 병렬 실행하되, 선행조건이 있는 명령은 순차 실행

예를 들어:

```text
"SCV로 팩토리 짓고 탱크 생산해"
```

는 건설과 생산을 무조건 병렬로 실행하면 안 됩니다.

```text
팩토리 건설
  -> 팩토리 완성 관찰
  -> 탱크 생산
```

반면:

```text
"SCV는 가스에 보내고 정찰 분대는 적 앞마당으로 보내"
```

는 서로 독립적이므로 병렬 실행할 수 있습니다.

현재 저장소에는 semantic 위치 해석, 건설 위치 탐색, 수리 대상 탐색의 기반은 있습니다. 하지만 **Direct-only 라우팅, 분대 registry, 명령 lifecycle, 만료 후 제어권 반환, 사용자용 위치 pin UX는 아직 완성해야 하는 부분**입니다.
````

## 원문 199888 — user — 2026-09-26T14:56:39.069Z

출처 JSONL 199888행. 아래 코드 블록 내부가 해당 메시지 전문이다.

````text
오케이 지금 계획에 하나더 추가해서 sc2 api에있는 모든 기능들 tool calling할수있도록 mcp로 만들어두고, micro 명령에서 바로 쓸 수있도록 만드는 계획까지 추가해서 문서화 시키고,  goal 만들어서 구현진행하자.
````
