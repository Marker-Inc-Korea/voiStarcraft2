"""Korean command interpreter mappings for Phase 0 ToyCraft Commander."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
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
    MoveCameraIntent,
    Priority,
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


@dataclass(frozen=True)
class ParsedAnchorLabel:
    """Supported semantic anchor label parsed from location phrases."""

    key: str
    label: str
    target: str
    aliases: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("parsed anchor key must be non-empty.")
        if not self.label.strip():
            raise ValueError("parsed anchor label must be non-empty.")
        if not self.target.strip():
            raise ValueError("parsed anchor target must be non-empty.")
        aliases = tuple(alias for alias in self.aliases if alias.strip())
        if not aliases:
            raise ValueError("parsed anchor aliases must be non-empty.")
        object.__setattr__(self, "aliases", aliases)

    @property
    def normalized_aliases(self) -> tuple[str, ...]:
        """Return whitespace-insensitive aliases for parser matching."""

        return _normalize_patterns((self.key, self.label, self.target, *self.aliases))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready anchor-label descriptor."""

        return {
            "key": self.key,
            "label": self.label,
            "target": self.target,
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class KoreanRelativeLocationPhrase:
    """Structured placement meaning parsed from anchored Korean location text."""

    anchor: str
    spatial_relation: str
    anchor_target: str
    source_text: str
    direction: str = ""
    direction_target: str = ""

    def __post_init__(self) -> None:
        if not self.anchor.strip():
            raise ValueError("relative location anchor must be non-empty.")
        if not self.spatial_relation.strip():
            raise ValueError("relative location spatial_relation must be non-empty.")
        if not self.anchor_target.strip():
            raise ValueError("relative location anchor_target must be non-empty.")
        if not self.source_text.strip():
            raise ValueError("relative location source_text must be non-empty.")

    def to_dict(self) -> dict[str, object]:
        """Return JSON-ready anchor/relation placement fields."""

        payload: dict[str, object] = {
            "anchor": self.anchor,
            "anchor_target": self.anchor_target,
            "spatial_relation": self.spatial_relation,
            "source_text": self.source_text,
        }
        if self.direction:
            payload["direction"] = self.direction
        if self.direction_target:
            payload["direction_target"] = self.direction_target
        return payload


@dataclass(frozen=True)
class KoreanBaseSelectionIntent:
    """Structured base selector parsed from explicit natural-language modifiers."""

    selector: str
    label: str
    target: str
    location: str
    source_text: str
    source: str = "natural_language"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        for field_name in ("selector", "label", "target", "location", "source_text", "source"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"base selection {field_name} must be non-empty.")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("base selection confidence must be between 0.0 and 1.0.")
        object.__setattr__(self, "confidence", float(self.confidence))

    def to_dict(self) -> dict[str, object]:
        """Return JSON-ready base-selection fields for parser diagnostics."""

        return {
            "selector": self.selector,
            "label": self.label,
            "target": self.target,
            "location": self.location,
            "source_text": self.source_text,
            "source": self.source,
            "confidence": self.confidence,
        }


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
MISSING_BUILD_ANCHOR_FAILURE_CODE: Final[str] = "missing_build_anchor"
MISSING_BUILD_ANCHOR_CLARIFICATION_REASON: Final[str] = (
    "Building placement named a distance modifier but no anchor location."
)
MISSING_BUILD_ANCHOR_CLARIFICATION_PROMPT: Final[str] = (
    "건설 위치가 거리만 있고 기준점이 없어 실행하지 않았습니다. "
    "필요한 정보(location): 어디를 기준으로, 어느 방향으로 더 멀게 지을지 말해 주세요. "
    "예: 본진에서 멀게 보급고 지어 / 본진 입구보다 뒤에 보급고 지어 / 앞마당 입구에 벙커 지어"
)
MISSING_BUILD_ANCHOR_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "본진에서 멀게 보급고 지어",
    "본진 입구보다 뒤에 보급고 지어",
    "앞마당 입구에 벙커 지어",
)
MISSING_BUILD_DIRECTION_FAILURE_CODE: Final[str] = "missing_build_direction"
MISSING_BUILD_DIRECTION_CLARIFICATION_REASON: Final[str] = (
    "Building placement named a farther comparison with a known anchor but no "
    "directional target."
)
MISSING_BUILD_DIRECTION_CLARIFICATION_PROMPT: Final[str] = (
    "건설 기준점은 알겠지만 어느 방향으로 더 멀게 지을지 몰라 실행하지 않았습니다. "
    "필요한 정보(direction): 기준점에서 더 멀어질 방향이나 목표 위치를 말해 주세요. "
    "예: 본진에서 앞마당으로 더 멀게 보급고 지어 / 본진 입구보다 뒤에 보급고 지어 / "
    "미네랄에서 떨어지게 보급고 지어"
)
MISSING_BUILD_DIRECTION_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "본진에서 앞마당으로 더 멀게 보급고 지어",
    "본진 입구보다 뒤에 보급고 지어",
    "미네랄에서 떨어지게 보급고 지어",
)
MISSING_BUILD_RELATIVE_ANCHOR_FAILURE_CODE: Final[str] = (
    "missing_build_relative_anchor"
)
MISSING_BUILD_RELATIVE_ANCHOR_CLARIFICATION_REASON: Final[str] = (
    "Building placement named a relative modifier but no reference anchor or "
    "direction."
)
MISSING_BUILD_RELATIVE_ANCHOR_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "앞마당 근처에 보급고 지어",
    "입구 쪽으로 보급고 지어",
    "미네랄에서 떨어지게 보급고 지어",
)
MISSING_RELATIVE_ACTION_ANCHOR_FAILURE_CODE: Final[str] = (
    "missing_relative_action_anchor"
)
MISSING_RELATIVE_ACTION_ANCHOR_CLARIFICATION_REASON: Final[str] = (
    "Action target named a relative modifier but no reference anchor or target."
)
MISSING_RELATIVE_ACTION_ANCHOR_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "본진 입구로 병력 보내",
    "앞마당으로 카메라 옮겨",
    "적 앞마당 정찰 보내",
)
MISSING_BUILD_SEMANTIC_TARGET_FAILURE_CODE: Final[str] = (
    "missing_build_semantic_target"
)
MISSING_BUILD_SEMANTIC_TARGET_CLARIFICATION_REASON: Final[str] = (
    "Building placement used a deictic pointer without a supported semantic target."
)
MISSING_BUILD_SEMANTIC_TARGET_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "본진에 보급고 지어",
    "본진 입구에 보급고 지어",
    "앞마당에 배럭 지어",
    "본진 가스에 정제소 지어",
)
SUPPORTED_BUILD_SEMANTIC_TARGET_LABELS: Final[tuple[str, ...]] = (
    "본진",
    "본진 입구",
    "앞마당",
    "앞마당 입구",
    "본진 가스",
)
MISSING_BUILD_STRUCTURE_FAILURE_CODE: Final[str] = "missing_build_structure"
MISSING_BUILD_STRUCTURE_CLARIFICATION_REASON: Final[str] = (
    "Build request named a construction action or location but no structure."
)
MISSING_BUILD_STRUCTURE_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "사령부 근처에 보급고 지어",
    "본진 사령부 근처에 배럭 지어",
    "앞마당 사령부 근처에 벙커 지어",
)
AMBIGUOUS_CAMERA_BASE_FAILURE_CODE: Final[str] = "ambiguous_camera_base"
AMBIGUOUS_CAMERA_BASE_CLARIFICATION_REASON: Final[str] = (
    "Camera target named a generic command center/base without choosing which base."
)
AMBIGUOUS_CAMERA_BASE_CLARIFICATION_PROMPT: Final[str] = (
    "어느 사령부로 카메라를 옮길지 몰라 실행하지 않았습니다. "
    "필요한 정보(target): 본진 사령부인지, 앞마당 사령부인지, 새로 지은 사령부인지 말해 주세요. "
    "예: 본진 보여줘 / 앞마당으로 카메라 옮겨 / 본진 입구 보여줘"
)
AMBIGUOUS_CAMERA_BASE_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "본진 보여줘",
    "앞마당으로 카메라 옮겨",
    "본진 입구 보여줘",
)
AMBIGUOUS_BUILD_BASE_FAILURE_CODE: Final[str] = "ambiguous_build_base"
AMBIGUOUS_BUILD_BASE_CLARIFICATION_REASON: Final[str] = (
    "Build placement named a generic command center/base without choosing which base."
)
AMBIGUOUS_BUILD_BASE_CLARIFICATION_PROMPT: Final[str] = (
    "어느 사령부 근처에 건설할지 몰라 실행하지 않았습니다. "
    "필요한 정보(location): 본진 사령부인지, 앞마당 사령부인지, 새로 지은 사령부인지 말해 주세요. "
    "예: 본진 사령부 근처에 배럭 지어 / 앞마당 사령부 근처에 보급고 지어"
)
AMBIGUOUS_BUILD_BASE_CLARIFICATION_ALTERNATIVES: Final[tuple[str, ...]] = (
    "본진 사령부 근처에 배럭 지어",
    "앞마당 사령부 근처에 보급고 지어",
    "본진에 보급고 지어",
)
MOVE_CAMERA_ALIAS: Final[str] = "move_camera"
MOVE_CAMERA_CONSTRAINT: Final[str] = "move camera to semantic target"
MOVE_CAMERA_RAMP_OR_ENTRANCE_TARGET_SLOT: Final[str] = "ramp_or_entrance"
MOVE_CAMERA_ENEMY_ENTRANCE_TARGET_SLOT: Final[str] = "enemy_entrance"
MOVE_CAMERA_NATURAL_EXPANSION_TARGET_SLOT: Final[str] = "natural_expansion"
MOVE_CAMERA_THIRD_BASE_TARGET_SLOT: Final[str] = "third_base"
MOVE_CAMERA_CHOKE_TARGET_SLOT: Final[str] = "choke"
MOVE_CAMERA_SCOUT_LOCATION_TARGET_SLOT: Final[str] = "scout_location"
MOVE_CAMERA_LAST_SEEN_ENEMY_AREA_TARGET_SLOT: Final[str] = "last_seen_enemy_area"

