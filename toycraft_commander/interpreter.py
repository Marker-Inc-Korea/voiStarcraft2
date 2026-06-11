"""Korean command interpreter mappings for Phase 0 ToyCraft Commander."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from toycraft_commander.failure import (
    CommandFailureReport,
    build_parsing_failure_report,
)
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    ExpandIntent,
    GatherResourceIntent,
    HarassIntent,
    IntentCommandPayload,
    IntentName,
    IntentPayload,
    RepairIntent,
    ScoutIntent,
    StructureName,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
    UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES,
)
from toycraft_commander.resources import ResourceName


@dataclass(frozen=True)
class InterpreterMapping:
    """Maps Korean free utterances to the nearest supported typed Intent DSL."""

    alias: str
    utterance: str
    payload: IntentPayload


@dataclass(frozen=True)
class ClarificationCandidate:
    """One supported interpretation competing inside an ambiguous command."""

    alias: str
    intent: IntentName
    description: str
    payload: IntentPayload

    def __post_init__(self) -> None:
        if not self.alias.strip():
            raise ValueError("clarification candidate alias must be non-empty.")
        if not self.description.strip():
            raise ValueError("clarification candidate description must be non-empty.")
        if self.intent != self.payload.intent:
            raise ValueError("candidate intent must match the payload intent.")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready candidate for UI clarification surfaces."""

        return {
            "alias": self.alias,
            "intent": self.intent,
            "description": self.description,
            "payload": self.payload.to_dict(),
        }


@dataclass(frozen=True)
class CommandInterpretationResult:
    """Typed result for one command interpretation attempt."""

    command_text: str
    payload: IntentPayload | None
    clarification_required: bool = False
    clarification_prompt: str = ""
    reason: str = ""
    alternatives: tuple[str, ...] = ()
    candidates: tuple[ClarificationCandidate, ...] = ()
    failure: CommandFailureReport | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "alternatives", tuple(self.alternatives))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if self.payload is not None and self.clarification_required:
            raise ValueError("resolved commands cannot require clarification.")
        if self.payload is not None and self.failure is not None:
            raise ValueError("resolved commands cannot include failure reports.")
        if self.payload is not None and self.candidates:
            raise ValueError("resolved commands cannot include clarification candidates.")
        if self.clarification_required and not self.clarification_prompt.strip():
            raise ValueError("clarification prompts must be non-empty.")
        if self.clarification_required and not self.reason.strip():
            raise ValueError("clarification reasons must be non-empty.")
        if self.clarification_required and self.failure is None:
            raise ValueError("clarification results must include failure reports.")
        if self.failure is not None and not self.clarification_required:
            raise ValueError("failure reports require clarification results.")

    def to_dsl_document(self) -> dict[str, object]:
        """Return the stable v1 DSL document for a resolved Korean command."""

        if self.payload is None:
            raise ValueError("only resolved commands can be serialized as Intent DSL.")
        return IntentCommandPayload(
            command_text=self.command_text,
            payload=self.payload,
        ).to_dsl_document()

    def to_dsl_json(self) -> str:
        """Render the stable v1 DSL document for a resolved Korean command."""

        if self.payload is None:
            raise ValueError("only resolved commands can be serialized as Intent DSL.")
        return IntentCommandPayload(
            command_text=self.command_text,
            payload=self.payload,
        ).to_dsl_json()


@dataclass(frozen=True)
class CommandPatternLexicon:
    """Supported command phrase families used by the lightweight interpreter."""

    category: str
    korean_patterns: tuple[str, ...]
    english_patterns: tuple[str, ...]


UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES: Final[tuple[IntentName, ...]] = (
    UTTERANCE_COVERAGE_CANONICAL_INTENT_NAMES
)
"""Canonical Intent DSL names that the Korean utterance matrix must cover."""

REPRESENTATIVE_UTTERANCES_PER_CANONICAL_INTENT: Final[int] = 2
"""Exact Korean utterance count required for each canonical intent."""

UNSUPPORTED_COMMAND_CLARIFICATION_REASON: Final[str] = (
    "Phase 0 ToyCraft supports only the 10 MVP commander intents."
)
UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "상태 알려줘",
    "일꾼 계속 찍어",
    "본진에 배럭 지어",
)
UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT: Final[str] = (
    "지원하지 않는 Phase 0 명령이라 실행하지 않았습니다. "
    "필요한 정보: 10개 MVP 의도 중 하나를 말해 주세요"
    "(상태 확인, 일꾼 생산, 자원 채취, 구조물 건설, 병력 생산, 정찰, 방어, 수리, 확장, 견제). "
    "ToyCraft MVP 명령 중 하나로 다시 말해 주세요. "
    "예: 상태 알려줘 / 일꾼 계속 찍어 / 본진에 배럭 지어"
)
MALFORMED_COMMAND_CLARIFICATION_REASON: Final[str] = (
    "Command text must be a non-empty string before it can be interpreted."
)
MALFORMED_COMMAND_CLARIFICATION_PROMPT: Final[str] = (
    "명령 문장이 비어 있거나 텍스트가 아니라 실행하지 않았습니다. "
    "필요한 정보: 실행할 한국어 명령 문장을 한 문장으로 입력해 주세요. "
    "예: 상태 알려줘 / 일꾼 계속 찍어 / 본진에 배럭 지어"
)
AMBIGUOUS_COMMAND_CLARIFICATION_REASON: Final[str] = (
    "Command matched multiple supported intent families and needs one clearer action."
)
AMBIGUOUS_COMMAND_CLARIFICATION_PROMPT: Final[str] = (
    "여러 Phase 0 명령으로 해석될 수 있어 실행하지 않았습니다. "
    "필요한 정보: 이번에 실행할 명령 하나만 선택해 주세요."
)
AMBIGUOUS_COMMAND_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "정찰 보내",
    "입구 막아",
    "마린 계속 뽑아",
)
MALFORMED_COMMAND_FAILURE_CODE: Final[str] = "malformed_command_text"
UNSUPPORTED_COMMAND_FAILURE_CODE: Final[str] = "unsupported_command_text"
AMBIGUOUS_COMMAND_FAILURE_CODE: Final[str] = "ambiguous_command_text"

GATHER_RESOURCE_ALIAS: Final[str] = "gather_resource"
GATHER_RESOURCE_CONSTRAINT: Final[str] = "assign workers to requested resource"
KEEP_WORKER_PRODUCTION_ALIAS: Final[str] = "keep_worker_production"
KEEP_WORKER_PRODUCTION_CONSTRAINT: Final[str] = "keep SCV production continuous"
PREVENT_SUPPLY_BLOCK_ALIAS: Final[str] = "prevent_supply_block"
PREVENT_SUPPLY_BLOCK_CONSTRAINT: Final[str] = "prevent supply block"
PREVENT_SUPPLY_BLOCK_LOCATION: Final[str] = "main ramp"
BUILD_STRUCTURE_ALIAS: Final[str] = "build_structure"
BUILD_STRUCTURE_CONSTRAINT: Final[str] = "construct requested Terran structure"
TRAIN_UNIT_ALIAS: Final[str] = "train_unit"
TRAIN_UNIT_CONSTRAINT: Final[str] = "train requested combat unit"
SEND_SCOUT_ALIAS: Final[str] = "send_scout"
SEND_SCOUT_CONSTRAINT: Final[str] = "reveal enemy position and pressure"
SEND_SCOUT_DEFAULT_TARGET: Final[str] = "enemy front"
SEND_SCOUT_DEFAULT_UNIT_GROUP: Final[str] = "1 SCV"
DEFEND_RAMP_ALIAS: Final[str] = "defend_ramp"
DEFEND_RAMP_CONSTRAINT: Final[str] = "hold ramp against early pressure"
DEFEND_RAMP_LOCATION: Final[str] = "main ramp"
DEFEND_RAMP_UNIT_GROUP: Final[str] = "available combat units"
RETREAT_ARMY_ALIAS: Final[str] = "retreat_army"
RETREAT_ARMY_CONSTRAINT: Final[str] = "preserve army by falling back to safety"
RETREAT_ARMY_LOCATION: Final[str] = "main base fallback"
RETREAT_ARMY_UNIT_GROUP: Final[str] = "available combat units"
PRESSURE_ENEMY_EXPANSION_ALIAS: Final[str] = "pressure_enemy_expansion"
PRESSURE_ENEMY_EXPANSION_CONSTRAINT: Final[str] = (
    "pressure enemy expansion without committing to a full fight"
)
PRESSURE_ENEMY_EXPANSION_TARGET: Final[str] = "enemy natural"
PRESSURE_ENEMY_EXPANSION_UNIT_GROUP: Final[str] = "available combat units"
HARASS_MINERAL_LINE_ALIAS: Final[str] = "harass_mineral_line"
HARASS_MINERAL_LINE_CONSTRAINT: Final[str] = (
    "disrupt enemy workers without committing to a full fight"
)
HARASS_MINERAL_LINE_TARGET: Final[str] = "enemy mineral line"
HARASS_MINERAL_LINE_UNIT_GROUP: Final[str] = "2 Marines"
SUMMARIZE_STATE_ALIAS: Final[str] = "summarize_state"
SUMMARIZE_STATE_CONSTRAINT: Final[str] = "summarize current ToyCraft state"
REPAIR_ALIAS: Final[str] = "repair"
REPAIR_CONSTRAINT: Final[str] = "repair damaged Terran target"
EXPAND_ALIAS: Final[str] = "expand"
EXPAND_CONSTRAINT: Final[str] = "take a feasible Terran expansion"

