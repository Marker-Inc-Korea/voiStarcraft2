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

import os as _os

DECISION_MODE = _os.environ.get("BENCH_DECISION", "new")  # see MODES below;  # 'baseline' = main before this change; 'new' = shipped web_gui._decide_realtime_intent

TOOL_NAME = RI.SUBMIT_COMMAND_TOOL_NAME
CANON_IS_VECTOR = True  # scoring.score_prediction uses canonicalize() below

# --- A/B variants over the SHIPPED prompt -----------------------------------------------------------------------------
# "shipped"  = main as of the baseline (byte-identical to the original build_realtime_instructions, no transcription hints)
# "rules"    = + rules 8-14;  "fewshot" = + nine extra examples;  "both" = + both (= product prompt);
# "lean1"    = rules 8-14, the nine extra few-shot examples dropped, restating tool-property descriptions trimmed
#              (enum values/required untouched);
# "lean2"    = lean1 + remaining tool descriptions shortened + the base-prompt example that duplicates rule 1 dropped.
# Suffixes (any order, after the base name): "_h" = transcription language/prompt hints in the minted session ("both_h");
# "_tcn" = tool_choice is the named function {type:function,name:submit_command} instead of "required".
BASE_VARIANTS = ("shipped", "rules", "fewshot", "both", "lean1", "lean2")
_INSTR = {"shipped": (False, False), "rules": (True, False), "fewshot": (False, True), "both": (True, True),
          "lean1": (True, False), "lean2": (True, False)}
VARIANTS = ("shipped", "rules", "fewshot", "both", "shipped_h", "both_h")  # registry default set (run.py list)
_DUP_EXAMPLE_PREFIX = "'마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격' -> operation"
_DUP_EXAMPLE_END = "flank_right}].\n"


def parse_variant(variant: str) -> tuple[str, bool, bool]:
    """'both_h_tcn' -> ('both', hints=True, named_tool_choice=True). Unknown base names raise ValueError."""
    parts = variant.split("_")
    base, flags = parts[0], parts[1:]
    if base not in BASE_VARIANTS or any(f not in ("h", "tcn", "cv1", "cv2", "cv3") for f in flags):
        raise ValueError(f"unknown prompt variant {variant!r}")
    return base, "h" in flags, "tcn" in flags


def catalog_mode(variant: str) -> str:
    """'both_cv2' -> 'v2' (catalog.intent + server resolver); 'both_cv1' -> 'v1' (catalog.command_id enum); else 'off' (V0)."""
    flags = variant.split("_")[1:]
    return "v1" if "cv1" in flags else "v2" if "cv2" in flags else "v3" if "cv3" in flags else "off"


def variant_has_hints(variant: str) -> bool:
    return parse_variant(variant)[1]


def variant_tool_choice(variant: str):
    return {"type": "function", "name": TOOL_NAME} if parse_variant(variant)[2] else "required"


def _set_desc(node: dict, text) -> None:
    if text is None:
        node.pop("description", None)
    else:
        node["description"] = text


def _lean_tool(tool: dict, level: int) -> dict:
    """Trim descriptions only (never enums / required / property names / types), so the lowering contract is unchanged.

    lean1: drop descriptions that merely restate the property name/enum or duplicate base-prompt rule 3
           (operations[].goal/operation_action/location/route_type, top-level goal, operations[]/micro[] array notes) and
           shorten composition/task_type hints.
    lean2: lean1 + shorten the tool, operation_id, emergency and clarification descriptions and drop the task_type/composition hints."""
    import copy

    t = copy.deepcopy(tool)
    props = t["parameters"]["properties"]
    ops = props["operations"]["items"]["properties"]
    for name in ("operation_action", "goal", "location", "route_type"):
        _set_desc(ops[name], None)
    _set_desc(ops["composition"], "Exact unit counts the user SAID; omit when none spoken.")
    _set_desc(ops["task_type"], "scout / pressure=attack / defend / harass workers or production / regroup=gather.")
    _set_desc(props["goal"], None)
    _set_desc(props["operations"], None)
    _set_desc(props["micro"], None)
    if level >= 2:
        t["description"] = ("Submit exactly one StarCraft II commander intent (emergency/micro/operation/macro; "
                            "parallel forces in operations[]).")
        _set_desc(ops["operation_id"], "Short english slug; reuse the SAME id to edit that force; omit for a new one.")
        _set_desc(ops["task_type"], None)
        _set_desc(ops["composition"], None)
        _set_desc(props["emergency"], "Only the flags the user asked for; exclusive with every other section.")
        _set_desc(props["clarification"], "ONE short question in the user's language when ambiguous, unintelligible, "
                  "a pure negation or not a command; leave every other section out.")
    return t


def build_tool(variant: str = "shipped") -> dict:
    base = parse_variant(variant)[0]
    tool = RI.build_submit_command_tool(catalog_mode=catalog_mode(variant))
    if base in ("lean1", "lean2"):
        return _lean_tool(tool, 1 if base == "lean1" else 2)
    return tool


