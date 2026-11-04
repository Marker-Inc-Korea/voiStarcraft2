"""Benchmark adapter over the SHIPPED contract: starcraft_commander/realtime_intent.py + web_gui intent handler.

Select with:  BENCH_SCHEMA_MODULE=shipped_schema   (see common.py)

* tool / session / instructions  = realtime_intent.build_submit_command_tool / _session_body / build_realtime_instructions
  (variant "shipped" = byte-identical shipped instructions; "shipped_fewshot" = shipped + extra non-corpus examples).
* "valid"  = realtime_intent.tool_arguments_to_provider_output succeeds (it ends in PolicyModulationVector.from_mapping).
* outcome  = what web_gui `_handle_realtime_intent` would do for (transcript, tool_arguments): emergency regex override,
  classify_tier, negation guards, lowering. Scoring then compares the resulting PolicyModulationVector semantics to gold.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starcraft_commander import policy_modulation as PM  # noqa: E402
from starcraft_commander import realtime_intent as RI  # noqa: E402
from starcraft_commander import web_gui as WG  # noqa: E402

TOOL_NAME = RI.SUBMIT_COMMAND_TOOL_NAME
VARIANTS = ("shipped", "shipped_fewshot")
CANON_IS_VECTOR = True  # scoring.score_prediction uses canonicalize() below

# --- few-shot additions. Every user text below was checked NOT to occur in corpus.jsonl (see test_scoring). -----------------
FEWSHOT_EXAMPLES = [
    ("군수공장 하나 지어줘", {"command_layer": "macro", "goal": "군수공장 건설", "macro": {"task_type": "tech_transition", "building_tasks": [{"building_type": "TERRAN_FACTORY", "count": 1}]}}),
    ("토르 중심으로 가자", {"command_layer": "macro", "goal": "토르 중심 운영", "macro": {"task_type": "sustain_production", "production_targets": ["TERRAN_THOR"], "standing_order": True}}),
    ("바이킹 두 기는 정찰, 탱크 세 기는 오른쪽 길로 적 본진 공격", {"command_layer": "operation", "goal": "정찰 + 우회 공격", "operations": [
        {"task_type": "scout_with_units", "composition": [{"unit_type": "TERRAN_VIKINGFIGHTER", "count": 2}]},
        {"task_type": "pressure_with_main_army", "composition": [{"unit_type": "TERRAN_SIEGETANK", "count": 3}], "route_type": "flank_right", "location": "enemy_main"}]}),
    ("공격 부대에 탱크 두 기 더 보내", {"command_layer": "operation", "goal": "공격 증원", "operations": [
        {"operation_id": "attack", "operation_action": "reinforce", "composition": [{"unit_type": "TERRAN_SIEGETANK", "count": 2}]}]}),
    ("공격 병력을 마린 여섯 기로 줄여", {"command_layer": "operation", "goal": "공격 병력 축소", "operations": [
        {"operation_id": "attack", "operation_action": "resize", "composition": [{"unit_type": "TERRAN_MARINE", "count": 6}]}]}),
    ("그 자리에서 멈춰", {"command_layer": "emergency", "goal": "제자리 정지", "emergency": {"hold_position": True}}),
    ("야마토 쏘지 마", {"command_layer": "micro", "goal": "야마토 금지", "clarification": "야마토를 쏘지 않을게요. 대신 무엇을 할까요?"}),
    ("맵 좌표 30, 40 으로 가", {"command_layer": "operation", "goal": "좌표 요청", "clarification": "좌표 대신 '적 앞마당으로 이동'처럼 장소 이름으로 말씀해 주세요."}),
    ("저글링 뽑아", {"command_layer": "macro", "goal": "비지원 종족", "clarification": "테란 유닛만 지원해요. 어떤 유닛을 생산할까요?"}),
]


def build_tool(variant: str = "shipped") -> dict:
    return RI.build_submit_command_tool()


def to_chat_tool(tool: dict) -> dict:
    import copy

    return {"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                                              "parameters": copy.deepcopy(tool["parameters"])}}


def build_instructions(variant: str = "shipped") -> str:
    base = RI.build_realtime_instructions()
    if variant == "shipped":
        return base
    if variant == "shipped_fewshot":
        import json

        shots = "\n".join(
            f"'{u}' -> " + json.dumps(a, ensure_ascii=False, separators=(",", ":")) for u, a in FEWSHOT_EXAMPLES
        )
        return base + "ADDITIONAL EXAMPLES / 추가 예시 (arguments as sent):\n" + shots + "\n"
    raise ValueError(variant)


def realtime_session_body(model: str, variant: str, *, vad_silence_ms: int | None, transcription: bool = True) -> dict:
    """The exact /realtime/client_secrets body the shipped backend sends (optionally swapping instructions / endpointing)."""
    cfg = RI.RealtimeUpstreamConfig(base_url=RI.DEFAULT_REALTIME_BASE_URL, model=model, api_key="",
                                    vad_silence_ms=vad_silence_ms or RI.DEFAULT_VAD_SILENCE_MS)
    body = RI._session_body(cfg, transcription=transcription, text_output=True)
    sess = body["session"]
    sess["instructions"] = build_instructions(variant)
    if vad_silence_ms is None:  # manual commit endpointing
        sess["audio"]["input"]["turn_detection"] = None
    return body


# --- validity / backend emulation ---------------------------------------------------------------------------------------
def validate_args(args) -> list[str]:  # JSON-schema level is the shipped lowering itself
    return []


def derive_layer(args) -> str:
    try:
        return RI.classify_tier(args)
    except (ValueError, TypeError):
        return "invalid"


def check_with_dsl(args) -> tuple[bool, str]:
    try:
        out = RI.tool_arguments_to_provider_output(args)
    except (ValueError, TypeError, KeyError) as exc:
        return False, f"lower: {str(exc)[:160]}"
    return True, "clarification" if out.get("status") == "clarification_required" else "ok"


def tool_args_to_provider_output(args):
    return RI.tool_arguments_to_provider_output(args)


def handler_outcome(transcript: str, tool_args) -> dict:
    """Emulate web_gui._handle_realtime_intent. kind: executed | clarification | blocked | invalid | unresolved."""
    transcript = " ".join((transcript or "").split())[:2000]
    try:
        if transcript and WG._micromachine_request_is_emergency(transcript, None):
            return {"kind": "executed", "tier": "emergency", "source": "emergency_override" if tool_args is not None else "emergency_fast_path",
                    "provider_output": WG._micromachine_emergency_safety_output(transcript)}
        if tool_args is not None:
            tier = RI.classify_tier(tool_args)
            if tier == RI.TIER_CLARIFICATION:
                RI.tool_arguments_to_provider_output(tool_args)
                return {"kind": "clarification", "tier": tier, "source": "tool_call"}
            negated = (tier == RI.TIER_EMERGENCY and RI.transcript_negates_emergency(transcript)) or (
                tier == RI.TIER_MICRO and RI.micro_phrase_is_negated(transcript))
            if negated:
                return {"kind": "blocked", "tier": tier, "source": "negation_blocked"}
            return {"kind": "executed", "tier": tier, "source": "tool_call",
                    "provider_output": RI.tool_arguments_to_provider_output(tool_args)}
        fast = RI.match_micro_fast_path(transcript)
        if fast is not None:
            return {"kind": "executed", "tier": RI.TIER_MICRO, "source": "micro_fast_path",
                    "provider_output": RI.tool_arguments_to_provider_output(fast)}
        if RI.transcript_is_pure_negated_emergency(transcript) or RI.micro_phrase_is_negated(transcript):
            return {"kind": "blocked", "tier": RI.TIER_CLARIFICATION, "source": "negation_guard"}
        return {"kind": "unresolved", "tier": "unresolved", "source": "text_llm"}
    except (ValueError, TypeError, KeyError) as exc:
        return {"kind": "invalid", "tier": "invalid", "source": "tool_call", "error": str(exc)[:160]}


# --- vector -> canonical semantic view (old gold-comparable arg format) ---------------------------------------------------
_ALIAS = {"siegetank": "siege_tank", "vikingfighter": "viking", "widowmine": "widow_mine", "supplydepot": "supply_depot",
          "commandcenter": "command_center", "engineeringbay": "engineering_bay", "ghostacademy": "ghost_academy",
          "fusioncore": "fusion_core", "hellion": "hellion", "hellbat": "hellion"}
_SLUG_TASK = {"scout": "scout_with_units", "attack": "pressure_with_main_army", "defend": "defend_with_units",
              "harass": "harass_with_units", "regroup": "regroup_with_units"}


def norm_token(tok: str) -> str:
    t = str(tok)
    if t.startswith("TERRAN_"):
        t = t[len("TERRAN_"):]
    t = t.lower()
    return _ALIAS.get(t.replace("_", ""), t)


def _op_canon(op) -> dict:
    tt = op.tactical_task.task_type
    if not tt:
        base = op.operation_id.split("-")[0]
        tt = _SLUG_TASK.get(base, "")
    d = {"operation_id": op.operation_id, "task_type": tt}
    comp = [{"unit_type": norm_token(c.unit_type), "count": c.count} for c in op.composition_requirements]
    if comp:
        d["units"] = comp
    loc = op.tactical_task.location_intent or op.scope.location_intent
    tgt = op.target_intent.target_type
    # EQUIVALENCE RULE 1 (shipped contract has two ways to say "attack the enemy main/natural": location or target_type,
    # and the shipped instructions teach target_type enemy_main/enemy_natural): fold target_type enemy_* into location.
    if not loc and tgt in ("enemy_main", "enemy_natural"):
        loc = tgt
    if tgt in ("enemy_main", "enemy_natural"):
        tgt = ""
    if loc:
        d["location"] = loc
    if op.route_intent.route_type:
        d["route_type"] = op.route_intent.route_type
    if tgt:
        d["target_type"] = tgt
    if op.operation_edit.action:
        d["edit_action"] = op.operation_edit.action
    return d


def canonicalize(args, transcript: str | None, outcome: dict | None = None):
    """-> (canon_args, layer, valid, reason, outcome)."""
    outcome = outcome or handler_outcome(transcript or "", args)
    kind = outcome["kind"]
    if kind == "clarification":
        return {"clarification": {"reason": "ask"}}, "clarification", True, "clarification", outcome
    if kind == "blocked":
        return {"clarification": {"reason": "blocked"}}, "clarification", True, outcome["source"], outcome
    if kind in ("invalid", "unresolved"):
        return None, "invalid" if kind == "invalid" else "none", False, outcome.get("error", kind), outcome
    po = outcome["provider_output"]
    try:
        vec = PM.PolicyModulationVector.from_mapping(po)
    except ValueError as exc:  # cannot happen for shipped lowering (it validates) but never trust
        return None, "invalid", False, f"dsl: {str(exc)[:120]}", outcome
    layer = vec.command_layer.value
    canon: dict = {}
    if layer == "emergency":
        canon["emergency"] = {k: True for k, v in vec.emergency.to_dict().items() if v}
    else:
        ops, micros = [], []
        for op in vec.operations:
            if op.command_layer.value == "micro":
                micros.append(op.tactical_task)
            else:
                ops.append(_op_canon(op))
        if not vec.operations and vec.tactical_task.task_type == "execute_ability":
            micros.append(vec.tactical_task)
        if ops:
            canon["operations"] = ops
        if micros:
            if len(micros) == 1 and not ops:
                t = micros[0]
                canon["micro"] = {"ability": t.ability, "unit_types": [norm_token(u) for u in t.unit_classes]}
            else:
                canon["micro"] = {"ability": "__multi__"}
        if vec.building_tasks:
            canon["buildings"] = [{"building_type": norm_token(b.building_type), "count": b.count,
                                   **({"placement_intent": b.placement_intent} if b.placement_intent else {})}
                                  for b in vec.building_tasks]
        if layer == "macro" or (vec.production_plan.targets and not ops):
            canon["production"] = {"task_type": vec.tactical_task.task_type,
                                   "targets": [norm_token(t) for t in vec.production_plan.targets or vec.tactical_task.production_targets]}
    return canon, layer, True, "ok", outcome


def fast_outcome_from_ext_rules(old_args: dict | None) -> dict | None:
    """Convert a fastpath.ext_rules result (private-schema shape) into a shipped tool call, then run it through the
    shipped lowering (so it is DSL-validated like everything else)."""
    if old_args is None:
        return None
    out = {"command_layer": old_args["command_layer"], "goal": old_args.get("goal", "fast")}
    if "emergency" in old_args:
        out["emergency"] = dict(old_args["emergency"])
    if "micro" in old_args:
        m = old_args["micro"]
        item = {"ability": m["ability"]}
        if m.get("unit_types"):
            item["unit_classes"] = [PM._canonicalize_allowed_task_token("u", u, allowed=PM.MICROMACHINE_ALLOWED_TASK_TOKENS) for u in m["unit_types"]]
        out["micro"] = [item]
    if "production" in old_args:
        p = old_args["production"]
        mac = {"task_type": p["task_type"]}
        if p.get("targets"):
            mac["production_targets"] = [PM._canonicalize_allowed_task_token("t", t, allowed=PM.MICROMACHINE_ALLOWED_TASK_TOKENS) for t in p["targets"]]
        out["macro"] = mac
    return out


# --- gold -> shipped tool args (proves the shipped contract can express every gold) ------------------------------------
def _first(v):
    return v[0] if isinstance(v, list) else v


def _tok(u: str) -> str:
    return PM._canonicalize_allowed_task_token("u", u, allowed=PM.MICROMACHINE_ALLOWED_TASK_TOKENS)


def gold_to_shipped_args(gold: dict, text: str = "gold") -> dict:
    layer = gold["layer"]
    a: dict = {"goal": text}
    if layer == "clarification":
        a.update(command_layer="operation", clarification="무엇을 할까요?")
    elif layer == "emergency":
        a.update(command_layer="emergency", emergency={f: True for f in gold["flags"]})
    elif layer == "micro":
        item = {"ability": _first(gold["ability"])}
        if gold.get("unit_types"):
            item["unit_classes"] = [_tok(u) for u in gold["unit_types"]]
        a.update(command_layer="micro", micro=[item])
    elif layer == "macro":
        p = gold["production"]
        m = {"task_type": _first(p["task_type"])}
        if p.get("targets"):
            m["production_targets"] = [_tok(t) for t in p["targets"]]
        a.update(command_layer="macro", macro=m)
    elif "buildings" in gold:
        bt = []
        for b in gold["buildings"]:
            e = {"building_type": _tok(b["building_type"]), "count": b.get("count", 1)}
            if b.get("placement_intent"):
                e["placement_intent"] = _first(b["placement_intent"])
            bt.append(e)
        a.update(command_layer="macro", macro={"task_type": "tech_transition", "building_tasks": bt})
    else:
        ops = []
        for g in gold["operations"]:
            o = {"task_type": _first(g["task_type"])}
            if g.get("units") not in (None, "any"):
                o["composition"] = [{"unit_type": _tok(u["unit_type"]), "count": 1 if u["count"] == "any" else u["count"]} for u in g["units"]]
            for k, dst in (("location", "location"), ("route_type", "route_type"), ("target_type", "target_type")):
                if g.get(k):
                    o[dst] = _first(g[k])
            if g.get("edit_action"):
                o["operation_action"] = _first(g["edit_action"])
                if g["edit_action"] in ("cancel", "restart", "retarget", "update") or True:
                    o["operation_id"] = {"scout_with_units": "scout", "pressure_with_main_army": "attack"}.get(o["task_type"], "attack")
            ops.append(o)
        a.update(command_layer="operation", operations=ops)
    return a