COMMAND_PATTERN_LEXICON_CATEGORIES: Final[tuple[str, ...]] = (
    "unit_selection",
    "movement",
    "production",
    "attack",
)
"""Interpreter lexicon categories supported in Korean and English."""

UNIT_SELECTION_COMMAND_PATTERNS: Final[CommandPatternLexicon] = CommandPatternLexicon(
    category="unit_selection",
    korean_patterns=(
        "SCV",
        "에스시비",
        "일꾼",
        "마린",
        "해병",
        "병력",
        "한 기",
        "두 기",
        "세 기",
        "네 기",
        "전체 병력",
    ),
    english_patterns=(
        "SCV",
        "worker",
        "workers",
        "Marine",
        "Marines",
        "army",
        "one Marine",
        "two Marines",
        "all combat units",
    ),
)
"""Unit and group selection terms accepted by Phase 0 command matching."""

MOVEMENT_COMMAND_PATTERNS: Final[CommandPatternLexicon] = CommandPatternLexicon(
    category="movement",
    korean_patterns=(
        "보내",
        "이동",
        "가",
        "정찰",
        "확인",
        "체크",
        "입구",
        "램프",
        "언덕",
        "앞마당",
        "본진",
        "뒤로",
        "후퇴",
        "빠져",
        "회군",
    ),
    english_patterns=(
        "send",
        "move",
        "scout",
        "check",
        "rally",
        "hold",
        "ramp",
        "choke",
        "enemy front",
        "enemy natural",
        "enemy main",
        "pull back",
        "fall back",
        "retreat",
    ),
)
"""Movement, scouting, hold-position, and fallback terms."""

PRODUCTION_COMMAND_PATTERNS: Final[CommandPatternLexicon] = CommandPatternLexicon(
    category="production",
    korean_patterns=(
        "찍어",
        "뽑아",
        "생산",
        "만들",
        "눌러",
        "지어",
        "짓",
        "올려",
        "건설",
        "확보",
        "서플",
        "보급고",
        "배럭",
        "병영",
        "리파이너리",
        "정제소",
        "벙커",
        "커맨드센터",
    ),
    english_patterns=(
        "train",
        "produce",
        "queue",
        "build",
        "construct",
        "make",
        "raise",
        "supply depot",
        "barracks",
        "refinery",
        "bunker",
        "command center",
    ),
)
"""Economy, unit-production, and construction terms."""

ATTACK_COMMAND_PATTERNS: Final[CommandPatternLexicon] = CommandPatternLexicon(
    category="attack",
    korean_patterns=(
        "공격",
        "압박",
        "견제",
        "방해",
        "흔들",
        "찌르",
        "괴롭",
        "적 미네랄",
        "상대 미네랄",
        "적 앞마당",
        "상대 앞마당",
    ),
    english_patterns=(
        "attack",
        "pressure",
        "harass",
        "deny",
        "disrupt",
        "hit",
        "strike",
        "raid",
        "enemy mineral line",
        "enemy natural",
        "enemy expansion",
    ),
)
"""Attack, harassment, pressure, and denial terms."""

COMMAND_PATTERN_LEXICONS: Final[tuple[CommandPatternLexicon, ...]] = (
    UNIT_SELECTION_COMMAND_PATTERNS,
    MOVEMENT_COMMAND_PATTERNS,
    PRODUCTION_COMMAND_PATTERNS,
    ATTACK_COMMAND_PATTERNS,
)
"""All supported command pattern lexicons for Phase 0 text interpretation."""

BUILD_STRUCTURE_DEFAULT_LOCATIONS: Final[dict[StructureName, str]] = {
    "Supply Depot": "main ramp",
    "Barracks": "main base",
    "Refinery": "main geyser",
    "Bunker": "natural choke",
    "Command Center": "main base",
}

GATHER_RESOURCE_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=GATHER_RESOURCE_ALIAS,
        utterance="미네랄에 일꾼 세 기 붙여",
        payload=GatherResourceIntent(
            priority="normal",
            constraints=(GATHER_RESOURCE_CONSTRAINT,),
            resource="minerals",
            worker_count=3,
            base="main",
        ),
    ),
    InterpreterMapping(
        alias=GATHER_RESOURCE_ALIAS,
        utterance="가스에 SCV 하나 붙여",
        payload=GatherResourceIntent(
            priority="high",
            constraints=(GATHER_RESOURCE_CONSTRAINT,),
            resource="gas",
            worker_count=1,
            base="main",
        ),
    ),
)

KEEP_WORKER_PRODUCTION_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=KEEP_WORKER_PRODUCTION_ALIAS,
        utterance="일꾼 계속 찍어",
        payload=TrainWorkerIntent(
            priority="normal",
            constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
            count=1,
        ),
    ),
    InterpreterMapping(
        alias=KEEP_WORKER_PRODUCTION_ALIAS,
        utterance="SCV 계속 생산해",
        payload=TrainWorkerIntent(
            priority="normal",
            constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
            count=1,
        ),
    ),
    InterpreterMapping(
        alias=KEEP_WORKER_PRODUCTION_ALIAS,
        utterance="에스시비 쉬지 말고 뽑아",
        payload=TrainWorkerIntent(
            priority="high",
            constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
            count=1,
        ),
    ),
    InterpreterMapping(
        alias=KEEP_WORKER_PRODUCTION_ALIAS,
        utterance="일꾼 생산 유지해",
        payload=TrainWorkerIntent(
            priority="normal",
            constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
            count=1,
        ),
    ),
    InterpreterMapping(
        alias=KEEP_WORKER_PRODUCTION_ALIAS,
        utterance="커맨드센터에서 SCV 하나씩 계속 찍어",
        payload=TrainWorkerIntent(
            priority="normal",
            constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
            count=1,
        ),
    ),
)

PREVENT_SUPPLY_BLOCK_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=PREVENT_SUPPLY_BLOCK_ALIAS,
        utterance="서플 막히지 않게 해",
        payload=BuildStructureIntent(
            priority="high",
            constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
            structure="Supply Depot",
            location=PREVENT_SUPPLY_BLOCK_LOCATION,
        ),
    ),
    InterpreterMapping(
        alias=PREVENT_SUPPLY_BLOCK_ALIAS,
        utterance="인구수 안 막히게 보급고 지어",
        payload=BuildStructureIntent(
            priority="high",
            constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
            structure="Supply Depot",
            location=PREVENT_SUPPLY_BLOCK_LOCATION,
        ),
    ),
    InterpreterMapping(
        alias=PREVENT_SUPPLY_BLOCK_ALIAS,
        utterance="서플라이 디포 미리 올려",
        payload=BuildStructureIntent(
            priority="normal",
            constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
            structure="Supply Depot",
            location=PREVENT_SUPPLY_BLOCK_LOCATION,
        ),
    ),
    InterpreterMapping(
        alias=PREVENT_SUPPLY_BLOCK_ALIAS,
        utterance="보급고 하나 지어서 인구 트이게 해",
        payload=BuildStructureIntent(
            priority="normal",
            constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
            structure="Supply Depot",
            location=PREVENT_SUPPLY_BLOCK_LOCATION,
        ),
    ),
    InterpreterMapping(
        alias=PREVENT_SUPPLY_BLOCK_ALIAS,
        utterance="인구 막히기 전에 서플 하나 지어",
        payload=BuildStructureIntent(
            priority="high",
            constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
            structure="Supply Depot",
            location=PREVENT_SUPPLY_BLOCK_LOCATION,
        ),
    ),
)