GATHER_RESOURCE_ALIAS: Final[str] = "gather_resource"
GATHER_RESOURCE_CONSTRAINT: Final[str] = "assign workers to requested resource"
KEEP_WORKER_PRODUCTION_ALIAS: Final[str] = "keep_worker_production"
KEEP_WORKER_PRODUCTION_CONSTRAINT: Final[str] = "keep SCV production continuous"
TRAIN_WORKER_ONESHOT_CONSTRAINT: Final[str] = "train requested SCV count"
PREVENT_SUPPLY_BLOCK_ALIAS: Final[str] = "prevent_supply_block"
PREVENT_SUPPLY_BLOCK_CONSTRAINT: Final[str] = "prevent supply block"
PREVENT_SUPPLY_BLOCK_LOCATION: Final[str] = "main ramp"
BUILD_STRUCTURE_ALIAS: Final[str] = "build_structure"
BUILD_STRUCTURE_CONSTRAINT: Final[str] = "construct requested Terran structure"
MAIN_ENTRANCE_PLACEMENT_POLICY: Final[dict[str, object]] = {
    "anchor": "main ramp",
    "anchor_target": "self_ramp",
    "spatial_relation": "near",
}
NATURAL_EXPANSION_PLACEMENT_POLICY: Final[dict[str, object]] = {
    "anchor": "natural expansion",
    "anchor_target": "self_natural",
    "spatial_relation": "near",
}
AWAY_FROM_MAIN_PLACEMENT_POLICY: Final[dict[str, object]] = {
    "anchor": "main base",
    "anchor_target": "self_main",
    "spatial_relation": "far_from",
}
MAIN_GEYSER_PLACEMENT_POLICY: Final[dict[str, object]] = {
    "anchor": "main geyser",
    "anchor_target": "self_geyser",
    "spatial_relation": "on",
}
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
EXPAND_DEFAULT_LOCATION: Final[str] = "natural expansion"

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
        utterance="자원채취",
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
        utterance="놀고 있는 일꾼들 일시켜",
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
        utterance="일꾼생산",
        payload=TrainWorkerIntent(
            priority="normal",
            constraints=(TRAIN_WORKER_ONESHOT_CONSTRAINT,),
            count=1,
        ),
    ),
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
        utterance="가스생산 시설 지어",
        payload=BuildStructureIntent(
            priority="high",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Refinery",
            location="main geyser",
            placement_policy=MAIN_GEYSER_PLACEMENT_POLICY,
        ),
    ),
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="배프빈가스 지어",
        payload=BuildStructureIntent(
            priority="high",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Refinery",
            location="main geyser",
            placement_policy=MAIN_GEYSER_PLACEMENT_POLICY,
        ),
    ),
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="배럴 지어",
        payload=BuildStructureIntent(
            priority="normal",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Barracks",
            location="main base",
        ),
    ),
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="뵤ㅗ급로 지어",
        payload=BuildStructureIntent(
            priority="normal",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Supply Depot",
            location="main ramp",
        ),
    ),
    InterpreterMapping(
        alias=BUILD_STRUCTURE_ALIAS,
        utterance="본진 입구에 서플라이 디포 지어",
        payload=BuildStructureIntent(
            priority="normal",
            constraints=(BUILD_STRUCTURE_CONSTRAINT,),
            structure="Supply Depot",
            location="main ramp",
            placement_policy=MAIN_ENTRANCE_PLACEMENT_POLICY,
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
            placement_policy=MAIN_GEYSER_PLACEMENT_POLICY,
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
            placement_policy=MAIN_GEYSER_PLACEMENT_POLICY,
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
        utterance="정찰보내",
        payload=ScoutIntent(
            priority="normal",
            constraints=(SEND_SCOUT_CONSTRAINT,),
            target=SEND_SCOUT_DEFAULT_TARGET,
            unit_group=SEND_SCOUT_DEFAULT_UNIT_GROUP,
        ),
    ),
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

MOVE_CAMERA_MAPPINGS: Final[tuple[InterpreterMapping, ...]] = (
    InterpreterMapping(
        alias=MOVE_CAMERA_ALIAS,
        utterance="본진 보여줘",
        payload=MoveCameraIntent(
            priority="normal",
            constraints=(MOVE_CAMERA_CONSTRAINT,),
            target="main base",
        ),
    ),
    InterpreterMapping(
        alias=MOVE_CAMERA_ALIAS,
        utterance="본진으로 카메라 옮겨",
        payload=MoveCameraIntent(
            priority="normal",
            constraints=(MOVE_CAMERA_CONSTRAINT,),
            target="main base",
        ),
    ),
    InterpreterMapping(
        alias=MOVE_CAMERA_ALIAS,
        utterance="본진으로 화면 이동",
        payload=MoveCameraIntent(
            priority="normal",
            constraints=(MOVE_CAMERA_CONSTRAINT,),
            target="main base",
        ),
    ),
    InterpreterMapping(
        alias=MOVE_CAMERA_ALIAS,
        utterance="본진 입구 카메라 보여줘",
        payload=MoveCameraIntent(
            priority="normal",
            constraints=(MOVE_CAMERA_CONSTRAINT,),
            target="main ramp",
            target_slot=MOVE_CAMERA_RAMP_OR_ENTRANCE_TARGET_SLOT,
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
    MOVE_CAMERA_MAPPINGS[0],
    MOVE_CAMERA_MAPPINGS[1],
)
"""Representative Korean matrix: exactly 2 utterances per canonical intent."""

KOREAN_COMMAND_TEST_CORPUS: Final[tuple[dict[str, object], ...]] = tuple(
    {
        "command_text": mapping.utterance,
        "expected_dsl": mapping.payload.to_dict(),
    }
    for mapping in REPRESENTATIVE_UTTERANCE_MATRIX
)
"""Korean test corpus with JSON-ready expected typed Intent DSL outputs."""

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
    *MOVE_CAMERA_MAPPINGS,
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

        payload, candidates = _resolve_command_payload(command_text, self.mappings)
        return _build_command_interpretation_result(
            command_text=command_text,
            payload=payload,
            candidates=candidates,
        )


DEFAULT_COMMAND_INTERPRETER: Final[CommandInterpreter] = CommandInterpreter()
"""Default Phase 0 interpreter used by module-level compatibility functions."""


def _interpret_command_text_with_mappings(
    command_text: str,
    mappings: tuple[InterpreterMapping, ...],
) -> IntentPayload | None:
    """Return the nearest supported typed Intent DSL payload for Korean text."""

    payload, _ = _resolve_command_payload(command_text, mappings)
    return payload


def _resolve_command_payload(
    command_text: str,
    mappings: tuple[InterpreterMapping, ...],
) -> tuple[IntentPayload | None, tuple[ClarificationCandidate, ...]]:
    """Resolve one command through the single ordered intent-family registry.

    Exact utterance matches resolve first. Otherwise the ordered candidate
    list is computed exactly once: one family match resolves directly, two or
    more surface the clarification candidates, and zero of either yields the
    unsupported-command case downstream.
    """

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        return None, ()

    exact_payload = _normalized_utterance_index(mappings).get(normalized_command)
    if exact_payload is not None:
        return exact_payload, ()

    candidates = _build_ambiguous_command_candidates(normalized_command)
    if len(candidates) == 1:
        if is_deictic_build_placement_missing_semantic_target(
            command_text,
            candidates[0].payload,
        ):
            return None, ()
        if is_distance_only_build_placement(command_text, candidates[0].payload):
            return None, ()
        if is_farther_build_placement_missing_direction(
            command_text,
            candidates[0].payload,
        ):
            return None, ()
        if is_unanchored_relative_build_placement(
            command_text,
            candidates[0].payload,
        ):
            return None, ()
        return candidates[0].payload, ()
    explicit_base_camera_payload = _explicit_base_camera_candidate_payload(
        normalized_command,
        candidates,
    )
    if explicit_base_camera_payload is not None:
        return explicit_base_camera_payload, ()
    semantic_camera_payload = _semantic_camera_candidate_payload(
        normalized_command,
        candidates,
    )
    if semantic_camera_payload is not None:
        return semantic_camera_payload, ()
    explicit_base_build_payload = _explicit_base_build_candidate_payload(
        normalized_command,
        candidates,
    )
    if explicit_base_build_payload is not None:
        return explicit_base_build_payload, ()
    return None, candidates


@lru_cache(maxsize=None)
def _normalized_utterance_index(
    mappings: tuple[InterpreterMapping, ...],
) -> dict[str, IntentPayload]:
    """Precompute the normalized exact-match lookup once per mapping table.

    First-listed mappings win duplicate normalized utterances, matching the
    historical first-match loop order.
    """

    index: dict[str, IntentPayload] = {}
    for mapping in mappings:
        index.setdefault(_normalize_command_text(mapping.utterance), mapping.payload)
    return index


def _build_command_interpretation_result(
    *,
    command_text: str,
    payload: IntentPayload | None,
    candidates: tuple[ClarificationCandidate, ...] = (),
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

    if is_distance_only_build_placement(command_text, payload):
        return build_missing_build_anchor_result(command_text)

    if is_farther_build_placement_missing_direction(command_text, payload):
        return build_missing_build_direction_result(command_text)

    if is_unanchored_relative_build_placement(command_text, payload):
        return build_missing_build_relative_anchor_result(command_text)

    if is_deictic_build_placement_missing_semantic_target(command_text, payload):
        return build_missing_build_semantic_target_result(command_text)

    if payload is not None:
        return CommandInterpretationResult(
            command_text=command_text,
            payload=payload,
            clarification_required=False,
        )

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

    if is_build_request_missing_structure(command_text):
        return build_missing_build_structure_result(command_text)

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


def build_missing_build_anchor_result(command_text: str) -> CommandInterpretationResult:
    """Ask for the missing placement anchor instead of guessing a build target."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    clarification_prompt = _build_missing_build_anchor_prompt(command_text_value)
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=clarification_prompt,
        reason=MISSING_BUILD_ANCHOR_CLARIFICATION_REASON,
        alternatives=MISSING_BUILD_ANCHOR_CLARIFICATION_ALTERNATIVES,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=MISSING_BUILD_ANCHOR_FAILURE_CODE,
            message=MISSING_BUILD_ANCHOR_CLARIFICATION_REASON,
            alternatives=MISSING_BUILD_ANCHOR_CLARIFICATION_ALTERNATIVES,
            intent="BUILD_STRUCTURE",
            metadata={
                "missing_fields": ["location"],
                "missing_anchor": True,
                "missing_direction": True,
                "placement_modifier": "distance",
            },
        ),
    )


def build_missing_build_direction_result(command_text: str) -> CommandInterpretationResult:
    """Ask for the missing farther-placement direction after an anchor is known."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    clarification_prompt = _build_missing_build_direction_prompt(command_text_value)
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=clarification_prompt,
        reason=MISSING_BUILD_DIRECTION_CLARIFICATION_REASON,
        alternatives=MISSING_BUILD_DIRECTION_CLARIFICATION_ALTERNATIVES,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=MISSING_BUILD_DIRECTION_FAILURE_CODE,
            message=MISSING_BUILD_DIRECTION_CLARIFICATION_REASON,
            alternatives=MISSING_BUILD_DIRECTION_CLARIFICATION_ALTERNATIVES,
            intent="BUILD_STRUCTURE",
            metadata={
                "missing_fields": ["direction"],
                "missing_anchor": False,
                "anchor_known": True,
                "missing_direction": True,
                "placement_modifier": "farther",
            },
        ),
    )


def build_missing_build_relative_anchor_result(
    command_text: str,
) -> CommandInterpretationResult:
    """Ask for the missing anchor/direction for relative build placement."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    clarification_prompt = _build_missing_build_relative_anchor_prompt(
        command_text_value
    )
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=clarification_prompt,
        reason=MISSING_BUILD_RELATIVE_ANCHOR_CLARIFICATION_REASON,
        alternatives=MISSING_BUILD_RELATIVE_ANCHOR_CLARIFICATION_ALTERNATIVES,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=MISSING_BUILD_RELATIVE_ANCHOR_FAILURE_CODE,
            message=MISSING_BUILD_RELATIVE_ANCHOR_CLARIFICATION_REASON,
            alternatives=MISSING_BUILD_RELATIVE_ANCHOR_CLARIFICATION_ALTERNATIVES,
            intent="BUILD_STRUCTURE",
            metadata={
                "missing_fields": ["location"],
                "missing_anchor": True,
                "missing_direction": True,
                "placement_modifier": "relative",
            },
        ),
    )


def build_missing_relative_action_anchor_result(
    command_text: str,
    payload: IntentPayload | None = None,
) -> CommandInterpretationResult:
    """Ask for a concrete anchor before moving camera, units, or game actions."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    intent_name = _payload_intent_name(payload) or "ACTION"
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=_build_missing_relative_action_anchor_prompt(
            command_text_value,
            intent_name,
        ),
        reason=MISSING_RELATIVE_ACTION_ANCHOR_CLARIFICATION_REASON,
        alternatives=MISSING_RELATIVE_ACTION_ANCHOR_CLARIFICATION_ALTERNATIVES,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=MISSING_RELATIVE_ACTION_ANCHOR_FAILURE_CODE,
            message=MISSING_RELATIVE_ACTION_ANCHOR_CLARIFICATION_REASON,
            alternatives=MISSING_RELATIVE_ACTION_ANCHOR_CLARIFICATION_ALTERNATIVES,
            intent=intent_name,
            metadata={
                "missing_fields": ["target"],
                "missing_anchor": True,
                "relative_modifier": True,
            },
        ),
    )


def build_missing_build_semantic_target_result(
    command_text: str,
) -> CommandInterpretationResult:
    """Ask for a supported semantic target instead of guessing a clicked spot."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    clarification_prompt = _build_missing_build_semantic_target_prompt(
        command_text_value
    )
    structure = _detect_structure_name(_normalize_command_text(command_text_value))
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=clarification_prompt,
        reason=MISSING_BUILD_SEMANTIC_TARGET_CLARIFICATION_REASON,
        alternatives=MISSING_BUILD_SEMANTIC_TARGET_CLARIFICATION_ALTERNATIVES,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=MISSING_BUILD_SEMANTIC_TARGET_FAILURE_CODE,
            message=MISSING_BUILD_SEMANTIC_TARGET_CLARIFICATION_REASON,
            alternatives=MISSING_BUILD_SEMANTIC_TARGET_CLARIFICATION_ALTERNATIVES,
            intent="BUILD_STRUCTURE",
            metadata={
                "missing_fields": ["location"],
                "deictic_target": True,
                "supported_semantic_targets": list(
                    SUPPORTED_BUILD_SEMANTIC_TARGET_LABELS
                ),
                "structure_detected": structure is not None,
            },
        ),
    )


def build_missing_build_structure_result(command_text: str) -> CommandInterpretationResult:
    """Ask which structure to build instead of guessing from location-only text."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    clarification_prompt = _build_missing_build_structure_prompt(command_text_value)
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=clarification_prompt,
        reason=MISSING_BUILD_STRUCTURE_CLARIFICATION_REASON,
        alternatives=MISSING_BUILD_STRUCTURE_CLARIFICATION_ALTERNATIVES,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=MISSING_BUILD_STRUCTURE_FAILURE_CODE,
            message=MISSING_BUILD_STRUCTURE_CLARIFICATION_REASON,
            alternatives=MISSING_BUILD_STRUCTURE_CLARIFICATION_ALTERNATIVES,
            intent="BUILD_STRUCTURE",
            metadata={
                "missing_fields": ["structure"],
                "structure_detected": False,
            },
        ),
    )


