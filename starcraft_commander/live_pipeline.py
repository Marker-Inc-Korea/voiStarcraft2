"""Live StarCraft II command pipeline: text -> intent -> plan -> narration.

This is the handoff Step 5 integration seam. One :class:`SC2CommandSession`
composes the Korean command interpreter (reused from the ToyCraft offline
harness, the one legitimate toycraft import in this package), the live
feasibility validator, the semantic SC2 action planner, the lifecycle-aware
runtime executor, the BotAI state resolver, and the Korean narrator into a
single async ``process_text`` call that returns one structured
:class:`SC2CommandOutcome` per executed (or honestly refused) command part.

The module is intentionally importable without StarCraft II, python-sc2,
faster-whisper, or sounddevice installed: every composed component is either
stdlib-only or lazy about its optional runtime, and bot objects are always
duck-typed. Conservative house rules apply end to end: unknown game state
rejects mutating commands, planner refusals surface their full supported
target listing, and skipped runtime work is never narrated as success.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from toycraft_commander.interpreter import (
    CommandInterpretationResult,
    DEFAULT_COMMAND_INTERPRETER,
    MOVE_CAMERA_CONSTRAINT,
    CommandInterpreterInterface,
    build_ambiguous_build_base_result,
    build_ambiguous_camera_base_result,
    build_missing_build_relative_anchor_result,
    build_missing_relative_action_anchor_result,
    is_ambiguous_build_base_target,
    is_ambiguous_camera_base_target,
    is_deictic_build_placement_missing_semantic_target,
    is_unanchored_relative_action_target,
    is_unanchored_relative_build_placement,
    parse_korean_base_selection,
)
from toycraft_commander.failure import build_parsing_failure_report
from toycraft_commander.intents import MoveCameraIntent

from starcraft_commander.contracts import (
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)
from starcraft_commander.feasibility import (
    DEFAULT_SC2_FEASIBILITY_VALIDATOR,
    SC2FeasibilityResult,
    SC2FeasibilityValidatorInterface,
)
from starcraft_commander.map_resolver import SC2MapResolver
from starcraft_commander.narrator import (
    DEFAULT_SC2_NARRATOR,
    SC2KoreanNarrator,
    SC2_KOREAN_TARGET_NAMES,
    SC2NarratorInterface,
)
from starcraft_commander.sc2_executor import (
    DEFAULT_SC2_ACTION_PLANNER,
    SC2ActionPlannerInterface,
    SC2ExecutorBoundaryInterface,
    SC2RuntimeExecutor,
)
from starcraft_commander.standing_orders import (
    CONSTRAINT_TO_STANDING_ORDER,
    STANDING_ORDER_KOREAN_LABELS,
)
from starcraft_commander.state_resolver import (
    DEFAULT_SC2_STATE_RESOLVER,
    SC2CommanderState,
    SC2StateResolverInterface,
)


SC2CommandOutcomeStatus = Literal[
    "executed",
    "partially_executed",
    "blocked",
    "read_only",
    "clarification",
]
"""Stable commander-facing outcome status values for one command part."""

SC2_COMMAND_OUTCOME_STATUSES: Final[frozenset[str]] = frozenset(
    {"executed", "partially_executed", "blocked", "read_only", "clarification"}
)
"""Every supported ``SC2CommandOutcome.status`` value.

The first four mirror the narrator statuses; ``clarification`` marks command
text the interpreter could not resolve into a supported Intent DSL payload.
"""

_SEQUENTIAL_VERB_STEM_SYLLABLES: Final[str] = "짓뽑내막찍리치키우들"
"""Final verb-stem syllables allowed before a sequential ``고 `` split.