BUILD_STRUCTURE_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="본진 입구에 서플라이 디포 지어",
        payload=BuildStructureIntent(
            priority="normal",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Supply Depot",
            location="main ramp",
        ),
    ),
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="본진에 배럭 지어",
        payload=BuildStructureIntent(
            priority="normal",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Barracks",
            location="main base",
        ),
    ),
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="병영 하나 앞마당 쪽에 올려",
        payload=BuildStructureIntent(
            priority="normal",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Barracks",
            location="natural approach",
        ),
    ),
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="본진 가스에 리파이너리 지어",
        payload=BuildStructureIntent(
            priority="high",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Refinery",
            location="main geyser",
        ),
    ),
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="정제소 지어서 가스 캐게 해",
        payload=BuildStructureIntent(
            priority="high",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Refinery",
            location="main geyser",
        ),
    ),
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="앞마당 입구에 벙커 건설해",
        payload=BuildStructureIntent(
            priority="high",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Bunker",
            location="natural choke",
        ),
    ),
)

TRAIN_UNIT_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=TRAIN_UNIT_ALIAS,
        utterance="마린 계속 뽑아",
        payload=TrainArmyIntent(
            priority="normal",
            constraints=(TRAIN_UNIT_CONSTRAINT,),
            unit_type="Marine",
            count=1,
        ),
    ),
    InterpreterMapping(
        alias=TRAIN_UNIT_ALIAS,
        utterance="해병 생산해",
        payload=TrainArmyIntent(
            priority="normal",
            constraints=(TRAIN_UNIT_CONSTRAINT,),
            unit_type="Marine",
            count=1,
        ),
    ),
    InterpreterMapping(
        alias=TRAIN_UNIT_ALIAS,
        utterance="배럭에서 마린 두 기 찍어",
        payload=TrainArmyIntent(
            priority="high",
            constraints=(TRAIN_UNIT_CONSTRAINT,),
            unit_type="Marine",
            count=2,
        ),
    ),
    InterpreterMapping(
        alias=TRAIN_UNIT_ALIAS,
        utterance="마린 세 기 추가해",
        payload=TrainArmyIntent(
            priority="normal",
            constraints=(TRAIN_UNIT_CONSTRAINT,),
            unit_type="Marine",
            count=3,
        ),
    ),
    InterpreterMapping(
        alias=TRAIN_UNIT_ALIAS,
        utterance="방어용 해병 네 기 만들어",
        payload=TrainArmyIntent(
            priority="high",
            constraints=(TRAIN_UNIT_CONSTRAINT,),
            unit_type="Marine",
            count=4,
        ),
    ),
)

SEND_SCOUT_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=SEND_SCOUT_ALIAS,
        utterance="SCV 하나로 정찰 보내",
        payload=ScoutIntent(
            priority="normal",
            constraints=(SEND_SCOUT_CONSTRAINT,),
            target=SEND_SCOUT_DEFAULT_TARGET,
            unit_group=SEND_SCOUT_DEFAULT_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=SEND_SCOUT_ALIAS,
        utterance="일꾼 하나 적 앞마당 확인해",
        payload=ScoutIntent(
            priority="high",
            constraints=(SEND_SCOUT_CONSTRAINT,),
            target="enemy natural",
            unit_group=SEND_SCOUT_DEFAULT_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=SEND_SCOUT_ALIAS,
        utterance="적 본진으로 정찰 가",
        payload=ScoutIntent(
            priority="normal",
            constraints=(SEND_SCOUT_CONSTRAINT,),
            target="enemy main",
            unit_group=SEND_SCOUT_DEFAULT_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=SEND_SCOUT_ALIAS,
        utterance="상대 입구 빨리 체크해",
        payload=ScoutIntent(
            priority="high",
            constraints=(SEND_SCOUT_CONSTRAINT,),
            target="enemy front",
            unit_group=SEND_SCOUT_DEFAULT_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=SEND_SCOUT_ALIAS,
        utterance="마린 한 기로 적 미네랄 라인 봐",
        payload=ScoutIntent(
            priority="normal",
            constraints=(SEND_SCOUT_CONSTRAINT,),
            target="enemy mineral line",
            unit_group="1 Marine",
        ),
    ),
)

DEFEND_RAMP_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=DEFEND_RAMP_ALIAS,
        utterance="입구 막아",
        payload=DefendIntent(
            priority="urgent",
            constraints=(DEFEND_RAMP_CONSTRAINT,),
            location=DEFEND_RAMP_LOCATION,
            unit_group=DEFEND_RAMP_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=DEFEND_RAMP_ALIAS,
        utterance="본진 입구 수비해",
        payload=DefendIntent(
            priority="urgent",
            constraints=(DEFEND_RAMP_CONSTRAINT,),
            location=DEFEND_RAMP_LOCATION,
            unit_group=DEFEND_RAMP_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=DEFEND_RAMP_ALIAS,
        utterance="마린들 램프에 세워",
        payload=DefendIntent(
            priority="high",
            constraints=(DEFEND_RAMP_CONSTRAINT,),
            location=DEFEND_RAMP_LOCATION,
            unit_group="Marines",
        ),
    ),
    InterpreterMapping(
        alias=DEFEND_RAMP_ALIAS,
        utterance="해병으로 언덕 지켜",
        payload=DefendIntent(
            priority="high",
            constraints=(DEFEND_RAMP_CONSTRAINT,),
            location=DEFEND_RAMP_LOCATION,
            unit_group="Marines",
        ),
    ),
    InterpreterMapping(
        alias=DEFEND_RAMP_ALIAS,
        utterance="초반 러시 오니까 입구 홀드해",
        payload=DefendIntent(
            priority="urgent",
            constraints=(DEFEND_RAMP_CONSTRAINT,),
            location=DEFEND_RAMP_LOCATION,
            unit_group=DEFEND_RAMP_UNIT_GROUP,
        ),
    ),
)

RETREAT_ARMY_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=RETREAT_ARMY_ALIAS,
        utterance="병력 뒤로 빼",
        payload=DefendIntent(
            priority="urgent",
            constraints=(RETREAT_ARMY_CONSTRAINT,),
            location=RETREAT_ARMY_LOCATION,
            unit_group=RETREAT_ARMY_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=RETREAT_ARMY_ALIAS,
        utterance="마린들 본진으로 후퇴시켜",
        payload=DefendIntent(
            priority="urgent",
            constraints=(RETREAT_ARMY_CONSTRAINT,),
            location=RETREAT_ARMY_LOCATION,
            unit_group="Marines",
        ),
    ),
    InterpreterMapping(
        alias=RETREAT_ARMY_ALIAS,
        utterance="싸움 빼고 병력 살려",
        payload=DefendIntent(
            priority="urgent",
            constraints=(RETREAT_ARMY_CONSTRAINT,),
            location=RETREAT_ARMY_LOCATION,
            unit_group=RETREAT_ARMY_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=RETREAT_ARMY_ALIAS,
        utterance="해병들 안전하게 뒤로 빠져",
        payload=DefendIntent(
            priority="high",
            constraints=(RETREAT_ARMY_CONSTRAINT,),
            location=RETREAT_ARMY_LOCATION,
            unit_group="Marines",
        ),
    ),
    InterpreterMapping(
        alias=RETREAT_ARMY_ALIAS,
        utterance="무리하지 말고 병력 회군해",
        payload=DefendIntent(
            priority="high",
            constraints=(RETREAT_ARMY_CONSTRAINT,),
            location=RETREAT_ARMY_LOCATION,
            unit_group=RETREAT_ARMY_UNIT_GROUP,
        ),
    ),
)

PRESSURE_ENEMY_EXPANSION_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=PRESSURE_ENEMY_EXPANSION_ALIAS,
        utterance="상대 앞마당 압박해",
        payload=HarassIntent(
            priority="high",
            constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
            target=PRESSURE_ENEMY_EXPANSION_TARGET,
            unit_group=PRESSURE_ENEMY_EXPANSION_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=PRESSURE_ENEMY_EXPANSION_ALIAS,
        utterance="마린으로 적 앞마당 견제해",
        payload=HarassIntent(
            priority="high",
            constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
            target=PRESSURE_ENEMY_EXPANSION_TARGET,
            unit_group="Marines",
        ),
    ),
    InterpreterMapping(
        alias=PRESSURE_ENEMY_EXPANSION_ALIAS,
        utterance="앞마당 먹는지 방해해",
        payload=HarassIntent(
            priority="normal",
            constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
            target=PRESSURE_ENEMY_EXPANSION_TARGET,
            unit_group=PRESSURE_ENEMY_EXPANSION_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=PRESSURE_ENEMY_EXPANSION_ALIAS,
        utterance="적 내추럴에 압박 넣어",
        payload=HarassIntent(
            priority="high",
            constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
            target=PRESSURE_ENEMY_EXPANSION_TARGET,
            unit_group=PRESSURE_ENEMY_EXPANSION_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=PRESSURE_ENEMY_EXPANSION_ALIAS,
        utterance="해병들로 상대 멀티 흔들어",
        payload=HarassIntent(
            priority="normal",
            constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
            target=PRESSURE_ENEMY_EXPANSION_TARGET,
            unit_group="Marines",
        ),
    ),
)

HARASS_MINERAL_LINE_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=HARASS_MINERAL_LINE_ALIAS,
        utterance="마린 두 기로 적 미네랄 라인 견제해",
        payload=HarassIntent(
            priority="high",
            constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
            target=HARASS_MINERAL_LINE_TARGET,
            unit_group=HARASS_MINERAL_LINE_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=HARASS_MINERAL_LINE_ALIAS,
        utterance="상대 일꾼 라인 흔들어",
        payload=HarassIntent(
            priority="high",
            constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
            target=HARASS_MINERAL_LINE_TARGET,
            unit_group=HARASS_MINERAL_LINE_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=HARASS_MINERAL_LINE_ALIAS,
        utterance="해병으로 적 본진 미네랄 괴롭혀",
        payload=HarassIntent(
            priority="normal",
            constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
            target=HARASS_MINERAL_LINE_TARGET,
            unit_group="Marines",
        ),
    ),
    InterpreterMapping(
        alias=HARASS_MINERAL_LINE_ALIAS,
        utterance="상대 미네랄 라인에 견제 넣어",
        payload=HarassIntent(
            priority="high",
            constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
            target=HARASS_MINERAL_LINE_TARGET,
            unit_group=HARASS_MINERAL_LINE_UNIT_GROUP,
        ),
    ),
    InterpreterMapping(
        alias=HARASS_MINERAL_LINE_ALIAS,
        utterance="적 일꾼 채취 방해해",
        payload=HarassIntent(
            priority="normal",
            constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
            target=HARASS_MINERAL_LINE_TARGET,
            unit_group=HARASS_MINERAL_LINE_UNIT_GROUP,
        ),
    ),
)

SUMMARIZE_STATE_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=SUMMARIZE_STATE_ALIAS,
        utterance="상태 알려줘",
        payload=SummarizeStateIntent(
            priority="normal",
            constraints=(SUMMARIZE_STATE_CONSTRAINT,),
        ),
    ),
    InterpreterMapping(
        alias=SUMMARIZE_STATE_ALIAS,
        utterance="현재 상황 요약해",
        payload=SummarizeStateIntent(
            priority="normal",
            constraints=(SUMMARIZE_STATE_CONSTRAINT,),
        ),
    ),
    InterpreterMapping(
        alias=SUMMARIZE_STATE_ALIAS,
        utterance="지금 뭐 하고 있어",
        payload=SummarizeStateIntent(
            priority="normal",
            constraints=(SUMMARIZE_STATE_CONSTRAINT,),
        ),
    ),
    InterpreterMapping(
        alias=SUMMARIZE_STATE_ALIAS,
        utterance="게임 상태 브리핑해",
        payload=SummarizeStateIntent(
            priority="normal",
            constraints=(SUMMARIZE_STATE_CONSTRAINT,),
        ),
    ),
    InterpreterMapping(
        alias=SUMMARIZE_STATE_ALIAS,
        utterance="summarize state",
        payload=SummarizeStateIntent(
            priority="normal",
            constraints=(SUMMARIZE_STATE_CONSTRAINT,),
        ),
    ),
    InterpreterMapping(
        alias=SUMMARIZE_STATE_ALIAS,
        utterance="show game status",
        payload=SummarizeStateIntent(
            priority="normal",
            constraints=(SUMMARIZE_STATE_CONSTRAINT,),
        ),
    ),
)

REPAIR_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=REPAIR_ALIAS,
        utterance="벙커 수리해",
        payload=RepairIntent(
            priority="high",
            constraints=(REPAIR_CONSTRAINT,),
            target="front bunker",
            worker_count=1,
        ),
    ),
    InterpreterMapping(
        alias=REPAIR_ALIAS,
        utterance="SCV 두 기로 앞 벙커 고쳐",
        payload=RepairIntent(
            priority="high",
            constraints=(REPAIR_CONSTRAINT,),
            target="front bunker",
            worker_count=2,
        ),
    ),
)