def build_ambiguous_camera_base_result(
    command_text: str,
    base_choices: tuple[str, ...] = (),
) -> CommandInterpretationResult:
    """Ask for a concrete camera base target instead of guessing a townhall."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    choices = tuple(choice.strip() for choice in base_choices if choice.strip())
    alternatives = choices or AMBIGUOUS_CAMERA_BASE_CLARIFICATION_ALTERNATIVES
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=_build_ambiguous_camera_base_prompt(choices),
        reason=AMBIGUOUS_CAMERA_BASE_CLARIFICATION_REASON,
        alternatives=alternatives,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=AMBIGUOUS_CAMERA_BASE_FAILURE_CODE,
            message=AMBIGUOUS_CAMERA_BASE_CLARIFICATION_REASON,
            alternatives=alternatives,
            intent="MOVE_CAMERA",
            metadata={
                "missing_fields": ["target"],
                "ambiguous_base": True,
                "supported_targets": list(alternatives),
            },
        ),
    )


def build_ambiguous_build_base_result(
    command_text: str,
    base_choices: tuple[str, ...] = (),
) -> CommandInterpretationResult:
    """Ask for a concrete build-near base target instead of guessing."""

    command_text_value = command_text if isinstance(command_text, str) else ""
    choices = tuple(choice.strip() for choice in base_choices if choice.strip())
    alternatives = choices or AMBIGUOUS_BUILD_BASE_CLARIFICATION_ALTERNATIVES
    return CommandInterpretationResult(
        command_text=command_text_value,
        payload=None,
        clarification_required=True,
        clarification_prompt=_build_ambiguous_build_base_prompt(
            command_text_value,
            choices,
        ),
        reason=AMBIGUOUS_BUILD_BASE_CLARIFICATION_REASON,
        alternatives=alternatives,
        failure=build_parsing_failure_report(
            command_text=command_text_value,
            code=AMBIGUOUS_BUILD_BASE_FAILURE_CODE,
            message=AMBIGUOUS_BUILD_BASE_CLARIFICATION_REASON,
            alternatives=alternatives,
            intent="BUILD_STRUCTURE",
            metadata={
                "missing_fields": ["location"],
                "ambiguous_base": True,
                "supported_targets": list(alternatives),
            },
        ),
    )


def _build_ambiguous_camera_base_prompt(base_choices: tuple[str, ...]) -> str:
    """Return a Korean reverse question listing observed base choices."""

    if not base_choices:
        return AMBIGUOUS_CAMERA_BASE_CLARIFICATION_PROMPT
    choices = " / ".join(base_choices)
    return (
        "어느 사령부로 카메라를 옮길지 몰라 실행하지 않았습니다. "
        f"가능한 선택지: {choices}. "
        "필요한 정보(target): 위 선택지 중 어느 사령부인지 말해 주세요. "
        "예: 본진 보여줘 / 앞마당으로 카메라 옮겨"
    )


def _build_ambiguous_build_base_prompt(
    command_text: str,
    base_choices: tuple[str, ...],
) -> str:
    """Return a Korean reverse question listing observed build anchor choices."""

    request = _describe_build_target_request(command_text)
    if not base_choices:
        return (
            f"{request} 요청은 유지하겠습니다. "
            f"{AMBIGUOUS_BUILD_BASE_CLARIFICATION_PROMPT}"
        )
    choices = " / ".join(base_choices)
    return (
        f"{request} 요청은 유지하겠습니다. "
        "어느 사령부 근처에 건설할지 몰라 실행하지 않았습니다. "
        f"가능한 선택지: {choices}. "
        "필요한 정보(location): 위 선택지 중 어느 사령부인지 말해 주세요. "
        "예: 본진 사령부 근처에 배럭 지어 / 앞마당 사령부 근처에 보급고 지어"
    )


def _build_missing_build_structure_prompt(command_text: str) -> str:
    """Return a Korean reverse question for build text without a structure."""

    command_text_value = command_text.strip()
    request_prefix = (
        f"`{command_text_value}` 요청은 유지하겠습니다. "
        if command_text_value
        else ""
    )
    return (
        f"{request_prefix}어떤 건물을 지을지 몰라 실행하지 않았습니다. "
        "필요한 정보(structure): 보급고, 배럭, 정제소, 벙커, 커맨드 센터 중 "
        "무엇을 지을지 말해 주세요. "
        "예: 사령부 근처에 보급고 지어 / 본진 사령부 근처에 배럭 지어 / "
        "앞마당 사령부 근처에 벙커 지어"
    )


def _build_missing_build_anchor_prompt(command_text: str) -> str:
    """Return a concrete Korean reverse question for distance-only building."""

    request = _describe_build_distance_request(command_text)
    return (
        f"{request} 요청은 유지하겠습니다. "
        "하지만 기준점과 멀어질 방향이 없어 실행하지 않았습니다. "
        "필요한 정보(location): 어디를 기준으로, 어느 방향으로 더 멀게 지을까요? "
        "예: 본진에서 앞마당 쪽으로 더 멀게 보급고 지어 / "
        "본진 입구보다 뒤에 보급고 지어 / 앞마당 입구에 벙커 지어"
    )


def _build_missing_build_direction_prompt(command_text: str) -> str:
    """Return a concrete Korean reverse question for anchored farther building."""

    request = _describe_build_distance_request(command_text)
    anchor = _describe_placement_anchor(command_text)
    return (
        f"{anchor} 기준으로 {request} 요청은 유지하겠습니다. "
        "하지만 더 멀어질 방향이나 목표 위치가 없어 실행하지 않았습니다. "
        "필요한 정보(direction): 어느 방향으로 더 멀게 지을까요? "
        "예: 본진에서 앞마당 쪽으로 더 멀게 보급고 지어 / "
        "본진 입구보다 뒤에 보급고 지어 / 미네랄에서 떨어지게 보급고 지어"
    )


def _build_missing_build_relative_anchor_prompt(command_text: str) -> str:
    """Return a concrete Korean reverse question for unanchored relative build."""

    request = _describe_build_target_request(command_text)
    return (
        f"{request} 요청은 유지하겠습니다. "
        "하지만 근처/쪽/떨어지게 같은 상대 위치에 필요한 기준점이나 방향이 없어 "
        "실행하지 않았습니다. "
        "필요한 정보(location): 어느 기준 위치나 방향으로 지을까요? "
        "예: 앞마당 근처에 보급고 지어 / 입구 쪽으로 보급고 지어 / "
        "미네랄에서 떨어지게 보급고 지어"
    )


def _build_missing_relative_action_anchor_prompt(
    command_text: str,
    intent_name: str,
) -> str:
    """Return a concrete Korean reverse question for relative action targets."""

    action_label = _relative_action_intent_label(intent_name)
    command_text_value = str(command_text or "").strip()
    request = (
        f"`{command_text_value}` {action_label}"
        if command_text_value
        else action_label
    )
    return (
        f"{request} 요청은 유지하겠습니다. "
        "하지만 근처/쪽/방향 같은 상대 위치에 필요한 기준점이나 대상이 없어 "
        "실행하지 않았습니다. "
        "필요한 정보(target): 어느 기준 위치나 대상으로 실행할까요? "
        "예: 본진 입구로 병력 보내 / 앞마당으로 카메라 옮겨 / 적 앞마당 정찰 보내"
    )


def _relative_action_intent_label(intent_name: str) -> str:
    labels = {
        "MOVE_CAMERA": "카메라 이동",
        "DEFEND": "병력 이동/방어",
        "SCOUT": "정찰",
        "HARASS": "견제",
        "REPAIR": "수리",
        "GATHER_RESOURCE": "자원 채취",
        "TRAIN_WORKER": "일꾼 생산",
        "TRAIN_ARMY": "병력 생산",
        "EXPAND": "확장",
    }
    return labels.get(str(intent_name or "").strip(), "게임 액션")


def _build_missing_build_semantic_target_prompt(command_text: str) -> str:
    """Return a Korean reverse question for deictic building placement."""

    request = _describe_build_target_request(command_text)
    supported_targets = ", ".join(SUPPORTED_BUILD_SEMANTIC_TARGET_LABELS)
    examples = " / ".join(MISSING_BUILD_SEMANTIC_TARGET_CLARIFICATION_ALTERNATIVES)
    return (
        f"{request} 요청은 유지하겠습니다. "
        "`저기/여기/거기`처럼 찍은 위치는 현재 지원되는 semantic target이 아니라 "
        "실행하지 않았습니다. "
        f"필요한 정보(location): 지원되는 semantic target 중 어디에 지을까요? "
        f"가능한 위치: {supported_targets}. "
        f"예: {examples}"
    )


def _describe_build_distance_request(command_text: str) -> str:
    normalized_command = _normalize_command_text(command_text)
    structure = _detect_structure_name(normalized_command)
    structure_label = _BUILD_STRUCTURE_KOREAN_OBJECT_LABELS.get(structure, "건물을")
    modifier_label = _detect_distance_modifier_label(normalized_command)
    return f"{structure_label} {modifier_label} 짓는"


def _describe_build_target_request(command_text: str) -> str:
    normalized_command = _normalize_command_text(command_text)
    structure = _detect_structure_name(normalized_command)
    structure_label = _BUILD_STRUCTURE_KOREAN_OBJECT_LABELS.get(structure, "건물을")
    return f"{structure_label} 짓는"


def _detect_distance_modifier_label(normalized_command: str) -> str:
    if _contains_any_pattern(normalized_command, _FARTHER_COMPARATIVE_PLACEMENT_PATTERNS):
        return "더 멀게"
    if _contains_any_pattern(normalized_command, _DISTANCE_ONLY_PLACEMENT_PATTERNS):
        return "멀게"
    return "거리 조건에 맞춰"


def _describe_placement_anchor(command_text: str) -> str:
    normalized_command = _normalize_command_text(command_text)
    for label, patterns in _PLACEMENT_ANCHOR_LABEL_PATTERNS:
        if _contains_any_pattern(normalized_command, patterns):
            return label
    return "말한 기준점"


def interpret_command_text(command_text: str) -> IntentPayload | None:
    """Return the nearest supported typed Intent DSL payload for Korean text."""

    return DEFAULT_COMMAND_INTERPRETER.interpret_text(command_text)


def interpret_command(command_text: str) -> CommandInterpretationResult:
    """Return payload or a commander-facing clarification prompt."""

    return DEFAULT_COMMAND_INTERPRETER.interpret(command_text)


def _build_ambiguous_command_candidates(
    normalized_command: str,
) -> tuple[ClarificationCandidate, ...]:
    """Build ordered candidates from the single intent-family registry."""

    if not normalized_command:
        return ()

    deduplicated: dict[tuple[str, str], ClarificationCandidate] = {}
    for spec in INTENT_CANDIDATE_SPECS:
        spec_payload = spec.build_payload(normalized_command)
        if spec_payload is None:
            continue
        candidate = ClarificationCandidate(
            alias=spec.alias,
            intent=spec.intent,
            description=spec.description,
            payload=spec_payload,
        )
        key = (candidate.alias, repr(candidate.payload.to_dict()))
        deduplicated.setdefault(key, candidate)
    return tuple(deduplicated.values())


def _explicit_base_camera_candidate_payload(
    normalized_command: str,
    candidates: tuple[ClarificationCandidate, ...],
) -> IntentPayload | None:
    """Prefer camera movement when an explicit base is only the camera target."""

    base_selection = parse_korean_base_selection(normalized_command)
    if base_selection is None:
        return None
    if not _contains_any_pattern(normalized_command, _CAMERA_ACTION_PATTERNS):
        return None
    if _contains_any_pattern(
        normalized_command,
        (*_BUILD_STRUCTURE_VERB_PATTERNS, "확장", "멀티"),
    ):
        return None
    camera_candidates = tuple(
        candidate for candidate in candidates if candidate.intent == "MOVE_CAMERA"
    )
    if len(camera_candidates) != 1:
        return None
    return camera_candidates[0].payload


def _semantic_camera_candidate_payload(
    normalized_command: str,
    candidates: tuple[ClarificationCandidate, ...],
) -> IntentPayload | None:
    """Prefer camera movement for explicit semantic map target references."""

    if not _contains_any_pattern(normalized_command, _CAMERA_ACTION_PATTERNS):
        return None
    if not _contains_any_pattern(
        normalized_command,
        (
            *_SCOUT_LOCATION_PATTERNS,
            *_LAST_SEEN_ENEMY_AREA_PATTERNS,
            *_THIRD_LOCATION_PATTERNS,
        ),
    ):
        return None
    if _contains_any_pattern(normalized_command, _BUILD_STRUCTURE_VERB_PATTERNS):
        return None
    camera_candidates = tuple(
        candidate for candidate in candidates if candidate.intent == "MOVE_CAMERA"
    )
    if len(camera_candidates) != 1:
        return None
    return camera_candidates[0].payload


def _explicit_base_build_candidate_payload(
    normalized_command: str,
    candidates: tuple[ClarificationCandidate, ...],
) -> IntentPayload | None:
    """Prefer a concrete structure build over EXPAND for base-qualified builds."""

    if parse_korean_base_selection(normalized_command) is None:
        return None
    if not _has_build_structure_verb(normalized_command):
        return None
    if _detect_structure_name(normalized_command) is None:
        return None
    if _has_explicit_expand_action_for_build_disambiguation(normalized_command):
        return None
    build_candidates = tuple(
        candidate for candidate in candidates if candidate.intent == "BUILD_STRUCTURE"
    )
    if len(build_candidates) != 1:
        return None
    return build_candidates[0].payload


def _has_explicit_expand_action_for_build_disambiguation(
    normalized_command: str,
) -> bool:
    """Return True for expansion verbs, excluding townhall words used as labels."""

    explicit_expand_verbs = _normalize_patterns(
        (
            "가져",
            "먹어",
            "먹자",
            "펴",
            "expand",
            "take",
            "secure",
            "prepare",
        )
    )
    if _contains_any_pattern(normalized_command, explicit_expand_verbs):
        return True
    return _contains_any_pattern(
        normalized_command,
        _normalize_patterns(
            (
                "확장해",
                "확장하",
                "확장하고",
                "멀티해",
                "멀티하고",
            )
        ),
    )


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


MALFORMED_KOREAN_INPUT_NORMALIZATION_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("가스배럴", "가스통"),
    ("배프빈가스", "베스핀가스"),
    ("뵤ㅗ급로", "보급고"),
    ("뵤ㅗ급", "보급"),
    ("뵤급로", "보급고"),
    ("뵤급", "보급"),
    ("보급로", "보급고"),
    ("배럴", "배럭"),
)
"""Known voice/STT malformations normalized before intent matching.

Rules are ordered from compound to simple forms so ``가스 배럴`` stays a
Refinery request while standalone ``배럴`` remains the common Barracks typo.
"""


def _normalize_command_text(command_text: str) -> str:
    if not isinstance(command_text, str):
        return ""
    normalized_command = "".join(command_text.casefold().split())
    for malformed_token, normalized_token in MALFORMED_KOREAN_INPUT_NORMALIZATION_RULES:
        normalized_command = normalized_command.replace(
            malformed_token,
            normalized_token,
        )
    return normalized_command


def _normalize_patterns(patterns: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize a constant pattern tuple once at module definition time."""

    return tuple(_normalize_command_text(pattern) for pattern in patterns)


def _contains_any_pattern(normalized_command: str, patterns: tuple[str, ...]) -> bool:
    """Return True when any pre-normalized pattern occurs in the command."""

    return any(pattern in normalized_command for pattern in patterns)


SUPPORTED_PARSED_ANCHOR_LABELS: Final[tuple[ParsedAnchorLabel, ...]] = (
    ParsedAnchorLabel(
        key="base",
        label="main base",
        target="self_main",
        aliases=(
            "본진",
            "우리 본진",
            "내 본진",
            "main",
            "base",
            "main base",
        ),
    ),
    ParsedAnchorLabel(
        key="mineral",
        label="mineral line",
        target="self_mineral_line",
        aliases=(
            "미네랄",
            "광물",
            "미네랄 라인",
            "본진 미네랄",
            "mineral",
            "minerals",
            "mineral line",
            "main mineral line",
        ),
    ),
    ParsedAnchorLabel(
        key="entrance",
        label="main ramp",
        target="self_ramp",
        aliases=(
            "입구",
            "본진 입구",
            "램프",
            "언덕",
            "초크",
            "entrance",
            "ramp",
            "main ramp",
            "choke",
        ),
    ),
    ParsedAnchorLabel(
        key="natural_expansion",
        label="natural expansion",
        target="self_natural",
        aliases=(
            "앞마당",
            "내추럴",
            "우리 앞마당",
            "멀티",
            "확장",
            "natural",
            "natural expansion",
            "expansion",
        ),
    ),
)
"""Supported parsed anchor labels for safe placement policies."""