Covers the command vocabulary verbs (짓고, 뽑고, 보내고, 막고, 찍고, 올리고,
고치고, 지키고, 세우고, 만들고). A curated allowlist instead of any Hangul
syllable keeps nouns ending in ``고`` (보급고, 창고) and noun phrases ending in
``하고`` (마린하고 SCV) from being split apart mid-word.
"""

_COMPOUND_COMMAND_SPLIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\s+)그리고\s+"  # Explicit connective, including utterance start.
    r"|\s+하고\s+(?!있)"  # Standalone connective word: "A 하고 B".
    r"|[,，;；]\s*"  # Dictation/list punctuation between short commands.
    r"|(?<=[가-힣])면서\s+"  # Simultaneous connective ending: "뽑으면서 B".
    r"|(?<=[가-힣])고\s+나서\s+"  # Sequential connective: "보내고 나서 B".
    r"|[한은]\s+다음\s+"  # Adnominal sequence: "생산한 다음 B".
    r"|(?<=생산)하고\s+(?!있)"
    r"|(?<=정찰)하고\s+(?!있)"
    r"|(?<=수리)하고\s+(?!있)"
    r"|(?<=확인)하고\s+(?!있)"
    r"|(?<=보고)하고\s+(?!있)"
    r"|(?<=채취)하고\s+(?!있)"
    r"|(?<=방어)하고\s+(?!있)"
    r"|(?<=수비)하고\s+(?!있)"
    r"|(?<=건설)하고\s+(?!있)"
    r"|(?<=공격)하고\s+(?!있)"
    r"|(?<=이동)하고\s+(?!있)"
    # Sequential verb ending: "보내고 B" — only after curated verb stems so
    # nouns ending in 고 (보급고, 창고) and progressive forms ("막고 있어")
    # are never split apart.
    rf"|(?<=[{_SEQUENTIAL_VERB_STEM_SYLLABLES}])고\s+(?!있)"
)
"""Heuristic Korean compound-command boundaries, standalone connectives first."""

_EXPLICIT_CONNECTIVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\s)그리고\s|\s하고\s"
)
"""Detector for explicit standalone connectives signaling a compound order."""

SC2_STANDING_ORDER_REGISTRATION_PREFIX: Final[str] = "상비 명령 등록"
"""Korean prefix of the narration suffix announcing new standing orders."""

_SUMMARIZE_STATE_INTENT_NAME: Final[str] = "SUMMARIZE_STATE"
"""Intent whose read-only outcomes get standing-order/memory enrichment."""

_ANSWER_QUESTION_INTENT_NAME: Final[str] = "ANSWER_QUESTION"
"""Read-only pseudo intent for help/capability questions, never game actions."""

_COMMAND_CENTER_STRUCTURE_NAMES: Final[frozenset[str]] = frozenset(
    {"COMMANDCENTER", "ORBITALCOMMAND", "PLANETARYFORTRESS"}
)
"""Own Terran townhall names that make generic camera-base targets ambiguous."""

_EXECUTED_OUTCOME_STATUSES: Final[frozenset[str]] = frozenset(
    {"executed", "partially_executed", "read_only"}
)
"""Outcome statuses that count as a successful execution for registration."""

_LOCATION_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "위치",
    "장소",
    "좌표",
    "대상",
    "타겟",
    "목표",
    "어디",
    "건물에",
    "건물 위치",
    "지정",
    "target",
    "place",
    "location",
    "position",
)
_VOICE_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "음성",
    "마이크",
    "말로",
    "voice",
    "microphone",
)
_CAPABILITY_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "뭐 할 수",
    "뭐할수",
    "뭐 할 줄",
    "뭐할줄",
    "뭘 할 수",
    "뭘할수",
    "뭘 할 줄",
    "뭘할줄",
    "무엇을 할 수",
    "무엇을할수",
    "도와줄 수",
    "도와줄수",
    "무슨 명령",
    "어떤 명령",
    "가능한 명령",
    "명령어",
    "지원하는 명령",
    "지원 기능",
    "무슨 기능",
    "어떤 기능",
    "할 수 있는 명령",
    "할수있는 명령",
    "할 수 있어",
    "할수 있어",
    "할수있어",
    "할 줄 알아",
    "할줄 알아",
    "할줄알아",
    "명령 알려",
    "지원하는 질문",
    "어떤 질문",
    "무슨 질문",
    "대화 가능",
    "대화가능",
    "사용법",
    "도움말",
    "help",
    "commands",
)
_LLM_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "llm",
    "gpt",
    "openai",
    "api 키",
    "api키",
    "키",
    "대화",
    "연결",
    "모델",
)
_NEXT_ACTION_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "다음할일",
    "다음 할 일",
    "다음엔 뭐",
    "다음에 뭐",
    "지금 할 일",
    "지금할거",
    "지금 할거",
    "지금 할 것",
    "이제 뭐",
    "추천",
    "조언",
    "운영 조언",
    "전략 조언",
    "조언해",
    "뭐해야",
    "뭐 해야",
    "뭐 하면",
    "뭐하지",
    "뭐 하지",
    "뭐할까",
    "뭐 할까",
    "뭘해야",
    "뭘 해야",
    "뭘 하면",
    "뭘할까",
    "뭘 할까",
    "무엇을 해야",
    "무엇을 하면",
    "어떻게 해야",
)
_META_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "너는 뭐",
    "넌 뭐",
    "너 뭐야",
    "넌 누구",
    "너는 누구",
    "이 커맨더 뭐",
    "커맨더 뭐",
    "이거 무슨 기능",
    "무슨 기능이야",
    "어떤 봇",
    "무슨 봇",
)
_CAMERA_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "카메라",
    "화면",
    "시점",
    "camera",
)
_CAMERA_COMMAND_PATTERNS: Final[tuple[str, ...]] = (
    "옮겨",
    "이동",
    "보여줘",
    "보여",
    "center",
    "move",
)
_CANCEL_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "취소",
    "cancel",
)
_FAILURE_REASON_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "왜 안돼",
    "왜 안 돼",
    "왜 안되",
    "왜 안 되",
    "왜 실패",
    "실패 이유",
    "실패한 이유",
    "안 되는 이유",
    "안되는 이유",
    "안 된 이유",
    "안된 이유",
    "실행 안 된 이유",
    "실행 안된 이유",
    "왜 실행 안",
    "왜 실행이 안",
    "왜 못",
    "왜 막혔",
    "왜 보류",
)
_TOWNHALL_STATE_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "사령부 상태",
    "사령부상태",
    "커맨드 상태",
    "커맨드상태",
    "커맨드센터 상태",
    "커맨드센터상태",
    "커맨드 센터 상태",
    "기지 상태",
    "기지상태",
    "베이스 상태",
    "베이스상태",
    "본진 상태",
    "본진상태",
    "앞마당 상태",
    "앞마당상태",
    "멀티 상태",
    "멀티상태",
    "base state",
    "townhall state",
    "town hall state",
    "command center state",
)
_QUESTION_MARKERS: Final[tuple[str, ...]] = (
    "?",
    "？",
    "가능",
    "되나",
    "돼",
    "되나요",
    "되냐",
    "할 수",
    "할수",
    "어떻게",
    "알려",
    "지원",
)

_LOCATION_QUESTION_ANSWER: Final[str] = (
    "네, 건물 위치는 현재 의미 기반 위치로 지정할 수 있습니다. "
    "예: `본진에 배럭 지어`, `본진 입구에 보급고 지어`, "
    "`본진 가스에 정제소 지어`, `앞마당에 커맨드 지어`, "
    "`앞마당 입구에 벙커 지어`. 지금은 마우스 좌표를 찍는 방식이 아니라 "
    "SC2 API가 이해할 수 있는 semantic target으로 변환해 건설합니다."
)
_VOICE_QUESTION_ANSWER: Final[str] = (
    "네, 음성 입력을 지원합니다. `[voice]` 의존성 설치 후 "
    "`python3 -m starcraft_commander.demo_sc2 --dry-run --voice` 또는 "
    "`python3 -m starcraft_commander.demo_sc2 --map AcropolisLE --difficulty easy --voice`"
    "로 실행합니다. macOS에서는 터미널 앱에 마이크 권한을 허용해야 합니다."
)

_SUPPORTED_COMMAND_CAPABILITIES: Final[tuple[tuple[str, str], ...]] = (
    ("상태 확인/브리핑", "`상태 알려줘`, `상태 확인`, `상태 보고하고 지금 할거 알려줘`"),
    ("경제", "`경제 안정화해`, `SCV 계속 찍어`, `놀고 있는 일꾼들 일시켜`, `자원채취`"),
    ("건설", "`본진에 배럭 지어`, `본진 입구에 보급고 지어`, `앞마당에 커맨드 지어`"),
    ("병력/전술", "`마린 생산해`, `정찰보내`, `본진 입구 막아`, `수리해`, `견제해`"),
    ("카메라", "`본진 보여줘`, `본진 입구로 카메라 옮겨`, `적 입구 보여줘`"),
)

_SUPPORTED_QUESTION_CAPABILITIES: Final[tuple[str, ...]] = (
    "`지금 뭐 해야 해?`",
    "`왜 안돼?`",
    "`위치 지정 가능해?`",
    "`카메라 움직일 수 있어?`",
    "`음성지원도 되나?`",
    "`llm이랑 대화 가능?`",
)

_SUPPORTED_CLARIFICATION_EXAMPLES: Final[tuple[str, ...]] = (
    "`더 멀게 지어` -> 어느 기준/방향인지 묻습니다",
    "`저기에 지어` -> 지원되는 semantic target을 묻습니다",
    "`사령부로 카메라 옮겨` + 다중 사령부 -> 본진/앞마당 중 무엇인지 묻습니다",
)

_SUPPORTED_LIMITATION_LINES: Final[tuple[str, ...]] = (
    "질문 답변은 항상 읽기 전용이며 게임 액션을 실행하지 않습니다",
    "LLM은 intent/ComboPlan 해석만 하고 python-sc2 API를 직접 호출하지 않습니다",
    "모든 실행 명령은 intent 검증, feasibility, planner, executor 안전층을 통과해야 합니다",
    "지원하지 않는 취소, 마우스 좌표 직접 클릭, 비정찰 위치는 실행하지 않고 이유를 보여줍니다",
)

_NEXT_ACTION_QUESTION_ANSWER: Final[str] = (
    "현재 추천 흐름은 상태 확인 -> 유휴 일꾼 미네랄 배정 -> 보급 여유 확인 -> "
    "병영/정제소 확보 -> 마린 생산 -> 정찰 유지입니다. 더 정확한 판단이 필요하면 "
    "`상태 보고하`를 먼저 실행한 뒤 전략 브리핑의 추천 보기를 열어 확인하세요."
)
_NEXT_ACTION_NO_STATE_ANSWER: Final[str] = (
    f"{_NEXT_ACTION_QUESTION_ANSWER} 현재 연결된 게임 상태를 읽지 못해 "
    "구체 추천은 보수적으로 제한했습니다."
)
_CAMERA_QUESTION_ANSWER: Final[str] = (
    "카메라 이동은 MOVE_CAMERA 계획으로 처리됩니다. 이 답변은 읽기 전용이라 "
    "카메라를 실제로 움직이지 않습니다."
)
_CANCEL_QUESTION_ANSWER: Final[str] = (
    "취소 명령은 아직 안전 실행 API로 연결되지 않았습니다. 지금은 어떤 건설/생산을 "
    "취소할지 식별하는 단계가 없어 게임 액션을 내지 않습니다. 이후 `마지막 건설 취소`, "
    "`선택한 배럭 취소`처럼 대상 지정이 가능한 CANCEL 액션으로 추가해야 합니다."
)
_FAILURE_REASON_QUESTION_ANSWER: Final[str] = (
    "최근 실패 기록이 아직 없습니다. 명령이 막히면 채팅/기록의 직전 "
    "`blocked` 또는 `clarification` 항목에 실행하지 않은 이유와 대안이 표시됩니다. "
    "이 질문은 읽기 전용이라 게임 명령을 실행하지 않습니다."
)
_TOWNHALL_STATE_QUESTION_ANSWER: Final[str] = (
    "현재 사령부/기지 상태를 읽어 옵니다. 이 답변은 읽기 전용이라 게임 명령을 실행하지 않습니다."
)
_AMBIGUOUS_TOWNHALL_STATE_FAILURE_CODE: Final[str] = "ambiguous_townhall_state"
_AMBIGUOUS_TOWNHALL_STATE_REASON: Final[str] = (
    "Multiple observed townhall candidates match a generic base-state question."
)


@dataclass(frozen=True)
class _DeterministicComboPlanTemplate:
    """Static combo plan whose steps still pass the normal command pipeline."""

    objective: str
    trigger_phrases: tuple[str, ...]
    steps: tuple[str, ...]


_OPENING_OPERATION_COMBO_TEMPLATE: Final[_DeterministicComboPlanTemplate] = (
    _DeterministicComboPlanTemplate(
        objective="secure early Terran economy, supply, and information",
        trigger_phrases=(
            "초반 운영",
            "초반운영",
            "초반 운영해",
            "초반운영해",
            "초반 운영 시작",
            "초반운영시작",
            "초반 세팅",
            "초반세팅",
            "초반 빌드",
            "초반빌드",
            "초반 빌드 오더",
            "초반 빌드오더",
            "초반빌드오더",
            "초반 작전",
            "초반작전",
            "오프닝",
            "오프닝 운영",
            "오프닝 빌드",
            "오프닝 작전",
            "opening",
            "opening operation",
            "opening macro",
        ),
        steps=("일꾼 계속 찍어", "보급고 지어", "정찰보내"),
    )
)
_SCOUT_BARRACKS_COMBO_TEMPLATE: Final[_DeterministicComboPlanTemplate] = (
    _DeterministicComboPlanTemplate(
        objective="send early scouting while starting Terran barracks tech",
        trigger_phrases=(
            "정찰보내고 병영올려",
            "정찰 보내고 병영 올려",
            "정찰 보내고 병영 지어",
            "정찰보내고 배럭올려",
            "정찰 보내고 배럭 올려",
            "정찰 보내고 배럭 지어",
            "정찰부터 하고 병영 올려",
            "정찰부터 하고 배럭 올려",
            "스카우트 보내고 병영 올려",
            "스카우트 보내고 배럭 올려",
            "scout and build barracks",
            "send scout and build barracks",
        ),
        steps=("정찰보내", "병영올려"),
    )
)
_STATUS_NEXT_ACTION_COMBO_TEMPLATE: Final[_DeterministicComboPlanTemplate] = (
    _DeterministicComboPlanTemplate(
        objective="read the current commander state before recommending the next action",
        trigger_phrases=(
            "상태 보고하고 지금 할거 알려줘",
            "상태 보고하고 지금 할 거 알려줘",
            "상태 보고하고 다음 할 일 알려줘",
            "상태 보고하고 다음 할일 알려줘",
            "상태 보고하고 다음 행동 알려줘",
            "상태 보고하고 다음 액션 알려줘",
            "상태 보고하고 추천해줘",
            "상태 확인하고 지금 할거 알려줘",
            "상태 확인하고 지금 할 거 알려줘",
            "상태 확인하고 다음 할 일 알려줘",
            "상태 확인하고 다음 할일 알려줘",
            "상태 확인하고 다음 행동 알려줘",
            "상태 확인하고 추천해줘",
            "상황 보고하고 지금 할거 알려줘",
            "상황 보고하고 다음 할 일 알려줘",
            "상황 보고하고 다음 행동 알려줘",
            "전황 보고하고 지금 할거 알려줘",
            "전황 보고하고 다음 할 일 알려줘",
            "브리핑하고 다음 할 일 알려줘",
            "status and next action",
            "status then next action",
        ),
        steps=("상태 보고하", "다음 할 일 알려줘"),
    )
)
_ECONOMY_STABILIZATION_COMBO_TEMPLATE: Final[_DeterministicComboPlanTemplate] = (
    _DeterministicComboPlanTemplate(
        objective="stabilize Terran economy with worker production, resource saturation, and supply",
        trigger_phrases=(
            "경제 안정화",
            "경제안정화",
            "경제 안정화해",
            "경제안정화해",
            "경제 안정시켜",
            "경제안정시켜",
            "경제 안정",
            "경제안정",
            "경제 세팅",
            "경제세팅",
            "경제 최적화",
            "경제최적화",
            "경제 운영",
            "경제운영",
            "자원 안정화",
            "자원안정화",
            "자원 안정",
            "자원안정",
            "자원 최적화",
            "자원최적화",
            "자원 세팅",
            "자원세팅",
            "일꾼 경제 안정화",
            "일꾼경제안정화",
            "economy stabilization",
            "stabilize economy",
            "economic stabilization",
            "economy macro",
        ),
        steps=("일꾼 계속 찍어", "놀고 있는 일꾼들 일시켜", "보급고 지어"),
    )
)
_OPENING_MACRO_PATTERNS: Final[tuple[str, ...]] = (
    _OPENING_OPERATION_COMBO_TEMPLATE.trigger_phrases
)
_DEFENSE_MACRO_PATTERNS: Final[tuple[str, ...]] = (
    "방어해",
    "수비해",
    "막아",
)
_CURRENT_STATE_QUESTION_PATTERNS: Final[tuple[str, ...]] = (
    "지금 어떻게",
    "어떻게 되어",
    "어떻게돼",
    "어떻게 돼",
)
_MACRO_COMBO_HINT_PATTERNS: Final[tuple[str, ...]] = (
    "알아서",
    "한번에",
    "한 번에",
    "동시에",
    "순서대로",
    "운영해",
    "운영 시작",
    "콤보",
    "세팅",
    "준비해",
)
_COMPOUND_FAMILY_PATTERNS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("state", ("상태", "전황", "보고", "브리핑")),
    ("worker_production", ("scv", "일꾼", "worker")),
    ("resource", ("자원", "미네랄", "가스", "채취", "캐", "일시켜")),
    ("build", ("보급고", "서플", "배럭", "병영", "정제소", "가스통", "벙커", "커맨드", "사령부")),
    ("army_production", ("마린", "해병", "marine")),
    ("scout", ("정찰", "스카우트", "scout")),
    ("defend", ("방어", "수비", "입구막", "램프막", "막아")),
    ("repair", ("수리", "고쳐", "repair")),
    ("expand", ("앞마당", "멀티", "확장")),
    ("camera", ("카메라", "화면", "시점", "보여줘")),
)
_CAMERA_SCOUT_LOCATION_REFERENCE_PATTERNS: Final[tuple[str, ...]] = (
    "정찰위치",
    "정찰지점",
    "정찰한곳",
    "scoutlocation",
    "scoutedlocation",
    "lastscoutlocation",
)
"""Scout-memory references that are camera targets, not scout actions."""

_CAMERA_BASE_LOCATION_REFERENCE_PATTERNS: Final[tuple[str, ...]] = (
    "앞마당",
    "내추럴",
    "natural",
    "third",
    "thirdbase",
    "3rdbase",
    "삼룡이",
    "3멀티",
    "세번째멀티",
    "셋째멀티",
)
"""Base/expansion words that can be camera targets rather than combo actions."""

_EXPLICIT_BASE_BUILD_STRUCTURE_TOKENS: Final[tuple[str, ...]] = (
    "보급고",
    "서플",
    "커맨드",
    "사령부",
    "배럭",
    "병영",
    "정제소",
    "가스통",
    "벙커",
    "supplydepot",
    "commandcenter",
    "commandcentre",
    "barracks",
    "refinery",
    "bunker",
)
_COMPOUND_ACTION_VERB_PATTERNS: Final[tuple[str, ...]] = (
    "해",
    "해줘",
    "시작",
    "찍",
    "뽑",
    "생산",
    "채취",
    "캐",
    "지어",
    "짓",
    "올려",
    "건설",
    "보내",
    "정찰",
    "막",
    "방어",
    "수비",
    "수리",
    "확장",
    "보여",
    "옮겨",
)
_UNSUPPORTED_COMPOUND_ACTION_PATTERNS: Final[tuple[str, ...]] = (
    "핵",
    "뉴클리어",
    "시즈",
    "탱크",
    "클로킹",
    "업그레이드",
    "업글",
    "개발",
    "드랍",
    "태워",
    "태우",
    "내려",
    "내리",
    "스팀",
    "스캔",
    "리콜",
)
"""Unsupported action words that can still signal a multi-step Korean combo."""

_UNSUPPORTED_COMPACT_COMPOUND_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:핵|뉴클리어|시즈|탱크|클로킹|업그레이드|업글|개발|드랍|태워|태우|내려|내리|스팀|스캔|리콜)"
    r"[가-힣a-z0-9]*"
    r"(?:하고|하면서|고나서|한다음|은다음|고)"
)
"""Compact Korean unsupported-action connective, e.g. ``시즈업하고``."""

_COMPOUND_MACRO_CLARIFICATION_REASON: Final[str] = (
    "Korean input appears to contain multiple actions but no safe combo plan was available."
)
_COMPOUND_MACRO_CLARIFICATION_PROMPT: Final[str] = (
    "여러 행동이 섞인 명령으로 보여 한 가지 액션만 임의로 실행하지 않았습니다. "
    "필요한 정보(combo_plan): 실행 순서를 짧은 한국어 명령으로 나눠 말해 주세요. "
    "예: 정찰 보내 그리고 보급고 지어 / SCV 계속 찍어 그리고 마린 생산해"
)
_COMPOUND_MACRO_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "정찰 보내 그리고 보급고 지어",
    "SCV 계속 찍어 그리고 마린 생산해",
    "상태 보고하고 지금 할거 알려줘",
)
_DIRECT_DEFENSE_SINGLE_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "입구막아",
        "본진입구막아",
        "입구방어해",
        "본진입구방어해",
        "입구수비해",
        "본진입구수비해",
    }
)


def split_compound_command(text: str) -> tuple[str, ...]:
    """Split one Korean utterance into candidate sub-commands heuristically.

    Splits on the explicit connectives ``그리고`` (also at utterance start)
    and standalone ``하고``, the simultaneous ending ``면서 ``, and sequential
    verb endings limited to a curated verb-stem allowlist (for example ``마린
    6기 입구로 보내고 SCV 계속 찍어``); nouns ending in ``고`` such as 보급고
    are never split apart. Parts are stripped and empties dropped. Simple
    commands without a connective come back as a single part.
    """

    if not isinstance(text, str):
        return ()
    parts = (part.strip() for part in _COMPOUND_COMMAND_SPLIT_PATTERN.split(text))
    return tuple(part for part in parts if part)


def is_compound_or_macro_intent(text: str) -> bool:
    """Return True when one utterance appears to request multiple actions."""

    if not isinstance(text, str) or not text.strip():
        return False
    if _normalize_compound_detection_text(text) in _DIRECT_DEFENSE_SINGLE_COMMANDS:
        return False
    macro_parts = _macro_command_parts_for(text)
    if len(macro_parts) >= 2:
        return True
    if _question_answer_for(text) is not None:
        return False
    if len(split_compound_command(text)) >= 2:
        return True
    return _needs_combo_plan_clarification(text)


def _needs_combo_plan_clarification(text: str) -> bool:
    """Return True when text looks multi-action but lacks safe split boundaries."""

    normalized = _normalize_compound_detection_text(text)
    if _looks_like_unsupported_compound_command(normalized):
        return True
    if _contains_compound_macro_hint(text) and _contains_compound_action_verb(
        normalized
    ):
        return True
    return len(_detected_compound_action_families(normalized)) >= 2


def _normalize_compound_detection_text(text: str) -> str:
    """Normalize text for broad action-family detection without touching secrets."""

    return "".join(text.casefold().split())


def _contains_compound_macro_hint(text: str) -> bool:
    normalized_spaced = " ".join(text.casefold().split())
    normalized_compact = _normalize_compound_detection_text(text)
    return any(
        pattern in normalized_spaced or "".join(pattern.split()) in normalized_compact
        for pattern in _MACRO_COMBO_HINT_PATTERNS
    )


def _contains_compound_action_verb(normalized_text: str) -> bool:
    return any(pattern in normalized_text for pattern in _COMPOUND_ACTION_VERB_PATTERNS)


def _contains_unsupported_compound_action(normalized_text: str) -> bool:
    return any(
        pattern in normalized_text for pattern in _UNSUPPORTED_COMPOUND_ACTION_PATTERNS
    )


def _looks_like_unsupported_compound_command(normalized_text: str) -> bool:
    """Detect unsupported compact Korean compound text before single-intent parse."""

    if not normalized_text or "하고있" in normalized_text:
        return False
    if not _contains_unsupported_compound_action(normalized_text):
        return False
    if _UNSUPPORTED_COMPACT_COMPOUND_PATTERN.search(normalized_text) is not None:
        return True
    return (
        ("하고" in normalized_text or "면서" in normalized_text)
        and _contains_compound_action_verb(normalized_text)
    )


def _detected_compound_action_families(normalized_text: str) -> frozenset[str]:
    """Detect broad command families to catch collapsed multi-action utterances."""

    if not normalized_text:
        return frozenset()
    families = {
        family
        for family, patterns in _COMPOUND_FAMILY_PATTERNS
        if any(pattern in normalized_text for pattern in patterns)
    }
    if "worker_production" in families and not any(
        pattern in normalized_text for pattern in ("찍", "뽑", "생산", "계속")
    ):
        families.discard("worker_production")
    if "army_production" in families and not any(
        pattern in normalized_text for pattern in ("찍", "뽑", "생산")
    ):
        families.discard("army_production")
    if "build" in families and not any(
        pattern in normalized_text for pattern in ("지어", "짓", "올려", "건설", "막아")
    ):
        families.discard("build")
    if "resource" in families and not any(
        pattern in normalized_text for pattern in ("채취", "캐", "일시켜", "붙여")
    ):
        families.discard("resource")
    if "expand" in families and not any(
        pattern in normalized_text for pattern in ("확장", "멀티", "커맨드", "사령부")
    ):
        families.discard("expand")
    if "build" in families and "expand" in families:
        base_selection = parse_korean_base_selection(normalized_text)
        if (
            base_selection is not None
            and any(
                pattern in normalized_text
                for pattern in _EXPLICIT_BASE_BUILD_STRUCTURE_TOKENS
            )
            and any(
                pattern in normalized_text
                for pattern in ("지어", "짓", "올려", "건설")
            )
        ):
            families.discard("expand")
    if "camera" in families and "expand" in families:
        base_selection = parse_korean_base_selection(normalized_text)
        is_camera_location_reference = (
            base_selection is not None
            or any(
                pattern in normalized_text
                for pattern in _CAMERA_BASE_LOCATION_REFERENCE_PATTERNS
            )
        )
        if is_camera_location_reference and not any(
            pattern in normalized_text for pattern in ("지어", "짓", "건설")
        ):
            families.discard("expand")
    if "camera" in families and not any(
        pattern in normalized_text for pattern in ("카메라", "화면", "시점", "보여")
    ):
        families.discard("camera")
    if "camera" in families and "scout" in families and any(
        pattern in normalized_text
        for pattern in _CAMERA_SCOUT_LOCATION_REFERENCE_PATTERNS
    ):
        families.discard("scout")
    return frozenset(families)


def _unresolved_compound_part_needs_whole_clarification(
    part: str,
    interpretation: object,
) -> bool:
    """Return True when executing other split parts would guess an unclear combo."""

    if _safe_interpretation_payload(interpretation) is not None:
        return False
    if _question_answer_for(part) is not None:
        return False
    if is_deictic_build_placement_missing_semantic_target(part):
        return True
    normalized = _normalize_compound_detection_text(part)
    if _contains_unsupported_compound_action(normalized):
        return True
    if any(pattern in normalized for pattern in ("알아서", "아무거나", "적당히", "대충")):
        return _contains_compound_action_verb(normalized)
    return bool(
        _detected_compound_action_families(normalized)
        and _contains_compound_action_verb(normalized)
    )


def _compound_or_macro_clarification_result(
    command_text: str,
) -> CommandInterpretationResult:
    """Ask for a concrete combo split instead of executing one collapsed part."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=_COMPOUND_MACRO_CLARIFICATION_PROMPT,
        reason=_COMPOUND_MACRO_CLARIFICATION_REASON,
        alternatives=_COMPOUND_MACRO_CLARIFICATION_ALTERNATIVES,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code="compound_combo_plan_unavailable",
            message=_COMPOUND_MACRO_CLARIFICATION_REASON,
            alternatives=_COMPOUND_MACRO_CLARIFICATION_ALTERNATIVES,
            metadata={
                "route": "combo",
                "missing_fields": ["combo_plan"],
                "detected_families": sorted(
                    _detected_compound_action_families(
                        _normalize_compound_detection_text(command_text_value)
                    )
                ),
            },
        ),
    )


def _has_explicit_connective(text: str) -> bool:
    """Return whether the utterance contains a standalone 그리고/하고."""

    return _EXPLICIT_CONNECTIVE_PATTERN.search(text) is not None


def _normalize_question_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    normalized = " ".join(text.casefold().split())
    if normalized.startswith("그리고 "):
        normalized = normalized[len("그리고 ") :]
    return normalized.strip()


def _contains_question_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _matches_combo_template(
    text: str,
    template: _DeterministicComboPlanTemplate,
) -> bool:
    """Return whether normalized text contains one of a template's triggers."""

    normalized_spaced = " ".join(text.casefold().split())
    normalized_compact = "".join(normalized_spaced.split())
    return any(
        trigger in normalized_spaced
        or "".join(trigger.casefold().split()) in normalized_compact
        for trigger in template.trigger_phrases
    )


def _format_capability_catalog() -> str:
    """Render the controller's supported Korean command and Q&A surface."""

    command_lines = "; ".join(
        f"{label}: {examples}" for label, examples in _SUPPORTED_COMMAND_CAPABILITIES
    )
    question_lines = ", ".join(_SUPPORTED_QUESTION_CAPABILITIES)
    clarification_lines = "; ".join(_SUPPORTED_CLARIFICATION_EXAMPLES)
    limitation_lines = "; ".join(_SUPPORTED_LIMITATION_LINES)
    return (
        f"지원 명령 예시: {command_lines}. "
        f"지원 질문 예시: {question_lines}. "
        f"확인 질문이 필요한 경우: {clarification_lines}. "
        f"제한: {limitation_lines}."
    )


def _build_capability_question_answer() -> str:
    """Build a general capability answer from the shared controller catalog."""

    return (
        "현재 Commander는 한국어 자연어 명령, 읽기 전용 질문, 필요한 경우의 "
        f"구체 확인 질문을 지원합니다. {_format_capability_catalog()}"
    )