EXPAND_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=EXPAND_ALIAS,
        utterance="앞마당 가져가",
        payload=ExpandIntent(
            priority="normal",
            constraints=(EXPAND_CONSTRAINT,),
            location="natural expansion",
        ),
    ),
    InterpreterMapping(
        alias=EXPAND_ALIAS,
        utterance="앞마당에 커맨드센터 준비해",
        payload=ExpandIntent(
            priority="normal",
            constraints=(EXPAND_CONSTRAINT,),
            location="natural expansion",
        ),
    ),
)

REPRESENTATIVE_UTTERANCE_MATRIX: Final[tuple[InterpreterMapping, ...]] = (
    GATHER_RESOURCE_MAPPINGS[0],
    GATHER_RESOURCE_MAPPINGS[1],
    BUILD_STRUCTURE_MAPPINGS[0],
    BUILD_STRUCTURE_MAPPINGS[1],
    KEEP_WORKER_PRODUCTION_MAPPINGS[0],
    KEEP_WORKER_PRODUCTION_MAPPINGS[1],
    TRAIN_UNIT_MAPPINGS[0],
    TRAIN_UNIT_MAPPINGS[1],
    SEND_SCOUT_MAPPINGS[0],
    SEND_SCOUT_MAPPINGS[1],
    SUMMARIZE_STATE_MAPPINGS[0],
    SUMMARIZE_STATE_MAPPINGS[1],
    DEFEND_RAMP_MAPPINGS[0],
    DEFEND_RAMP_MAPPINGS[1],
    REPAIR_MAPPINGS[0],
    REPAIR_MAPPINGS[1],
    EXPAND_MAPPINGS[0],
    EXPAND_MAPPINGS[1],
    HARASS_MINERAL_LINE_MAPPINGS[0],
    HARASS_MINERAL_LINE_MAPPINGS[1],
)
"""Representative Korean matrix: exactly 2 utterances per canonical intent."""

KOREAN_COMMAND_TEST_CORPUS: Final[tuple[dict[str, object], ...]] = tuple(
    {
        "command_text": mapping.utterance,
        "expected_dsl": mapping.payload.to_dict(),
    }
    for mapping in REPRESENTATIVE_UTTERANCE_MATRIX
)
"""20-row Korean test corpus with JSON-ready expected typed Intent DSL outputs."""

INTERPRETER_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    *KEEP_WORKER_PRODUCTION_MAPPINGS,
    *PREVENT_SUPPLY_BLOCK_MAPPINGS,
    *BUILD_STRUCTURE_MAPPINGS,
    *TRAIN_UNIT_MAPPINGS,
    *SEND_SCOUT_MAPPINGS,
    *DEFEND_RAMP_MAPPINGS,
    *RETREAT_ARMY_MAPPINGS,
    *PRESSURE_ENEMY_EXPANSION_MAPPINGS,
    *HARASS_MINERAL_LINE_MAPPINGS,
    *SUMMARIZE_STATE_MAPPINGS,
    *GATHER_RESOURCE_MAPPINGS,
    *REPAIR_MAPPINGS,
    *EXPAND_MAPPINGS,
)


@runtime_checkable
class CommandInterpreterInterface(Protocol):
    """Boundary for turning commander text into typed Intent DSL payloads."""

    def interpret_text(self, command_text: str) -> IntentPayload | None:
        """Return the nearest supported typed Intent DSL payload, if any."""

    def interpret(self, command_text: str) -> CommandInterpretationResult:
        """Return a typed interpretation result or safe clarification."""


@dataclass(frozen=True)
class CommandInterpreter:
    """Reusable Korean natural-language interpreter for the Phase 0 DSL."""

    mappings: tuple[InterpreterMapping, ...] = INTERPRETER_MAPPINGS
    pattern_lexicons: tuple[CommandPatternLexicon, ...] = COMMAND_PATTERN_LEXICONS
    canonical_intents: tuple[IntentName, ...] = UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES

    def __post_init__(self) -> None:
        object.__setattr__(self, "mappings", tuple(self.mappings))
        object.__setattr__(self, "pattern_lexicons", tuple(self.pattern_lexicons))
        object.__setattr__(self, "canonical_intents", tuple(self.canonical_intents))
        if not self.mappings:
            raise ValueError("CommandInterpreter requires at least one mapping.")
        if self.canonical_intents != UTTERANCE_MATRIX_CANONICAL_INTENT_NAMES:
            raise ValueError("CommandInterpreter must preserve the 10 MVP intents.")
        for mapping in self.mappings:
            if not isinstance(mapping, InterpreterMapping):
                raise ValueError("mappings must contain InterpreterMapping values.")
            if mapping.payload.intent not in self.canonical_intents:
                raise ValueError(
                    "mapping payload intent must be one of the 10 MVP intents."
                )
        for lexicon in self.pattern_lexicons:
            if not isinstance(lexicon, CommandPatternLexicon):
                raise ValueError(
                    "pattern_lexicons must contain CommandPatternLexicon values."
                )

    def interpret_text(self, command_text: str) -> IntentPayload | None:
        """Return the nearest supported typed Intent DSL payload, if any."""

        return _interpret_command_text_with_mappings(command_text, self.mappings)

    def interpret(self, command_text: str) -> CommandInterpretationResult:
        """Return payload or a commander-facing clarification prompt."""

        return _build_command_interpretation_result(
            command_text=command_text,
            payload=self.interpret_text(command_text),
        )