def to_chat_tool(tool: dict) -> dict:
    import copy

    return {"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                                              "parameters": copy.deepcopy(tool["parameters"])}}


def build_instructions(variant: str = "shipped") -> str:
    base = parse_variant(variant)[0]
    rules, fewshot = _INSTR[base]
    text = RI.build_realtime_instructions(rules=rules, fewshot=fewshot, catalog_mode=catalog_mode(variant))
    if base == "lean2":
        i = text.index(_DUP_EXAMPLE_PREFIX)
        j = text.index(_DUP_EXAMPLE_END, i) + len(_DUP_EXAMPLE_END)
        text = text[:i] + text[j:]
    return text


def turn_detection(endpoint: str, vad_silence_ms: int | None = None):
    """endpoint: manual | vadNNN | semhigh | semauto (semantic_vad eagerness high/auto). Same dict for mint + session.update."""
    if endpoint == "manual":
        return None
    if endpoint in ("semhigh", "semauto"):
        return {"type": "semantic_vad", "eagerness": "high" if endpoint == "semhigh" else "auto",
                "create_response": True, "interrupt_response": False}
    if endpoint.startswith("vad") and endpoint[3:].isdigit():
        return {"type": "server_vad", "silence_duration_ms": int(endpoint[3:]), "prefix_padding_ms": 300,
                "create_response": True, "interrupt_response": False}
    raise ValueError(f"unknown endpoint {endpoint!r}")


def realtime_session_body(model: str, variant: str, *, vad_silence_ms: int | None = None, transcription: bool = True,
                          endpoint: str | None = None) -> dict:
    """The exact /realtime/client_secrets body the shipped backend sends (optionally swapping instructions / tools /
    tool_choice / endpointing). ``endpoint`` (manual|vadNNN|semhigh|semauto) wins over ``vad_silence_ms``."""
    hints = variant_has_hints(variant)
    cfg = RI.RealtimeUpstreamConfig(base_url=RI.DEFAULT_REALTIME_BASE_URL, model=model, api_key="",
                                    vad_silence_ms=vad_silence_ms or RI.DEFAULT_VAD_SILENCE_MS,
                                    transcribe_language=RI.HINTS_TRANSCRIBE_LANGUAGE if hints else "",
                                    transcribe_prompt=RI.HINTS_TRANSCRIBE_PROMPT if hints else "")
    body = RI._session_body(cfg, transcription=transcription, text_output=True, transcription_hints=hints)
    sess = body["session"]
    sess["instructions"] = build_instructions(variant)
    sess["tools"] = [build_tool(variant)]
    sess["tool_choice"] = variant_tool_choice(variant)
    if endpoint is not None:
        td = turn_detection(endpoint)
        if td is not None and td["type"] == "server_vad":
            td = {**sess["audio"]["input"]["turn_detection"], **td}  # keep the product's threshold/etc. keys, override silence
        sess["audio"]["input"]["turn_detection"] = td
    elif vad_silence_ms is None:  # manual commit endpointing
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


def _baseline_decide(transcript: str, tool_args):
    """Decision logic of main BEFORE this change (emergency regex + named-cancel guard, tool call + negation guards,
    legacy micro fast path, negation guard). Kept verbatim for the A/B."""
    if transcript and WG._micromachine_request_is_emergency(transcript, None) and not WG._realtime_call_is_named_operation_cancel(tool_args, transcript):
        return {"kind": "executed", "tier": "emergency", "source": "emergency_override" if tool_args is not None else "emergency_fast_path",
                "provider_output": WG._micromachine_emergency_safety_output(transcript)}
    if tool_args is not None:
        tier = RI.classify_tier(tool_args)
        if tier == RI.TIER_CLARIFICATION:
            RI.tool_arguments_to_provider_output(tool_args)
            return {"kind": "clarification", "tier": tier, "source": "tool_call"}
        if (tier == RI.TIER_EMERGENCY and RI.transcript_negates_emergency(transcript)) or (
                tier == RI.TIER_MICRO and RI.micro_phrase_is_negated(transcript)):
            return {"kind": "blocked", "tier": tier, "source": "negation_blocked"}
        return {"kind": "executed", "tier": tier, "source": "tool_call", "provider_output": RI.tool_arguments_to_provider_output(tool_args)}
    fast = RI.match_micro_fast_path(transcript)
    if fast is not None:
        return {"kind": "executed", "tier": RI.TIER_MICRO, "source": "micro_fast_path", "provider_output": RI.tool_arguments_to_provider_output(fast)}
    if RI.transcript_is_pure_negated_emergency(transcript) or RI.micro_phrase_is_negated(transcript):
        return {"kind": "blocked", "tier": RI.TIER_CLARIFICATION, "source": "negation_guard"}
    return {"kind": "unresolved", "tier": "unresolved", "source": "text_llm"}


MODES = ("baseline", "guard", "fast", "new")  # decision layers: pre-change | +raw-control guard | +fast path | both (= product)


def set_mode(mode: str) -> None:
    global DECISION_MODE
    if mode not in MODES:
        raise ValueError(mode)
    DECISION_MODE = mode


def handler_outcome(transcript: str, tool_args) -> dict:
    """Emulate web_gui `/api/realtime/intent`. kind: executed | clarification | blocked | invalid | unresolved.

    Modes: baseline = decision logic of main before this change; guard = shipped function with the extended fast path
    disabled; fast = shipped function with the raw-control/injection guard disabled; new = the shipped function."""
    import contextlib
    from unittest import mock

    transcript = " ".join((transcript or "").split())[:2000]
    try:
        if DECISION_MODE == "baseline":
            return _baseline_decide(transcript, tool_args)
        with contextlib.ExitStack() as stack:
            if DECISION_MODE == "guard":
                stack.enter_context(mock.patch.object(RI, "match_fast_path", lambda t: None))
            if DECISION_MODE == "fast":
                stack.enter_context(mock.patch.object(RI, "transcript_is_raw_control_or_injection", lambda t: False))
            d = WG._decide_realtime_intent(transcript, tool_args)
        out = {"kind": d.kind, "tier": d.tier, "source": d.source, "family": d.family}
        if d.provider_output is not None:
            out["provider_output"] = d.provider_output
        return out
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


def _unused_fast_outcome_from_ext_rules(old_args: dict | None) -> dict | None:
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