SUPPORTED_PARSED_ANCHOR_LABEL_KEYS: Final[tuple[str, ...]] = tuple(
    anchor.key for anchor in SUPPORTED_PARSED_ANCHOR_LABELS
)
"""Stable parsed-anchor key order exposed for tests and UI diagnostics."""

SUPPORTED_PARSED_ANCHOR_DISPLAY_LABELS: Final[tuple[str, ...]] = tuple(
    anchor.label for anchor in SUPPORTED_PARSED_ANCHOR_LABELS
)
"""Human-readable parsed-anchor labels accepted by placement parsing."""

_PARSED_ANCHOR_LABEL_BY_KEY: Final[dict[str, ParsedAnchorLabel]] = {
    anchor.key: anchor for anchor in SUPPORTED_PARSED_ANCHOR_LABELS
}
_PARSED_ANCHOR_NORMALIZED_ALIAS_INDEX: Final[dict[str, ParsedAnchorLabel]] = {
    alias: anchor
    for anchor in SUPPORTED_PARSED_ANCHOR_LABELS
    for alias in anchor.normalized_aliases
}


def parsed_anchor_label_for_key(anchor_key: str) -> ParsedAnchorLabel:
    """Return one supported parsed-anchor label by stable key."""

    try:
        return _PARSED_ANCHOR_LABEL_BY_KEY[anchor_key]
    except KeyError as exc:
        supported = ", ".join(SUPPORTED_PARSED_ANCHOR_LABEL_KEYS)
        raise ValueError(
            f"Unsupported parsed anchor label key: {anchor_key!r}. "
            f"Supported keys: {supported}."
        ) from exc


def normalize_parsed_anchor_label(anchor_text: str) -> ParsedAnchorLabel | None:
    """Normalize a user/reference anchor phrase to a supported parsed label."""

    normalized = _normalize_command_text(anchor_text)
    if not normalized:
        return None
    return _PARSED_ANCHOR_NORMALIZED_ALIAS_INDEX.get(normalized)


def _relative_location_phrase(
    *,
    anchor_key: str,
    spatial_relation: str,
    source_text: str,
    direction_key: str = "",
) -> KoreanRelativeLocationPhrase:
    """Build placement policy fields from normalized parsed-anchor labels."""

    anchor = parsed_anchor_label_for_key(anchor_key)
    direction = parsed_anchor_label_for_key(direction_key) if direction_key else None
    return KoreanRelativeLocationPhrase(
        anchor=anchor.label,
        anchor_target=anchor.target,
        spatial_relation=spatial_relation,
        source_text=source_text,
        direction=direction.label if direction else "",
        direction_target=direction.target if direction else "",
    )


def _away_from_main_relative_location_phrase(
    *,
    source_text: str,
    direction_key: str = "",
) -> KoreanRelativeLocationPhrase:
    """Build the explicit away-from-main placement policy fields."""

    direction = parsed_anchor_label_for_key(direction_key) if direction_key else None
    return KoreanRelativeLocationPhrase(
        anchor=str(AWAY_FROM_MAIN_PLACEMENT_POLICY["anchor"]),
        anchor_target=str(AWAY_FROM_MAIN_PLACEMENT_POLICY["anchor_target"]),
        spatial_relation=str(AWAY_FROM_MAIN_PLACEMENT_POLICY["spatial_relation"]),
        source_text=source_text,
        direction=direction.label if direction else "",
        direction_target=direction.target if direction else "",
    )


def parse_korean_relative_location_phrase(
    command_text: str,
) -> KoreanRelativeLocationPhrase | None:
    """Parse anchored Korean relative placement into anchor/relation fields."""

    normalized_command = _normalize_command_text(command_text)
    source_text = command_text.strip() if isinstance(command_text, str) else ""
    if not normalized_command or not source_text:
        return None

    if _contains_any_pattern(
        normalized_command,
        _MINERAL_AWAY_RELATIVE_LOCATION_PATTERNS,
    ):
        return _relative_location_phrase(
            anchor_key="mineral",
            spatial_relation="away_from",
            source_text=source_text,
        )
    if _contains_any_pattern(
        normalized_command,
        _RAMP_TOWARD_RELATIVE_LOCATION_PATTERNS,
    ):
        return _relative_location_phrase(
            anchor_key="entrance",
            spatial_relation="toward",
            source_text=source_text,
        )
    if _contains_any_pattern(
        normalized_command,
        _NATURAL_NEAR_RELATIVE_LOCATION_PATTERNS,
    ):
        return _relative_location_phrase(
            anchor_key="natural_expansion",
            spatial_relation="near",
            source_text=source_text,
        )
    has_main_far_phrase = _contains_any_pattern(
        normalized_command,
        _MAIN_FAR_RELATIVE_LOCATION_PATTERNS,
    ) or (
        _contains_any_pattern(
            normalized_command,
            _MAIN_ANCHOR_RELATIVE_LOCATION_PATTERNS,
        )
        and _contains_any_pattern(
            normalized_command,
            _DISTANCE_ONLY_PLACEMENT_PATTERNS,
        )
    )
    if has_main_far_phrase:
        direction_key = ""
        if _contains_any_pattern(
            normalized_command,
            _NATURAL_DIRECTION_RELATIVE_LOCATION_PATTERNS,
        ):
            direction_key = "natural_expansion"
        return _away_from_main_relative_location_phrase(
            source_text=source_text,
            direction_key=direction_key,
        )
    return None


_WORKER_SUBJECT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "SCV",
        # Whisper renders the spoken letters S-C-V in several hangul forms;
        # all common transliterations must resolve, not just one.
        "에스시비",
        "에스씨브이",
        "에스시브이",
        "에스씨비",
        "일꾼",
        "worker",
        "workers",
    ),
)
_PRODUCTION_CONTINUITY_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("계속", "유지", "쉬지말고", "끊기지않게", "keep", "continuous", "constantly"),
)
_WORKER_TRAINING_VERB_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "찍어",
        "뽑아",
        "생산",
        "생성",
        "만들",
        "눌러",
        "train",
        "produce",
        "queue",
        "make",
    ),
)
_GATHER_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "붙여",
        "붙이고",
        "붙여줘",
        "채취",
        "캐",
        "캐게",
        "보내",
        "일시켜",
        "일하게",
        "놀고있는일꾼",
        "assign",
        "gather",
        "mine",
        "harvest",
    ),
)
_GAS_RESOURCE_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("가스", "베스핀", "배스핀", "배프빈", "vespene", "gas"),
)
_MINERAL_RESOURCE_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("미네랄", "광물", "자원", "resource", "resources", "mineral", "minerals"),
)
_GENERIC_RESOURCE_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("자원", "resource", "resources"),
)
_NATURAL_BASE_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("앞마당", "내추럴", "natural", "expansion"),
)
_BASE_SELECTION_DEFINITIONS: Final[
    tuple[tuple[str, str, str, str, tuple[str, ...]], ...]
] = (
    (
        "main",
        "main base",
        "self_main",
        "main base",
        _normalize_patterns(
            (
                "본진",
                "본진 사령부",
                "메인",
                "메인 베이스",
                "main",
                "main base",
                "main command center",
                "1번 사령부",
                "첫 사령부",
                "첫번째 사령부",
                "첫째 사령부",
            )
        ),
    ),
    (
        "natural",
        "natural expansion",
        "self_natural",
        "natural expansion",
        _normalize_patterns(
            (
                "앞마당",
                "앞마당 사령부",
                "내추럴",
                "내추럴 사령부",
                "멀티",
                "확장",
                "natural",
                "natural expansion",
                "natural command center",
                "2번 사령부",
                "두번째 사령부",
                "둘째 사령부",
            )
        ),
    ),
    (
        "third",
        "third base",
        "self_third",
        "third base",
        _normalize_patterns(
            (
                "third",
                "third base",
                "third command center",
                "3rd base",
                "3번 사령부",
                "세번째 사령부",
                "셋째 사령부",
                "삼룡이",
                "3멀티",
                "세번째 멀티",
            )
        ),
    ),
    (
        "newest",
        "newest base",
        "self_newest",
        "newest base",
        _normalize_patterns(
            (
                "newest",
                "newest base",
                "latest base",
                "새로 지은 사령부",
                "새 사령부",
                "최근 사령부",
                "가장 최근 사령부",
                "막 지은 사령부",
                "새로 먹은 멀티",
            )
        ),
    ),
)
_ADDITIONAL_BASE_SELECTION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:추가사령부|추가커맨드|additional(?:base|commandcenter|cc))(?P<index>\d+)"
)


def parse_korean_base_selection(command_text: str) -> KoreanBaseSelectionIntent | None:
    """Parse explicit base modifiers into a structured selector.

    Generic words like ``사령부`` or bare ``base`` intentionally do not match:
    those remain clarification cases when multiple townhalls exist.
    """

    normalized_command = _normalize_command_text(command_text)
    source_text = command_text.strip() if isinstance(command_text, str) else ""
    if not normalized_command or not source_text:
        return None

    additional_match = _ADDITIONAL_BASE_SELECTION_PATTERN.search(normalized_command)
    if additional_match is not None:
        index = additional_match.group("index")
        return KoreanBaseSelectionIntent(
            selector=f"additional_{index}",
            label=f"additional base {index}",
            target=f"self_additional_{index}",
            location=f"additional base {index}",
            source_text=source_text,
        )

    matches: list[KoreanBaseSelectionIntent] = []
    for selector, label, target, location, patterns in _BASE_SELECTION_DEFINITIONS:
        if _contains_any_pattern(normalized_command, patterns):
            matches.append(
                KoreanBaseSelectionIntent(
                    selector=selector,
                    label=label,
                    target=target,
                    location=location,
                    source_text=source_text,
                )
            )
    if len(matches) != 1:
        return None
    return matches[0]


def _has_explicit_base_selection(normalized_command: str) -> bool:
    return parse_korean_base_selection(normalized_command) is not None


def _requires_structured_base_selection_metadata(
    selection: KoreanBaseSelectionIntent | None,
) -> bool:
    return selection is not None


def _explicit_base_selection_placement_policy(
    normalized_command: str,
    selection: KoreanBaseSelectionIntent | None,
) -> dict[str, object]:
    """Return placement metadata that keeps explicit base builds auditable."""

    if not _requires_structured_base_selection_metadata(selection):
        return {}
    assert selection is not None
    policy: dict[str, object] = {"base_selection": selection.to_dict()}
    if _contains_any_pattern(normalized_command, _BUILD_NEAR_BASE_RELATION_PATTERNS):
        policy["anchor"] = selection.label
        policy["anchor_target"] = selection.target
        policy["spatial_relation"] = "near"
    return policy