DEFAULT_COMMAND_INTERPRETER: Final[CommandInterpreter] = CommandInterpreter()
"""Default Phase 0 interpreter used by module-level compatibility functions."""


def _interpret_command_text_with_mappings(
    command_text: str,
    mappings: tuple[InterpreterMapping, ...],
) -> IntentPayload | None:
    """Return the nearest supported typed Intent DSL payload for Korean text."""

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        return None

    for mapping in mappings:
        if normalized_command == _normalize_command_text(mapping.utterance):
            return mapping.payload

    if _is_ambiguous_command(normalized_command):
        return None

    if _looks_like_gather_resource(normalized_command):
        resource = _detect_resource_name(normalized_command)
        if resource is not None:
            return GatherResourceIntent(
                priority=_detect_gather_resource_priority(normalized_command, resource),
                constraints=(GATHER_RESOURCE_CONSTRAINT,),
                resource=resource,
                worker_count=_detect_count(normalized_command, default=3),
                base=_detect_worker_base(normalized_command),
            )

    if _looks_like_keep_worker_production(normalized_command):
        return TrainWorkerIntent(
            priority="normal",
            constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
            count=1,
        )

    if _looks_like_prevent_supply_block(normalized_command):
        return BuildStructureIntent(
            priority="high",
            constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
            structure="Supply Depot",
            location=PREVENT_SUPPLY_BLOCK_LOCATION,
        )

    if _looks_like_repair(normalized_command):
        return RepairIntent(
            priority=_detect_repair_priority(normalized_command),
            constraints=(REPAIR_CONSTRAINT,),
            target=_detect_repair_target(normalized_command),
            worker_count=_detect_count(normalized_command, default=1),
        )

    build_structure_target = _build_structure_target_from_command(normalized_command)
    if build_structure_target is not None:
        structure, location = build_structure_target
        priority = "high" if structure in ("Refinery", "Bunker") else "normal"
        return BuildStructureIntent(
            priority=priority,
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure=structure,
            location=location,
        )

    if _looks_like_train_unit(normalized_command):
        return TrainArmyIntent(
            priority=_detect_train_unit_priority(normalized_command),
            constraints=(TRAIN_UNIT_CONSTRAINT,),
            unit_type="Marine",
            count=_detect_train_unit_count(normalized_command),
        )

    if _looks_like_send_scout(normalized_command):
        return ScoutIntent(
            priority=_detect_send_scout_priority(normalized_command),
            constraints=(SEND_SCOUT_CONSTRAINT,),
            target=_detect_send_scout_target(normalized_command),
            unit_group=_detect_send_scout_unit_group(normalized_command),
        )

    if _looks_like_defend_ramp(normalized_command):
        return DefendIntent(
            priority=_detect_defend_ramp_priority(normalized_command),
            constraints=(DEFEND_RAMP_CONSTRAINT,),
            location=DEFEND_RAMP_LOCATION,
            unit_group=_detect_defend_ramp_unit_group(normalized_command),
        )

    if _looks_like_retreat_army(normalized_command):
        return DefendIntent(
            priority=_detect_retreat_army_priority(normalized_command),
            constraints=(RETREAT_ARMY_CONSTRAINT,),
            location=RETREAT_ARMY_LOCATION,
            unit_group=_detect_retreat_army_unit_group(normalized_command),
        )

    if _looks_like_harass_mineral_line(normalized_command):
        return HarassIntent(
            priority=_detect_harass_mineral_line_priority(normalized_command),
            constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
            target=HARASS_MINERAL_LINE_TARGET,
            unit_group=_detect_harass_mineral_line_unit_group(normalized_command),
        )

    if _looks_like_pressure_enemy_expansion(normalized_command):
        return HarassIntent(
            priority=_detect_pressure_enemy_expansion_priority(normalized_command),
            constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
            target=PRESSURE_ENEMY_EXPANSION_TARGET,
            unit_group=_detect_pressure_enemy_expansion_unit_group(normalized_command),
        )

    if _looks_like_expand(normalized_command):
        return ExpandIntent(
            priority=_detect_expand_priority(normalized_command),
            constraints=(EXPAND_CONSTRAINT,),
            location=_detect_expand_location(normalized_command),
        )

    if _looks_like_summarize_state(normalized_command):
        return SummarizeStateIntent(
            priority="normal",
            constraints=(SUMMARIZE_STATE_CONSTRAINT,),
        )

    return None


def _build_command_interpretation_result(
    *,
    command_text: str,
    payload: IntentPayload | None,
) -> CommandInterpretationResult:
    """Return payload or a commander-facing clarification prompt."""

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        command_text_value = command_text if isinstance(command_text, str) else ""
        return CommandInterpretationResult(
            command_text=command_text_value,
            payload=None,
            clarification_required=True,
            clarification_prompt=MALFORMED_COMMAND_CLARIFICATION_PROMPT,
            reason=MALFORMED_COMMAND_CLARIFICATION_REASON,
            alternatives=UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
            failure=build_parsing_failure_report(
                command_text=command_text_value,
                code=MALFORMED_COMMAND_FAILURE_CODE,
                message=MALFORMED_COMMAND_CLARIFICATION_REASON,
                alternatives=UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
            ),
        )

    if payload is not None:
        return CommandInterpretationResult(
            command_text=command_text,
            payload=payload,
            clarification_required=False,
        )

    candidates = _build_ambiguous_command_candidates(normalized_command)
    if len(candidates) > 1:
        candidate_metadata = {
            "candidates": [candidate.to_dict() for candidate in candidates],
        }
        clarification_prompt = _build_ambiguous_clarification_prompt(candidates)
        return CommandInterpretationResult(
            command_text=command_text,
            payload=None,
            clarification_required=True,
            clarification_prompt=clarification_prompt,
            reason=AMBIGUOUS_COMMAND_CLARIFICATION_REASON,
            alternatives=AMBIGUOUS_COMMAND_CLARIFICATION_ALTERNATIVES,
            candidates=candidates,
            failure=build_parsing_failure_report(
                command_text=command_text,
                code=AMBIGUOUS_COMMAND_FAILURE_CODE,
                message=AMBIGUOUS_COMMAND_CLARIFICATION_REASON,
                alternatives=AMBIGUOUS_COMMAND_CLARIFICATION_ALTERNATIVES,
                metadata=candidate_metadata,
            ),
        )

    command_text_value = command_text if isinstance(command_text, str) else ""
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=UNSUPPORTED_COMMAND_CLARIFICATION_PROMPT,
        reason=UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
        alternatives=UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=UNSUPPORTED_COMMAND_FAILURE_CODE,
            message=UNSUPPORTED_COMMAND_CLARIFICATION_REASON,
            alternatives=UNSUPPORTED_COMMAND_CLARIFICATION_ALTERNATIVES,
        ),
    )


def interpret_command_text(command_text: str) -> IntentPayload | None:
    """Return the nearest supported typed Intent DSL payload for Korean text."""

    return DEFAULT_COMMAND_INTERPRETER.interpret_text(command_text)


def interpret_command(command_text: str) -> CommandInterpretationResult:
    """Return payload or a commander-facing clarification prompt."""

    return DEFAULT_COMMAND_INTERPRETER.interpret(command_text)


def _is_ambiguous_command(normalized_command: str) -> bool:
    if not normalized_command:
        return False

    return len(_build_ambiguous_command_candidates(normalized_command)) > 1


def _build_ambiguous_command_candidates(
    normalized_command: str,
) -> tuple[ClarificationCandidate, ...]:
    if not normalized_command:
        return ()

    candidates = tuple(
        candidate
        for candidate in (
            _candidate_gather_resource(normalized_command),
            _candidate_keep_worker_production(normalized_command),
            _candidate_prevent_supply_block(normalized_command),
            _candidate_repair(normalized_command),
            _candidate_build_structure(normalized_command),
            _candidate_train_unit(normalized_command),
            _candidate_send_scout(normalized_command),
            _candidate_defend_ramp(normalized_command),
            _candidate_retreat_army(normalized_command),
            _candidate_harass_mineral_line(normalized_command),
            _candidate_pressure_enemy_expansion(normalized_command),
            _candidate_expand(normalized_command),
            _candidate_summarize_state(normalized_command),
        )
        if candidate is not None
    )
    deduplicated: dict[tuple[str, str], ClarificationCandidate] = {}
    for candidate in candidates:
        key = (candidate.alias, repr(candidate.payload.to_dict()))
        deduplicated.setdefault(key, candidate)
    return tuple(deduplicated.values())