def _build_llm_question_answer() -> str:
    """Build the LLM conversation answer without exposing provider secrets."""

    return (
        "네, 이 Commander는 LLM-first 대화형 입력을 지원합니다. "
        "LLM은 한국어 문장을 command, combo, question, clarification 대상으로 "
        "분류/해석하지만, 실제 게임 변경은 안전 파이프라인이 맡습니다. "
        "API 키나 내부 해석 세부정보는 답변/로그에 노출하지 않습니다. "
        f"{_format_capability_catalog()} "
        "키가 잘못되면 LLM 설정 영역에 실패 이유가 표시되고, 키가 설정됐는데 "
        "게임이 움직이지 않으면 현재 탭이 dry-run 탭이 아닌 실제 Live GUI URL인지 "
        "확인하세요."
    )


def _build_meta_question_answer() -> str:
    """Build a read-only answer for Korean about-this-commander questions."""

    return (
        "저는 LLM-first StarCraft 커맨더입니다. 한국어 자연어를 command, combo, "
        "question, clarification 대상으로 분류하고, 질문은 읽기 전용으로 답합니다. "
        "실제 플레이 명령은 intent 검증, feasibility 검사, planner, executor "
        f"안전 계층을 통과한 경우에만 실행합니다. {_format_capability_catalog()}"
    )


def _macro_command_parts_for(text: str) -> tuple[str, ...]:
    """Return deterministic combo-plan parts for supported macro utterances."""

    normalized = _normalize_question_text(text)
    if not normalized:
        return ()
    if _contains_question_pattern(normalized, _CURRENT_STATE_QUESTION_PATTERNS):
        return ("상태 확인",)
    if _matches_combo_template(normalized, _STATUS_NEXT_ACTION_COMBO_TEMPLATE):
        return _STATUS_NEXT_ACTION_COMBO_TEMPLATE.steps
    if _matches_combo_template(normalized, _OPENING_OPERATION_COMBO_TEMPLATE):
        return _OPENING_OPERATION_COMBO_TEMPLATE.steps
    if _matches_combo_template(normalized, _SCOUT_BARRACKS_COMBO_TEMPLATE):
        return _SCOUT_BARRACKS_COMBO_TEMPLATE.steps
    if _matches_combo_template(normalized, _ECONOMY_STABILIZATION_COMBO_TEMPLATE):
        return _ECONOMY_STABILIZATION_COMBO_TEMPLATE.steps
    if "입구" in normalized and _contains_question_pattern(normalized, _DEFENSE_MACRO_PATTERNS):
        return ("본진 입구에 보급고 지어", "본진 입구에 배럭지어")
    return ()


def _question_answer_for(text: str) -> tuple[str, str] | None:
    """Return a read-only answer for known capability questions."""

    normalized = _normalize_question_text(text)
    if not normalized:
        return None
    if _is_townhall_state_question(normalized):
        return "townhall_state_help", _TOWNHALL_STATE_QUESTION_ANSWER
    if _contains_question_pattern(normalized, _NEXT_ACTION_QUESTION_PATTERNS):
        return "next_action_help", _NEXT_ACTION_QUESTION_ANSWER
    if _contains_question_pattern(normalized, _META_QUESTION_PATTERNS):
        return "commander_meta_help", _build_meta_question_answer()
    if _contains_question_pattern(
        normalized,
        _CAMERA_QUESTION_PATTERNS,
    ) and not _contains_question_pattern(normalized, _CAMERA_COMMAND_PATTERNS):
        return "camera_help", _CAMERA_QUESTION_ANSWER
    if normalized in _CANCEL_QUESTION_PATTERNS:
        return "cancel_help", _CANCEL_QUESTION_ANSWER
    if _contains_question_pattern(normalized, _FAILURE_REASON_QUESTION_PATTERNS):
        return "failure_reason_help", _FAILURE_REASON_QUESTION_ANSWER
    if not _contains_question_pattern(normalized, _QUESTION_MARKERS):
        return None
    if _contains_question_pattern(normalized, _LOCATION_QUESTION_PATTERNS):
        return "building_location_help", _LOCATION_QUESTION_ANSWER
    if _contains_question_pattern(normalized, _VOICE_QUESTION_PATTERNS):
        return "voice_help", _VOICE_QUESTION_ANSWER
    if _contains_question_pattern(normalized, _LLM_QUESTION_PATTERNS):
        return "llm_help", _build_llm_question_answer()
    if _contains_question_pattern(normalized, _CAPABILITY_QUESTION_PATTERNS):
        return "capability_help", _build_capability_question_answer()
    return None


def _is_townhall_state_question(normalized_text: str) -> bool:
    """Return True for read-only current townhall/base state questions."""

    if _contains_question_pattern(normalized_text, _TOWNHALL_STATE_QUESTION_PATTERNS):
        return True
    compact = "".join(normalized_text.split())
    has_base_noun = any(
        token in compact
        for token in (
            "사령부",
            "커맨드",
            "커맨드센터",
            "기지",
            "베이스",
            "본진",
            "앞마당",
        )
    )
    has_state_request = any(
        token in compact
        for token in (
            "상태",
            "어때",
            "어떻게",
            "알려",
            "보고",
            "확인",
        )
    )
    return has_base_noun and has_state_request


def _is_generic_townhall_state_question(command_text: str) -> bool:
    """Return True when a townhall-state question lacks a concrete base target."""

    normalized = _normalize_question_text(command_text)
    return _is_townhall_state_question(normalized) and (
        _townhall_state_requested_target(normalized) is None
    )


def _townhall_state_requested_target(command_text: str) -> str | None:
    """Return a concrete requested base label from a townhall-state question."""

    normalized = _normalize_question_text(command_text)
    compact = "".join(normalized.split())
    if any(token in compact for token in ("본진", "메인", "main")):
        return "본진 사령부"
    if any(token in compact for token in ("앞마당", "앞멀티", "내추럴", "natural")):
        return "앞마당 사령부"
    if "멀티" in compact:
        return "멀티 사령부"
    return None


def _filter_townhall_choices_for_target(
    choices: tuple[str, ...],
    requested_target: str | None,
) -> tuple[str, ...]:
    """Select observed townhall labels matching the user's concrete target."""

    if requested_target is None:
        return choices
    if requested_target == "본진 사령부":
        return tuple(choice for choice in choices if "본진" in choice)
    if requested_target == "앞마당 사령부":
        return tuple(choice for choice in choices if "앞마당" in choice)
    if requested_target == "멀티 사령부":
        expansion_choices = tuple(choice for choice in choices if "본진" not in choice)
        return expansion_choices
    return ()


def _build_ambiguous_townhall_state_result(
    command_text: str,
    choices: tuple[str, ...],
) -> CommandInterpretationResult:
    """Ask which observed townhall/base state the user wants."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    alternatives = choices or ("본진 사령부 상태 알려줘", "앞마당 사령부 상태 알려줘")
    choices_text = ", ".join(alternatives)
    prompt = (
        "어느 사령부/기지 상태를 확인할지 몰라 실행하지 않았습니다. "
        "필요한 정보(target): 위 선택지 중 어느 사령부인지 말해 주세요. "
        f"가능한 선택지: {choices_text}. "
        "예: 본진 사령부 상태 알려줘 / 앞마당 사령부 상태 알려줘"
    )
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=prompt,
        reason=_AMBIGUOUS_TOWNHALL_STATE_REASON,
        alternatives=alternatives,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=_AMBIGUOUS_TOWNHALL_STATE_FAILURE_CODE,
            message=_AMBIGUOUS_TOWNHALL_STATE_REASON,
            alternatives=alternatives,
            metadata={
                "route": "question",
                "topic": "townhall_state_help",
                "missing_fields": ["target"],
                "ambiguous_base": True,
                "candidate_townhalls": list(alternatives),
            },
        ),
    )


def _failure_reason_answer_from_memory(event_memory: object | None) -> str:
    """Answer a why-failed question from recent read-only command memory."""

    event = _recent_blocking_event(event_memory)
    if event is None:
        return _FAILURE_REASON_QUESTION_ANSWER
    command_text = _event_value(event, "command_text")
    narration = _event_value(event, "narration")
    command_fragment = f"`{command_text}` 명령은 " if command_text else ""
    return (
        f"{command_fragment}실행되지 않았습니다. 이유: {narration} "
        "이 답변은 읽기 전용이라 게임 명령을 실행하지 않습니다."
    )


def _recent_blocking_event(event_memory: object | None) -> object | None:
    """Return the most recent blocked/clarification event, if memory supports it."""

    recent = getattr(event_memory, "recent", None) if event_memory is not None else None
    if not callable(recent):
        return None
    try:
        events = tuple(recent(10))
    except Exception:  # noqa: BLE001 - memory failures must not route to execution
        return None
    for event in reversed(events):
        status = _event_value(event, "status")
        if status not in {"blocked", "clarification"}:
            continue
        if _event_value(event, "narration"):
            return event
    return None


def _failure_reason_answer_from_context(
    event_memory: object | None,
    state: SC2CommanderState | None,
    standing_orders: object | None,
) -> str:
    """Answer why-not-possible questions from memory, then live controller state."""

    memory_answer = _failure_reason_answer_from_memory(event_memory)
    if memory_answer != _FAILURE_REASON_QUESTION_ANSWER:
        return memory_answer

    facts = _state_fact_sentence(state)
    blockers = _current_blockers_from_state(state)
    order_status = _standing_order_status(standing_orders)
    if blockers:
        blocker_text = " / ".join(blockers)
        context = f"{facts} 현재 막힐 가능성이 큰 이유: {blocker_text}"
        if order_status:
            context += f" {order_status}"
        return (
            f"{context} 최근 실패 기록은 없어서 직전 명령 기준이 아니라 "
            "현재 관측 기준으로 답합니다. 이 답변은 읽기 전용이라 게임 명령을 실행하지 않습니다."
        )

    if state is None:
        return (
            "최근 실패 기록이 없고 현재 게임 상태도 읽지 못했습니다. Live GUI 연결과 "
            "SC2 런타임 바인딩을 확인하세요. 이 답변은 읽기 전용이라 게임 명령을 실행하지 않습니다."
        )

    context = facts
    if order_status:
        context += f" {order_status}"
    return (
        f"{context} 현재 관측만으로는 명확한 차단 이유가 보이지 않습니다. "
        "명령이 막히면 직전 blocked/clarification 기록의 이유를 우선 확인하세요. "
        "이 답변은 읽기 전용이라 게임 명령을 실행하지 않습니다."
    )


def _next_action_answer_from_state(
    state: SC2CommanderState | None,
    event_memory: object | None = None,
    standing_orders: object | None = None,
) -> str:
    """Answer next-action questions from the current read-only commander state."""

    strategy = _current_strategy_sentence(state, event_memory)
    if state is None:
        return f"{strategy} {_NEXT_ACTION_NO_STATE_ANSWER}"

    facts = _state_fact_sentence(state)
    advice = _next_action_recommendation_from_state(state)
    controller_context = _next_action_controller_context(event_memory, standing_orders)
    caveat = (
        " 관측 누락이 있어 먼저 `상태 보고하`로 확인하세요."
        if not state.observation_complete
        else ""
    )
    return (
        f"{strategy} {facts} 추천 흐름: {advice}{controller_context}{caveat} "
        "이 답변은 읽기 전용이라 게임 명령을 실행하지 않습니다."
    )


def _targeting_answer_from_context(
    state: SC2CommanderState | None,
    map_resolver: object | None,
    game_bot: object | None,
) -> str:
    """Answer targeting/location questions from observed entities and targets."""

    sections = [
        "건물 위치와 명령 대상은 의미 기반 위치, 즉 semantic target으로 지정합니다.",
        _state_fact_sentence(state),
        _selected_units_sentence(game_bot),
        _visible_entities_sentence(state),
        _semantic_target_sentence(map_resolver),
    ]
    return (
        " ".join(section for section in sections if section)
        + " 예: `본진에 배럭 지어`, `본진 입구에 보급고 지어`, "
        "`앞마당에 커맨드 지어`, `적 앞마당으로 정찰보내`. "
        "이 답변은 읽기 전용이라 게임 명령을 실행하지 않습니다."
    )


def _camera_answer_from_context(
    state: SC2CommanderState | None,
    map_resolver: object | None,
    runtime: object | None,
    game_bot: object | None,
) -> str:
    """Answer camera questions from runtime capability and target context."""

    capability = _camera_capability_sentence(runtime, game_bot)
    camera_position = _camera_position_sentence(game_bot)
    sections = [
        _CAMERA_QUESTION_ANSWER,
        capability,
        camera_position,
        _state_fact_sentence(state),
        _selected_units_sentence(game_bot),
        _visible_entities_sentence(state),
        _semantic_target_sentence(map_resolver),
    ]
    return (
        " ".join(section for section in sections if section)
        + " 예: `카메라 적 본진으로 옮겨`, `화면 본진 입구로 이동`. "
        "이 답변은 읽기 전용이라 카메라나 유닛을 실제로 움직이지 않습니다."
    )


def _townhall_state_answer_from_context(
    command_text: str,
    state: SC2CommanderState | None,
    game_bot: object | None,
) -> str:
    """Answer current townhall/base state questions without game mutation."""

    if state is None:
        return (
            "현재 사령부/기지 상태: 게임 상태를 읽지 못했습니다. "
            "이 답변은 읽기 전용이라 게임 명령을 실행하지 않습니다."
        )
    choices = _command_center_base_choices(game_bot, state)
    requested_target = _townhall_state_requested_target(command_text)
    selected_choices = _filter_townhall_choices_for_target(choices, requested_target)
    if requested_target and not selected_choices:
        selected_choices = (f"{requested_target} 후보를 현재 관측에서 찾지 못함",)
    if not selected_choices:
        selected_choices = choices
    candidate_text = (
        ", ".join(selected_choices)
        if selected_choices
        else "현재 관측된 사령부 후보 없음"
    )
    completed_count = sum(
        count
        for name, count in state.own_structures.items()
        if _normalized_structure_name(name) in _COMMAND_CENTER_STRUCTURE_NAMES
    )
    in_progress_count = sum(
        count
        for name, count in state.structures_in_progress.items()
        if _normalized_structure_name(name) in _COMMAND_CENTER_STRUCTURE_NAMES
    )
    target_prefix = f"요청 대상: {requested_target}. " if requested_target else ""
    caveat = (
        " 관측 누락이 있어 후보 수가 보수적으로 표시될 수 있습니다."
        if not state.observation_complete
        else ""
    )
    return (
        f"현재 사령부/기지 상태: {target_prefix}후보 {len(selected_choices)}개: "
        f"{candidate_text}. 완성 사령부 {completed_count}, 건설 중 사령부 {in_progress_count}. "
        f"{_state_fact_sentence(state)}{caveat} "
        "이 답변은 읽기 전용이라 게임 명령을 실행하지 않습니다."
    )


def _state_fact_sentence(state: SC2CommanderState | None) -> str:
    """Render compact live-state facts for read-only question answers."""

    if state is None:
        return "현재 관측: 게임 상태를 읽지 못했습니다."
    return (
        f"현재 관측: 미네랄 {state.minerals}, 가스 {state.vespene}, "
        f"보급 {state.supply_used}/{state.supply_cap}(여유 {state.supply_left}), "
        f"유휴 SCV {state.idle_worker_count}, 병력 {state.army_count}."
    )


def _selected_units_sentence(game_bot: object | None) -> str:
    """Render selected-unit context without depending on python-sc2 types."""

    if game_bot is None:
        return "선택 유닛: 런타임이 연결되지 않아 확인할 수 없습니다."
    selected = _first_readable_attribute(
        game_bot,
        ("selected_units", "selected_units_tags", "selected"),
    )
    if selected is None:
        return "선택 유닛: 현재 선택 정보가 노출되지 않았습니다."
    if isinstance(selected, (str, bytes)):
        selected_items = (selected,)
    else:
        try:
            selected_items = tuple(selected)
        except Exception:  # noqa: BLE001 - Q&A must not crash on BotAI wrappers
            return "선택 유닛: 선택 정보를 읽을 수 없습니다."
    if not selected_items:
        return "선택 유닛: 없음."
    named_counts: dict[str, int] = {}
    tag_count = 0
    for item in selected_items:
        name = _unit_display_name(item)
        if name:
            named_counts[name] = named_counts.get(name, 0) + 1
        else:
            tag_count += 1
    parts = [f"{name} {count}" for name, count in sorted(named_counts.items())]
    if tag_count:
        parts.append(f"태그 {tag_count}개")
    return f"선택 유닛: {', '.join(parts)}."


def _visible_entities_sentence(state: SC2CommanderState | None) -> str:
    """Render visible enemy entities from the commander state."""

    if state is None:
        return "보이는 적: 게임 상태를 읽지 못해 확인할 수 없습니다."
    visible_parts = [
        *_count_mapping_parts(state.visible_enemy_units),
        *_count_mapping_parts(state.visible_enemy_structures),
    ]
    if not visible_parts:
        return "보이는 적: 현재 관측된 적 유닛/건물이 없습니다."
    return f"보이는 적: {', '.join(visible_parts)}."


def _semantic_target_sentence(map_resolver: object | None) -> str:
    """Render semantic target availability and first failure reasons."""

    if map_resolver is None:
        return "semantic target: 지도 리졸버를 만들 수 없어 사용 가능 위치를 확인하지 못했습니다."
    catalog = getattr(map_resolver, "semantic_target_catalog", None)
    if catalog is None:
        return "semantic target: 지도 리졸버가 target catalog를 제공하지 않습니다."
    try:
        entries = tuple(catalog)
    except Exception:  # noqa: BLE001 - Q&A must stay resilient
        return "semantic target: target catalog를 읽을 수 없습니다."
    available = [entry for entry in entries if bool(getattr(entry, "available", False))]
    unavailable = [
        entry
        for entry in entries
        if not bool(getattr(entry, "available", False))
        and str(getattr(entry, "failure_reason", "")).strip()
    ]
    available_text = (
        "가능 위치: "
        + ", ".join(_semantic_entry_label(entry) for entry in available[:6])
        if available
        else "가능 위치: 없음"
    )
    unavailable_text = ""
    if unavailable:
        unavailable_text = " 불가 예시: " + " / ".join(
            _semantic_entry_failure(entry) for entry in unavailable[:2]
        )
    return f"semantic target: {available_text}.{unavailable_text}"


def _camera_capability_sentence(runtime: object | None, game_bot: object | None) -> str:
    """Render whether the current runtime exposes a camera movement API."""

    if runtime is None and game_bot is None:
        return "카메라 API: 런타임이 연결되지 않아 사용할 수 없습니다."
    runtime_has_method = callable(getattr(runtime, "move_camera", None))
    explicit_camera_flag = _safe_attribute(game_bot, "supports_camera")
    bot_has_method = (
        explicit_camera_flag is not False
        and any(
            callable(getattr(game_bot, method_name, None))
            for method_name in (
                "move_camera",
                "center_camera",
                "set_camera_position",
                "move_camera_spatial",
            )
        )
    )
    client = (
        _first_readable_attribute(game_bot, ("client", "_client"))
        if game_bot is not None
        else None
    )
    client_has_method = any(
        callable(getattr(client, method_name, None))
        for method_name in ("move_camera", "center_camera")
    )
    if runtime_has_method and (bot_has_method or client_has_method):
        return "카메라 API: 현재 런타임에서 MOVE_CAMERA 실행을 지원합니다."
    if runtime_has_method:
        return (
            "카메라 API: 계획/검증 경로는 있지만 현재 BotAI/클라이언트가 "
            "카메라 이동 메서드를 노출하지 않습니다."
        )
    return "카메라 API: 현재 런타임 어댑터가 MOVE_CAMERA 메서드를 제공하지 않습니다."


def _camera_position_sentence(game_bot: object | None) -> str:
    """Render current camera position if the runtime exposes one."""

    if game_bot is None:
        return "현재 카메라: 런타임이 없어 확인할 수 없습니다."
    candidate = _first_readable_attribute(
        game_bot,
        (
            "camera_position",
            "camera_center",
            "camera_location",
            "screen_center",
        ),
    )
    point_text = _point_text(candidate)
    if point_text:
        return f"현재 카메라: {point_text}."
    client = _first_readable_attribute(game_bot, ("client", "_client"))
    point_text = _point_text(
        _first_readable_attribute(
            client,
            ("camera_position", "camera_center", "camera_location"),
        )
    )
    if point_text:
        return f"현재 카메라: {point_text}."
    return "현재 카메라: 위치 정보가 노출되지 않았습니다."


def _count_mapping_parts(counts: Mapping[str, int]) -> list[str]:
    return [f"{name} {count}" for name, count in sorted(dict(counts).items()) if count > 0]


def _semantic_entry_label(entry: object) -> str:
    target = str(getattr(entry, "target", "unknown"))
    label = SC2_KOREAN_TARGET_NAMES.get(target, target)
    position = getattr(entry, "position", None)
    point = _point_text(position)
    source = str(getattr(entry, "source", "") or "").strip()
    source_suffix = f", {source}" if source else ""
    return f"{label}({target}{', ' + point if point else ''}{source_suffix})"


def _semantic_entry_failure(entry: object) -> str:
    target = str(getattr(entry, "target", "unknown"))
    label = SC2_KOREAN_TARGET_NAMES.get(target, target)
    reason = str(getattr(entry, "failure_reason", "") or "").strip()
    return f"{label}({target}: {reason})"


def _unit_display_name(unit: object) -> str:
    if isinstance(unit, str):
        return unit.strip()
    for attribute in ("name", "type_name"):
        value = _safe_attribute(unit, attribute)
        if isinstance(value, str) and value.strip():
            return value.strip()
    type_id = _safe_attribute(unit, "type_id")
    value = _safe_attribute(type_id, "name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _point_text(candidate: object) -> str:
    if candidate is None:
        return ""
    point = candidate
    x = _safe_attribute(point, "x")
    y = _safe_attribute(point, "y")
    if _is_real_number(x) and _is_real_number(y):
        return f"({float(x):.1f}, {float(y):.1f})"
    position = _safe_attribute(candidate, "position")
    if position is not None and position is not candidate:
        return _point_text(position)
    if isinstance(candidate, (tuple, list)) and len(candidate) == 2:
        x, y = candidate
        if _is_real_number(x) and _is_real_number(y):
            return f"({float(x):.1f}, {float(y):.1f})"
    return ""


def _is_real_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(
        float(value)
    )


def _first_readable_attribute(obj: object | None, names: tuple[str, ...]) -> object | None:
    if obj is None:
        return None
    for name in names:
        value = _safe_attribute(obj, name)
        if value is not None:
            return value
    return None


def _safe_attribute(obj: object | None, name: str) -> object | None:
    if obj is None:
        return None
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _next_action_controller_context(
    event_memory: object | None,
    standing_orders: object | None,
) -> str:
    """Render optional controller context without exposing internal request text."""

    parts: list[str] = []
    order_status = _standing_order_status(standing_orders)
    if order_status:
        parts.append(order_status)
    memory_hint = _recent_memory_hint(event_memory)
    if memory_hint:
        parts.append(memory_hint)
    if not parts:
        return ""
    return " " + " ".join(parts)


def _standing_order_status(standing_orders: object | None) -> str:
    """Return a safe Korean standing-order status line, if available."""

    status = getattr(standing_orders, "korean_status", None)
    if not callable(status):
        return ""
    try:
        status_line = str(status()).strip()
    except Exception:  # noqa: BLE001 - Q&A must stay read-only and resilient
        return ""
    if not status_line:
        return ""
    return f"현재 {status_line}."


def _recent_memory_hint(event_memory: object | None) -> str:
    """Summarize recent controller memory as counts, not internal request text."""

    recent = getattr(event_memory, "recent", None) if event_memory is not None else None
    if not callable(recent):
        return ""
    try:
        events = tuple(recent(5))
    except Exception:  # noqa: BLE001 - Q&A must stay read-only and resilient
        return ""
    if not events:
        return ""
    success_count = sum(
        1
        for event in events
        if _event_value(event, "status") in _EXECUTED_OUTCOME_STATUSES
    )
    blocked_count = sum(
        1
        for event in events
        if _event_value(event, "status") in {"blocked", "clarification"}
    )
    return (
        f"최근 기록 {len(events)}건 중 성공/정보 {success_count}건, "
        f"차단/확인필요 {blocked_count}건."
    )


def _current_blockers_from_state(state: SC2CommanderState | None) -> tuple[str, ...]:
    """Infer likely blockers from live state without validating a fake command."""

    if state is None:
        return ("게임 상태를 읽지 못해 mutating 명령을 안전하게 실행할 수 없음",)
    blockers: list[str] = []
    if not state.observation_complete:
        blockers.append("관측 정보가 불완전함")
    if state.supply_left <= 0:
        blockers.append("보급이 막힘")
    elif state.supply_left <= 1:
        blockers.append("보급 여유가 거의 없음")
    if state.minerals < 50:
        blockers.append(f"미네랄이 낮음({state.minerals})")
    own_structures = set(state.own_structures)
    structures_in_progress = set(state.structures_in_progress)
    known_structures = own_structures | structures_in_progress
    if "COMMANDCENTER" not in own_structures:
        blockers.append("완성된 사령부가 없어 SCV 생산이 불가함")
    if "BARRACKS" not in known_structures:
        blockers.append("병영이 없어 해병 생산/방어 병력 확보가 불가함")
    if state.army_count <= 0:
        blockers.append("전투 병력이 없어 방어/견제 명령이 제한됨")
    if not state.own_units.get("SCV", 0):
        blockers.append("가용 SCV가 없어 건설/수리/채취 명령이 제한됨")
    return tuple(blockers[:4])


def _current_strategy_sentence(
    state: SC2CommanderState | None,
    event_memory: object | None,
) -> str:
    """Identify the player's current strategic posture in Korean."""

    return f"현재 전략: {_current_strategy_summary(state, event_memory)}"