_SUPPLY_SUBJECT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "서플",
        "서플라이",
        "디포",
        "보급",
        "보급고",
        "보급로",
        "뵤급",
        "뵤급로",
        "뵤ㅗ급",
        "뵤ㅗ급로",
        "인구",
        "supply",
        "supply depot",
        "depot",
    ),
)
_REFINERY_COMPOUND_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "가스배럴",
        "가스시설",
        "가스생산",
        "가스생산시설",
        "가스통",
        "베스핀가스",
        "배스핀가스",
        "배프빈가스",
    ),
)
_SUPPLY_PRESSURE_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("막히", "안막히", "트이", "부족", "늘려", "뚫", "미리", "block", "blocked", "cap", "room"),
)
_STRUCTURE_NAME_ALIASES: Final[tuple[tuple[StructureName, tuple[str, ...]], ...]] = (
    (
        "Supply Depot",
        _normalize_patterns(
            (
                "서플라이디포",
                "서플라이",
                "서플",
                "보급",
                "보급고",
                "보급로",
                "뵤급",
                "뵤급로",
                "뵤ㅗ급",
                "뵤ㅗ급로",
                "supplydepot",
                "depot",
            ),
        ),
    ),
    ("Barracks", _normalize_patterns(("배럭스", "배럭", "배럴", "병영", "barracks", "rax"))),
    (
        "Refinery",
        _normalize_patterns(
            (
                "리파이너리",
                "정제소",
                "가스통",
                "가스시설",
                "가스생산",
                "가스생산시설",
                "가스배럴",
                "베스핀가스",
                "배스핀가스",
                "배프빈가스",
                "refinery",
            ),
        ),
    ),
    ("Bunker", _normalize_patterns(("벙커", "bunker"))),
    (
        "Command Center",
        _normalize_patterns(
            ("커맨드센터", "커맨드", "commandcenter", "commandcentre", "cc"),
        ),
    ),
)
_BUILD_STRUCTURE_VERB_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "지어",
        "지어서",
        "짓",
        "올려",
        "건설",
        "설치",
        "생산",
        "만들",
        "build",
        "construct",
        "make",
        "raise",
    ),
)
_NATURAL_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("앞마당", "내추럴", "natural"),
)
_CHOKE_HINT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("입구", "언덕", "초크", "쪽", "choke"),
)
_RAMP_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("입구", "언덕", "램프", "ramp"),
)
_RAMP_ONLY_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("램프", "ramp"),
)
_CHOKE_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("초크", "choke", "앞마당 입구", "앞마당입구"),
)
_GEYSER_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("가스", "geyser"),
)
_EXPANSION_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("앞마당", "내추럴", "멀티", "확장", "natural", "expansion"),
)
_GENERIC_EXPANSION_CAMERA_BASE_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("멀티", "확장", "expansion"),
)
_THIRD_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "third",
        "third base",
        "3rd base",
        "삼룡이",
        "3멀티",
        "세번째 멀티",
        "세 번째 멀티",
        "셋째 멀티",
    ),
)
_SCOUT_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "정찰 위치",
        "정찰위치",
        "정찰 지점",
        "정찰지점",
        "정찰한 곳",
        "scout location",
        "scouted location",
        "last scout location",
    ),
)
_LAST_SEEN_ENEMY_AREA_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "마지막 적 위치",
        "마지막적위치",
        "마지막으로 본 적",
        "최근 본 적 위치",
        "최근적위치",
        "last seen enemy",
        "last seen enemy area",
        "enemy last seen",
        "last enemy position",
    ),
)
_FAR_FROM_MAIN_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "본진과떨어",
        "본진에서떨어",
        "본진에서멀게",
        "본진에서멀리",
        "본진밖",
        "본진밖에",
        "본진바깥",
        "본진바깥에",
        "본진외곽",
        "본진외곽에",
        "본진밖쪽",
        "본진바깥쪽",
        "먼곳",
        "먼 곳",
        "멀리",
    ),
)
_DISTANCE_ONLY_PLACEMENT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "더멀게",
        "더멀리",
        "멀게",
        "멀리",
        "먼곳",
        "먼곳에",
        "먼데",
        "떨어진곳",
        "떨어진곳에",
    ),
)
_FARTHER_COMPARATIVE_PLACEMENT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "더멀게",
        "더멀리",
        "더먼곳",
        "더먼곳에",
        "더떨어",
        "더떨어진",
        "보다멀게",
        "보다멀리",
    ),
)
_UNANCHORED_RELATIVE_PLACEMENT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "근처",
        "근처에",
        "가까이",
        "쪽으로",
        "쪽에",
        "쪽",
        "방향으로",
        "방향에",
        "떨어지게",
        "떨어져",
        "떨어뜨려",
        "뒤쪽",
        "앞쪽",
        "위쪽",
        "아래쪽",
        "왼쪽",
        "오른쪽",
        "near",
        "toward",
        "away",
    ),
)
_BARE_DISTANCE_MODIFIER_PLACEMENT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "더멀게",
        "더멀리",
        "좀더멀게",
        "좀더멀리",
        "멀게",
        "멀리",
        "더먼곳",
        "더먼곳에",
        "먼곳",
        "먼곳에",
    ),
)
_PLACEMENT_ANCHOR_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "본진",
        "앞마당",
        "내추럴",
        "입구",
        "언덕",
        "램프",
        "가스",
        "미네랄",
        "광물",
        "멀티",
        "확장",
        "초크",
        "사령부",
        "커맨드",
        "main",
        "base",
        "natural",
        "ramp",
        "geyser",
        "mineral",
        "expansion",
        "choke",
    ),
)
_PLACEMENT_DIRECTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "쪽으로",
        "쪽에",
        "쪽",
        "앞마당으로",
        "내추럴로",
        "입구로",
        "램프로",
        "미네랄로",
        "가스로",
        "방향",
        "향해",
        "향해서",
        "뒤",
        "뒤에",
        "뒤쪽",
        "앞쪽",
        "위",
        "위쪽",
        "아래",
        "아래쪽",
        "왼쪽",
        "오른쪽",
        "근처",
        "near",
        "toward",
        "towards",
        "behind",
        "back",
        "left",
        "right",
    ),
)
_DEICTIC_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "저기",
        "여기",
        "거기",
        "저곳",
        "이곳",
        "그곳",
        "저쪽",
        "이쪽",
        "그쪽",
        "here",
        "there",
    ),
)
_BUILD_STRUCTURE_KOREAN_OBJECT_LABELS: Final[dict[StructureName | None, str]] = {
    "Supply Depot": "보급고를",
    "Barracks": "배럭을",
    "Refinery": "정제소를",
    "Bunker": "벙커를",
    "Command Center": "커맨드 센터를",
    None: "건물을",
}
_PLACEMENT_ANCHOR_LABEL_PATTERNS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("본진", _normalize_patterns(("본진", "main", "base"))),
    ("앞마당", _normalize_patterns(("앞마당", "내추럴", "natural"))),
    ("입구", _normalize_patterns(("입구", "초크", "choke"))),
    ("램프", _normalize_patterns(("램프", "언덕", "ramp"))),
    ("가스", _normalize_patterns(("가스", "geyser"))),
    ("확장", _normalize_patterns(("멀티", "확장", "expansion"))),
)
_MAIN_FAR_RELATIVE_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "본진에서멀게",
        "본진에서멀리",
        "본진과떨어",
        "본진에서떨어",
        "본진보다멀게",
        "본진보다멀리",
        "본진보다더멀게",
        "본진보다더멀리",
        "본진밖",
        "본진밖에",
        "본진바깥",
        "본진바깥에",
        "본진외곽",
        "본진외곽에",
        "본진밖쪽",
        "본진바깥쪽",
    ),
)
_MAIN_ANCHOR_RELATIVE_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "본진에서",
        "본진보다",
        "본진과",
        "본진밖",
        "본진바깥",
        "본진외곽",
    ),
)
_MINERAL_AWAY_RELATIVE_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "미네랄에서떨어",
        "미네랄라인에서떨어",
        "미네랄에서멀게",
        "미네랄에서멀리",
        "광물에서떨어",
        "mineralaway",
        "awayfromminerals",
    ),
)
_RAMP_TOWARD_RELATIVE_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "입구쪽으로",
        "입구쪽에",
        "입구쪽",
        "램프쪽으로",
        "램프쪽에",
        "언덕쪽으로",
        "choketoward",
        "towardramp",
    ),
)
_NATURAL_NEAR_RELATIVE_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "앞마당근처",
        "앞마당근처에",
        "앞마당가까이",
        "내추럴근처",
        "naturalnear",
        "nearnatural",
    ),
)
_NATURAL_DIRECTION_RELATIVE_LOCATION_PATTERNS: Final[tuple[str, ...]] = (
    _normalize_patterns(("앞마당으로", "내추럴로", "natural"))
)
_MAIN_ENTRANCE_DIRECT_PLACEMENT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "본진입구",
        "우리입구",
        "우리본진입구",
        "아군입구",
        "내입구",
        "입구에",
        "입구앞",
        "입구근처",
        "입구막",
        "입구방어",
        "입구수비",
    ),
)
_MAIN_BASE_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("본진", "main base", "base"),
)
_MARINE_UNIT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("마린", "해병", "marine", "marines"),
)
_ARMY_TRAINING_VERB_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
_SCOUT_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("정찰", "확인", "체크", "봐", "보러", "살펴", "scout", "check", "send"),
)
_CAMERA_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "카메라",
        "화면",
        "시점",
        "보여줘",
        "보여",
        "center",
        "camera",
        "view",
    ),
)
_CAMERA_GENERIC_BASE_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "사령부",
        "커맨드센터",
        "커맨드",
        "commandcenter",
        "command centre",
        "cc",
        "기지",
        "base",
    )
)
_BUILD_NEAR_BASE_RELATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "근처",
        "주변",
        "옆",
        "근방",
        "가까이",
        "near",
        "around",
        "nextto",
        "next to",
    )
)
_EXPLICIT_CAMERA_BASE_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("본진", "main", "main base")
)
_ENEMY_CAMERA_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("적", "상대", "enemy")
)
_PLAIN_SCOUT_ORDER_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("정찰", "scout"),
)
_SCOUT_TARGET_CONTEXT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
_SCOUT_MINERAL_TARGET_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("미네랄", "mineral", "mineral line"),
)
_SCOUT_NATURAL_TARGET_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("앞마당", "내추럴", "natural"),
)
_SCOUT_MAIN_TARGET_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("본진", "main", "main base"),
)
_SCOUT_FRONT_TARGET_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("입구", "front", "초크"),
)
_RAMP_DEFENSE_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("입구", "램프", "언덕", "ramp", "choke"),
)
_SCOUT_EXCLUSIVE_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("정찰", "체크", "확인", "살펴", "scout", "check"),
)
"""Scout-only verbs that must keep ramp commands out of the defend family.

These deliberately exclude the shared movement verbs (보내, send, 봐) so
``마린 6기 입구로 보내`` still resolves to ramp defense while ``적 입구 정찰
보내`` resolves to a scout order instead of bouncing as ambiguous.
"""
_DEFENSE_EXCLUSIVE_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
        "rally",
    ),
)
"""Defense-only verbs that keep explicitly mixed commands ambiguous.

``정찰 보내고 입구 막아`` names both a scout verb and a defense verb, so it
must stay a multi-intent clarification (and split per part in the live
pipeline) instead of silently resolving to a single scout order.
"""
_RAMP_DEFENSE_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "막아",
        "막고",
        "막게",
        "수비",
        "방어",
        "지켜",
        "세워",
        "홀드",
        "보내",
        "이동",
        "가서",
        "가라",
        "hold",
        "defend",
        "guard",
        "send",
        "move",
        "rally",
    ),
)
_ARMY_SUBJECT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("병력", "마린", "해병", "marine", "marines", "army"),
)
_RETREAT_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
_ENEMY_EXPANSION_TARGET_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
)
_PRESSURE_EXPANSION_WORD_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("앞마당", "내추럴", "멀티", "확장", "natural", "expansion"),
)
_ENEMY_OWNER_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("적", "상대", "enemy"),
)
_PRESSURE_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
_MINERAL_LINE_TARGET_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
)
_WORKER_WORD_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("일꾼", "worker", "workers"),
)
_GATHER_LINE_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("채취", "캐", "라인", "line"),
)
_HARASS_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
_REPAIR_TARGET_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
_REPAIR_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("수리", "고쳐", "고치", "복구", "repair", "fix", "restore"),
)
_REPAIR_BUNKER_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("벙커", "bunker"),
)
_REPAIR_BARRACKS_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("배럭", "병영", "barracks"),
)
_REPAIR_DEPOT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("서플", "보급고", "depot"),
)
_REPAIR_COMMAND_CENTER_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("커맨드센터", "커맨드", "commandcenter", "command center"),
)
_EXPAND_LOCATION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("앞마당", "내추럴", "멀티", "확장", "natural", "expansion", "expo"),
)
_EXPAND_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
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
_STATE_SUBJECT_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("상태", "상황", "현황", "브리핑", "요약", "status", "state", "summary"),
)
_SUMMARY_ACTION_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    (
        "알려",
        "보여",
        "확인",
        "요약",
        "브리핑",
        "정리",
        "보고",
        "show",
        "check",
        "summarize",
        "brief",
        "report",
    ),
)
_CURRENT_ACTIVITY_PATTERNS: Final[tuple[str, ...]] = _normalize_patterns(
    ("뭐하고", "어떻게되고", "무슨일", "whatsgoingon", "whatshappening"),
)
_COUNT_KEYWORDS: Final[tuple[tuple[int, tuple[str, ...]], ...]] = (
    # English aliases stop at the single digits below: short English number
    # words are unsafe substrings ("ten" occurs inside "scout enemy"), and
    # English digit counts are covered exactly by _DIGIT_COUNT_PATTERN.
    (12, _normalize_patterns(("열두기", "열두마리", "열둘"))),
    (11, _normalize_patterns(("열한기", "열한마리", "열하나"))),
    (10, _normalize_patterns(("열기", "열마리"))),
    (9, _normalize_patterns(("아홉기", "아홉마리", "아홉"))),
    (8, _normalize_patterns(("여덟기", "8기", "여덟", "eight"))),
    (7, _normalize_patterns(("일곱기", "7기", "일곱", "seven"))),
    (6, _normalize_patterns(("여섯기", "6기", "여섯", "six"))),
    (5, _normalize_patterns(("다섯기", "5기", "다섯", "five"))),
    (4, _normalize_patterns(("네기", "4기", "넷", "네마리", "four"))),
    (
        3,
        _normalize_patterns(
            ("세기", "3기", "셋", "세마리", "여러기", "여러개", "여러마리", "three"),
        ),
    ),
    (2, _normalize_patterns(("두기", "2기", "둘", "두마리", "two"))),
    (1, _normalize_patterns(("한기", "1기", "하나", "한마리", "하나씩", "one"))),
)
"""Korean and English count aliases checked from the largest count down.

Native compound numerals (열두기 = 12) are listed before their substrings
(두기 = 2) so descending iteration never matches a fragment of a larger
numeral. Digit counts are parsed separately and exactly by
``_DIGIT_COUNT_PATTERN``, never by substring.
"""

_DIGIT_COUNT_PATTERN: Final[re.Pattern[str]] = re.compile(r"(\d+)(?:기|마리|명)")
"""Exact digit-count parser on normalized text: ``12기`` is 12, never 2.

Substring matching of patterns like ``2기`` against ``12기`` silently
executed the wrong count, which the conservative house rules forbid.
"""

GATHER_RESOURCE_PRIORITY_KEYWORDS: Final[dict[Priority, tuple[str, ...]]] = {
    "high": _normalize_patterns(("빨리", "급해", "부족", "당장", "quick", "urgent", "need")),
}
TRAIN_UNIT_PRIORITY_KEYWORDS: Final[dict[Priority, tuple[str, ...]]] = {
    "high": _normalize_patterns(
        ("방어", "압박", "급해", "빨리", "막아", "defense", "pressure", "urgent", "quick"),
    ),
}
SEND_SCOUT_PRIORITY_KEYWORDS: Final[dict[Priority, tuple[str, ...]]] = {
    "high": _normalize_patterns(
        ("초반", "빨리", "러시", "압박", "몰래", "확인", "체크", "rush", "quick", "early"),
    ),
}
DEFEND_RAMP_PRIORITY_KEYWORDS: Final[dict[Priority, tuple[str, ...]]] = {
    "urgent": _normalize_patterns(
        ("러시", "찌르", "압박", "급해", "빨리", "막아", "rush", "pressure", "urgent", "quick"),
    ),
}
RETREAT_ARMY_PRIORITY_KEYWORDS: Final[dict[Priority, tuple[str, ...]]] = {
    "urgent": _normalize_patterns(
        ("압박", "실패", "위험", "빨리", "급해", "살려", "후퇴", "retreat", "danger", "urgent"),
    ),
}
PRESSURE_ENEMY_EXPANSION_PRIORITY_KEYWORDS: Final[dict[Priority, tuple[str, ...]]] = {
    "high": _normalize_patterns(
        ("빨리", "강하게", "지금", "압박", "찌르", "pressure", "attack", "hit", "now"),
    ),
}
HARASS_MINERAL_LINE_PRIORITY_KEYWORDS: Final[dict[Priority, tuple[str, ...]]] = {
    "high": _normalize_patterns(
        ("빨리", "지금", "강하게", "견제", "흔들", "공격", "harass", "attack", "hit", "now"),
    ),
}
REPAIR_PRIORITY_KEYWORDS: Final[dict[Priority, tuple[str, ...]]] = {
    "urgent": _normalize_patterns(
        ("빨리", "당장", "불타", "위험", "urgent", "quick", "burning"),
    ),
}
EXPAND_PRIORITY_KEYWORDS: Final[dict[Priority, tuple[str, ...]]] = {
    "normal": _normalize_patterns(("안전", "여유", "safe", "when safe")),
    "high": _normalize_patterns(("빨리", "지금", "당장", "quick", "now")),
}