def _build_ambiguous_clarification_prompt(
    candidates: tuple[ClarificationCandidate, ...],
) -> str:
    """Ask the commander to choose exactly one detected supported action."""

    if not candidates:
        return AMBIGUOUS_COMMAND_CLARIFICATION_PROMPT
    choices = " / ".join(candidate.description for candidate in candidates)
    return (
        f"{AMBIGUOUS_COMMAND_CLARIFICATION_PROMPT} "
        f"가능한 해석: {choices}. "
        "한 번에 하나의 목표로 다시 말해 주세요. "
        "예: 정찰 보내 / 입구 막아 / 마린 계속 뽑아"
    )


def _candidate_gather_resource(
    normalized_command: str,
) -> ClarificationCandidate | None:
    if not _looks_like_gather_resource(normalized_command):
        return None
    resource = _detect_resource_name(normalized_command)
    if resource is None:
        return None
    return ClarificationCandidate(
        alias=GATHER_RESOURCE_ALIAS,
        intent="GATHER_RESOURCE",
        description="자원 채취 명령",
        payload=GatherResourceIntent(
            priority=_detect_gather_resource_priority(normalized_command, resource),
            constraints=(GATHER_RESOURCE_CONSTRAINT,),
            resource=resource,
            worker_count=_detect_count(normalized_command, default=3),
            base=_detect_worker_base(normalized_command),
        ),
    )


def _candidate_keep_worker_production(
    normalized_command: str,
) -> ClarificationCandidate | None:
    if not _looks_like_keep_worker_production(normalized_command):
        return None
    return ClarificationCandidate(
        alias=KEEP_WORKER_PRODUCTION_ALIAS,
        intent="TRAIN_WORKER",
        description="SCV 생산 유지 명령",
        payload=TrainWorkerIntent(
            priority="normal",
            constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
            count=1,
        ),
    )


def _candidate_prevent_supply_block(
    normalized_command: str,
) -> ClarificationCandidate | None:
    if not _looks_like_prevent_supply_block(normalized_command):
        return None
    return ClarificationCandidate(
        alias=PREVENT_SUPPLY_BLOCK_ALIAS,
        intent="BUILD_STRUCTURE",
        description="보급 막힘 방지 명령",
        payload=BuildStructureIntent(
            priority="high",
            constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
            structure="Supply Depot",
            location=PREVENT_SUPPLY_BLOCK_LOCATION,
        ),
    )


def _candidate_repair(normalized_command: str) -> ClarificationCandidate | None:
    if not _looks_like_repair(normalized_command):
        return None
    return ClarificationCandidate(
        alias=REPAIR_ALIAS,
        intent="REPAIR",
        description="손상된 아군 대상 수리 명령",
        payload=RepairIntent(
            priority=_detect_repair_priority(normalized_command),
            constraints=(REPAIR_CONSTRAINT,),
            target=_detect_repair_target(normalized_command),
            worker_count=_detect_count(normalized_command, default=1),
        ),
    )


def _candidate_build_structure(
    normalized_command: str,
) -> ClarificationCandidate | None:
    build_structure_target = _build_structure_target_from_command(normalized_command)
    if build_structure_target is None:
        return None
    structure, location = build_structure_target
    priority = "high" if structure in ("Refinery", "Bunker") else "normal"
    return ClarificationCandidate(
        alias=BUILD_STRUCTURE_ALIAS,
        intent="BUILD_STRUCTURE",
        description="Terran 구조물 건설 명령",
        payload=BuildStructureIntent(
            priority=priority,
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure=structure,
            location=location,
        ),
    )


def _candidate_train_unit(normalized_command: str) -> ClarificationCandidate | None:
    if not _looks_like_train_unit(normalized_command):
        return None
    return ClarificationCandidate(
        alias=TRAIN_UNIT_ALIAS,
        intent="TRAIN_ARMY",
        description="Marine 생산 명령",
        payload=TrainArmyIntent(
            priority=_detect_train_unit_priority(normalized_command),
            constraints=(TRAIN_UNIT_CONSTRAINT,),
            unit_type="Marine",
            count=_detect_train_unit_count(normalized_command),
        ),
    )


def _candidate_send_scout(normalized_command: str) -> ClarificationCandidate | None:
    if not _looks_like_send_scout(normalized_command):
        return None
    return ClarificationCandidate(
        alias=SEND_SCOUT_ALIAS,
        intent="SCOUT",
        description="적 위치 확인 정찰 명령",
        payload=ScoutIntent(
            priority=_detect_send_scout_priority(normalized_command),
            constraints=(SEND_SCOUT_CONSTRAINT,),
            target=_detect_send_scout_target(normalized_command),
            unit_group=_detect_send_scout_unit_group(normalized_command),
        ),
    )


def _candidate_defend_ramp(normalized_command: str) -> ClarificationCandidate | None:
    if not _looks_like_defend_ramp(normalized_command):
        return None
    return ClarificationCandidate(
        alias=DEFEND_RAMP_ALIAS,
        intent="DEFEND",
        description="입구 방어 명령",
        payload=DefendIntent(
            priority=_detect_defend_ramp_priority(normalized_command),
            constraints=(DEFEND_RAMP_CONSTRAINT,),
            location=DEFEND_RAMP_LOCATION,
            unit_group=_detect_defend_ramp_unit_group(normalized_command),
        ),
    )


def _candidate_retreat_army(normalized_command: str) -> ClarificationCandidate | None:
    if not _looks_like_retreat_army(normalized_command):
        return None
    return ClarificationCandidate(
        alias=RETREAT_ARMY_ALIAS,
        intent="DEFEND",
        description="병력 후퇴 명령",
        payload=DefendIntent(
            priority=_detect_retreat_army_priority(normalized_command),
            constraints=(RETREAT_ARMY_CONSTRAINT,),
            location=RETREAT_ARMY_LOCATION,
            unit_group=_detect_retreat_army_unit_group(normalized_command),
        ),
    )


def _candidate_harass_mineral_line(
    normalized_command: str,
) -> ClarificationCandidate | None:
    if not _looks_like_harass_mineral_line(normalized_command):
        return None
    return ClarificationCandidate(
        alias=HARASS_MINERAL_LINE_ALIAS,
        intent="HARASS",
        description="적 미네랄 라인 견제 명령",
        payload=HarassIntent(
            priority=_detect_harass_mineral_line_priority(normalized_command),
            constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
            target=HARASS_MINERAL_LINE_TARGET,
            unit_group=_detect_harass_mineral_line_unit_group(normalized_command),
        ),
    )


def _candidate_pressure_enemy_expansion(
    normalized_command: str,
) -> ClarificationCandidate | None:
    if not _looks_like_pressure_enemy_expansion(normalized_command):
        return None
    return ClarificationCandidate(
        alias=PRESSURE_ENEMY_EXPANSION_ALIAS,
        intent="HARASS",
        description="적 앞마당 압박 명령",
        payload=HarassIntent(
            priority=_detect_pressure_enemy_expansion_priority(normalized_command),
            constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
            target=PRESSURE_ENEMY_EXPANSION_TARGET,
            unit_group=_detect_pressure_enemy_expansion_unit_group(normalized_command),
        ),
    )


def _candidate_expand(normalized_command: str) -> ClarificationCandidate | None:
    if not _looks_like_expand(normalized_command):
        return None
    return ClarificationCandidate(
        alias=EXPAND_ALIAS,
        intent="EXPAND",
        description="앞마당 확장 명령",
        payload=ExpandIntent(
            priority=_detect_expand_priority(normalized_command),
            constraints=(EXPAND_CONSTRAINT,),
            location=_detect_expand_location(normalized_command),
        ),
    )


def _candidate_summarize_state(
    normalized_command: str,
) -> ClarificationCandidate | None:
    if not _looks_like_summarize_state(normalized_command):
        return None
    return ClarificationCandidate(
        alias=SUMMARIZE_STATE_ALIAS,
        intent="SUMMARIZE_STATE",
        description="현재 상태 요약 명령",
        payload=SummarizeStateIntent(
            priority="normal",
            constraints=(SUMMARIZE_STATE_CONSTRAINT,),
        ),
    )