def _current_strategy_summary(
    state: SC2CommanderState | None,
    event_memory: object | None,
) -> str:
    """Infer a concise, user-facing strategy label from safe context."""

    recent_texts = _recent_command_texts(event_memory)
    text = " ".join(recent_texts).lower()
    if text:
        if "정찰" in text or "scout" in text:
            return "정찰 중심으로 정보 우위를 확보하는 운영입니다."
        if any(token in text for token in ("방어", "수비", "입구", "벙커", "막아")):
            return "본진 방어와 생존을 우선하는 수비 운영입니다."
        if any(token in text for token in ("병영", "배럭", "마린", "barracks", "marine")):
            return "테란 생산 인프라를 확보하는 운영입니다."
        if any(token in text for token in ("scv", "자원", "미네랄", "보급", "일꾼")):
            return "경제와 생산 기반을 안정화하는 운영입니다."

    known_structures: set[str] = set()
    if state is not None:
        known_structures.update(str(name).upper() for name in state.own_structures)
        known_structures.update(str(name).upper() for name in state.structures_in_progress)
    if "BARRACKS" in known_structures:
        return "테란 생산 인프라를 확보하는 운영입니다."
    if known_structures & {"SUPPLYDEPOT", "REFINERY", "COMMANDCENTER"}:
        return "경제와 생산 기반을 안정화하는 운영입니다."
    return "아직 명령 기록이 부족해 전장 상태 파악 단계입니다."


def _recent_command_texts(event_memory: object | None) -> tuple[str, ...]:
    """Read recent command texts defensively without exposing internal prompts."""

    recent = getattr(event_memory, "recent", None) if event_memory is not None else None
    if not callable(recent):
        return ()
    try:
        events = tuple(recent(5))
    except Exception:  # noqa: BLE001 - Q&A must stay read-only and resilient
        return ()
    return tuple(
        command_text
        for event in events
        if (command_text := _event_value(event, "command_text"))
    )


def _next_action_recommendation_from_state(state: SC2CommanderState) -> str:
    """Choose one conservative, explainable next-action recommendation."""

    if state.idle_worker_count > 0:
        return "유휴 일꾼을 먼저 미네랄이나 가스로 붙이세요."
    if state.supply_left <= 1:
        return "보급이 막히기 직전이므로 보급고를 먼저 확보하세요."
    own_structures = set(state.own_structures)
    structures_in_progress = set(state.structures_in_progress)
    has_barracks = bool({"BARRACKS"} & (own_structures | structures_in_progress))
    has_refinery = bool({"REFINERY"} & (own_structures | structures_in_progress))
    if state.minerals >= 150 and not has_barracks:
        return "병영이 없으니 배럭을 올리고 정제소 준비를 이어가세요."
    if state.minerals >= 75 and not has_refinery:
        return "가스 기반 테크를 위해 본진 가스에 정제소를 준비하세요."
    if state.army_count <= 0 and has_barracks:
        return "병영이 있으니 마린 생산을 시작하고 정찰을 유지하세요."
    return "상태 확인, 일꾼 생산, 보급 여유, 병력 생산, 정찰을 순서대로 점검하세요."


def _event_value(event: object, field_name: str) -> str:
    """Read a string field from an event object or mapping."""

    if isinstance(event, Mapping):
        value = event.get(field_name, "")
    else:
        value = getattr(event, field_name, "")
    return str(value or "").strip()


def _command_center_count(state: SC2CommanderState) -> int:
    """Count own completed or in-progress Terran townhalls from observed state."""

    total = 0
    for counts in (state.own_structures, state.structures_in_progress):
        total += sum(
            count
            for name, count in counts.items()
            if _normalized_structure_name(name) in _COMMAND_CENTER_STRUCTURE_NAMES
        )
    return total


def _command_center_base_choices(
    game_bot: object | None,
    state: SC2CommanderState,
) -> tuple[str, ...]:
    """Render observed townhall choices for a concrete Korean clarification."""

    structures = _observed_command_centers(game_bot)
    if structures:
        choices = _observed_command_center_labels(structures, game_bot)
        if choices:
            return choices
    return _fallback_command_center_choices(state)


def _observed_command_centers(game_bot: object | None) -> tuple[object, ...]:
    """Return own townhall-like structure objects from BotAI observations."""

    structures = _safe_attribute(game_bot, "structures")
    if structures is None:
        return ()
    try:
        entries = tuple(structures)
    except TypeError:
        return ()
    except Exception:  # noqa: BLE001 - clarification should survive bad fakes/adapters
        return ()
    return tuple(
        structure
        for structure in entries
        if _normalized_structure_name(_unit_display_name(structure))
        in _COMMAND_CENTER_STRUCTURE_NAMES
    )


def _observed_command_center_labels(
    structures: tuple[object, ...],
    game_bot: object | None,
) -> tuple[str, ...]:
    main_point = _point_xy(_safe_attribute(game_bot, "start_location"))
    natural_point = _natural_expansion_point(game_bot, main_point)
    labels: list[str] = []
    extra_index = 1
    for structure in structures:
        point = _point_xy(structure)
        if point is not None and main_point is not None and _distance_sq(point, main_point) <= 25:
            label = "본진 사령부"
        elif (
            point is not None
            and natural_point is not None
            and _distance_sq(point, natural_point) <= 25
        ):
            label = "앞마당 사령부"
        else:
            label = f"추가 사령부 {extra_index}"
            extra_index += 1
        point_text = _point_text(structure)
        labels.append(f"{label}{point_text}" if point_text else label)
    return tuple(_deduplicated_labels(labels))


def _fallback_command_center_choices(state: SC2CommanderState) -> tuple[str, ...]:
    count = _command_center_count(state)
    if count <= 1:
        return ()
    labels = ["본진 사령부", "앞마당 사령부"]
    labels.extend(f"추가 사령부 {index}" for index in range(1, count - 1))
    return tuple(labels[:count])


def _camera_base_answer_target(command_text: str) -> str | None:
    """Map a concrete Korean clarification answer to a semantic camera target."""

    normalized = str(command_text or "").strip().lower()
    compact = re.sub(r"[\s\.,!?？。]+", "", normalized)
    if not compact:
        return None

    has_camera_followup = _contains_question_pattern(
        normalized,
        (*_CAMERA_QUESTION_PATTERNS, *_CAMERA_COMMAND_PATTERNS),
    )
    has_non_camera_action = any(
        token in compact
        for token in (
            "지어",
            "짓",
            "건설",
            "뽑",
            "찍",
            "생산",
            "보내",
            "정찰",
            "막",
            "수리",
            "공격",
            "채취",
        )
    )
    if has_non_camera_action and not has_camera_followup:
        return None

    base_selection = parse_korean_base_selection(command_text)
    if base_selection is not None:
        return base_selection.location

    main_selected = any(
        token in compact
        for token in (
            "본진",
            "메인",
            "main",
            "1번",
            "첫번째",
            "첫째",
            "첫사령부",
        )
    )
    natural_selected = any(
        token in compact
        for token in (
            "앞마당",
            "내추럴",
            "natural",
            "2번",
            "두번째",
            "둘째",
            "두번째사령부",
            "확장",
        )
    )
    if main_selected == natural_selected:
        return None
    if natural_selected:
        return "natural expansion"
    return "main base"


def _build_base_answer_target(command_text: str) -> str | None:
    """Map a base-only clarification answer to a semantic build location."""

    normalized = str(command_text or "").strip().lower()
    compact = re.sub(r"[\s\.,!?？。]+", "", normalized)
    if not compact or _question_answer_for(normalized) is not None:
        return None
    has_new_action = any(
        token in compact
        for token in (
            "지어",
            "짓",
            "건설",
            "뽑",
            "찍",
            "생산",
            "보내",
            "정찰",
            "막",
            "수리",
            "공격",
            "채취",
            "상태",
            "알려",
            "왜",
            "뭐",
            "어디",
            "가능",
        )
    )
    if has_new_action:
        return None

    base_selection = parse_korean_base_selection(command_text)
    if base_selection is not None:
        return base_selection.location

    main_selected = any(
        token in compact
        for token in (
            "본진",
            "메인",
            "main",
            "1번",
            "첫번째",
            "첫째",
            "첫사령부",
        )
    )
    natural_selected = any(
        token in compact
        for token in (
            "앞마당",
            "내추럴",
            "natural",
            "2번",
            "두번째",
            "둘째",
            "두번째사령부",
            "확장",
        )
    )
    if main_selected == natural_selected:
        return None
    if natural_selected:
        return "natural expansion"
    return "main base"