def detect_priority(
    command_text: str,
    keyword_map: Mapping[Priority, tuple[str, ...]],
    default: Priority,
) -> Priority:
    """Return the first priority whose keyword table matches the command text.

    ``keyword_map`` entries are checked in insertion order and must contain
    pre-normalized patterns (see ``_normalize_patterns``); ``default`` is
    returned when no table matches.
    """

    normalized_command = _normalize_command_text(command_text)
    for priority, keywords in keyword_map.items():
        if _contains_any_pattern(normalized_command, keywords):
            return priority
    return default


def _looks_like_keep_worker_production(normalized_command: str) -> bool:
    return (
        _contains_any_pattern(normalized_command, _WORKER_SUBJECT_PATTERNS)
        and _contains_any_pattern(normalized_command, _PRODUCTION_CONTINUITY_PATTERNS)
        and _contains_any_pattern(normalized_command, _WORKER_TRAINING_VERB_PATTERNS)
    )


def _looks_like_one_shot_worker_training(normalized_command: str) -> bool:
    """Worker subject plus a training verb, without continuity words.

    Structure names and marine words bow out so build commands phrased with a
    worker ("SCV로 벙커 만들어") and army training stay in their own families.
    """

    return (
        _contains_any_pattern(normalized_command, _WORKER_SUBJECT_PATTERNS)
        and _contains_any_pattern(normalized_command, _WORKER_TRAINING_VERB_PATTERNS)
        and not _contains_any_pattern(normalized_command, _MARINE_UNIT_PATTERNS)
        and _detect_structure_name(normalized_command) is None
    )


def _looks_like_gather_resource(normalized_command: str) -> bool:
    if _contains_any_pattern(normalized_command, _ENEMY_OWNER_PATTERNS):
        return False
    has_worker_subject = _contains_any_pattern(
        normalized_command,
        _WORKER_SUBJECT_PATTERNS,
    )
    is_compact_resource_order = _contains_any_pattern(
        normalized_command,
        _GENERIC_RESOURCE_PATTERNS,
    )
    is_worker_send_without_target = (
        has_worker_subject
        and _detect_resource_name(normalized_command) is None
        and _contains_any_pattern(normalized_command, _GATHER_ACTION_PATTERNS)
        and not _contains_any_pattern(normalized_command, _SCOUT_TARGET_CONTEXT_PATTERNS)
        and not _detect_structure_name(normalized_command)
    )
    if is_worker_send_without_target:
        return True
    return (
        _detect_resource_name(normalized_command) is not None
        and _contains_any_pattern(normalized_command, _GATHER_ACTION_PATTERNS)
        and (has_worker_subject or is_compact_resource_order)
    )


def _detect_resource_name(normalized_command: str) -> ResourceName | None:
    if _contains_any_pattern(normalized_command, _GAS_RESOURCE_PATTERNS):
        return "gas"
    if _contains_any_pattern(normalized_command, _MINERAL_RESOURCE_PATTERNS):
        return "minerals"
    if (
        _contains_any_pattern(normalized_command, _WORKER_SUBJECT_PATTERNS)
        and _contains_any_pattern(normalized_command, _GATHER_ACTION_PATTERNS)
    ):
        return "minerals"
    return None


def _detect_worker_base(normalized_command: str) -> str:
    base_selection = parse_korean_base_selection(normalized_command)
    if base_selection is not None:
        return base_selection.selector
    if _contains_any_pattern(normalized_command, _NATURAL_BASE_PATTERNS):
        return "natural"
    return "main"


def _looks_like_prevent_supply_block(normalized_command: str) -> bool:
    has_supply_subject = _contains_any_pattern(
        normalized_command,
        _SUPPLY_SUBJECT_PATTERNS,
    )
    has_supply_action = _contains_any_pattern(
        normalized_command,
        _SUPPLY_PRESSURE_PATTERNS,
    )
    return has_supply_subject and has_supply_action


def _build_structure_target_from_command(
    normalized_command: str,
) -> tuple[StructureName, str] | None:
    structure = _detect_structure_name(normalized_command)
    if structure is None or not _has_build_structure_verb(normalized_command):
        return None
    return structure, _detect_structure_location(normalized_command, structure)


def _detect_structure_name(normalized_command: str) -> StructureName | None:
    if _contains_any_pattern(normalized_command, _REFINERY_COMPOUND_PATTERNS):
        return "Refinery"
    for structure, aliases in _STRUCTURE_NAME_ALIASES:
        if _contains_any_pattern(normalized_command, aliases):
            return structure
    return None


def _has_build_structure_verb(normalized_command: str) -> bool:
    return _contains_any_pattern(normalized_command, _BUILD_STRUCTURE_VERB_PATTERNS)


def _detect_structure_location(
    normalized_command: str,
    structure: StructureName,
) -> str:
    base_selection = parse_korean_base_selection(normalized_command)
    if _contains_any_pattern(
        normalized_command, _NATURAL_LOCATION_PATTERNS
    ) and _contains_any_pattern(normalized_command, _CHOKE_HINT_PATTERNS):
        return "natural choke"
    if _contains_any_pattern(normalized_command, _RAMP_LOCATION_PATTERNS):
        return "main ramp"
    if _contains_any_pattern(normalized_command, _GEYSER_LOCATION_PATTERNS):
        return "main geyser"
    if _contains_any_pattern(normalized_command, _EXPANSION_LOCATION_PATTERNS):
        return "natural expansion"
    if _contains_any_pattern(normalized_command, _FAR_FROM_MAIN_LOCATION_PATTERNS):
        return "natural expansion"
    if base_selection is not None:
        return base_selection.location
    if _contains_any_pattern(normalized_command, _MAIN_BASE_LOCATION_PATTERNS):
        return "main base"
    return BUILD_STRUCTURE_DEFAULT_LOCATIONS[structure]


def _main_entrance_placement_policy_for(
    normalized_command: str,
    location: str,
) -> dict[str, object]:
    """Return the explicit self-ramp policy for Korean entrance build phrases."""

    if location != "main ramp":
        return {}
    if _contains_any_pattern(normalized_command, _ENEMY_OWNER_PATTERNS):
        return {}
    if (
        _contains_any_pattern(normalized_command, _NATURAL_LOCATION_PATTERNS)
        and _contains_any_pattern(normalized_command, _CHOKE_HINT_PATTERNS)
    ):
        return {}
    if not _contains_any_pattern(
        normalized_command,
        _MAIN_ENTRANCE_DIRECT_PLACEMENT_PATTERNS,
    ):
        return {}
    return dict(MAIN_ENTRANCE_PLACEMENT_POLICY)


def _natural_expansion_placement_policy_for(
    normalized_command: str,
    location: str,
) -> dict[str, object]:
    """Return the explicit self-natural policy for direct natural build phrases."""

    if location != "natural expansion":
        return {}
    if _contains_any_pattern(normalized_command, _ENEMY_OWNER_PATTERNS):
        return {}
    if not _contains_any_pattern(normalized_command, _EXPANSION_LOCATION_PATTERNS):
        return {}
    return dict(NATURAL_EXPANSION_PLACEMENT_POLICY)


def _main_geyser_placement_policy_for(
    normalized_command: str,
    location: str,
) -> dict[str, object]:
    """Return the explicit self-geyser policy for refinery build phrases."""

    if location != "main geyser":
        return {}
    if _contains_any_pattern(normalized_command, _ENEMY_OWNER_PATTERNS):
        return {}
    if not _contains_any_pattern(normalized_command, _GEYSER_LOCATION_PATTERNS):
        return {}
    return dict(MAIN_GEYSER_PLACEMENT_POLICY)


def is_distance_only_build_placement(
    command_text: str,
    payload: IntentPayload | None = None,
) -> bool:
    """Return True when a build request gives distance but no placement anchor."""

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        return False
    if payload is not None and _payload_intent_name(payload) != "BUILD_STRUCTURE":
        return False
    if not _has_distance_modifier_build_context(normalized_command, payload):
        return False
    if not _contains_any_pattern(normalized_command, _DISTANCE_ONLY_PLACEMENT_PATTERNS):
        return False
    return not _contains_any_pattern(normalized_command, _PLACEMENT_ANCHOR_PATTERNS)


def _has_distance_modifier_build_context(
    normalized_command: str,
    payload: IntentPayload | None,
) -> bool:
    if _has_build_structure_verb(normalized_command):
        return True
    if payload is not None:
        return True
    if _detect_structure_name(normalized_command) is not None:
        return True
    return _is_bare_distance_modifier_placement(normalized_command)


def _is_bare_distance_modifier_placement(normalized_command: str) -> bool:
    """Return True for modifier-only Korean placement phrases like ``더 멀게``."""

    if normalized_command not in _BARE_DISTANCE_MODIFIER_PLACEMENT_PATTERNS:
        return False
    if _contains_any_pattern(normalized_command, _PLACEMENT_ANCHOR_PATTERNS):
        return False
    return not _contains_any_pattern(normalized_command, _PLACEMENT_DIRECTION_PATTERNS)


def is_farther_build_placement_missing_direction(
    command_text: str,
    payload: IntentPayload | None = None,
) -> bool:
    """Return True when a farther build request has an anchor but no direction."""

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        return False
    if payload is not None and _payload_intent_name(payload) != "BUILD_STRUCTURE":
        return False
    if not _has_build_structure_verb(normalized_command):
        return False
    if not _contains_any_pattern(
        normalized_command,
        _FARTHER_COMPARATIVE_PLACEMENT_PATTERNS,
    ):
        return False
    if not _contains_any_pattern(normalized_command, _PLACEMENT_ANCHOR_PATTERNS):
        return False
    return not _contains_any_pattern(normalized_command, _PLACEMENT_DIRECTION_PATTERNS)


def is_unanchored_relative_build_placement(
    command_text: str,
    payload: IntentPayload | None = None,
) -> bool:
    """Return True when a build request has a relative modifier but no anchor."""

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        return False
    if payload is not None and _payload_intent_name(payload) != "BUILD_STRUCTURE":
        return False
    if _contains_any_pattern(normalized_command, _DEICTIC_LOCATION_PATTERNS):
        return False
    if not _has_relative_modifier_build_context(normalized_command, payload):
        return False
    if parse_korean_base_selection(normalized_command) is not None:
        return False
    if _contains_any_pattern(normalized_command, _CAMERA_GENERIC_BASE_PATTERNS):
        return False
    if not _contains_any_pattern(
        normalized_command,
        _UNANCHORED_RELATIVE_PLACEMENT_PATTERNS,
    ):
        return False
    if _payload_has_resolved_placement_anchor(payload):
        return False
    return not _contains_any_pattern(normalized_command, _PLACEMENT_ANCHOR_PATTERNS)


def is_unanchored_relative_action_target(
    command_text: str,
    payload: IntentPayload | None = None,
) -> bool:
    """Return True when a mutating payload guessed an unanchored relative target."""

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        return False
    intent_name = _payload_intent_name(payload)
    if not intent_name or intent_name == "SUMMARIZE_STATE":
        return False
    if intent_name == "BUILD_STRUCTURE":
        return is_unanchored_relative_build_placement(command_text, payload)
    if _contains_any_pattern(normalized_command, _DEICTIC_LOCATION_PATTERNS):
        return False
    if not _contains_any_pattern(
        normalized_command,
        _UNANCHORED_RELATIVE_PLACEMENT_PATTERNS,
    ):
        return False
    if _contains_any_pattern(normalized_command, _PLACEMENT_ANCHOR_PATTERNS):
        return False
    return _payload_has_target_like_field(payload)


def _payload_has_target_like_field(payload: IntentPayload | None) -> bool:
    if payload is None:
        return False
    return any(
        type(_payload_field(payload, field_name)) is str
        and bool(str(_payload_field(payload, field_name)).strip())
        for field_name in ("target", "location", "unit_group", "resource", "base")
    )


def _payload_intent_name(payload: IntentPayload | Mapping[str, object] | None) -> str:
    return str(_payload_field(payload, "intent") or "")


def _payload_field(
    payload: IntentPayload | Mapping[str, object] | None,
    field_name: str,
) -> object:
    if payload is None:
        return None
    if isinstance(payload, Mapping):
        return payload.get(field_name)
    return getattr(payload, field_name, None)


def _payload_has_resolved_placement_anchor(payload: IntentPayload | None) -> bool:
    if payload is None or _payload_intent_name(payload) != "BUILD_STRUCTURE":
        return False
    placement_policy = _payload_field(payload, "placement_policy")
    if not isinstance(placement_policy, Mapping):
        return False
    if isinstance(placement_policy.get("base_selection"), Mapping):
        return True
    return any(
        type(placement_policy.get(key)) is str and placement_policy.get(key).strip()
        for key in ("anchor_target", "anchor", "target")
    )


def _has_relative_modifier_build_context(
    normalized_command: str,
    payload: IntentPayload | None,
) -> bool:
    if payload is not None:
        return True
    return _detect_structure_name(normalized_command) is not None


def is_deictic_build_placement_missing_semantic_target(
    command_text: str,
    payload: IntentPayload | None = None,
) -> bool:
    """Return True when a build request points to here/there instead of a target."""

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        return False
    if payload is not None and _payload_intent_name(payload) != "BUILD_STRUCTURE":
        return False
    if not _has_build_structure_verb(normalized_command):
        return False
    if not _contains_any_pattern(normalized_command, _DEICTIC_LOCATION_PATTERNS):
        return False
    return not _contains_any_pattern(normalized_command, _PLACEMENT_ANCHOR_PATTERNS)