def _looks_like_keep_worker_production(normalized_command: str) -> bool:
    has_worker_subject = _contains_any_pattern(
        normalized_command,
        ("SCV", "에스시비", "일꾼", "worker", "workers"),
    )
    has_production_action = _contains_any_pattern(
        normalized_command,
        (
            "계속",
            "유지",
            "쉬지말고",
            "끊기지않게",
            "keep",
            "continuous",
            "constantly",
        ),
    )
    has_training_verb = _contains_any_pattern(
        normalized_command,
        (
            "찍어",
            "뽑아",
            "생산",
            "만들",
            "눌러",
            "train",
            "produce",
            "queue",
            "make",
        ),
    )
    return has_worker_subject and has_production_action and has_training_verb


def _looks_like_gather_resource(normalized_command: str) -> bool:
    has_worker_subject = _contains_any_pattern(
        normalized_command,
        ("SCV", "에스시비", "일꾼", "worker", "workers"),
    )
    has_resource = _detect_resource_name(normalized_command) is not None
    has_gather_action = _contains_any_pattern(
        normalized_command,
        (
            "붙여",
            "붙이고",
            "붙여줘",
            "채취",
            "캐",
            "캐게",
            "보내",
            "assign",
            "gather",
            "mine",
            "harvest",
        ),
    )
    return has_worker_subject and has_resource and has_gather_action


def _detect_resource_name(normalized_command: str) -> ResourceName | None:
    if _contains_any_pattern(normalized_command, ("가스", "vespene", "gas")):
        return "gas"
    if _contains_any_pattern(
        normalized_command,
        ("미네랄", "광물", "mineral", "minerals"),
    ):
        return "minerals"
    return None


def _detect_gather_resource_priority(
    normalized_command: str,
    resource: ResourceName,
) -> str:
    if resource == "gas" or _contains_any_pattern(
        normalized_command,
        ("빨리", "급해", "부족", "당장", "quick", "urgent", "need"),
    ):
        return "high"
    return "normal"


def _detect_worker_base(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        ("앞마당", "내추럴", "natural", "expansion"),
    ):
        return "natural"
    return "main"


def _looks_like_prevent_supply_block(normalized_command: str) -> bool:
    has_supply_subject = _contains_any_pattern(
        normalized_command,
        (
            "서플",
            "서플라이",
            "디포",
            "보급고",
            "인구",
            "supply",
            "supply depot",
            "depot",
        ),
    )
    has_supply_action = _contains_any_pattern(
        normalized_command,
        (
            "막히",
            "안막히",
            "트이",
            "부족",
            "늘려",
            "뚫",
            "미리",
            "block",
            "blocked",
            "cap",
            "room",
        ),
    )
    has_build_verb = _contains_any_pattern(
        normalized_command,
        (
            "지어",
            "올려",
            "건설",
            "짓",
            "확보",
            "build",
            "construct",
            "raise",
        ),
    )
    return has_supply_subject and (has_supply_action or has_build_verb)


def _build_structure_target_from_command(
    normalized_command: str,
) -> tuple[StructureName, str] | None:
    structure = _detect_structure_name(normalized_command)
    if structure is None or not _has_build_structure_verb(normalized_command):
        return None
    return structure, _detect_structure_location(normalized_command, structure)


def _detect_structure_name(normalized_command: str) -> StructureName | None:
    structure_aliases: tuple[tuple[StructureName, tuple[str, ...]], ...] = (
        (
            "Supply Depot",
            ("서플라이디포", "서플라이", "서플", "보급고", "supplydepot", "depot"),
        ),
        ("Barracks", ("배럭스", "배럭", "병영", "barracks", "rax")),
        ("Refinery", ("리파이너리", "정제소", "가스통", "refinery")),
        ("Bunker", ("벙커", "bunker")),
        ("Command Center", ("커맨드센터", "커맨드", "commandcenter", "commandcentre", "cc")),
    )
    for structure, aliases in structure_aliases:
        if any(alias in normalized_command for alias in aliases):
            return structure
    return None


def _has_build_structure_verb(normalized_command: str) -> bool:
    return _contains_any_pattern(
        normalized_command,
        (
            "지어",
            "지어서",
            "짓",
            "올려",
            "건설",
            "만들",
            "build",
            "construct",
            "make",
            "raise",
        ),
    )


def _detect_structure_location(
    normalized_command: str,
    structure: StructureName,
) -> str:
    if _contains_any_pattern(normalized_command, ("앞마당", "natural")) and any(
        token in normalized_command for token in ("입구", "언덕", "초크", "쪽", "choke")
    ):
        return "natural choke"
    if _contains_any_pattern(normalized_command, ("입구", "언덕", "램프", "ramp")):
        return "main ramp"
    if _contains_any_pattern(normalized_command, ("가스", "geyser")):
        return "main geyser"
    if _contains_any_pattern(normalized_command, ("앞마당", "멀티", "확장", "natural", "expansion")):
        return "natural expansion"
    if _contains_any_pattern(normalized_command, ("본진", "main base", "base")):
        return "main base"
    return BUILD_STRUCTURE_DEFAULT_LOCATIONS[structure]


def _looks_like_train_unit(normalized_command: str) -> bool:
    has_supported_unit = _contains_any_pattern(
        normalized_command,
        (
            "마린",
            "해병",
            "marine",
            "marines",
        ),
    )
    has_training_verb = _contains_any_pattern(
        normalized_command,
        (
            "찍어",
            "뽑아",
            "생산",
            "만들",
            "추가",
            "눌러",
            "뽑",
            "train",
            "produce",
            "queue",
            "make",
        ),
    )
    return has_supported_unit and has_training_verb


def _detect_train_unit_priority(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        (
            "방어",
            "압박",
            "급해",
            "빨리",
            "막아",
            "defense",
            "pressure",
            "urgent",
            "quick",
        ),
    ):
        return "high"
    return "normal"


def _detect_train_unit_count(normalized_command: str) -> int:
    return _detect_count(normalized_command, default=1)


def _looks_like_send_scout(normalized_command: str) -> bool:
    has_scout_action = _contains_any_pattern(
        normalized_command,
        (
            "정찰",
            "확인",
            "체크",
            "봐",
            "보러",
            "살펴",
            "scout",
            "check",
            "send",
        ),
    )
    has_target_context = _contains_any_pattern(
        normalized_command,
        (
            "적",
            "상대",
            "enemy",
            "앞마당",
            "본진",
            "입구",
            "미네랄",
            "natural",
            "front",
            "main",
        ),
    )
    return has_scout_action and has_target_context


def _detect_send_scout_priority(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        (
            "초반",
            "빨리",
            "러시",
            "압박",
            "몰래",
            "확인",
            "체크",
            "rush",
            "quick",
            "early",
        ),
    ):
        return "high"
    return "normal"


def _detect_send_scout_target(normalized_command: str) -> str:
    if _contains_any_pattern(normalized_command, ("미네랄", "mineral", "mineral line")):
        return "enemy mineral line"
    if _contains_any_pattern(normalized_command, ("앞마당", "내추럴", "natural")):
        return "enemy natural"
    if _contains_any_pattern(normalized_command, ("본진", "main", "main base")):
        return "enemy main"
    if _contains_any_pattern(normalized_command, ("입구", "front", "초크")):
        return "enemy front"
    return SEND_SCOUT_DEFAULT_TARGET


def _detect_send_scout_unit_group(normalized_command: str) -> str:
    marine_count = _detect_marine_count(normalized_command)
    if marine_count is not None:
        return _format_unit_group(marine_count, "Marine")
    return SEND_SCOUT_DEFAULT_UNIT_GROUP


def _looks_like_defend_ramp(normalized_command: str) -> bool:
    has_ramp_location = _contains_any_pattern(
        normalized_command,
        (
            "입구",
            "램프",
            "언덕",
            "ramp",
            "choke",
        ),
    )
    has_defense_action = _contains_any_pattern(
        normalized_command,
        (
            "막아",
            "막고",
            "막게",
            "수비",
            "방어",
            "지켜",
            "세워",
            "홀드",
            "hold",
            "defend",
            "guard",
            "move",
            "rally",
        ),
    )
    return has_ramp_location and has_defense_action


def _detect_defend_ramp_priority(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        (
            "러시",
            "찌르",
            "압박",
            "급해",
            "빨리",
            "막아",
            "rush",
            "pressure",
            "urgent",
            "quick",
        ),
    ):
        return "urgent"
    return "high"


def _detect_defend_ramp_unit_group(normalized_command: str) -> str:
    if _contains_any_pattern(normalized_command, ("마린", "해병", "marine", "marines")):
        return "Marines"
    return DEFEND_RAMP_UNIT_GROUP


def _looks_like_retreat_army(normalized_command: str) -> bool:
    has_army_subject = _contains_any_pattern(
        normalized_command,
        (
            "병력",
            "마린",
            "해병",
            "marine",
            "marines",
            "army",
        ),
    )
    has_retreat_action = _contains_any_pattern(
        normalized_command,
        (
            "뒤로",
            "후퇴",
            "빠져",
            "빼",
            "빼고",
            "살려",
            "회군",
            "pullback",
            "pull back",
            "fallback",
            "fall back",
            "retreat",
        ),
    )
    return has_army_subject and has_retreat_action