def _rewrite_ambiguous_build_base_command(command_text: str, target: str) -> str:
    """Inject the clarified base while keeping the pending build request."""

    original = str(command_text or "").strip()
    if target == "natural expansion":
        base_label = "앞마당 사령부"
    elif target == "third base":
        base_label = "세번째 사령부"
    elif target == "newest base":
        base_label = "새로 지은 사령부"
    elif target.startswith("additional base "):
        base_label = f"추가 사령부 {target.removeprefix('additional base ')}"
    else:
        base_label = "본진 사령부"
    rewritten, replacement_count = re.subn(
        r"(사령부|커맨드\s*센터|커맨드센터|커맨드|기지|베이스)",
        base_label,
        original,
        count=1,
    )
    if replacement_count:
        return rewritten
    return f"{base_label} 근처에 {original}".strip()


def _natural_expansion_point(
    game_bot: object | None,
    main_point: tuple[float, float] | None,
) -> tuple[float, float] | None:
    if main_point is None:
        return None
    expansions = _safe_attribute(game_bot, "expansion_locations_list")
    if expansions is None:
        return None
    try:
        points = tuple(
            point
            for candidate in expansions
            if (point := _point_xy(candidate)) is not None
            and _distance_sq(point, main_point) > 4
        )
    except TypeError:
        return None
    except Exception:  # noqa: BLE001 - bad observation data should only reduce detail
        return None
    if not points:
        return None
    return min(points, key=lambda point: _distance_sq(point, main_point))


def _deduplicated_labels(labels: list[str]) -> list[str]:
    seen: set[str] = set()
    deduplicated: list[str] = []
    for label in labels:
        if label in seen:
            continue
        seen.add(label)
        deduplicated.append(label)
    return deduplicated


def _normalized_structure_name(name: object) -> str:
    return str(name or "").upper().replace(" ", "").replace("_", "")


def _point_xy(candidate: object) -> tuple[float, float] | None:
    if candidate is None:
        return None
    x = _safe_attribute(candidate, "x")
    y = _safe_attribute(candidate, "y")
    if _is_real_number(x) and _is_real_number(y):
        return (float(x), float(y))
    position = _safe_attribute(candidate, "position")
    if position is not None and position is not candidate:
        return _point_xy(position)
    if isinstance(candidate, (tuple, list)) and len(candidate) == 2:
        x, y = candidate
        if _is_real_number(x) and _is_real_number(y):
            return (float(x), float(y))
    return None