def _looks_like_train_unit(normalized_command: str) -> bool:
    return _contains_any_pattern(
        normalized_command, _MARINE_UNIT_PATTERNS
    ) and _contains_any_pattern(normalized_command, _ARMY_TRAINING_VERB_PATTERNS)


def _looks_like_send_scout(normalized_command: str) -> bool:
    has_scout_action = _contains_any_pattern(normalized_command, _SCOUT_ACTION_PATTERNS)
    has_target_context = _contains_any_pattern(
        normalized_command,
        _SCOUT_TARGET_CONTEXT_PATTERNS,
    )
    is_plain_scout_order = _contains_any_pattern(
        normalized_command,
        _PLAIN_SCOUT_ORDER_PATTERNS,
    )
    return has_scout_action and (has_target_context or is_plain_scout_order)


def _detect_send_scout_target(normalized_command: str) -> str:
    if _contains_any_pattern(normalized_command, _SCOUT_MINERAL_TARGET_PATTERNS):
        return "enemy mineral line"
    if _contains_any_pattern(normalized_command, _SCOUT_NATURAL_TARGET_PATTERNS):
        return "enemy natural"
    if _contains_any_pattern(normalized_command, _SCOUT_MAIN_TARGET_PATTERNS):
        return "enemy main"
    if _contains_any_pattern(normalized_command, _SCOUT_FRONT_TARGET_PATTERNS):
        return "enemy front"
    return SEND_SCOUT_DEFAULT_TARGET


def _looks_like_move_camera(normalized_command: str) -> bool:
    if not _contains_any_pattern(normalized_command, _CAMERA_ACTION_PATTERNS):
        return False
    if _contains_any_pattern(normalized_command, _SCOUT_LOCATION_PATTERNS):
        return True
    if _contains_any_pattern(normalized_command, _SCOUT_EXCLUSIVE_ACTION_PATTERNS):
        return False
    return any(
        _contains_any_pattern(normalized_command, patterns)
        for patterns in (
            _MAIN_BASE_LOCATION_PATTERNS,
            _NATURAL_LOCATION_PATTERNS,
            _THIRD_LOCATION_PATTERNS,
            _CHOKE_LOCATION_PATTERNS,
            _RAMP_LOCATION_PATTERNS,
            _GEYSER_LOCATION_PATTERNS,
            _EXPANSION_LOCATION_PATTERNS,
            _SCOUT_LOCATION_PATTERNS,
            _LAST_SEEN_ENEMY_AREA_PATTERNS,
            _CAMERA_GENERIC_BASE_PATTERNS,
            _ENEMY_CAMERA_PATTERNS,
        )
    ) or _has_explicit_base_selection(normalized_command)


def is_ambiguous_camera_base_target(command_text: str) -> bool:
    normalized_command = _normalize_command_text(command_text)
    return _is_ambiguous_camera_base_normalized(normalized_command)


def is_ambiguous_build_base_target(
    command_text: str,
    payload: IntentPayload | None = None,
) -> bool:
    """Return True for generic build-near townhall/base anchors."""

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        return False
    if payload is not None and _payload_intent_name(payload) != "BUILD_STRUCTURE":
        return False
    if not _has_build_structure_verb(normalized_command):
        return False
    if not _contains_any_pattern(normalized_command, _BUILD_NEAR_BASE_RELATION_PATTERNS):
        return False
    if not _contains_any_pattern(normalized_command, _CAMERA_GENERIC_BASE_PATTERNS):
        return False
    explicit_base = (
        _contains_any_pattern(normalized_command, _EXPLICIT_CAMERA_BASE_PATTERNS)
        or _contains_any_pattern(normalized_command, _NATURAL_LOCATION_PATTERNS)
        or _contains_any_pattern(normalized_command, _EXPANSION_LOCATION_PATTERNS)
        or _contains_any_pattern(normalized_command, _RAMP_LOCATION_PATTERNS)
        or _contains_any_pattern(normalized_command, _GEYSER_LOCATION_PATTERNS)
        or _contains_any_pattern(normalized_command, _ENEMY_OWNER_PATTERNS)
        or _has_explicit_base_selection(normalized_command)
    )
    return not explicit_base


def is_build_request_missing_structure(command_text: str) -> bool:
    """Return True when build text gives action/location but omits structure."""

    normalized_command = _normalize_command_text(command_text)
    if not normalized_command:
        return False
    if not _has_build_structure_verb(normalized_command):
        return False
    if _detect_structure_name(normalized_command) is not None:
        return False
    return (
        _contains_any_pattern(normalized_command, _PLACEMENT_ANCHOR_PATTERNS)
        or _contains_any_pattern(normalized_command, _CAMERA_GENERIC_BASE_PATTERNS)
        or _contains_any_pattern(normalized_command, _BUILD_NEAR_BASE_RELATION_PATTERNS)
    )


def _is_ambiguous_camera_base_normalized(normalized_command: str) -> bool:
    if not _looks_like_move_camera(normalized_command):
        return False
    if _contains_any_pattern(normalized_command, _ENEMY_CAMERA_PATTERNS):
        return False
    if _is_generic_expansion_camera_base_reference(normalized_command):
        return True
    if not _contains_any_pattern(normalized_command, _CAMERA_GENERIC_BASE_PATTERNS):
        return False
    explicit_base = (
        _contains_any_pattern(normalized_command, _EXPLICIT_CAMERA_BASE_PATTERNS)
        or _contains_any_pattern(normalized_command, _NATURAL_LOCATION_PATTERNS)
        or _contains_any_pattern(normalized_command, _EXPANSION_LOCATION_PATTERNS)
        or _contains_any_pattern(normalized_command, _RAMP_LOCATION_PATTERNS)
        or _has_explicit_base_selection(normalized_command)
    )
    return not explicit_base


def _is_generic_expansion_camera_base_reference(normalized_command: str) -> bool:
    """Return True for bare expansion words that can point at several bases."""

    if not _contains_any_pattern(
        normalized_command,
        _GENERIC_EXPANSION_CAMERA_BASE_PATTERNS,
    ):
        return False
    return not any(
        _contains_any_pattern(normalized_command, patterns)
        for patterns in (
            _NATURAL_LOCATION_PATTERNS,
            _THIRD_LOCATION_PATTERNS,
            _RAMP_LOCATION_PATTERNS,
        )
    ) and not any(
        token in normalized_command
        for token in (
            "새로",
            "최근",
            "막지은",
            "가장최근",
            "추가",
            "newest",
            "latest",
            "additional",
        )
    )


def _detect_camera_target(normalized_command: str) -> str:
    if _contains_any_pattern(normalized_command, _LAST_SEEN_ENEMY_AREA_PATTERNS):
        return "last seen enemy area"
    if _contains_any_pattern(normalized_command, _SCOUT_LOCATION_PATTERNS):
        return "scout location"
    if _contains_any_pattern(normalized_command, _ENEMY_CAMERA_PATTERNS):
        if _contains_any_pattern(normalized_command, _THIRD_LOCATION_PATTERNS):
            return "enemy third"
        if _contains_any_pattern(normalized_command, _NATURAL_LOCATION_PATTERNS):
            return "enemy natural"
        if _contains_any_pattern(normalized_command, _CHOKE_LOCATION_PATTERNS):
            return "enemy choke"
        if _contains_any_pattern(normalized_command, _RAMP_ONLY_LOCATION_PATTERNS):
            return "enemy ramp"
        if _contains_any_pattern(normalized_command, _RAMP_LOCATION_PATTERNS):
            return "enemy front"
        return "enemy main"
    if _contains_any_pattern(normalized_command, _GEYSER_LOCATION_PATTERNS):
        return "main geyser"
    if _contains_any_pattern(normalized_command, _CHOKE_LOCATION_PATTERNS):
        return "natural choke"
    if _contains_any_pattern(normalized_command, _RAMP_LOCATION_PATTERNS):
        return "main ramp"
    if _contains_any_pattern(normalized_command, _THIRD_LOCATION_PATTERNS):
        return "third base"
    if _contains_any_pattern(normalized_command, _NATURAL_LOCATION_PATTERNS):
        return "natural expansion"
    base_selection = parse_korean_base_selection(normalized_command)
    if base_selection is not None:
        return base_selection.location
    return "main base"


def _detect_camera_target_slot(normalized_command: str) -> str:
    """Return an auditable semantic slot for camera target phrases."""

    if _contains_any_pattern(normalized_command, _LAST_SEEN_ENEMY_AREA_PATTERNS):
        return MOVE_CAMERA_LAST_SEEN_ENEMY_AREA_TARGET_SLOT
    if _contains_any_pattern(normalized_command, _SCOUT_LOCATION_PATTERNS):
        return MOVE_CAMERA_SCOUT_LOCATION_TARGET_SLOT
    if _contains_any_pattern(normalized_command, _CHOKE_LOCATION_PATTERNS):
        return MOVE_CAMERA_CHOKE_TARGET_SLOT
    if _contains_any_pattern(normalized_command, _THIRD_LOCATION_PATTERNS):
        return MOVE_CAMERA_THIRD_BASE_TARGET_SLOT
    if _contains_any_pattern(normalized_command, _RAMP_LOCATION_PATTERNS):
        if _contains_any_pattern(normalized_command, _ENEMY_CAMERA_PATTERNS):
            return MOVE_CAMERA_ENEMY_ENTRANCE_TARGET_SLOT
        return MOVE_CAMERA_RAMP_OR_ENTRANCE_TARGET_SLOT
    if (
        not _contains_any_pattern(normalized_command, _ENEMY_CAMERA_PATTERNS)
        and _contains_any_pattern(normalized_command, _NATURAL_LOCATION_PATTERNS)
    ):
        return MOVE_CAMERA_NATURAL_EXPANSION_TARGET_SLOT
    return ""


def _detect_send_scout_unit_group(normalized_command: str) -> str:
    marine_count = _detect_marine_count(normalized_command)
    if marine_count is not None:
        return _format_unit_group(marine_count, "Marine")
    return SEND_SCOUT_DEFAULT_UNIT_GROUP


def _looks_like_defend_ramp(normalized_command: str) -> bool:
    if _contains_any_pattern(
        normalized_command, _CAMERA_ACTION_PATTERNS
    ) and not _contains_any_pattern(
        normalized_command, _DEFENSE_EXCLUSIVE_ACTION_PATTERNS
    ):
        return False
    if _contains_any_pattern(
        normalized_command, _SCOUT_EXCLUSIVE_ACTION_PATTERNS
    ) and not _contains_any_pattern(
        normalized_command, _DEFENSE_EXCLUSIVE_ACTION_PATTERNS
    ):
        # Explicit scout vocabulary wins: "적 입구 정찰 보내" is a scout
        # order, not a ramp defense, even though it names a ramp word.
        # Commands naming both vocabularies stay ambiguous.
        return False
    return _contains_any_pattern(
        normalized_command, _RAMP_DEFENSE_LOCATION_PATTERNS
    ) and _contains_any_pattern(normalized_command, _RAMP_DEFENSE_ACTION_PATTERNS)


def _detect_defend_ramp_unit_group(normalized_command: str) -> str:
    marine_count = _detect_marine_count(normalized_command)
    if marine_count is not None:
        return _format_unit_group(marine_count, "Marine")
    return DEFEND_RAMP_UNIT_GROUP


def _looks_like_retreat_army(normalized_command: str) -> bool:
    return _contains_any_pattern(
        normalized_command, _ARMY_SUBJECT_PATTERNS
    ) and _contains_any_pattern(normalized_command, _RETREAT_ACTION_PATTERNS)


def _detect_retreat_army_unit_group(normalized_command: str) -> str:
    if _contains_any_pattern(normalized_command, _MARINE_UNIT_PATTERNS):
        return "Marines"
    return RETREAT_ARMY_UNIT_GROUP


def _looks_like_pressure_enemy_expansion(normalized_command: str) -> bool:
    has_enemy_expansion_target = _contains_any_pattern(
        normalized_command,
        _ENEMY_EXPANSION_TARGET_PATTERNS,
    ) or (
        _contains_any_pattern(normalized_command, _PRESSURE_EXPANSION_WORD_PATTERNS)
        and _contains_any_pattern(normalized_command, _ENEMY_OWNER_PATTERNS)
    )
    return has_enemy_expansion_target and _contains_any_pattern(
        normalized_command,
        _PRESSURE_ACTION_PATTERNS,
    )


def _detect_pressure_enemy_expansion_unit_group(normalized_command: str) -> str:
    marine_count = _detect_marine_count(normalized_command)
    if marine_count is not None:
        return _format_unit_group(marine_count, "Marine")
    return PRESSURE_ENEMY_EXPANSION_UNIT_GROUP


def _looks_like_harass_mineral_line(normalized_command: str) -> bool:
    has_mineral_line_target = _contains_any_pattern(
        normalized_command,
        _MINERAL_LINE_TARGET_PATTERNS,
    ) or (
        _contains_any_pattern(normalized_command, _WORKER_WORD_PATTERNS)
        and _contains_any_pattern(normalized_command, _GATHER_LINE_PATTERNS)
        and _contains_any_pattern(normalized_command, _ENEMY_OWNER_PATTERNS)
    )
    return has_mineral_line_target and _contains_any_pattern(
        normalized_command,
        _HARASS_ACTION_PATTERNS,
    )


def _detect_harass_mineral_line_unit_group(normalized_command: str) -> str:
    marine_count = _detect_marine_count(normalized_command)
    if marine_count is not None:
        return _format_unit_group(marine_count, "Marine")
    return HARASS_MINERAL_LINE_UNIT_GROUP


def _looks_like_repair(normalized_command: str) -> bool:
    return _contains_any_pattern(
        normalized_command, _REPAIR_TARGET_PATTERNS
    ) and _contains_any_pattern(normalized_command, _REPAIR_ACTION_PATTERNS)


def _detect_repair_target(normalized_command: str) -> str:
    if _contains_any_pattern(normalized_command, _REPAIR_BUNKER_PATTERNS):
        return "front bunker"
    if _contains_any_pattern(normalized_command, _REPAIR_BARRACKS_PATTERNS):
        return "Barracks"
    if _contains_any_pattern(normalized_command, _REPAIR_DEPOT_PATTERNS):
        return "Supply Depot"
    if _contains_any_pattern(normalized_command, _REPAIR_COMMAND_CENTER_PATTERNS):
        return "Command Center"
    return "front bunker"