def _detect_retreat_army_priority(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        (
            "압박",
            "실패",
            "위험",
            "빨리",
            "급해",
            "살려",
            "후퇴",
            "retreat",
            "danger",
            "urgent",
        ),
    ):
        return "urgent"
    return "high"


def _detect_retreat_army_unit_group(normalized_command: str) -> str:
    if _contains_any_pattern(normalized_command, ("마린", "해병", "marine", "marines")):
        return "Marines"
    return RETREAT_ARMY_UNIT_GROUP


def _looks_like_pressure_enemy_expansion(normalized_command: str) -> bool:
    has_enemy_expansion_target = _contains_any_pattern(
        normalized_command,
        (
            "상대앞마당",
            "적앞마당",
            "적내추럴",
            "상대내추럴",
            "상대멀티",
            "적멀티",
            "enemynatural",
            "enemy natural",
            "enemyexpansion",
            "enemy expansion",
            "enemyexpo",
        ),
    ) or (
        _contains_any_pattern(
            normalized_command,
            ("앞마당", "내추럴", "멀티", "확장", "natural", "expansion"),
        )
        and _contains_any_pattern(normalized_command, ("상대", "적", "enemy"))
    )
    has_pressure_action = _contains_any_pattern(
        normalized_command,
        (
            "압박",
            "견제",
            "방해",
            "흔들",
            "찌르",
            "괴롭",
            "공격",
            "pressure",
            "harass",
            "deny",
            "attack",
            "hit",
            "strike",
        ),
    )
    return has_enemy_expansion_target and has_pressure_action


def _detect_pressure_enemy_expansion_priority(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        (
            "빨리",
            "강하게",
            "지금",
            "압박",
            "찌르",
            "pressure",
            "attack",
            "hit",
            "now",
        ),
    ):
        return "high"
    return "normal"


def _detect_pressure_enemy_expansion_unit_group(normalized_command: str) -> str:
    marine_count = _detect_marine_count(normalized_command)
    if marine_count is not None:
        return _format_unit_group(marine_count, "Marine")
    return PRESSURE_ENEMY_EXPANSION_UNIT_GROUP


def _looks_like_harass_mineral_line(normalized_command: str) -> bool:
    has_mineral_line_target = _contains_any_pattern(
        normalized_command,
        (
            "미네랄라인",
            "일꾼라인",
            "상대미네랄",
            "적미네랄",
            "mineralline",
            "mineral line",
            "enemyminerals",
            "enemy minerals",
        ),
    ) or (
        _contains_any_pattern(normalized_command, ("일꾼", "worker", "workers"))
        and _contains_any_pattern(normalized_command, ("채취", "캐", "라인", "line"))
        and _contains_any_pattern(normalized_command, ("상대", "적", "enemy"))
    )
    has_harass_action = _contains_any_pattern(
        normalized_command,
        (
            "견제",
            "흔들",
            "괴롭",
            "방해",
            "찌르",
            "공격",
            "harass",
            "disrupt",
            "deny",
            "attack",
            "hit",
            "raid",
        ),
    )
    return has_mineral_line_target and has_harass_action


def _detect_harass_mineral_line_priority(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        (
            "빨리",
            "지금",
            "강하게",
            "견제",
            "흔들",
            "공격",
            "harass",
            "attack",
            "hit",
            "now",
        ),
    ):
        return "high"
    return "normal"


def _detect_harass_mineral_line_unit_group(normalized_command: str) -> str:
    marine_count = _detect_marine_count(normalized_command)
    if marine_count is not None:
        return _format_unit_group(marine_count, "Marine")
    return HARASS_MINERAL_LINE_UNIT_GROUP


def _looks_like_repair(normalized_command: str) -> bool:
    has_target = _contains_any_pattern(
        normalized_command,
        (
            "벙커",
            "배럭",
            "병영",
            "서플",
            "보급고",
            "커맨드",
            "bunker",
            "barracks",
            "depot",
            "commandcenter",
            "command center",
        ),
    )
    has_repair_action = _contains_any_pattern(
        normalized_command,
        (
            "수리",
            "고쳐",
            "고치",
            "복구",
            "repair",
            "fix",
            "restore",
        ),
    )
    return has_target and has_repair_action


def _detect_repair_priority(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        ("빨리", "당장", "불타", "위험", "urgent", "quick", "burning"),
    ):
        return "urgent"
    return "high"


def _detect_repair_target(normalized_command: str) -> str:
    if _contains_any_pattern(normalized_command, ("벙커", "bunker")):
        return "front bunker"
    if _contains_any_pattern(normalized_command, ("배럭", "병영", "barracks")):
        return "Barracks"
    if _contains_any_pattern(normalized_command, ("서플", "보급고", "depot")):
        return "Supply Depot"
    if _contains_any_pattern(
        normalized_command,
        ("커맨드센터", "커맨드", "commandcenter", "command center"),
    ):
        return "Command Center"
    return "front bunker"


def _looks_like_expand(normalized_command: str) -> bool:
    if _contains_any_pattern(normalized_command, ("적", "상대", "enemy")) and (
        _looks_like_pressure_enemy_expansion(normalized_command)
        or _looks_like_harass_mineral_line(normalized_command)
    ):
        return False

    has_expansion_location = _contains_any_pattern(
        normalized_command,
        ("앞마당", "내추럴", "멀티", "확장", "natural", "expansion", "expo"),
    )
    has_expand_action = _contains_any_pattern(
        normalized_command,
        (
            "가져",
            "먹어",
            "먹자",
            "펴",
            "확장",
            "멀티",
            "준비",
            "커맨드센터",
            "커맨드",
            "expand",
            "take",
            "secure",
            "prepare",
            "commandcenter",
            "command center",
            "cc",
        ),
    )
    return has_expansion_location and has_expand_action


def _detect_expand_priority(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        ("안전", "여유", "safe", "when safe"),
    ):
        return "normal"
    if _contains_any_pattern(
        normalized_command,
        ("빨리", "지금", "당장", "quick", "now"),
    ):
        return "high"
    return "normal"


def _detect_expand_location(normalized_command: str) -> str:
    if _contains_any_pattern(
        normalized_command,
        ("앞마당", "내추럴", "natural", "expansion", "expo"),
    ):
        return "natural expansion"
    return "natural expansion"


def _looks_like_summarize_state(normalized_command: str) -> bool:
    has_state_subject = any(
        token in normalized_command
        for token in (
            "상태",
            "상황",
            "현황",
            "브리핑",
            "요약",
            "status",
            "state",
            "summary",
        )
    )
    has_summary_action = any(
        token in normalized_command
        for token in (
            "알려",
            "보여",
            "요약",
            "브리핑",
            "정리",
            "보고",
            "show",
            "summarize",
            "brief",
            "report",
        )
    )
    asks_current_activity = any(
        token in normalized_command
        for token in (
            "뭐하고",
            "어떻게되고",
            "무슨일",
            "whatsgoingon",
            "whatshappening",
        )
    )
    return (has_state_subject and has_summary_action) or asks_current_activity


def _normalize_command_text(command_text: str) -> str:
    if not isinstance(command_text, str):
        return ""
    return "".join(command_text.casefold().split())


def _contains_any_pattern(normalized_command: str, patterns: tuple[str, ...]) -> bool:
    return any(_normalize_command_text(pattern) in normalized_command for pattern in patterns)


def _detect_count(normalized_command: str, *, default: int) -> int:
    count_aliases: tuple[tuple[int, tuple[str, ...]], ...] = (
        (8, ("여덟기", "8기", "여덟", "eight")),
        (7, ("일곱기", "7기", "일곱", "seven")),
        (6, ("여섯기", "6기", "여섯", "six")),
        (5, ("다섯기", "5기", "다섯", "five")),
        (4, ("네기", "4기", "넷", "네마리", "four")),
        (3, ("세기", "3기", "셋", "세마리", "three")),
        (2, ("두기", "2기", "둘", "두마리", "two")),
        (1, ("한기", "1기", "하나", "한마리", "하나씩", "one")),
    )
    for count, aliases in count_aliases:
        if any(alias in normalized_command for alias in aliases):
            return count
    return default


def _detect_marine_count(normalized_command: str) -> int | None:
    if not _contains_any_pattern(normalized_command, ("마린", "해병", "marine", "marines")):
        return None
    return _detect_count(normalized_command, default=0)


def _format_unit_group(count: int, unit_name: str) -> str:
    if count <= 0:
        return f"{unit_name}s"
    if count == 1:
        return f"1 {unit_name}"
    return f"{count} {unit_name}s"