def _distance_sq(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _question_outcome(command_text: str, topic: str, answer: str) -> SC2CommandOutcome:
    """Build a read-only outcome for commander questions without touching SC2."""

    action = SC2CommandAction(
        action_type=SC2ActionType.OBSERVE,
        subject="help",
        target=topic,
        count=1,
        metadata={"question": command_text},
    )
    plan = SC2ExecutionPlan(
        intent_name=_ANSWER_QUESTION_INTENT_NAME,
        priority="normal",
        ordered_actions=(action,),
        constraints=("answer commander question without issuing game actions",),
        requires_live_sc2=False,
        notes=("Question answers are read-only and never issue SC2 API commands.",),
        audit={"topic": topic},
    )
    execution_result = SC2PlanExecutionResult(
        plan=plan,
        attempted_actions=(action,),
        applied_actions=(action,),
        audit={"topic": topic},
    )
    return SC2CommandOutcome(
        command_text=command_text,
        status="read_only",
        narration=answer,
        intent_dsl={
            "intent": _ANSWER_QUESTION_INTENT_NAME,
            "topic": topic,
            "read_only": True,
        },
        plan=plan,
        execution_result=execution_result,
    )


def _camera_target_clarification_for_plan(
    command_text: str,
    plan: SC2ExecutionPlan,
    map_resolver: object | None,
) -> SC2CommandOutcome | None:
    """Ask a concrete reverse question for ambiguous camera map targets."""

    if map_resolver is None:
        return None
    resolve = getattr(map_resolver, "resolve", None)
    if not callable(resolve):
        resolve = getattr(map_resolver, "lookup", None)
    if not callable(resolve):
        return None
    for action in plan.actions:
        if action.action_type != SC2ActionType.MOVE_CAMERA:
            continue
        try:
            resolution = resolve(action.target)
        except Exception:  # noqa: BLE001 - ambiguity preflight must not crash commands
            continue
        if bool(getattr(resolution, "available", False)):
            continue
        reason = str(getattr(resolution, "reason", "") or "")
        if not _is_ambiguous_camera_target_reason(reason):
            continue
        alternatives = _resolution_alternatives(resolution)
        alternatives_text = ", ".join(alternatives) if alternatives else action.target
        return SC2CommandOutcome(
            command_text=command_text,
            status="clarification",
            narration=(
                "카메라 대상 위치가 여러 후보와 맞아 실행하지 않았습니다. "
                "필요한 정보(target): 어느 위치로 카메라를 이동할까요? "
                f"가능한 선택지: {alternatives_text}. "
                "예: 본진 입구로 카메라 옮겨 / 적 입구 보여줘"
            ),
        )
    return None


def _is_ambiguous_camera_target_reason(reason: str) -> bool:
    normalized = reason.casefold()
    return "ambiguous" in normalized or "multiple" in normalized


def _resolution_alternatives(resolution: object) -> tuple[str, ...]:
    try:
        return tuple(str(item) for item in getattr(resolution, "alternatives", ()))
    except TypeError:
        return ()


@dataclass(frozen=True)
class SC2CommandOutcome:
    """Structured outcome for one commander command (or compound part).

    ``narration`` is the commander-facing Korean response. ``intent_dsl``,
    ``plan``, ``execution_result``, and ``feasibility`` carry the structured
    pipeline artifacts that were actually produced; stages that never ran stay
    ``None`` so a blocked or clarification outcome can never masquerade as an
    executed one.
    """

    command_text: str
    status: SC2CommandOutcomeStatus
    narration: str
    intent_dsl: Mapping[str, object] | None = None
    plan: SC2ExecutionPlan | None = None
    execution_result: SC2PlanExecutionResult | None = None
    feasibility: SC2FeasibilityResult | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_text", str(self.command_text))
        if self.status not in SC2_COMMAND_OUTCOME_STATUSES:
            supported = ", ".join(sorted(SC2_COMMAND_OUTCOME_STATUSES))
            raise ValueError(
                f"SC2 command outcome status must be one of: {supported}. "
                f"Unknown status: {self.status!r}."
            )
        if not str(self.narration).strip():
            raise ValueError("SC2 command outcome narration must be non-empty.")
        object.__setattr__(self, "narration", str(self.narration))
        if self.intent_dsl is not None:
            if not isinstance(self.intent_dsl, Mapping):
                raise TypeError("SC2 command outcome intent_dsl must be a mapping or None.")
            object.__setattr__(self, "intent_dsl", dict(self.intent_dsl))
        if self.plan is not None and not isinstance(self.plan, SC2ExecutionPlan):
            raise TypeError("SC2 command outcome plan must be an SC2ExecutionPlan or None.")
        if self.execution_result is not None and not isinstance(
            self.execution_result, SC2PlanExecutionResult
        ):
            raise TypeError(
                "SC2 command outcome execution_result must be an "
                "SC2PlanExecutionResult or None."
            )
        if self.feasibility is not None and not isinstance(
            self.feasibility, SC2FeasibilityResult
        ):
            raise TypeError(
                "SC2 command outcome feasibility must be an SC2FeasibilityResult or None."
            )
        if self.status == "clarification":
            if (
                self.intent_dsl is not None
                or self.plan is not None
                or self.execution_result is not None
                or self.feasibility is not None
            ):
                raise ValueError(
                    "clarification outcomes cannot carry pipeline artifacts."
                )
        if self.status in ("executed", "partially_executed", "read_only"):
            if self.plan is None or self.execution_result is None:
                raise ValueError(
                    f"{self.status} outcomes require both a plan and an execution result."
                )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready outcome payload."""

        return {
            "command_text": self.command_text,
            "status": self.status,
            "narration": self.narration,
            "intent_dsl": dict(self.intent_dsl) if self.intent_dsl is not None else None,
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "execution_result": (
                self.execution_result.to_dict()
                if self.execution_result is not None
                else None
            ),
            "feasibility": (
                self.feasibility.to_dict() if self.feasibility is not None else None
            ),
        }


@dataclass(frozen=True)
class _PreparedIntentExecution:
    """Validated and planned intent ready for ordered runtime dispatch."""

    interpretation: object
    payload: object
    command_text: str
    intent_dsl: Mapping[str, object] | None
    state: SC2CommanderState | None
    feasibility: SC2FeasibilityResult
    plan: SC2ExecutionPlan


@dataclass(frozen=True)
class _PreparedCommandOutcome:
    """Non-mutating or rejected outcome prepared before combo dispatch."""

    outcome: SC2CommandOutcome
    state: SC2CommanderState | None


_COMBO_FAILURE_POLICY_STOP_ON_STEP_FAILURE: Final[str] = "stop_on_step_failure"
_COMBO_FAILURE_DECISION_STOP_REMAINING: Final[str] = "stop_remaining_steps"
_COMBO_FAILURE_STATUSES: Final[frozenset[str]] = frozenset(
    {"blocked", "clarification", "partially_executed"}
)


@dataclass(frozen=True)
class _ValidatedComboPlan:
    """Runtime-normalized ComboPlan ready for safe step execution."""

    parts: tuple[str, ...]
    failure_policy: str = _COMBO_FAILURE_POLICY_STOP_ON_STEP_FAILURE

    def __post_init__(self) -> None:
        parts = tuple(
            part.strip() for part in self.parts if isinstance(part, str) and part.strip()
        )
        if len(parts) < 2:
            raise ValueError("validated combo plan requires at least two steps.")
        object.__setattr__(self, "parts", parts)
        failure_policy = (
            self.failure_policy.strip()
            if isinstance(self.failure_policy, str)
            else ""
        )
        if failure_policy != _COMBO_FAILURE_POLICY_STOP_ON_STEP_FAILURE:
            failure_policy = _COMBO_FAILURE_POLICY_STOP_ON_STEP_FAILURE
        object.__setattr__(self, "failure_policy", failure_policy)


@dataclass(frozen=True)
class _ComboStepExecutionLog:
    """JSON-ready audit record for one validated ComboPlan step."""

    step_index: int
    step_count: int
    step_id: str
    input_command: str
    validation_result: Mapping[str, object]
    execution_result: Mapping[str, object]
    timing: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.step_index < 1:
            raise ValueError("combo step log step_index must be one-based.")
        if self.step_count < self.step_index:
            raise ValueError("combo step log step_count cannot be below step_index.")
        if not self.step_id.strip():
            raise ValueError("combo step log step_id must be non-empty.")
        if not self.input_command.strip():
            raise ValueError("combo step log input_command must be non-empty.")
        object.__setattr__(self, "step_id", str(self.step_id))
        object.__setattr__(self, "input_command", str(self.input_command))
        object.__setattr__(self, "validation_result", dict(self.validation_result))
        object.__setattr__(self, "execution_result", dict(self.execution_result))
        object.__setattr__(self, "timing", dict(self.timing))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready ComboPlan step execution log."""

        return {
            "step_index": self.step_index,
            "step_count": self.step_count,
            "step_id": self.step_id,
            "input_command": self.input_command,
            "validation_result": dict(self.validation_result),
            "execution_result": dict(self.execution_result),
            "timing": dict(self.timing),
        }


def _combo_step_post_execution_failure(
    prepared: _PreparedIntentExecution,
    execution_result: object,
) -> str | None:
    """Return a ComboPlan post-execution contract failure, if any."""

    if not isinstance(execution_result, SC2PlanExecutionResult):
        return (
            "combo_plan 단계 실행 후 검증 실패: 실행기가 표준 실행 결과를 "
            "반환하지 않았습니다."
        )
    if execution_result.plan != prepared.plan:
        return (
            "combo_plan 단계 실행 후 검증 실패: 실행기가 검증된 계획과 다른 "
            "계획을 반환했습니다."
        )
    expected_intent = str(prepared.plan.intent_name)
    reported_intent = str(execution_result.plan.intent_name)
    if reported_intent != expected_intent:
        return (
            "combo_plan 단계 실행 후 검증 실패: 실행 결과의 intent가 "
            f"{reported_intent}로 바뀌었습니다."
        )

    planned_actions = prepared.plan.ordered_actions
    reported_action_groups = (
        ("attempted", execution_result.attempted_actions),
        ("applied", execution_result.applied_actions),
        ("skipped", execution_result.skipped_actions),
    )
    for group_name, actions in reported_action_groups:
        for action in actions:
            if action not in planned_actions:
                return (
                    "combo_plan 단계 실행 후 검증 실패: 실행 결과가 계획에 "
                    f"없는 {group_name} 액션을 보고했습니다."
                )
    for action in execution_result.applied_actions:
        if action not in execution_result.attempted_actions:
            return (
                "combo_plan 단계 실행 후 검증 실패: 시도하지 않은 액션이 "
                "실행됨으로 보고되었습니다."
            )
    return None


def _combo_step_identity(step_index: int, step_count: int) -> str:
    return f"combo-step-{step_index}-of-{step_count}"


def _combo_step_timing(
    *,
    started_at: float,
    validation_finished_at: float,
    finished_at: float,
    execution_started_at: float | None = None,
) -> dict[str, object]:
    timing = {
        "total_ms": _elapsed_ms(started_at, finished_at),
        "validation_ms": _elapsed_ms(started_at, validation_finished_at),
    }
    if execution_started_at is not None:
        timing["execution_ms"] = _elapsed_ms(execution_started_at, finished_at)
    else:
        timing["execution_ms"] = 0.0
    return timing


def _elapsed_ms(started_at: float, finished_at: float) -> float:
    return round(max(0.0, finished_at - started_at) * 1000.0, 3)


def _combo_validation_result(
    prepared: _PreparedIntentExecution | _PreparedCommandOutcome,
) -> dict[str, object]:
    if isinstance(prepared, _PreparedIntentExecution):
        result = prepared.feasibility.to_dict()
        result["status"] = "executable"
        result["plan_intent_name"] = prepared.plan.intent_name
        return result
    outcome = prepared.outcome
    if outcome.feasibility is not None:
        result = outcome.feasibility.to_dict()
    else:
        result = {
            "executable": False,
            "intent_name": (
                str(outcome.intent_dsl.get("intent", ""))
                if outcome.intent_dsl is not None
                else ""
            ),
            "reason_codes": [],
            "reasons": [outcome.narration],
            "alternative": "",
            "checked": [],
        }
    result["status"] = outcome.status
    return result


def _combo_execution_result(
    execution_result: object | None,
    *,
    status: str,
    reason: str = "",
) -> dict[str, object]:
    if isinstance(execution_result, SC2PlanExecutionResult):
        return {
            "status": status,
            "success": execution_result.success,
            "intent_name": execution_result.plan.intent_name,
            "attempted_count": len(execution_result.attempted_actions),
            "applied_count": len(execution_result.applied_actions),
            "skipped_count": len(execution_result.skipped_actions),
            "error_count": len(execution_result.errors),
            "errors": [error.to_dict() for error in execution_result.errors],
            "reason": reason,
        }
    if execution_result is None:
        return {
            "status": status,
            "success": False,
            "intent_name": "",
            "attempted_count": 0,
            "applied_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "errors": [],
            "reason": reason,
        }
    return {
        "status": status,
        "success": False,
        "intent_name": "",
        "attempted_count": 0,
        "applied_count": 0,
        "skipped_count": 0,
        "error_count": 1,
        "errors": [
            {
                "message": "Executor returned a non-standard execution result.",
                "exception_type": type(execution_result).__name__,
            }
        ],
        "reason": reason,
    }


def _build_combo_step_execution_log(
    *,
    step_index: int,
    step_count: int,
    input_command: str,
    prepared: _PreparedIntentExecution | _PreparedCommandOutcome,
    execution_result: object | None,
    execution_status: str,
    started_at: float,
    validation_finished_at: float,
    finished_at: float,
    execution_started_at: float | None = None,
    execution_reason: str = "",
) -> _ComboStepExecutionLog:
    return _ComboStepExecutionLog(
        step_index=step_index,
        step_count=step_count,
        step_id=_combo_step_identity(step_index, step_count),
        input_command=input_command,
        validation_result=_combo_validation_result(prepared),
        execution_result=_combo_execution_result(
            execution_result,
            status=execution_status,
            reason=execution_reason,
        ),
        timing=_combo_step_timing(
            started_at=started_at,
            validation_finished_at=validation_finished_at,
            execution_started_at=execution_started_at,
            finished_at=finished_at,
        ),
    )


def _intent_dsl_with_combo_step_log(
    intent_dsl: Mapping[str, object] | None,
    log: _ComboStepExecutionLog | None,
) -> Mapping[str, object] | None:
    if log is None:
        return intent_dsl
    result = dict(intent_dsl or {})
    result["combo_step_execution_log"] = log.to_dict()
    return result


def _outcome_with_combo_step_log(
    outcome: SC2CommandOutcome,
    log: _ComboStepExecutionLog,
) -> SC2CommandOutcome:
    if outcome.status == "clarification":
        return outcome
    execution_result = outcome.execution_result
    if isinstance(execution_result, SC2PlanExecutionResult):
        execution_result = _execution_result_with_combo_step_log(execution_result, log)
    return SC2CommandOutcome(
        command_text=outcome.command_text,
        status=outcome.status,
        narration=outcome.narration,
        intent_dsl=_intent_dsl_with_combo_step_log(outcome.intent_dsl, log),
        plan=outcome.plan,
        execution_result=execution_result,
        feasibility=outcome.feasibility,
    )


def _execution_result_with_combo_step_log(
    execution_result: SC2PlanExecutionResult,
    log: _ComboStepExecutionLog,
) -> SC2PlanExecutionResult:
    audit = dict(execution_result.audit)
    audit["combo_step_execution_log"] = log.to_dict()
    return SC2PlanExecutionResult(
        plan=execution_result.plan,
        attempted_actions=execution_result.attempted_actions,
        applied_actions=execution_result.applied_actions,
        skipped_actions=execution_result.skipped_actions,
        errors=execution_result.errors,
        audit=audit,
    )


def _combo_plan_failure_summary(
    *,
    plan: _ValidatedComboPlan,
    failed_step_index: int,
    failed_input_command: str,
    failure_status: str,
    failure_reason: str,
) -> dict[str, object]:
    """Return a JSON-ready ComboPlan policy decision for a failed step."""

    step_count = len(plan.parts)
    skipped_steps = [
        {
            "step_index": index,
            "step_count": step_count,
            "step_id": _combo_step_identity(index, step_count),
            "input_command": command,
            "status": "skipped",
            "reason": (
                "previous combo step failed; "
                f"{plan.failure_policy} policy stopped remaining steps"
            ),
        }
        for index, command in enumerate(plan.parts, start=1)
        if index > failed_step_index
    ]
    return {
        "policy": plan.failure_policy,
        "decision": _COMBO_FAILURE_DECISION_STOP_REMAINING,
        "failed_step": {
            "step_index": failed_step_index,
            "step_count": step_count,
            "step_id": _combo_step_identity(failed_step_index, step_count),
            "input_command": failed_input_command,
            "status": failure_status,
            "reason": failure_reason,
        },
        "skipped_step_count": len(skipped_steps),
        "skipped_steps": skipped_steps,
    }


def _combo_failure_reason(
    outcome: SC2CommandOutcome,
    log: _ComboStepExecutionLog | None,
) -> str:
    """Return the clearest user-facing reason for a failed ComboPlan step."""

    if log is not None:
        execution_reason = str(log.execution_result.get("reason", "")).strip()
        if execution_reason and "blocked during" not in execution_reason:
            return execution_reason
        validation_reasons = log.validation_result.get("reasons")
        if isinstance(validation_reasons, list):
            joined = ", ".join(
                str(reason).strip()
                for reason in validation_reasons
                if str(reason).strip()
            )
            if joined:
                return joined
    return outcome.narration


def _combo_failure_narration(
    original_narration: str,
    summary: Mapping[str, object],
) -> str:
    failed_step = summary.get("failed_step")
    failed = failed_step if isinstance(failed_step, Mapping) else {}
    step_index = failed.get("step_index", "?")
    step_count = failed.get("step_count", "?")
    command = str(failed.get("input_command", "")).strip()
    skipped_count = int(summary.get("skipped_step_count", 0) or 0)
    suffix = (
        f" 남은 {skipped_count}개 단계는 "
        f"{summary.get('policy')} 정책에 따라 실행하지 않았습니다."
        if skipped_count
        else " 추가로 실행할 남은 단계는 없습니다."
    )
    return (
        f"ComboPlan {step_index}/{step_count}단계에서 중단했습니다. "
        f"실패 단계: {command}.{suffix} {original_narration}"
    )


def _execution_result_with_combo_failure_summary(
    execution_result: SC2PlanExecutionResult,
    summary: Mapping[str, object],
) -> SC2PlanExecutionResult:
    audit = dict(execution_result.audit)
    audit["combo_plan_failure_summary"] = dict(summary)
    return SC2PlanExecutionResult(
        plan=execution_result.plan,
        attempted_actions=execution_result.attempted_actions,
        applied_actions=execution_result.applied_actions,
        skipped_actions=execution_result.skipped_actions,
        errors=execution_result.errors,
        audit=audit,
    )


def _outcome_with_combo_failure_summary(
    outcome: SC2CommandOutcome,
    *,
    plan: _ValidatedComboPlan,
    failed_step_index: int,
    failed_input_command: str,
    log: _ComboStepExecutionLog | None = None,
) -> SC2CommandOutcome:
    """Attach a policy-level ComboPlan failure summary to a failed outcome."""

    summary = _combo_plan_failure_summary(
        plan=plan,
        failed_step_index=failed_step_index,
        failed_input_command=failed_input_command,
        failure_status=outcome.status,
        failure_reason=_combo_failure_reason(outcome, log),
    )
    intent_dsl = (
        dict(outcome.intent_dsl)
        if outcome.intent_dsl is not None
        else None
    )
    if intent_dsl is not None:
        intent_dsl["combo_plan_failure_summary"] = summary
    execution_result = outcome.execution_result
    if execution_result is not None:
        execution_result = _execution_result_with_combo_failure_summary(
            execution_result,
            summary,
        )
    return SC2CommandOutcome(
        command_text=outcome.command_text,
        status=outcome.status,
        narration=_combo_failure_narration(outcome.narration, summary),
        intent_dsl=intent_dsl,
        plan=outcome.plan,
        execution_result=execution_result,
        feasibility=outcome.feasibility,
    )


@dataclass(frozen=True)
class SC2CommandSession:
    """Composable live command pipeline session for one StarCraft II runtime.

    Defaults wire the real components: the Korean ToyCraft interpreter, the
    conservative live feasibility validator, the deterministic SC2 action
    planner, a fresh (unbound) runtime executor, the duck-typed BotAI state
    resolver, and the Korean narrator. Bind a runtime by constructing the
    session with ``executor=SC2RuntimeExecutor(bot=adapter)`` where ``adapter``
    is typically a ``PythonSC2BotAdapter`` wrapping the live BotAI object.

    Two optional, duck-typed integrations:

    - ``event_memory`` (``record(outcome, game_time_seconds=None)``, for
      example :class:`~starcraft_commander.event_memory.CommanderEventMemory`)
      records every produced outcome — including blocked and clarification
      ones — stamped with the resolved state's game time when available.
    - ``standing_orders`` (``register_from_payload(payload)`` +
      ``korean_status()``, for example
      :class:`~starcraft_commander.standing_orders.StandingOrderController`)
      is registered from each successfully executed payload's constraints.
      Newly registered orders are announced with an honest Korean narration
      suffix, and because the controller genuinely enforces the
      continuous-production constraint, a default ``SC2KoreanNarrator`` is
      upgraded to treat those constraints as enforced (full execution)
      instead of disclosing them as dropped. Sessions WITHOUT a controller
      keep today's honest ``지속 생산 미지원`` disclosure.
    """

    interpreter: CommandInterpreterInterface = DEFAULT_COMMAND_INTERPRETER
    validator: SC2FeasibilityValidatorInterface = DEFAULT_SC2_FEASIBILITY_VALIDATOR
    planner: SC2ActionPlannerInterface = DEFAULT_SC2_ACTION_PLANNER
    executor: SC2ExecutorBoundaryInterface = field(default_factory=SC2RuntimeExecutor)
    state_resolver: SC2StateResolverInterface = DEFAULT_SC2_STATE_RESOLVER
    narrator: SC2NarratorInterface = DEFAULT_SC2_NARRATOR
    event_memory: object | None = None
    standing_orders: object | None = None
    _pending_camera_base_clarification: str | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _pending_build_base_clarification: str | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        seams = (
            ("interpreter", self.interpreter, "interpret"),
            ("validator", self.validator, "validate_payload"),
            ("planner", self.planner, "build_plan"),
            ("executor", self.executor, "execute"),
            ("state_resolver", self.state_resolver, "resolve"),
            ("narrator", self.narrator, "narrate_plan_result"),
            ("narrator", self.narrator, "narrate_rejection"),
        )
        for field_name, component, method_name in seams:
            if not callable(getattr(component, method_name, None)):
                raise TypeError(
                    f"SC2 command session {field_name} must implement {method_name}()."
                )
        if self.event_memory is not None and not callable(
            getattr(self.event_memory, "record", None)
        ):
            raise TypeError("SC2 command session event_memory must implement record().")
        if self.standing_orders is not None:
            for method_name in ("register_from_payload", "korean_status"):
                if not callable(getattr(self.standing_orders, method_name, None)):
                    raise TypeError(
                        "SC2 command session standing_orders must implement "
                        f"{method_name}()."
                    )
            # The controller genuinely enforces the standing-order constraints,
            # so a default Korean narrator must stop disclosing them as
            # dropped. Custom narrator implementations are left untouched.
            if isinstance(self.narrator, SC2KoreanNarrator):
                enforced = self.narrator.enforced_constraints | frozenset(
                    CONSTRAINT_TO_STANDING_ORDER
                )
                if enforced != self.narrator.enforced_constraints:
                    object.__setattr__(
                        self,
                        "narrator",
                        SC2KoreanNarrator(enforced_constraints=enforced),
                    )

    async def process_text(self, command_text: str) -> tuple[SC2CommandOutcome, ...]:
        """Process one commander utterance into one outcome per command part.

        Compound utterances are honored part by part so no command part is
        ever silently dropped inside a single-outcome success. Per-part
        processing is preferred when the splitter recovers at least two
        resolvable parts, or when an explicit connective (그리고/하고) signals
        a compound order, or when the whole text fails to interpret but at
        least one part resolves; unsupported parts become honest
        clarification outcomes. Otherwise a resolved whole text executes as
        one command, and unresolvable text returns the interpreter's own
        Korean clarification unchanged.
        """

        self._refresh_llm_runtime_context()

        pending_camera_answer = self._pending_camera_base_interpretation_for(command_text)
        if pending_camera_answer is not None:
            self._clear_pending_camera_base_clarification()
            return (await self._process_interpretation(pending_camera_answer),)
        pending_camera_reask = self._pending_camera_base_reask_for(command_text)
        if pending_camera_reask is not None:
            return (self._finalize_clarification(pending_camera_reask),)

        pending_build_answer = self._pending_build_base_interpretation_for(command_text)
        if pending_build_answer is not None:
            self._clear_pending_build_base_clarification()
            if getattr(pending_build_answer, "clarification_required", False):
                return (self._finalize_clarification(pending_build_answer),)
            return (await self._process_interpretation(pending_build_answer),)
        pending_build_reask = self._pending_build_base_reask_for(command_text)
        if pending_build_reask is not None:
            return (self._finalize_clarification(pending_build_reask),)

        townhall_state_ambiguity = self._townhall_state_ambiguity_for(command_text)
        if townhall_state_ambiguity is not None:
            interpretation, state = townhall_state_ambiguity
            return (
                self._finalize_outcome(
                    _clarification_outcome(interpretation),
                    state,
                ),
            )

        camera_ambiguity = self._camera_base_ambiguity_for(command_text)
        if camera_ambiguity is not None:
            interpretation, state = camera_ambiguity
            self._remember_pending_camera_base_clarification(command_text)
            return (
                self._finalize_outcome(
                    _clarification_outcome(interpretation),
                    state,
                ),
            )

        build_base_ambiguity = self._build_base_ambiguity_for(command_text)
        if build_base_ambiguity is not None:
            interpretation, state = build_base_ambiguity
            self._remember_pending_build_base_clarification(command_text)
            return (
                self._finalize_outcome(
                    _clarification_outcome(interpretation),
                    state,
                ),
            )

        compound_or_macro_intent = is_compound_or_macro_intent(command_text)
        combo_plan_attempted = False
        if compound_or_macro_intent:
            combo_plan_attempted = True
            combo_parts = self._plan_llm_combo_parts(command_text)
            if combo_parts:
                return await self._process_validated_combo_parts(combo_parts)

        macro_parts = _macro_command_parts_for(command_text)
        if macro_parts:
            return await self._process_command_parts(macro_parts)

        question_response = self._question_response_for(command_text)
        if question_response is not None:
            topic, answer, state = question_response
            return (
                self._finalize_outcome(
                    _question_outcome(command_text, topic, answer),
                    state,
                ),
            )

        interpretation = self.interpreter.interpret(command_text)
        full_payload = _safe_interpretation_payload(interpretation)
        full_resolved = full_payload is not None
        needs_combo_clarification = _needs_combo_plan_clarification(command_text)

        if (
            not full_resolved
            and not combo_plan_attempted
            and not is_deictic_build_placement_missing_semantic_target(command_text)
        ):
            combo_parts = self._plan_llm_combo_parts(command_text)
            if combo_parts:
                return await self._process_validated_combo_parts(combo_parts)
        if full_resolved and compound_or_macro_intent and not combo_plan_attempted:
            combo_parts = self._plan_llm_combo_parts(command_text)
            if combo_parts:
                return await self._process_validated_combo_parts(combo_parts)

        parts = split_compound_command(command_text)
        if len(parts) >= 2:
            part_interpretations = tuple(
                self.interpreter.interpret(part) for part in parts
            )
            resolved_payloads = tuple(
                payload
                for part_result in part_interpretations
                if (payload := _safe_interpretation_payload(part_result)) is not None
            )
            resolved_part_count = len(resolved_payloads)
            if 0 < resolved_part_count < len(parts) and not combo_plan_attempted:
                combo_parts = self._plan_llm_combo_parts(command_text)
                if combo_parts:
                    return await self._process_validated_combo_parts(combo_parts)
            if 0 < resolved_part_count < len(parts) and any(
                _unresolved_compound_part_needs_whole_clarification(
                    part,
                    part_result,
                )
                for part, part_result in zip(parts, part_interpretations, strict=True)
            ):
                return (
                    self._finalize_clarification(
                        _compound_or_macro_clarification_result(command_text)
                    ),
                )
            # When the whole text resolves to exactly one part's payload, the
            # interpreter ignored the other parts: executing the whole text
            # as one command would silently drop them.
            full_collapses_to_one_part = full_resolved and any(
                payload == full_payload for payload in resolved_payloads
            )
            prefer_parts = resolved_part_count >= 2 or (
                resolved_part_count >= 1
                and (
                    _has_explicit_connective(command_text)
                    or not full_resolved
                    or full_collapses_to_one_part
                )
            )
            if prefer_parts:
                outcomes = []
                for part_result in part_interpretations:
                    if _safe_interpretation_payload(part_result) is not None:
                        outcomes.append(await self._process_interpretation(part_result))
                    else:
                        outcomes.append(self._finalize_clarification(part_result))
                return tuple(outcomes)

        if full_resolved:
            if needs_combo_clarification:
                return (
                    self._finalize_clarification(
                        _compound_or_macro_clarification_result(command_text)
                    ),
                )
            return (await self._process_interpretation(interpretation),)
        if needs_combo_clarification:
            return (
                self._finalize_clarification(
                    _compound_or_macro_clarification_result(command_text)
                ),
            )
        return (self._finalize_clarification(interpretation),)

    def _plan_llm_combo_parts(self, command_text: str) -> _ValidatedComboPlan | None:
        """Return a runtime-normalized LLM ComboPlan if one is available."""

        planner = getattr(self.interpreter, "plan_combo", None)
        if not callable(planner):
            return None
        try:
            plan = planner(command_text)
        except Exception:  # noqa: BLE001 - LLM combo planning must not crash live play
            return None
        steps = getattr(plan, "steps", ())
        if not isinstance(steps, tuple) or len(steps) < 2:
            return None
        ordered_steps = getattr(plan, "ordered_steps", ())
        if ordered_steps and not isinstance(ordered_steps, tuple):
            return None
        if ordered_steps and len(ordered_steps) != len(steps):
            return None
        resolved_steps = []
        for index, step in enumerate(steps):
            if not isinstance(step, str) or not step.strip():
                return None
            interpretation = self.interpreter.interpret(step)
            payload = _safe_interpretation_payload(interpretation)
            if payload is None:
                return None
            if ordered_steps:
                expected_intent = str(
                    getattr(ordered_steps[index], "expected_intent", "") or ""
                ).strip()
                actual_intent = str(getattr(payload, "intent", "") or "").strip()
                if expected_intent and actual_intent != expected_intent:
                    return None
            resolved_steps.append(step.strip())
        return _ValidatedComboPlan(
            tuple(resolved_steps),
            failure_policy=str(
                getattr(
                    plan,
                    "failure_policy",
                    _COMBO_FAILURE_POLICY_STOP_ON_STEP_FAILURE,
                )
            ),
        )

    async def _process_validated_combo_parts(
        self,
        plan: _ValidatedComboPlan,
    ) -> tuple[SC2CommandOutcome, ...]:
        """Preflight every LLM ComboPlan step before dispatching step one."""

        parts = plan.parts
        step_count = len(parts)
        for offset, part in enumerate(parts):
            step_index = offset + 1
            started_at = time.perf_counter()
            prepared = self._prepare_safe_command_part(part)
            validation_finished_at = time.perf_counter()
            if isinstance(prepared, _PreparedCommandOutcome) and prepared.outcome.status in {
                "blocked",
                "clarification",
            }:
                finished_at = time.perf_counter()
                log = _build_combo_step_execution_log(
                    step_index=step_index,
                    step_count=step_count,
                    input_command=part,
                    prepared=prepared,
                    execution_result=None,
                    execution_status="not_started",
                    execution_reason="combo step blocked during preflight",
                    started_at=started_at,
                    validation_finished_at=validation_finished_at,
                    finished_at=finished_at,
                )
                outcome = _outcome_with_combo_step_log(prepared.outcome, log)
                outcome = _outcome_with_combo_failure_summary(
                    outcome,
                    plan=plan,
                    failed_step_index=step_index,
                    failed_input_command=part,
                    log=log,
                )
                return (
                    self._finalize_outcome(
                        outcome,
                        prepared.state,
                    ),
                )

        outcomes = []
        for offset, part in enumerate(parts):
            outcome = await self._process_validated_combo_part(
                part,
                step_index=offset + 1,
                step_count=step_count,
                combo_plan=plan,
            )
            outcomes.append(outcome)
            if outcome.status in _COMBO_FAILURE_STATUSES:
                break
        return tuple(outcomes)

    async def _process_validated_combo_part(
        self,
        part: str,
        *,
        step_index: int,
        step_count: int,
        combo_plan: _ValidatedComboPlan,
    ) -> SC2CommandOutcome:
        """Re-validate, execute, and post-validate one executable ComboPlan step."""

        started_at = time.perf_counter()
        prepared = self._prepare_safe_command_part(part)
        validation_finished_at = time.perf_counter()
        if isinstance(prepared, _PreparedCommandOutcome):
            finished_at = time.perf_counter()
            log = _build_combo_step_execution_log(
                step_index=step_index,
                step_count=step_count,
                input_command=part,
                prepared=prepared,
                execution_result=None,
                execution_status="not_started",
                execution_reason="combo step blocked during validation",
                started_at=started_at,
                validation_finished_at=validation_finished_at,
                finished_at=finished_at,
            )
            outcome = _outcome_with_combo_step_log(prepared.outcome, log)
            if outcome.status in _COMBO_FAILURE_STATUSES:
                outcome = _outcome_with_combo_failure_summary(
                    outcome,
                    plan=combo_plan,
                    failed_step_index=step_index,
                    failed_input_command=part,
                    log=log,
                )
            return self._finalize_outcome(
                outcome,
                prepared.state,
            )
        execution_started_at = time.perf_counter()
        execution_result = await self.executor.execute(prepared.plan)
        finished_at = time.perf_counter()
        post_execution_failure = _combo_step_post_execution_failure(
            prepared,
            execution_result,
        )
        execution_status = (
            "contract_failed" if post_execution_failure is not None else "completed"
        )
        log = _build_combo_step_execution_log(
            step_index=step_index,
            step_count=step_count,
            input_command=part,
            prepared=prepared,
            execution_result=execution_result,
            execution_status=execution_status,
            execution_reason=post_execution_failure or "",
            started_at=started_at,
            validation_finished_at=validation_finished_at,
            execution_started_at=execution_started_at,
            finished_at=finished_at,
        )
        logged_execution_result = (
            _execution_result_with_combo_step_log(execution_result, log)
            if isinstance(execution_result, SC2PlanExecutionResult)
            else execution_result
        )
        if post_execution_failure is not None:
            rejection = self.narrator.narrate_rejection(post_execution_failure)
            outcome = SC2CommandOutcome(
                command_text=prepared.command_text,
                status="blocked",
                narration=rejection.response_text,
                intent_dsl=_intent_dsl_with_combo_step_log(
                    prepared.intent_dsl,
                    log,
                ),
                plan=prepared.plan,
                execution_result=(
                    logged_execution_result
                    if isinstance(logged_execution_result, SC2PlanExecutionResult)
                    else None
                ),
                feasibility=prepared.feasibility,
            )
            outcome = _outcome_with_combo_failure_summary(
                outcome,
                plan=combo_plan,
                failed_step_index=step_index,
                failed_input_command=part,
                log=log,
            )
            return self._finalize_outcome(
                outcome,
                prepared.state,
            )
        return self._finalize_execution_result(
            prepared,
            logged_execution_result,
            combo_step_log=log,
            combo_plan=combo_plan,
            combo_step_index=step_index,
            combo_step_input=part,
        )

    async def _process_safe_command_part(self, part: str) -> SC2CommandOutcome:
        """Process one already-split command through the normal safety gates."""

        prepared = self._prepare_safe_command_part(part)
        if isinstance(prepared, _PreparedCommandOutcome):
            return self._finalize_outcome(prepared.outcome, prepared.state)
        return await self._execute_prepared_interpretation(prepared)

    def _prepare_safe_command_part(
        self,
        part: str,
    ) -> _PreparedIntentExecution | _PreparedCommandOutcome:
        """Resolve one split command part without mutating game state."""

        townhall_state_ambiguity = self._townhall_state_ambiguity_for(part)
        if townhall_state_ambiguity is not None:
            interpretation, state = townhall_state_ambiguity
            return _PreparedCommandOutcome(_clarification_outcome(interpretation), state)

        question_response = self._question_response_for(part)
        if question_response is not None:
            topic, answer, state = question_response
            return _PreparedCommandOutcome(_question_outcome(part, topic, answer), state)

        camera_ambiguity = self._camera_base_ambiguity_for(part)
        if camera_ambiguity is not None:
            interpretation, state = camera_ambiguity
            self._remember_pending_camera_base_clarification(part)
            return _PreparedCommandOutcome(_clarification_outcome(interpretation), state)

        build_base_ambiguity = self._build_base_ambiguity_for(part)
        if build_base_ambiguity is not None:
            interpretation, state = build_base_ambiguity
            self._remember_pending_build_base_clarification(part)
            return _PreparedCommandOutcome(_clarification_outcome(interpretation), state)

        interpretation = self.interpreter.interpret(part)
        if _safe_interpretation_payload(interpretation) is None:
            return _PreparedCommandOutcome(_clarification_outcome(interpretation), None)
        return self._prepare_interpretation(interpretation)

    async def _process_command_parts(
        self,
        parts: tuple[str, ...],
    ) -> tuple[SC2CommandOutcome, ...]:
        """Process already-split command parts without re-entering combo planning."""

        outcomes = []
        for part in parts:
            outcomes.append(await self._process_safe_command_part(part))
        return tuple(outcomes)

    def _question_response_for(
        self,
        command_text: str,
    ) -> tuple[str, str, SC2CommanderState | None] | None:
        """Return a session-aware read-only Q&A response and context state."""

        question_answer = _question_answer_for(command_text)
        if question_answer is None:
            return None
        topic, answer = question_answer
        state = self._state_for_question(topic)
        runtime = self._runtime_for_question()
        game_bot = self._game_bot_for_question(runtime)
        map_resolver = self._map_resolver_for_question(runtime, game_bot)
        if topic == "failure_reason_help":
            answer = _failure_reason_answer_from_context(
                self.event_memory,
                state,
                self.standing_orders,
            )
        if topic == "next_action_help":
            answer = _next_action_answer_from_state(
                state,
                self.event_memory,
                self.standing_orders,
            )
        if topic == "building_location_help":
            answer = _targeting_answer_from_context(state, map_resolver, game_bot)
        if topic == "camera_help":
            answer = _camera_answer_from_context(
                state,
                map_resolver,
                runtime,
                game_bot,
            )
        if topic == "townhall_state_help":
            answer = _townhall_state_answer_from_context(command_text, state, game_bot)
        llm_answer = self._llm_question_answer(command_text, topic, answer)
        if llm_answer:
            answer = llm_answer
        return topic, answer, state

    def _llm_question_answer(
        self,
        command_text: str,
        topic: str,
        fallback_answer: str,
    ) -> str:
        """Ask the configured LLM to reinterpret read-only question context."""

        self._refresh_llm_runtime_context()
        context = self._llm_runtime_context()
        context["question"] = {
            "text": command_text,
            "topic": topic,
            "fallback_answer": fallback_answer,
            "read_only": True,
        }
        for candidate in (
            self.interpreter,
            getattr(self.interpreter, "llm_interpreter", None),
        ):
            method = getattr(candidate, "answer_question", None)
            if not callable(method):
                continue
            try:
                value = method(command_text, context)
            except Exception:  # noqa: BLE001 - questions must stay read-only
                continue
            if not isinstance(value, Mapping):
                continue
            answer = str(value.get("answer", "") or value.get("summary", "")).strip()
            if answer:
                return answer
        return ""

    def _townhall_state_ambiguity_for(
        self,
        command_text: str,
    ) -> tuple[object, SC2CommanderState] | None:
        """Ask which townhall/base only when a generic state question has many."""

        if not _is_generic_townhall_state_question(command_text):
            return None
        state = self._resolve_state()
        if state is None or _command_center_count(state) <= 1:
            return None
        choices = _command_center_base_choices(self._game_bot_for_question(), state)
        return _build_ambiguous_townhall_state_result(command_text, choices), state

    def _camera_base_ambiguity_for(
        self,
        command_text: str,
    ) -> tuple[object, SC2CommanderState] | None:
        """Ask which base only when current observations expose multiple townhalls."""

        if not is_ambiguous_camera_base_target(command_text):
            return None
        state = self._resolve_state()
        if state is None or _command_center_count(state) <= 1:
            return None
        choices = _command_center_base_choices(self._game_bot_for_question(), state)
        return build_ambiguous_camera_base_result(command_text, choices), state

    def _build_base_ambiguity_for(
        self,
        command_text: str,
    ) -> tuple[object, SC2CommanderState] | None:
        """Ask which base for generic build-near text only with multiple townhalls."""

        if not is_ambiguous_build_base_target(command_text):
            return None
        state = self._resolve_state()
        if state is None or _command_center_count(state) <= 1:
            return None
        choices = _command_center_base_choices(self._game_bot_for_question(), state)
        return build_ambiguous_build_base_result(command_text, choices), state

    def _pending_camera_base_interpretation_for(
        self,
        command_text: str,
    ) -> CommandInterpretationResult | None:
        """Resolve a follow-up answer to the pending camera-base question."""

        if self._pending_camera_base_clarification is None:
            return None
        target = _camera_base_answer_target(command_text)
        if target is None:
            return None
        return CommandInterpretationResult(
            command_text=command_text,
            payload=MoveCameraIntent(
                priority="normal",
                constraints=(MOVE_CAMERA_CONSTRAINT,),
                target=target,
            ),
        )

    def _pending_camera_base_reask_for(
        self,
        command_text: str,
    ) -> CommandInterpretationResult | None:
        """Re-ask the concrete camera-base question for unresolved answers."""

        if self._pending_camera_base_clarification is None:
            return None
        if not _is_unresolved_clarification_followup(command_text):
            return None
        state = self._resolve_state()
        choices = (
            _command_center_base_choices(self._game_bot_for_question(), state)
            if state is not None
            else ()
        )
        return _interpretation_with_command_text(
            build_ambiguous_camera_base_result(
                self._pending_camera_base_clarification,
                choices,
            ),
            command_text,
        )

    def _pending_build_base_interpretation_for(
        self,
        command_text: str,
    ) -> CommandInterpretationResult | None:
        """Resolve a follow-up answer to the pending build-base question."""

        if self._pending_build_base_clarification is None:
            return None
        target = _build_base_answer_target(command_text)
        if target is None:
            return None
        resolved_command = _rewrite_ambiguous_build_base_command(
            self._pending_build_base_clarification,
            target,
        )
        interpretation = self.interpreter.interpret(resolved_command)
        payload = _safe_interpretation_payload(interpretation)
        if getattr(interpretation, "clarification_required", False):
            return interpretation
        if getattr(payload, "intent", None) != "BUILD_STRUCTURE":
            return None
        return interpretation

    def _pending_build_base_reask_for(
        self,
        command_text: str,
    ) -> CommandInterpretationResult | None:
        """Re-ask the concrete build-base question for unresolved answers."""

        if self._pending_build_base_clarification is None:
            return None
        if not _is_unresolved_clarification_followup(command_text):
            return None
        state = self._resolve_state()
        choices = (
            _command_center_base_choices(self._game_bot_for_question(), state)
            if state is not None
            else ()
        )
        return _interpretation_with_command_text(
            build_ambiguous_build_base_result(
                self._pending_build_base_clarification,
                choices,
            ),
            command_text,
        )

    def _remember_pending_camera_base_clarification(self, command_text: str) -> None:
        """Remember that the next base-only answer may complete MOVE_CAMERA."""

        object.__setattr__(
            self,
            "_pending_camera_base_clarification",
            str(command_text or "").strip() or "camera_base",
        )

    def _clear_pending_camera_base_clarification(self) -> None:
        """Clear the one-shot camera-base clarification state."""

        object.__setattr__(self, "_pending_camera_base_clarification", None)

    def _remember_pending_build_base_clarification(self, command_text: str) -> None:
        """Remember that the next base-only answer may complete BUILD_STRUCTURE."""

        object.__setattr__(
            self,
            "_pending_build_base_clarification",
            str(command_text or "").strip() or "build_base",
        )

    def _clear_pending_build_base_clarification(self) -> None:
        """Clear the one-shot build-base clarification state."""

        object.__setattr__(self, "_pending_build_base_clarification", None)

    def _question_answer_for(self, command_text: str) -> tuple[str, str] | None:
        """Return a session-aware read-only Q&A answer, if the text is a question."""

        question_response = self._question_response_for(command_text)
        if question_response is None:
            return None
        topic, answer, _state = question_response
        return topic, answer

    def _state_for_question(self, topic: str) -> SC2CommanderState | None:
        """Resolve read-only state for question topics that need live context."""

        if topic not in {
            "next_action_help",
            "failure_reason_help",
            "building_location_help",
            "camera_help",
            "townhall_state_help",
        }:
            return None
        return self._resolve_state()

    async def _process_interpretation(
        self,
        interpretation: object,
    ) -> SC2CommandOutcome:
        """Validate, plan, execute, and narrate one resolved Intent DSL payload."""

        prepared = self._prepare_interpretation(interpretation)
        if isinstance(prepared, _PreparedCommandOutcome):
            return self._finalize_outcome(prepared.outcome, prepared.state)
        return await self._execute_prepared_interpretation(prepared)

    def _prepare_interpretation(
        self,
        interpretation: object,
    ) -> _PreparedIntentExecution | _PreparedCommandOutcome:
        """Run validation and planning without executing the resulting plan."""

        if _interpretation_failure(interpretation) is not None:
            return _PreparedCommandOutcome(
                _clarification_outcome(_failure_classified_interpretation(interpretation)),
                None,
            )
        payload = getattr(interpretation, "payload")
        command_text = str(getattr(interpretation, "command_text", ""))
        if is_unanchored_relative_build_placement(command_text, payload):
            return _PreparedCommandOutcome(
                _clarification_outcome(
                    build_missing_build_relative_anchor_result(command_text)
                ),
                None,
            )
        if is_unanchored_relative_action_target(command_text, payload):
            return _PreparedCommandOutcome(
                _clarification_outcome(
                    build_missing_relative_action_anchor_result(command_text, payload)
                ),
                None,
            )
        intent_dsl = _payload_document(payload)

        state = self._resolve_state()
        feasibility = self.validator.validate_payload(payload, state)
        if not feasibility.executable:
            rejection = self.narrator.narrate_rejection(feasibility)
            return _PreparedCommandOutcome(
                SC2CommandOutcome(
                    command_text=command_text,
                    status="blocked",
                    narration=rejection.response_text,
                    intent_dsl=intent_dsl,
                    feasibility=feasibility,
                ),
                state,
            )

        try:
            plan = self.planner.build_plan(payload)
        except ValueError as error:
            # Planner internals may include raw alias registries; never expose
            # those to the commander. Surface a location clarification instead.
            rejection = self.narrator.narrate_rejection(
                _planner_value_error_user_message(error)
            )
            return _PreparedCommandOutcome(
                SC2CommandOutcome(
                    command_text=command_text,
                    status="blocked",
                    narration=rejection.response_text,
                    intent_dsl=intent_dsl,
                    feasibility=feasibility,
                ),
                state,
            )

        camera_target_clarification = _camera_target_clarification_for_plan(
            command_text,
            plan,
            self._map_resolver_for_question(
                self._runtime_for_question(),
                self._game_bot_for_question(),
            ),
        )
        if camera_target_clarification is not None:
            return _PreparedCommandOutcome(camera_target_clarification, state)

        return _PreparedIntentExecution(
            interpretation=interpretation,
            payload=payload,
            command_text=command_text,
            intent_dsl=intent_dsl,
            state=state,
            feasibility=feasibility,
            plan=plan,
        )

    async def _execute_prepared_interpretation(
        self,
        prepared: _PreparedIntentExecution,
    ) -> SC2CommandOutcome:
        """Execute one already-preflighted intent and record the outcome."""

        execution_result = await self.executor.execute(prepared.plan)
        return self._finalize_execution_result(prepared, execution_result)

    def _finalize_execution_result(
        self,
        prepared: _PreparedIntentExecution,
        execution_result: SC2PlanExecutionResult,
        *,
        combo_step_log: _ComboStepExecutionLog | None = None,
        combo_plan: _ValidatedComboPlan | None = None,
        combo_step_index: int | None = None,
        combo_step_input: str = "",
    ) -> SC2CommandOutcome:
        """Narrate, record, and return a validated execution result."""

        payload = prepared.payload
        plan = prepared.plan
        narration = self.narrator.narrate_plan_result(execution_result)
        narration_text = narration.response_text
        if (
            self.standing_orders is not None
            and narration.status in _EXECUTED_OUTCOME_STATUSES
        ):
            newly_registered = tuple(
                self.standing_orders.register_from_payload(payload)
            )
            if newly_registered:
                narration_text += _standing_order_registration_suffix(
                    newly_registered
                )
        if narration.status == "read_only" and (
            plan.intent_name == _SUMMARIZE_STATE_INTENT_NAME
        ):
            narration_text = self._enriched_state_narration(narration_text)
        outcome = SC2CommandOutcome(
            command_text=prepared.command_text,
            status=narration.status,
            narration=narration_text,
            intent_dsl=_intent_dsl_with_combo_step_log(
                prepared.intent_dsl,
                combo_step_log,
            ),
            plan=plan,
            execution_result=execution_result,
            feasibility=prepared.feasibility,
        )
        if (
            combo_plan is not None
            and combo_step_index is not None
            and outcome.status in _COMBO_FAILURE_STATUSES
        ):
            outcome = _outcome_with_combo_failure_summary(
                outcome,
                plan=combo_plan,
                failed_step_index=combo_step_index,
                failed_input_command=combo_step_input,
                log=combo_step_log,
            )
        return self._finalize_outcome(outcome, prepared.state)

    def _finalize_clarification(self, interpretation: object) -> SC2CommandOutcome:
        """Build and record one clarification outcome (no resolved state)."""

        return self._finalize_outcome(_clarification_outcome(interpretation), None)

    def _finalize_outcome(
        self,
        outcome: SC2CommandOutcome,
        state: SC2CommanderState | None,
    ) -> SC2CommandOutcome:
        """Record one outcome into the optional event memory and return it.

        The game time stamp comes from the resolved commander state when one
        was available for this command; clarification outcomes (no state was
        ever resolved) are recorded without a game time.
        """

        if self.event_memory is not None:
            self.event_memory.record(
                outcome,
                game_time_seconds=_state_game_time_seconds(state),
            )
        return outcome

    def _enriched_state_narration(self, narration_text: str) -> str:
        """Append standing-order status and recent-command lines, if present.

        ``SUMMARIZE_STATE`` is the commander's situation report: when the
        session carries a standing-order controller and/or an event memory
        with a ``korean_summary`` renderer, the report honestly includes the
        currently active standing orders and the most recent command log.
        """

        sections = [narration_text]
        if self.standing_orders is not None:
            status_line = str(self.standing_orders.korean_status()).strip()
            if status_line:
                sections.append(status_line)
        summary_renderer = (
            getattr(self.event_memory, "korean_summary", None)
            if self.event_memory is not None
            else None
        )
        if callable(summary_renderer):
            summary_text = str(summary_renderer()).strip()
            if summary_text:
                sections.append(summary_text)
        llm_summary = self.briefing_llm_summary()
        if isinstance(llm_summary, Mapping):
            summary_text = str(llm_summary.get("summary", "")).strip()
            if summary_text:
                sections.append(f"LLM 전략 브리핑: {summary_text}")
        return "\n".join(sections)

    def _refresh_llm_runtime_context(self) -> None:
        """Attach live state/map/history context to LLM-capable interpreters."""

        setter = getattr(self.interpreter, "set_context_provider", None)
        if callable(setter):
            try:
                setter(self._llm_runtime_context)
            except Exception:  # noqa: BLE001 - context is advisory only
                return
            return
        llm = getattr(self.interpreter, "llm_interpreter", None)
        setter = getattr(llm, "set_context_provider", None)
        if callable(setter):
            try:
                setter(self._llm_runtime_context)
            except Exception:  # noqa: BLE001 - context is advisory only
                return

    def briefing_llm_summary(self) -> dict[str, object] | None:
        """Return an optional LLM strategic briefing for dashboard snapshots."""

        self._refresh_llm_runtime_context()
        context = self._llm_runtime_context()
        for candidate in (
            self.interpreter,
            getattr(self.interpreter, "llm_interpreter", None),
        ):
            summary = getattr(candidate, "briefing_llm_summary", None)
            if not callable(summary):
                summary = getattr(candidate, "briefing_summary", None)
            if not callable(summary):
                continue
            try:
                value = summary(context)
            except Exception:  # noqa: BLE001 - dashboard must stay available
                continue
            if isinstance(value, dict):
                return value
        return None

    def _llm_runtime_context(self) -> dict[str, object]:
        """Build the safe context the LLM uses for target/strategy reasoning."""

        runtime = self._runtime_for_question()
        game_bot = self._game_bot_for_question(runtime)
        state = self._resolve_state()
        resolver = self._map_resolver_for_question(runtime, game_bot)
        return {
            "state": _state_context_document(state),
            "semantic_target_catalog": _semantic_catalog_context_document(resolver),
            "recent_events": _recent_event_context_document(self.event_memory),
            "standing_orders": _standing_order_context_document(self.standing_orders),
            "instructions": (
                "Choose semantic targets from semantic_target_catalog. "
                "For building placement, output intent location plus placement "
                "policy such as near/far_from/away_from/avoid_choke when useful. "
                "If the catalog is insufficient or multiple bases match, ask a "
                "Korean clarification question instead of inventing coordinates."
            ),
        }

    def _resolve_state(self) -> SC2CommanderState | None:
        """Resolve live commander state from the executor's bound runtime.

        Returns ``None`` when no runtime is bound so the validator can reject
        conservatively. When the bound runtime is an adapter that wraps the
        actual game bot (duck-typed via its ``bot`` attribute, like
        ``PythonSC2BotAdapter``), the inner game bot is observed instead of
        the adapter itself.
        """

        game_bot = self._game_bot_for_question()
        if game_bot is None:
            return None
        return self.state_resolver.resolve(game_bot)

    def _runtime_for_question(self) -> object | None:
        """Return the bound executor runtime/adapter, if any."""

        return getattr(self.executor, "bot", None)

    def _game_bot_for_question(self, runtime: object | None = None) -> object | None:
        """Return the raw game bot behind the runtime adapter, if any."""

        resolved_runtime = self._runtime_for_question() if runtime is None else runtime
        if resolved_runtime is None:
            return None
        inner_bot = getattr(resolved_runtime, "bot", None)
        return inner_bot if inner_bot is not None else resolved_runtime

    def _map_resolver_for_question(
        self,
        runtime: object | None,
        game_bot: object | None,
    ) -> object | None:
        """Return or derive a read-only semantic map resolver for Q&A context."""

        resolver = getattr(runtime, "map_resolver", None) if runtime is not None else None
        if resolver is not None:
            return resolver
        if game_bot is None:
            return None
        try:
            return SC2MapResolver.from_bot(game_bot)
        except Exception:  # noqa: BLE001 - Q&A should expose unavailability, not crash
            return None


async def process_commander_text(
    session: SC2CommandSession,
    text: str,
) -> tuple[SC2CommandOutcome, ...]:
    """Process one commander utterance through an existing session."""

    return await session.process_text(text)


def _payload_document(payload: object) -> dict[str, object] | None:
    """Render one Intent DSL payload as a JSON-ready mapping, if possible."""

    if payload is None:
        return None
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        document = dict(to_dict())
        target_slot = getattr(payload, "target_slot", "")
        if isinstance(target_slot, str) and target_slot.strip():
            document["target_slot"] = target_slot.strip()
        return document
    if isinstance(payload, Mapping):
        return dict(payload)
    return None


def _interpretation_failure(interpretation: object) -> object | None:
    """Return an interpretation failure report, if one was classified."""

    return getattr(interpretation, "failure", None)


def _safe_interpretation_payload(interpretation: object) -> object | None:
    """Return payload only when the interpretation was not failure-classified."""

    if _interpretation_failure(interpretation) is not None:
        return None
    return getattr(interpretation, "payload", None)


def _interpretation_with_command_text(
    interpretation: object,
    command_text: str,
) -> CommandInterpretationResult:
    """Return a clarification interpretation shown against the latest utterance."""

    return CommandInterpretationResult(
        command_text=str(command_text or ""),
        payload=None,
        clarification_required=True,
        clarification_prompt=str(
            getattr(interpretation, "clarification_prompt", "") or ""
        ),
        reason=str(getattr(interpretation, "reason", "") or ""),
        alternatives=tuple(getattr(interpretation, "alternatives", ()) or ()),
        candidates=tuple(getattr(interpretation, "candidates", ()) or ()),
        failure=_interpretation_failure(interpretation),
    )


def _is_unresolved_clarification_followup(command_text: str) -> bool:
    """Return True for answer-like text that does not resolve a pending question."""

    normalized = str(command_text or "").strip().lower()
    compact = re.sub(r"[\s\.,!?？。]+", "", normalized)
    if not compact:
        return False
    if _question_answer_for(normalized) is not None:
        return False
    new_command_tokens = (
        "지어",
        "짓",
        "건설",
        "뽑",
        "찍",
        "생산",
        "보내",
        "정찰",
        "막",
        "방어",
        "수비",
        "수리",
        "고쳐",
        "공격",
        "채취",
        "캐",
        "확장",
        "상태",
        "알려",
        "보고",
    )
    return not any(token in compact for token in new_command_tokens)


def _failure_classified_interpretation(interpretation: object) -> object:
    """Normalize failed interpreter-shaped objects into clarification results."""

    failure = _interpretation_failure(interpretation)
    command_text = str(getattr(interpretation, "command_text", "") or "")
    prompt = str(getattr(interpretation, "clarification_prompt", "") or "").strip()
    reason = str(getattr(interpretation, "reason", "") or "").strip()
    alternatives = tuple(getattr(interpretation, "alternatives", ()) or ())
    primary_reason = getattr(failure, "primary_reason", None)
    if not prompt:
        prompt = str(getattr(primary_reason, "message", "") or "").strip()
    if not reason:
        reason = str(getattr(primary_reason, "message", "") or prompt).strip()
    if not prompt and not reason:
        prompt = (
            "요청이 실패로 분류되어 실행하지 않았습니다. "
            "필요한 정보를 구체화해 다시 말해 주세요."
        )
        reason = prompt
    if not alternatives:
        alternative = str(getattr(primary_reason, "alternative", "") or "").strip()
        alternatives = (alternative,) if alternative else ()
    return CommandInterpretationResult(
        command_text=command_text,
        payload=None,
        clarification_required=True,
        clarification_prompt=prompt or reason,
        reason=reason or prompt,
        alternatives=alternatives,
        candidates=tuple(getattr(interpretation, "candidates", ()) or ()),
        failure=failure,
    )


def _standing_order_registration_suffix(kinds: tuple[str, ...]) -> str:
    """Render the Korean narration suffix for newly registered standing orders."""

    labels = ", ".join(
        STANDING_ORDER_KOREAN_LABELS.get(kind, kind) for kind in kinds
    )
    return f" {SC2_STANDING_ORDER_REGISTRATION_PREFIX}: {labels}."


def _state_game_time_seconds(state: SC2CommanderState | None) -> float | None:
    """Read a recordable game time from one resolved state, defensively."""

    if state is None:
        return None
    value = getattr(state, "game_time_seconds", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0.0:
        return None
    return seconds


def _clarification_outcome(interpretation: object) -> SC2CommandOutcome:
    """Build one clarification outcome reusing the interpreter's own wording."""

    command_text = str(getattr(interpretation, "command_text", ""))
    prompt = str(getattr(interpretation, "clarification_prompt", "") or "").strip()
    reason = str(getattr(interpretation, "reason", "") or "").strip()
    return SC2CommandOutcome(
        command_text=command_text,
        status="clarification",
        narration=prompt or reason,
    )


def _planner_value_error_user_message(error: ValueError) -> str:
    """Convert strict planner errors into commander-safe Korean guidance."""

    message = str(error)
    if "unsupported SC2 target location" not in message:
        return message
    return (
        "위치를 특정하지 못했습니다. "
        "LLM이 추론한 위치가 현재 지도 의미 좌표로 연결되지 않았습니다. "
        "다시 말해 주세요: 본진에 지어 / 본진 입구에 지어 / "
        "앞마당에 지어 / 본진 가스에 정제소 지어."
    )


def _state_context_document(state: SC2CommanderState | None) -> dict[str, object]:
    if state is None:
        return {"available": False}
    to_dict = getattr(state, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except Exception:  # noqa: BLE001 - context should stay best-effort
            payload = None
        if isinstance(payload, Mapping):
            return _bounded_context_mapping(payload)
    return {
        "available": True,
        "minerals": getattr(state, "minerals", None),
        "vespene": getattr(state, "vespene", None),
        "supply_used": getattr(state, "supply_used", None),
        "supply_cap": getattr(state, "supply_cap", None),
        "supply_left": getattr(state, "supply_left", None),
        "own_units": dict(getattr(state, "own_units", {}) or {}),
        "own_structures": dict(getattr(state, "own_structures", {}) or {}),
        "structures_in_progress": dict(getattr(state, "structures_in_progress", {}) or {}),
        "idle_worker_count": getattr(state, "idle_worker_count", None),
        "army_count": getattr(state, "army_count", None),
    }


def _semantic_catalog_context_document(resolver: object | None) -> list[dict[str, object]]:
    if resolver is None:
        return []
    catalog = getattr(resolver, "semantic_target_catalog", None)
    if catalog is None:
        return []
    try:
        entries = tuple(catalog)
    except Exception:  # noqa: BLE001 - context should stay best-effort
        return []
    documents: list[dict[str, object]] = []
    for entry in entries:
        to_dict = getattr(entry, "to_dict", None)
        if callable(to_dict):
            try:
                payload = to_dict()
            except Exception:  # noqa: BLE001 - skip bad catalog entries
                continue
            if isinstance(payload, Mapping):
                documents.append(_bounded_context_mapping(payload))
                continue
        documents.append(
            {
                "target": str(getattr(entry, "target", "")),
                "aliases": list(getattr(entry, "aliases", ()) or ())[:8],
                "available": bool(getattr(entry, "available", False)),
                "position": _point_context(getattr(entry, "position", None)),
                "failure_reason": str(getattr(entry, "failure_reason", "") or "")[:180],
                "source": str(getattr(entry, "source", "") or "")[:120],
            }
        )
    return documents


def _recent_event_context_document(event_memory: object | None) -> list[dict[str, object]]:
    if event_memory is None:
        return []
    recent = getattr(event_memory, "recent", None)
    if not callable(recent):
        return []
    try:
        events = tuple(recent(8))
    except Exception:  # noqa: BLE001 - context should stay best-effort
        return []
    documents = []
    for event in events:
        to_dict = getattr(event, "to_dict", None)
        if callable(to_dict):
            try:
                payload = to_dict()
            except Exception:  # noqa: BLE001
                payload = None
            if isinstance(payload, Mapping):
                documents.append(_bounded_context_mapping(payload, text_limit=500))
                continue
        documents.append(
            {
                "seq": getattr(event, "seq", None),
                "command_text": str(getattr(event, "command_text", "") or "")[:160],
                "status": str(getattr(event, "status", "") or ""),
                "intent_name": str(getattr(event, "intent_name", "") or ""),
                "narration": str(getattr(event, "narration", "") or "")[:260],
            }
        )
    return documents


def _standing_order_context_document(source: object | None) -> dict[str, object]:
    if source is None:
        return {}
    document: dict[str, object] = {}
    for name in ("korean_status", "active_kinds"):
        method = getattr(source, name, None)
        if not callable(method):
            continue
        try:
            value = method()
        except Exception:  # noqa: BLE001 - context should stay best-effort
            continue
        if isinstance(value, tuple):
            value = list(value)
        document[name] = value
    return _bounded_context_mapping(document)


def _bounded_context_mapping(
    payload: Mapping[object, object],
    *,
    text_limit: int = 700,
) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in payload.items():
        key_text = str(key)
        normalized = re.sub(r"[^a-z0-9]", "", key_text.casefold())
        if "apikey" in normalized or normalized == "key" or "secret" in normalized:
            continue
        document[key_text] = _bounded_context_value(value, text_limit=text_limit)
    return document


def _bounded_context_value(value: object, *, text_limit: int) -> object:
    if isinstance(value, Mapping):
        return _bounded_context_mapping(value, text_limit=text_limit)
    if isinstance(value, (list, tuple)):
        return [
            _bounded_context_value(item, text_limit=text_limit)
            for item in tuple(value)[:16]
        ]
    if isinstance(value, str):
        return value[:text_limit]
    return value


def _point_context(point: object) -> dict[str, float] | None:
    if point is None:
        return None
    x = getattr(point, "x", None)
    y = getattr(point, "y", None)
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return {"x": float(x), "y": float(y)}
    return None