def _looks_like_expand(normalized_command: str) -> bool:
    if _contains_any_pattern(normalized_command, _ENEMY_OWNER_PATTERNS) and (
        _looks_like_pressure_enemy_expansion(normalized_command)
        or _looks_like_harass_mineral_line(normalized_command)
    ):
        return False

    return _contains_any_pattern(
        normalized_command, _EXPAND_LOCATION_PATTERNS
    ) and _contains_any_pattern(normalized_command, _EXPAND_ACTION_PATTERNS)


def _looks_like_summarize_state(normalized_command: str) -> bool:
    has_state_subject = _contains_any_pattern(
        normalized_command,
        _STATE_SUBJECT_PATTERNS,
    )
    has_summary_action = _contains_any_pattern(
        normalized_command,
        _SUMMARY_ACTION_PATTERNS,
    )
    asks_current_activity = _contains_any_pattern(
        normalized_command,
        _CURRENT_ACTIVITY_PATTERNS,
    )
    return (has_state_subject and has_summary_action) or asks_current_activity


def _detect_count(normalized_command: str, *, default: int) -> int:
    digit_match = _DIGIT_COUNT_PATTERN.search(normalized_command)
    if digit_match is not None:
        return int(digit_match.group(1))
    for count, aliases in _COUNT_KEYWORDS:
        if _contains_any_pattern(normalized_command, aliases):
            return count
    return default


def _detect_marine_count(normalized_command: str) -> int | None:
    if not _contains_any_pattern(normalized_command, _MARINE_UNIT_PATTERNS):
        return None
    return _detect_count(normalized_command, default=0)


def _format_unit_group(count: int, unit_name: str) -> str:
    if count <= 0:
        return f"{unit_name}s"
    if count == 1:
        return f"1 {unit_name}"
    return f"{count} {unit_name}s"


def _gather_resource_payload(normalized_command: str) -> IntentPayload | None:
    """Build the GATHER_RESOURCE payload when the command matches the family."""

    if not _looks_like_gather_resource(normalized_command):
        return None
    resource = _detect_resource_name(normalized_command)
    if resource is None:
        return None
    priority: Priority = (
        "high"
        if resource == "gas"
        else detect_priority(normalized_command, GATHER_RESOURCE_PRIORITY_KEYWORDS, "normal")
    )
    return GatherResourceIntent(
        priority=priority,
        constraints=(GATHER_RESOURCE_CONSTRAINT,),
        resource=resource,
        worker_count=_detect_count(normalized_command, default=3),
        base=_detect_worker_base(normalized_command),
    )


def _keep_worker_production_payload(normalized_command: str) -> IntentPayload | None:
    """Build the TRAIN_WORKER payload when the command matches the family.

    Continuity phrasing ("SCV 계속 찍어") keeps the continuous-production
    constraint; one-shot phrasing ("일꾼 뽑아", "SCV 두 기 찍어") trains the
    requested count exactly once without pretending continuity.
    """

    if _looks_like_keep_worker_production(normalized_command):
        return TrainWorkerIntent(
            priority="normal",
            constraints=(KEEP_WORKER_PRODUCTION_CONSTRAINT,),
            count=1,
        )
    if _looks_like_one_shot_worker_training(normalized_command):
        return TrainWorkerIntent(
            priority="normal",
            constraints=(TRAIN_WORKER_ONESHOT_CONSTRAINT,),
            count=_detect_count(normalized_command, default=1),
        )
    return None


def _prevent_supply_block_payload(normalized_command: str) -> IntentPayload | None:
    """Build the supply-block BUILD_STRUCTURE payload when the family matches."""

    if not _looks_like_prevent_supply_block(normalized_command):
        return None
    return BuildStructureIntent(
        priority="high",
        constraints=(PREVENT_SUPPLY_BLOCK_CONSTRAINT,),
        structure="Supply Depot",
        location=PREVENT_SUPPLY_BLOCK_LOCATION,
    )


def _repair_payload(normalized_command: str) -> IntentPayload | None:
    """Build the REPAIR payload when the command matches the family."""

    if not _looks_like_repair(normalized_command):
        return None
    return RepairIntent(
        priority=detect_priority(normalized_command, REPAIR_PRIORITY_KEYWORDS, "high"),
        constraints=(REPAIR_CONSTRAINT,),
        target=_detect_repair_target(normalized_command),
        worker_count=_detect_count(normalized_command, default=1),
    )


def _build_structure_payload(normalized_command: str) -> IntentPayload | None:
    """Build the BUILD_STRUCTURE payload when the command matches the family."""

    build_structure_target = _build_structure_target_from_command(normalized_command)
    if build_structure_target is None:
        return None
    structure, location = build_structure_target
    priority = "high" if structure in ("Refinery", "Bunker") else "normal"
    relative_location = parse_korean_relative_location_phrase(normalized_command)
    base_selection = parse_korean_base_selection(normalized_command)
    placement_policy: dict[str, object] = {}
    if relative_location is not None:
        placement_policy.update(relative_location.to_dict())
    if not placement_policy:
        placement_policy.update(
            _main_entrance_placement_policy_for(normalized_command, location)
        )
    if not placement_policy:
        placement_policy.update(
            _natural_expansion_placement_policy_for(normalized_command, location)
        )
    if not placement_policy:
        placement_policy.update(
            _main_geyser_placement_policy_for(normalized_command, location)
        )
    placement_policy.update(
        _explicit_base_selection_placement_policy(normalized_command, base_selection)
    )
    return BuildStructureIntent(
        priority=priority,
        constraints=(BUILD_STRUCTURE_CONSTRAINT,),
        structure=structure,
        location=location,
        placement_policy=placement_policy or None,
    )


def _train_unit_payload(normalized_command: str) -> IntentPayload | None:
    """Build the TRAIN_ARMY payload when the command matches the family."""

    if not _looks_like_train_unit(normalized_command):
        return None
    return TrainArmyIntent(
        priority=detect_priority(normalized_command, TRAIN_UNIT_PRIORITY_KEYWORDS, "normal"),
        constraints=(TRAIN_UNIT_CONSTRAINT,),
        unit_type="Marine",
        count=_detect_count(normalized_command, default=1),
    )


def _send_scout_payload(normalized_command: str) -> IntentPayload | None:
    """Build the SCOUT payload when the command matches the family."""

    if not _looks_like_send_scout(normalized_command):
        return None
    return ScoutIntent(
        priority=detect_priority(normalized_command, SEND_SCOUT_PRIORITY_KEYWORDS, "normal"),
        constraints=(SEND_SCOUT_CONSTRAINT,),
        target=_detect_send_scout_target(normalized_command),
        unit_group=_detect_send_scout_unit_group(normalized_command),
    )


def _defend_ramp_payload(normalized_command: str) -> IntentPayload | None:
    """Build the ramp DEFEND payload when the command matches the family."""

    if not _looks_like_defend_ramp(normalized_command):
        return None
    return DefendIntent(
        priority=detect_priority(normalized_command, DEFEND_RAMP_PRIORITY_KEYWORDS, "high"),
        constraints=(DEFEND_RAMP_CONSTRAINT,),
        location=DEFEND_RAMP_LOCATION,
        unit_group=_detect_defend_ramp_unit_group(normalized_command),
    )


def _retreat_army_payload(normalized_command: str) -> IntentPayload | None:
    """Build the retreat DEFEND payload when the command matches the family."""

    if not _looks_like_retreat_army(normalized_command):
        return None
    return DefendIntent(
        priority=detect_priority(normalized_command, RETREAT_ARMY_PRIORITY_KEYWORDS, "high"),
        constraints=(RETREAT_ARMY_CONSTRAINT,),
        location=RETREAT_ARMY_LOCATION,
        unit_group=_detect_retreat_army_unit_group(normalized_command),
    )


def _harass_mineral_line_payload(normalized_command: str) -> IntentPayload | None:
    """Build the mineral-line HARASS payload when the family matches."""

    if not _looks_like_harass_mineral_line(normalized_command):
        return None
    return HarassIntent(
        priority=detect_priority(
            normalized_command,
            HARASS_MINERAL_LINE_PRIORITY_KEYWORDS,
            "normal",
        ),
        constraints=(HARASS_MINERAL_LINE_CONSTRAINT,),
        target=HARASS_MINERAL_LINE_TARGET,
        unit_group=_detect_harass_mineral_line_unit_group(normalized_command),
    )


def _pressure_enemy_expansion_payload(normalized_command: str) -> IntentPayload | None:
    """Build the enemy-expansion HARASS payload when the family matches."""

    if not _looks_like_pressure_enemy_expansion(normalized_command):
        return None
    return HarassIntent(
        priority=detect_priority(
            normalized_command,
            PRESSURE_ENEMY_EXPANSION_PRIORITY_KEYWORDS,
            "normal",
        ),
        constraints=(PRESSURE_ENEMY_EXPANSION_CONSTRAINT,),
        target=PRESSURE_ENEMY_EXPANSION_TARGET,
        unit_group=_detect_pressure_enemy_expansion_unit_group(normalized_command),
    )


def _expand_payload(normalized_command: str) -> IntentPayload | None:
    """Build the EXPAND payload when the command matches the family."""

    if not _looks_like_expand(normalized_command):
        return None
    return ExpandIntent(
        priority=detect_priority(normalized_command, EXPAND_PRIORITY_KEYWORDS, "normal"),
        constraints=(EXPAND_CONSTRAINT,),
        location=EXPAND_DEFAULT_LOCATION,
    )


def _move_camera_payload(normalized_command: str) -> IntentPayload | None:
    """Build the MOVE_CAMERA payload when a semantic camera command matches."""

    if not _looks_like_move_camera(normalized_command):
        return None
    return MoveCameraIntent(
        priority="normal",
        constraints=(MOVE_CAMERA_CONSTRAINT,),
        target=_detect_camera_target(normalized_command),
        target_slot=_detect_camera_target_slot(normalized_command),
    )


def _summarize_state_payload(normalized_command: str) -> IntentPayload | None:
    """Build the SUMMARIZE_STATE payload when the command matches the family."""

    if not _looks_like_summarize_state(normalized_command):
        return None
    return SummarizeStateIntent(
        priority="normal",
        constraints=(SUMMARIZE_STATE_CONSTRAINT,),
    )


@dataclass(frozen=True)
class IntentCandidateSpec:
    """One supported intent family: payload builder plus clarification labels."""

    alias: str
    intent: IntentName
    description: str
    build_payload: Callable[[str], IntentPayload | None]

    def __post_init__(self) -> None:
        if not self.alias.strip():
            raise ValueError("intent candidate spec alias must be non-empty.")
        if not self.description.strip():
            raise ValueError("intent candidate spec description must be non-empty.")


INTENT_CANDIDATE_SPECS: Final[tuple[IntentCandidateSpec, ...]] = (
    IntentCandidateSpec(
        alias=GATHER_RESOURCE_ALIAS,
        intent="GATHER_RESOURCE",
        description="자원 채취 명령",
        build_payload=_gather_resource_payload,
    ),
    IntentCandidateSpec(
        alias=KEEP_WORKER_PRODUCTION_ALIAS,
        intent="TRAIN_WORKER",
        description="SCV 생산 유지 명령",
        build_payload=_keep_worker_production_payload,
    ),
    IntentCandidateSpec(
        alias=PREVENT_SUPPLY_BLOCK_ALIAS,
        intent="BUILD_STRUCTURE",
        description="보급 막힘 방지 명령",
        build_payload=_prevent_supply_block_payload,
    ),
    IntentCandidateSpec(
        alias=REPAIR_ALIAS,
        intent="REPAIR",
        description="손상된 아군 대상 수리 명령",
        build_payload=_repair_payload,
    ),
    IntentCandidateSpec(
        alias=BUILD_STRUCTURE_ALIAS,
        intent="BUILD_STRUCTURE",
        description="Terran 구조물 건설 명령",
        build_payload=_build_structure_payload,
    ),
    IntentCandidateSpec(
        alias=TRAIN_UNIT_ALIAS,
        intent="TRAIN_ARMY",
        description="Marine 생산 명령",
        build_payload=_train_unit_payload,
    ),
    IntentCandidateSpec(
        alias=SEND_SCOUT_ALIAS,
        intent="SCOUT",
        description="적 위치 확인 정찰 명령",
        build_payload=_send_scout_payload,
    ),
    IntentCandidateSpec(
        alias=DEFEND_RAMP_ALIAS,
        intent="DEFEND",
        description="입구 방어 명령",
        build_payload=_defend_ramp_payload,
    ),
    IntentCandidateSpec(
        alias=RETREAT_ARMY_ALIAS,
        intent="DEFEND",
        description="병력 후퇴 명령",
        build_payload=_retreat_army_payload,
    ),
    IntentCandidateSpec(
        alias=HARASS_MINERAL_LINE_ALIAS,
        intent="HARASS",
        description="적 미네랄 라인 견제 명령",
        build_payload=_harass_mineral_line_payload,
    ),
    IntentCandidateSpec(
        alias=PRESSURE_ENEMY_EXPANSION_ALIAS,
        intent="HARASS",
        description="적 앞마당 압박 명령",
        build_payload=_pressure_enemy_expansion_payload,
    ),
    IntentCandidateSpec(
        alias=EXPAND_ALIAS,
        intent="EXPAND",
        description="앞마당 확장 명령",
        build_payload=_expand_payload,
    ),
    IntentCandidateSpec(
        alias=MOVE_CAMERA_ALIAS,
        intent="MOVE_CAMERA",
        description="카메라 이동 명령",
        build_payload=_move_camera_payload,
    ),
    IntentCandidateSpec(
        alias=SUMMARIZE_STATE_ALIAS,
        intent="SUMMARIZE_STATE",
        description="현재 상태 요약 명령",
        build_payload=_summarize_state_payload,
    ),
)
"""Ordered intent-family registry shared by resolution and clarification.

The order reproduces the legacy if-chain precedence (which matched the legacy
candidate registration order exactly) and drives the candidate order shown in
ambiguous-command clarification prompts and failure metadata.
"""
