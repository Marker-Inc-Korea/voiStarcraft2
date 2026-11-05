"""Offline utterance -> DSL -> reducer -> blackboard/Direct -> evidence replay.

Every case is a user utterance (Korean or English) with the ``provider_output`` a
correct LLM would emit for it.  The output is driven through the REAL code:

``compile_policy_modulation_provider_output`` -> ``MicroMachineLiveTextSession``
(live reducer, parallel-operation registry, layer state) -> either

* **projection**: the typed blackboard projection MicroMachine reads
  (``publish_non_macro_projection=True`` + ``MicroMachineFilesystemBlackboard`` in a
  temp dir).  This is the path the recorded QA30 run exercised, minus the C++.
* **production**: what the shipped cockpit does (``web_gui --dry-run``): macro to the
  blackboard, operation/micro/emergency to the Direct executor over the scripted
  ``DemoFakeBotAI`` (NOT StarCraft II).

Evidence is interpreted by the real classifiers
(``classify_micromachine_operation_executions`` /
``classify_micromachine_command_execution``) from synthetic telemetry built in the
recorded QA30 shape, or from the recorded QA30 fixtures themselves.

Nothing here talks to SC2.  A passing replay is a statement about Python-side
fidelity, never about live game behaviour.

Run:  python -m scripts.fidelity.replay_chain [--json OUT] [--markdown OUT]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from starcraft_commander.demo_sc2 import build_dry_run_session
from starcraft_commander.micromachine_command_execution import (
    COMMAND_EXECUTION_STAGES,
    classify_micromachine_command_execution,
    classify_micromachine_operation_executions,
)
from starcraft_commander.micromachine_live_session import (
    LiveModulationStatus,
    MicroMachineLiveTextSession,
    StaticJsonPolicyModulationProvider,
)
from starcraft_commander.micromachine_runtime import MicroMachineFilesystemBlackboard

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "qa30"

#: What the shipped cockpit attaches for Direct execution (web_gui.py:13291-13309,
#: local_cockpit.py:885).  The replay's production mode is labelled with it.
DIRECT_RUNTIME_KIND = "dry_run_fake_bot"

#: Invariant violations the replay is EXPECTED to find today.  Each is an open
#: handoff item documented in docs/intent-fidelity-audit.md; the unit test pins
#: this list exactly so a fix (or a new regression) changes it visibly.
#: H-4: the live reducer rewrites an emergency TTL (45 -> 60) without a warning.
#: Fixed in a separate commit because it changes the pre-live journey evidence.
EXPECTED_OPEN_VIOLATIONS: tuple[str, ...] = (
    "emergency-hold::no_silent_clamp",
    "emergency-keeps-macro::no_silent_clamp",
    "emergency-retreat-ko::no_silent_clamp",
    "emergency-then-macro::no_silent_clamp",
)

# ---------------------------------------------------------------------------
# Provider-output builders (the shapes of docs/micromachine-rich-dsl-examples.md
# and integrations/micromachine/scripts/qa30_scenario_command.py)
# ---------------------------------------------------------------------------


def op(
    operation_id: str,
    task: str,
    location: str,
    count: Any,
    *,
    unit: str = "TERRAN_MARINE",
    role: str = "frontline",
    route: str | None = None,
    target: str | None = None,
    edit: Mapping[str, Any] | None = None,
    state: str | None = None,
    mode: str = "until_completed",
    conditions: Sequence[str] = ("target_reached",),
    ability: str | None = None,
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "operation_id": operation_id,
        "tactical_task": {
            "task_type": task,
            "unit_classes": [unit],
            "location_intent": location,
        },
        "composition_requirements": [
            {"unit_type": unit, "count": count, "role": role}
        ],
    }
    if ability:
        operation["tactical_task"]["ability"] = ability
    if route:
        operation["route_intent"] = {"route_type": route}
    if target:
        operation["target_intent"] = {"target_type": target, "priority": 0.9}
    if edit:
        operation["operation_edit"] = dict(edit)
    if state:
        operation["lifetime"] = {
            "mode": mode,
            "completion_state": state,
            "completion_conditions": list(conditions),
        }
    return operation


def operations(*ops: Mapping[str, Any], goal: str = "fidelity replay") -> dict[str, Any]:
    return {"goal": goal, "command_layer": "operation", "operations": list(ops)}


def macro(
    unit: str = "TERRAN_MARINE",
    *,
    goal: str = "마린 계속 생산",
    standing: bool = False,
    ttl: int | None = None,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "goal": goal,
        "command_layer": "macro",
        "production": {"queue_biases": {unit: 0.9}},
        "tactical_task": {
            "task_type": "sustain_production",
            "production_targets": [unit],
        },
    }
    if standing:
        output["lifetime"] = {
            "mode": "standing_order",
            "completion_conditions": ["cancelled_by_user"],
            "completion_state": "active",
        }
    if ttl is not None:
        output["ttl_seconds"] = ttl
    return output


def emergency(*, retreat: bool = True, cancel: bool = True, hold: bool = False) -> dict[str, Any]:
    return {
        "goal": "긴급",
        "override_level": "emergency",
        "ttl_seconds": 45,
        "emergency": {
            "force_retreat": retreat,
            "cancel_attacks": cancel,
            "hold_position": hold,
        },
    }


def micro(ability: str | None, unit: str = "TERRAN_MARINE", location: str = "enemy_main") -> dict[str, Any]:
    task: dict[str, Any] = {
        "task_type": "execute_ability",
        "unit_classes": [unit],
        "location_intent": location,
    }
    if ability:
        task["ability"] = ability
    return {"goal": "ability", "command_layer": "micro", "tactical_task": task}


def S(
    text: str,
    output: Mapping[str, Any],
    frame: int,
    *,
    id: str | None = None,
    telemetry: Mapping[str, Any] | None = None,
    tick: int | None = None,
    fail_publish: bool = False,
    expect: Mapping[str, Any] | None = None,
    expect_production: Mapping[str, Any] | None = None,
    worker_job: bool = False,
) -> dict[str, Any]:
    return {
        "text": text,
        "output": dict(output),
        "frame": frame,
        "id": id,
        "telemetry": telemetry,
        "tick": tick,
        "fail_publish": fail_publish,
        "expect": dict(expect or {}),
        "expect_production": dict(expect_production or {}),
        "worker_job": worker_job,
    }


def C(case_id: str, utterance: str, ask: str, steps: Sequence[dict[str, Any]], **meta: Any) -> dict[str, Any]:
    return {"id": case_id, "utterance": utterance, "ask": ask, "steps": list(steps), **meta}


# ---------------------------------------------------------------------------
# Synthetic telemetry in the recorded QA30 shape
# ---------------------------------------------------------------------------

_TAGS = itertools.count(4_000_000_001)


def _family_rows(op_dict: Mapping[str, Any], update_id: str, tags: Sequence[int], stage: str, frames: Mapping[str, int]) -> list[dict[str, Any]]:
    rows = []
    for requirement in op_dict.get("composition_requirements", ()):
        unit_type = requirement["unit_type"]
        role = requirement["role"]
        owned = list(tags)[: requirement["count"]]
        rank = ("received", "assigned", "ordered", "acted", "effect").index(stage) if stage in ("received", "assigned", "ordered", "acted", "effect") else 0
        attempted = owned if rank >= 2 else []
        submitted = owned if rank >= 3 else []
        effect = owned if rank >= 4 else []
        rows.append(
            {
                "update_id": update_id,
                "operation_id": op_dict["operation_id"],
                "generation": op_dict["generation"],
                "family": "marine" if "MARINE" in unit_type else unit_type.removeprefix("TERRAN_").lower(),
                "unit_type": unit_type,
                "role": role,
                "action": "attack_move",
                "required_effect": "movement_or_engagement",
                "attempt_generation": op_dict["generation"],
                "assigned": len(owned),
                "represented": len(owned),
                "attempted_count": len(attempted),
                "attempted_unit_tags": attempted,
                "attempted_frame": frames["ordered"] if attempted else 0,
                "submitted_count": len(submitted),
                "submitted_unit_tags": submitted,
                "submitted_frame": frames["acted"] if submitted else 0,
                "effect_kind": "movement" if effect else "",
                "effect_count": len(effect),
                "effect_unit_tags": effect,
                "effect_frame": frames["effect"] if effect else 0,
                "blocker_manager": "",
                "blocker": "",
            }
        )
    return rows


_ORDER = ("received", "assigned", "ordered", "acted", "effect", "completed")


def director_entry(op_dict: Mapping[str, Any], update_id: str, issued: int, spec: Any, tag_pool: Sequence[int]) -> dict[str, Any]:
    stage, assigned_n = (spec if isinstance(spec, tuple) else (spec, None))
    frames = {
        "received": issued + 10,
        "assigned": issued + 20,
        "ordered": issued + 30,
        "acted": issued + 40,
        "effect": issued + 50,
    }
    task_type = op_dict["tactical_task"]["task_type"]
    canonical = {
        "scout_with_units": ("scout", "scout"),
        "pressure_with_main_army": ("attack", "attack"),
        "defend_with_units": ("defend", "defend"),
        "harass_with_units": ("harass", "harass"),
        "regroup_with_units": ("regroup", "regroup"),
    }[task_type]
    required = sum(int(r["count"]) for r in op_dict.get("composition_requirements", ())) or 1
    owned = list(tag_pool)[: (assigned_n if assigned_n is not None else required)]
    entry: dict[str, Any] = {
        "operation_id": op_dict["operation_id"],
        "generation": op_dict["generation"],
        "requested_task_type": task_type,
        "task_type": canonical[0],
        "squad_order": canonical[1],
        "status": "RECEIVED",
        "blocked_reason": "",
        "received_frame": frames["received"],
        "assigned_frame": 0,
        "submitted_frame": 0,
        "last_action_frame": 0,
        "assigned_unit_tags": [],
        "assigned_count": 0,
        "max_home_distance": 0.0,
        "engaged": False,
        "completed": False,
        "family_evidence": [],
    }
    if stage.startswith("blocked:"):
        entry["status"] = "BLOCKED"
        entry["blocked_reason"] = stage.split(":", 1)[1]
        return entry
    if stage == "waiting":
        entry.update(status="WAITING_FOR_UNITS", blocked_reason="composition_assignment_pending", squad_order="idle")
        return entry
    if stage == "cancelled":
        entry.update(status="CANCELLED", blocked_reason="cancelled_by_policy", squad_order="none", last_action_frame=frames["acted"], completed=True)
        return entry
    if stage == "expired":
        entry.update(status="EXPIRED", blocked_reason="policy_inactive_or_expired")
        return entry
    if stage == "stale":
        entry["generation"] = max(1, op_dict["generation"] - 1)
        return entry
    if stage == "objective":
        entry.update(
            status="COMPLETED",
            blocked_reason="objective_observed_complete",
            assigned_frame=frames["assigned"],
            submitted_frame=frames["ordered"],
            last_action_frame=frames["acted"],
            completed=True,
            max_home_distance=6.7,
        )
        rows = _family_rows(op_dict, update_id, owned, "acted", frames)
        for row in rows:
            row["effect_kind"], row["effect_count"], row["effect_unit_tags"], row["effect_frame"] = "", 0, [], 0
            row["blocker_manager"], row["blocker"] = "OperationDirector", "objective_observed_complete"
        entry["family_evidence"] = rows
        return entry
    rank = _ORDER.index(stage)
    if rank >= 1:
        entry.update(status="ASSIGNED", assigned_frame=frames["assigned"], assigned_unit_tags=owned, assigned_count=len(owned))
    if rank >= 2:
        entry.update(status="SUBMITTED", submitted_frame=frames["ordered"])
    if rank >= 3:
        entry.update(last_action_frame=frames["acted"])
    if rank >= 4:
        entry.update(status="MOVING", max_home_distance=30.0)
    if rank >= 5:
        entry.update(status="COMPLETED", completed=True)
    entry["family_evidence"] = _family_rows(op_dict, update_id, owned, _ORDER[min(rank, 4)], frames)
    return entry


def build_telemetry(update: Any, spec: Mapping[str, Any], frame: int, overlap: bool = False) -> dict[str, Any]:
    uid = update.update_id
    issued = update.issued_at_frame
    managers: dict[str, Any] = {}
    active: list[str] = []
    ops_spec = spec.get("ops", {})
    if ops_spec:
        shared = [next(_TAGS) for _ in range(8)]
        entries = []
        for operation in update.vector.operations:
            wanted = ops_spec.get(operation.operation_id)
            if wanted is None:
                continue
            pool = shared if overlap else [next(_TAGS) for _ in range(8)]
            entries.append(director_entry(operation.to_dict(), uid, issued, wanted, pool))
        managers["OperationDirector"] = {"policy_update_id": uid, "operations": entries}
        active.append(uid)
    if spec.get("macro") == "consumed":
        managers["GameCommander"] = {"update_id": uid, "policy_active": True}
        active.append(uid)
    return {"frame": frame, "managers": managers, "active_modulation_ids": active}


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


class _FailingBlackboard(MicroMachineFilesystemBlackboard):
    def publish_vector(self, *args: Any, **kwargs: Any) -> Any:
        raise OSError("blackboard directory unavailable")


class Chain:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._tmp = tempfile.TemporaryDirectory()
        self.backend = MicroMachineFilesystemBlackboard(self._tmp.name)
        self.executor = None
        self.bot = None
        if mode == "production":
            session, self.bot = build_dry_run_session()
            self.executor = session.executor
            # DemoFakeBotAI units lack stop/hold (demo_sc2.py:155); without them
            # every emergency stop is refused as insufficient_units.
            for unit in self.bot.units:
                unit.stop = lambda *a, _u=unit, **k: _u._record("stop", None)
                unit.hold_position = lambda *a, _u=unit, **k: _u._record("hold", None)

    def close(self) -> None:
        self._tmp.cleanup()

    @property
    def lifecycle(self) -> Any:
        return self.executor.direct_lifecycle

    def published_count(self) -> int:
        path = Path(self._tmp.name) / "modulation_updates.jsonl"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def orders(self) -> int:
        return len(self.bot.issued_commands) if self.bot is not None else 0

    def submit(self, step: Mapping[str, Any], index: int) -> Any:
        backend = self.backend
        if step["fail_publish"]:
            backend = _FailingBlackboard(self._tmp.name)
        kwargs: dict[str, Any] = {}
        if self.mode == "production":
            kwargs["direct_executor"] = self.executor
        else:
            kwargs["publish_non_macro_projection"] = True
        live = MicroMachineLiveTextSession(
            backend, StaticJsonPolicyModulationProvider(step["output"]), **kwargs
        )
        return live.submit_text(
            step["text"],
            current_frame=step["frame"],
            update_id=step["id"] or f"fid-{self.mode[:4]}-{index}",
        )


# ---------------------------------------------------------------------------
# Summaries and the UI claim (the executable form of ui_status_contract)
# ---------------------------------------------------------------------------

_STAGE_RANK = {name: rank for rank, name in enumerate(COMMAND_EXECUTION_STAGES)}


def _rank(stage: str) -> int:
    return _STAGE_RANK.get(stage, -1)


def _vector_ops(vector: Any) -> list[dict[str, Any]]:
    if vector is None:
        return []
    result = []
    for operation in vector.operations:
        task = operation.tactical_task
        result.append(
            {
                "operation_id": operation.operation_id,
                "generation": operation.generation,
                "task_type": task.task_type,
                "location": task.location_intent or operation.scope.location_intent,
                "count": sum(r.count for r in operation.composition_requirements),
                "min_units": task.min_units,
                "route": operation.route_intent.route_type,
                "target": operation.target_intent.target_type,
                "edit": operation.operation_edit.action,
                "completion_state": operation.lifetime.completion_state,
            }
        )
    return result


def _direct_plan_tags(result: Any) -> dict[str, list[int]]:
    tags: dict[str, list[int]] = {}
    for tool in result.tool_results:
        outer = tool.get("result")
        execution = outer.get("result") if isinstance(outer, Mapping) else None
        plan = execution.get("plan") if isinstance(execution, Mapping) else None
        if not isinstance(plan, Mapping):
            continue
        for action in plan.get("ordered_actions", ()):
            metadata = action.get("metadata", {})
            owner = str(metadata.get("operation_id", "") or "")
            if not owner:
                # Emergency stop+retreat deliberately address the same group
                # sequentially; only operation-keyed actions are "one unit one op".
                continue
            tags.setdefault(owner, []).extend(metadata.get("_direct_unit_tags", ()))
    return tags


def user_reasons(result: Any) -> list[str]:
    """The user-displayable reason fields, in the order a UI should consult them."""

    reasons: list[str] = []
    cr = result.compile_result
    if cr.refusal_reason:
        reasons.append(cr.refusal_reason)
    if cr.clarification_prompt:
        reasons.append(cr.clarification_prompt)
    queue = result.command_queue or {}
    outcome = queue.get("direct_outcome")
    if isinstance(outcome, Mapping):
        reasons.extend(str(item) for item in outcome.get("reasons", ()))
    failure = queue.get("failure")
    if isinstance(failure, Mapping) and failure.get("reason"):
        reasons.append(str(failure["reason"]))
    return list(dict.fromkeys(reasons))


def summarize(chain: Chain, result: Any, before: Mapping[str, int]) -> dict[str, Any]:
    queue = result.command_queue or {}
    cr = result.compile_result
    vector = result.update.vector if result.update is not None else cr.vector
    summary = {
        "status": result.status.value,
        "ok": result.ok,
        "consumption": result.consumption_status.value,
        "compile": {
            "status": cr.status.value,
            "refusal_reason": cr.refusal_reason,
            "clarification_prompt": cr.clarification_prompt,
            "warnings": list(cr.warnings),
        },
        "published": result.update is not None,
        "update_id": result.update.update_id if result.update is not None else "",
        "layers": list(queue.get("active_command_layers", ())),
        "layer_action": queue.get("layer_action", ""),
        "ops": _vector_ops(vector),
        "ttl_seconds": vector.ttl_seconds if vector is not None else None,
        "macro_biases": sorted(vector.production.queue_biases.to_dict()) if vector is not None else [],
        "emergency_flags": (
            {
                key: getattr(vector.emergency, key)
                for key in ("force_retreat", "cancel_attacks", "hold_position")
            }
            if vector is not None
            else {}
        ),
        "direct_outcome": queue.get("direct_outcome"),
        "failure": queue.get("failure"),
        "tool_names": [tool["name"] for tool in result.tool_results],
        "direct_tags_by_operation": _direct_plan_tags(result),
        "reasons": user_reasons(result),
        "blackboard_writes": chain.published_count() - before["writes"],
        "sc2_orders_issued": chain.orders() - before["orders"],
    }
    if chain.executor is not None:
        summary["leases"] = {
            command_id: [lease.state.value, lease.release_reason]
            for command_id, lease in chain.lifecycle._leases.items()
        }
        summary["owned_tags"] = sorted(
            {tag for lease in chain.lifecycle.active_leases() for tag in lease.owned_unit_tags}
        )
    return summary


def ui_claim(summary: Mapping[str, Any], reports: Sequence[Any], legacy: Any, mode: str) -> dict[str, Any]:
    """What the UI may truthfully say, derived ONLY from evidence-layer output.

    ``assigned/ordered/effect_observed`` use ``achieved_stage`` (prefix semantics),
    never the legacy ``state`` (which names the first MISSING stage) and never
    ``status``/``ok`` (which only describe publication).
    """

    understood = summary["compile"]["status"] == "compiled"
    claim: dict[str, Any] = {
        "heard": True,
        "understood": understood,
        "assigned": False,
        "ordered": False,
        "effect_observed": False,
        "simulated": False,
        "blocked": False,
        "reasons": list(summary["reasons"]),
        "operations": [],
        "label": "",
    }
    if not understood:
        claim["blocked"] = True
        claim["label"] = summary["compile"]["status"]
        return claim
    per_op = []
    for report in reports:
        document = report.to_dict()
        stage = document["achieved_stage"]
        blocked = bool(document["failed"] or document["expired"] or document["state"] in {"blocked", "cancelled"})
        per_op.append(
            {
                "operation_id": document["operation_id"],
                "state": document["state"],
                "achieved_stage": stage,
                "assigned": _rank(stage) >= _rank("queued_or_assigned"),
                "ordered": _rank(stage) >= _rank("order_issued"),
                "effect_observed": _rank(stage) >= _rank("effect_observed"),
                "blocked": blocked,
                "reason": document["blocker_reason"],
                "shortfall": document["shortfall"],
            }
        )
    if legacy is not None:
        document = legacy.to_dict()
        stage = document["achieved_stage"]
        per_op.append(
            {
                "operation_id": "",
                "state": document["state"],
                "achieved_stage": stage,
                "assigned": _rank(stage) >= _rank("queued_or_assigned"),
                "ordered": _rank(stage) >= _rank("order_issued"),
                "effect_observed": _rank(stage) >= _rank("effect_observed"),
                "blocked": bool(document["failed"] or document["expired"]),
                "reason": document["blocker_reason"],
                "shortfall": {},
            }
        )
    claim["operations"] = per_op
    if per_op:
        claim["assigned"] = all(item["assigned"] for item in per_op)
        claim["ordered"] = all(item["ordered"] for item in per_op)
        claim["effect_observed"] = all(item["effect_observed"] for item in per_op)
        claim["blocked"] = any(
            item["blocked"]
            or (
                item["shortfall"]
                and item["shortfall"].get("assigned", 0) > 0
                and not item["shortfall"].get("allow_partial", True)
            )
            for item in per_op
        )
        for item in per_op:
            if item["reason"]:
                claim["reasons"].append(item["reason"])
            if item["shortfall"].get("reason"):
                claim["reasons"].append(item["shortfall"]["reason"])
    if mode == "production" and summary["direct_outcome"]:
        outcome = summary["direct_outcome"]
        executed = outcome["status"] in {"executed", "released"}
        claim["simulated"] = executed and DIRECT_RUNTIME_KIND != "live"
        claim["ordered"] = executed and DIRECT_RUNTIME_KIND == "live"
        claim["blocked"] = outcome["status"] not in {"executed", "released", "planned"}
    claim["reasons"] = list(dict.fromkeys(claim["reasons"]))
    claim["label"] = (
        "blocked" if claim["blocked"]
        else "effect_observed" if claim["effect_observed"]
        else "ordered" if claim["ordered"]
        else "simulated" if claim["simulated"]
        else "assigned" if claim["assigned"]
        else "published" if summary["published"]
        else "understood"
    )
    return claim


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def _requested_counts(output: Mapping[str, Any]) -> dict[str, int]:
    """User-intended counts per operation id ('' for top-level), valid ints only."""

    counts: dict[str, int] = {}
    for operation in output.get("operations", ()) or ():
        total = 0
        for requirement in operation.get("composition_requirements", ()):
            value = requirement.get("count")
            if type(value) is int and value > 0:
                total += value
        if total:
            counts[operation["operation_id"]] = total
    return counts


def _violation(case: str, step: int, mode: str, invariant: str, detail: str) -> dict[str, Any]:
    return {"case": case, "step": step, "mode": mode, "invariant": invariant, "detail": detail}


def check_step(case: Mapping[str, Any], index: int, mode: str, step: Mapping[str, Any], summary: Mapping[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    cid = case["id"]
    out: list[dict[str, Any]] = []
    # I1 no silent unit-count downgrade ---------------------------------
    if summary["compile"]["status"] == "compiled":
        requested = _requested_counts(step["output"])
        compiled = {item["operation_id"]: item["count"] for item in summary["ops"]}
        for op_id, wanted in requested.items():
            got = compiled.get(op_id)
            surfaced = any("count" in w for w in summary["compile"]["warnings"])
            if got is not None and got != wanted and not surfaced:
                out.append(_violation(cid, index, mode, "no_silent_count_downgrade", f"{op_id}: asked {wanted}, compiled {got}"))
        # I11 no silent ttl clamp
        asked_ttl = step["output"].get("ttl_seconds")
        if type(asked_ttl) is int and summary["ttl_seconds"] is not None and asked_ttl != summary["ttl_seconds"]:
            if not any("ttl_seconds" in w for w in summary["compile"]["warnings"]):
                out.append(_violation(cid, index, mode, "no_silent_clamp", f"ttl asked {asked_ttl}, applied {summary['ttl_seconds']}"))
    # I7 clarification / refusal never publishes ------------------------
    if summary["compile"]["status"] in {"clarification_required", "refused"}:
        if summary["published"] or summary["blackboard_writes"] or summary["sc2_orders_issued"] or summary["tool_names"]:
            out.append(_violation(cid, index, mode, "clarification_never_publishes", str({k: summary[k] for k in ("published", "blackboard_writes", "sc2_orders_issued", "tool_names")})))
    # I8 refusal reasons are displayable ---------------------------------
    outcome = summary["direct_outcome"] or {}
    needs_reason = (
        summary["status"] in {"refused", "clarification_required", "publish_failed", "direct_failed"}
        or outcome.get("status") in {"partial", "refused", "conflict", "runtime_not_attached", "no_executable_action"}
    )
    if needs_reason and not summary["reasons"]:
        out.append(_violation(cid, index, mode, "refusal_reason_displayable", f"status={summary['status']} outcome={outcome.get('status')}"))
    # I3 one unit one operation (Direct plan) -----------------------------
    seen: dict[int, str] = {}
    for owner, tags in summary["direct_tags_by_operation"].items():
        for tag in tags:
            if tag in seen and seen[tag] != owner:
                out.append(_violation(cid, index, mode, "one_unit_one_operation", f"tag {tag} ordered by both {seen[tag]} and {owner}"))
                break
            seen[tag] = owner
    # I2 same id upserts / different ids parallel (projection) -------------
    if mode == "projection" and summary["published"] and state.get("track_ops", True):
        high = state.setdefault("high_water", {})
        live = state.setdefault("live_ids", set())
        incoming = {item["operation_id"]: item for item in _vector_ops_from_output(step["output"])}
        ops = summary["ops"]
        ids = [item["operation_id"] for item in ops]
        if len(ids) != len(set(ids)):
            out.append(_violation(cid, index, mode, "same_id_upserts_diff_ids_parallel", f"duplicate ids {ids}"))
        if incoming:
            expected = (set(live) | set(incoming)) if summary["layer_action"] in {"upsert_operations", "activate_layer", "merge_cross_layer", "supersede_same_layer"} else set(incoming)
            if summary["layer_action"] == "supersede_same_layer":
                expected = set(incoming) | set(live) if state.get("upsert_expected", True) else set(incoming)
            if set(ids) != expected:
                out.append(_violation(cid, index, mode, "same_id_upserts_diff_ids_parallel", f"ids {sorted(ids)} != expected {sorted(expected)}"))
            for item in ops:
                terminal = item["completion_state"] in {"cancelled", "completed", "expired", "failed", "superseded"}
                if item["operation_id"] in incoming and not terminal:
                    want = high.get(item["operation_id"], 0) + 1
                    if item["generation"] != want:
                        out.append(_violation(cid, index, mode, "same_id_upserts_diff_ids_parallel", f"{item['operation_id']} generation {item['generation']} != {want}"))
                if item["operation_id"] in incoming:
                    high[item["operation_id"]] = max(high.get(item["operation_id"], 0), item["generation"])
            live.clear()
            live.update(
                item["operation_id"] for item in ops if item["completion_state"] not in {"cancelled", "completed", "expired", "failed", "superseded"}
            )
    # I9 macro persistence / supersession -----------------------------------
    if summary["published"] and mode == "projection":
        layer = step["output"].get("command_layer")
        if layer == "macro":
            state["macro_keys"] = set(summary["macro_biases"])
        elif state.get("macro_keys") and layer in {"operation", "micro"} and not state.get("macro_expected_gone"):
            lost = state["macro_keys"] - set(summary["macro_biases"])
            if lost:
                out.append(_violation(cid, index, mode, "macro_persists_until_superseded", f"lost {sorted(lost)}"))
    # I4 emergency preempts only affected layers ------------------------------
    if mode == "projection" and summary["published"] and step["output"].get("override_level") == "emergency":
        previous = set(state.get("last_layers", ()))
        affected = {"operation", "micro"}
        survivors_expected = previous - affected - {"emergency"}
        lost = survivors_expected - set(summary["layers"])
        if lost:
            out.append(_violation(cid, index, mode, "emergency_preempts_only_affected_layers", f"layers lost {sorted(lost)} (kept {summary['layers']})"))
        if state.get("macro_keys") and not (state["macro_keys"] & set(summary["macro_biases"])):
            out.append(_violation(cid, index, mode, "emergency_preempts_only_affected_layers", f"macro standing order {sorted(state['macro_keys'])} erased by emergency"))
            state["macro_expected_gone"] = True
    if mode == "production" and step["output"].get("override_level") == "emergency" and step.get("worker_job"):
        if summary["leases"].get("fid-worker-job", ["", ""])[0] not in {"pending", "active"}:
            out.append(_violation(cid, index, mode, "emergency_preempts_only_affected_layers", "emergency released a worker (non-combat) lease"))
    if summary["published"]:
        state["last_layers"] = summary["layers"]
    return out


def _vector_ops_from_output(output: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"operation_id": o["operation_id"]} for o in output.get("operations", ()) or ()]


def check_expectations(case: Mapping[str, Any], index: int, mode: str, step: Mapping[str, Any], summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    wanted = step["expect"] if mode == "projection" else step["expect_production"]
    problems = []
    for key, value in wanted.items():
        actual = _lookup(summary, key)
        if actual != value:
            problems.append(_violation(case["id"], index, mode, "expectation_mismatch", f"{key}: expected {value!r}, got {actual!r}"))
    return problems


def _lookup(summary: Mapping[str, Any], dotted: str) -> Any:
    value: Any = summary
    for part in dotted.split("."):
        if part.isdigit():
            value = value[int(part)] if isinstance(value, Sequence) and int(part) < len(value) else None
        elif isinstance(value, Mapping):
            value = value.get(part)
        elif part == "len" and isinstance(value, Sequence):
            return len(value)
        else:
            return None
    return value


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------


def _fastpath(case: Mapping[str, Any]) -> dict[str, Any] | None:
    spec = case.get("fastpath")
    if not spec:
        return None
    from starcraft_commander.web_gui import (
        _micromachine_emergency_safety_output,
        _micromachine_request_is_emergency,
    )

    triggered = _micromachine_request_is_emergency(case["utterance"], None)
    flags = {}
    if triggered:
        emergency_block = _micromachine_emergency_safety_output(case["utterance"])["emergency"]
        flags = {key: bool(emergency_block.get(key)) for key in ("force_retreat", "cancel_attacks", "hold_position")}
    return {"triggered": triggered, "flags": flags, "expected": spec}


def run_projection_or_production(case: Mapping[str, Any], mode: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chain = Chain(mode)
    violations: list[dict[str, Any]] = []
    steps_out: list[dict[str, Any]] = []
    state: dict[str, Any] = {}
    try:
        for index, step in enumerate(case["steps"]):
            if step["worker_job"] and chain.executor is not None:
                chain.lifecycle.pending(
                    command_id="fid-worker-job",
                    issued_at_frame=step["frame"],
                    ttl_seconds=600,
                    owned_unit_tags=(1,),
                    command_metadata={
                        "actions": [
                            {"action_type": "build_structure", "subject": "SUPPLYDEPOT", "target": "self_main", "count": 1, "metadata": {}}
                        ]
                    },
                )
            before = {"writes": chain.published_count(), "orders": chain.orders()}
            result = chain.submit(step, index)
            summary = summarize(chain, result, before)
            reports: list[Any] = []
            legacy = None
            spec = step["telemetry"]
            if mode == "projection" and result.update is not None:
                update = result.update.to_dict()
                telemetry = build_telemetry(result.update, spec or {}, step["frame"] + 60, overlap=bool(spec and spec.get("overlap")))
                if spec is not None:
                    if result.update.vector.operations:
                        reports = list(
                            classify_micromachine_operation_executions(
                                latest_update=update, latest_telemetry=telemetry, latest_frame=step["frame"] + 60
                            )
                        )
                    else:
                        legacy = classify_micromachine_command_execution(
                            latest_update=update, latest_telemetry=telemetry, latest_frame=step["frame"] + 60
                        )
                else:
                    # No telemetry yet: the evidence layer sees only the publication.
                    if result.update.vector.operations:
                        reports = list(
                            classify_micromachine_operation_executions(
                                latest_update=update,
                                latest_telemetry={"frame": step["frame"] + 1, "managers": {}},
                                latest_frame=step["frame"] + 1,
                            )
                        )
                    else:
                        legacy = classify_micromachine_command_execution(
                            latest_update=update,
                            latest_telemetry={"frame": step["frame"] + 1, "managers": {}},
                            latest_frame=step["frame"] + 1,
                        )
            if step["tick"] is not None and chain.executor is not None:
                chain.executor.tick_direct_commands(step["tick"])
                summary["leases_after_tick"] = {
                    command_id: [lease.state.value, lease.release_reason]
                    for command_id, lease in chain.lifecycle._leases.items()
                }
                summary["owned_tags_after_tick"] = sorted(
                    {tag for lease in chain.lifecycle.active_leases() for tag in lease.owned_unit_tags}
                )
            summary["ui"] = ui_claim(summary, reports, legacy, mode)
            step_violations = check_step(case, index, mode, step, summary, state)
            step_violations += check_expectations(case, index, mode, step, summary)
            violations.extend(step_violations)
            steps_out.append({"index": index, "text": step["text"], "frame": step["frame"], mode: summary})
    finally:
        chain.close()
    return steps_out, violations


def run_case(case: Mapping[str, Any]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": case["id"],
        "utterance": case["utterance"],
        "ask": case["ask"],
        "kind": case.get("kind", "replay"),
        "steps": [],
        "violations": [],
        "error": "",
    }
    try:
        if case.get("kind") == "recorded":
            return run_recorded(case, record)
        merged: dict[int, dict[str, Any]] = {}
        for mode in case.get("modes", ("projection", "production")):
            steps, violations = run_projection_or_production(case, mode)
            record["violations"].extend(violations)
            for item in steps:
                merged.setdefault(item["index"], {"index": item["index"], "text": item["text"], "frame": item["frame"]}).update(item)
        record["steps"] = [merged[key] for key in sorted(merged)]
        fastpath = _fastpath(case)
        if fastpath is not None:
            record["fastpath"] = fastpath
            expected = fastpath["expected"]
            if fastpath["triggered"] != expected["triggered"]:
                record["violations"].append(_violation(case["id"], -1, "router", "emergency_fastpath_matches_ask", f"triggered={fastpath['triggered']} expected={expected['triggered']}"))
            elif fastpath["triggered"]:
                for key, wanted in expected["flags"].items():
                    if fastpath["flags"].get(key) != wanted:
                        record["violations"].append(_violation(case["id"], -1, "router", "emergency_fastpath_matches_ask", f"{key}={fastpath['flags'].get(key)} but the user asked {wanted}"))
    except Exception as error:  # noqa: BLE001 - reported, never hidden
        record["error"] = f"{type(error).__name__}: {error}"
    return record


def run_recorded(case: Mapping[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    scenario = case["scenario"]
    update = json.loads((FIXTURE_DIR / scenario / "latest_modulation.json").read_text())
    telemetry = json.loads((FIXTURE_DIR / scenario / "latest_telemetry.json").read_text())
    reports = []
    legacy = None
    if update["vector"].get("operations"):
        reports = list(classify_micromachine_operation_executions(latest_update=update, latest_telemetry=telemetry))
    else:
        legacy = classify_micromachine_command_execution(latest_update=update, latest_telemetry=telemetry)
    summary = {
        "status": "recorded",
        "ok": None,
        "compile": {"status": "compiled", "refusal_reason": "", "clarification_prompt": "", "warnings": []},
        "published": True,
        "direct_outcome": None,
        "reasons": [],
        "ops": [],
    }
    claim = ui_claim(summary, reports, legacy, "projection")
    observed = {
        item["operation_id"] or "<legacy>": {"state": item["state"], "achieved_stage": item["achieved_stage"]}
        for item in claim["operations"]
    }
    step = {
        "index": 0,
        "text": f"recorded {scenario}",
        "frame": telemetry["frame"],
        "projection": {**summary, "ui": claim, "recorded_states": observed},
    }
    record["steps"] = [step]
    for key, value in case["expect_states"].items():
        if observed.get(key) != value:
            record["violations"].append(_violation(case["id"], 0, "recorded", "expectation_mismatch", f"{key}: expected {value}, got {observed.get(key)}"))
    for item in claim["operations"]:
        if item["effect_observed"] and not (item["assigned"] and item["ordered"]):
            record["violations"].append(_violation(case["id"], 0, "recorded", "published_never_effect", str(item)))
    return record


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------

M = "TERRAN_MARINE"
T = "TERRAN_SIEGETANK"
V = "TERRAN_VIKINGFIGHTER"

CASES: list[dict[str, Any]] = [
    # --- parallel operations ------------------------------------------------
    C("par-scout-attack", "마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격",
      "Two independent concurrent operations: 2 marines scout enemy main, 4 marines attack via the right flank.",
      [S("마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격",
         operations(op("scout-a", "scout_with_units", "enemy_main", 2, role="scout"),
                    op("atk-b", "pressure_with_main_army", "enemy_natural", 4, route="flank_right"), goal="2 scout 4 attack"),
         100, telemetry={"ops": {"scout-a": "effect", "atk-b": ("assigned", 2)}},
         expect={"ops.len": 2, "layer_action": "activate_layer", "ui.assigned": False, "ui.blocked": True},
         expect_production={"status": "direct_executed"})]),
    C("par-attack-defend-en", "Send 4 marines to attack the natural and keep 2 at the ramp",
      "Attack and defend operations run concurrently with their own counts.",
      [S("Send 4 marines to attack the natural and keep 2 at the ramp",
         operations(op("atk-n", "pressure_with_main_army", "enemy_natural", 4), op("def-ramp", "defend_with_units", "ramp", 2, role="defensive_hold")),
         100, telemetry={"ops": {"atk-n": "effect", "def-ramp": "effect"}},
         expect={"ops.len": 2, "ui.effect_observed": True})]),
    C("par-three-ops-ground", "마린 2기 정찰, 마린 3기 램프 방어, 마린 4기 압박",
      "Three concurrent operations with the same unit type (needs 9 distinct marines).",
      [S("마린 2기 정찰, 마린 3기 램프 방어, 마린 4기 압박",
         operations(op("sc", "scout_with_units", "enemy_main", 2), op("df", "defend_with_units", "ramp", 3), op("pr", "pressure_with_main_army", "enemy_natural", 4)),
         100, expect={"ops.len": 3})]),
    C("par-mixed-units", "마린 2기 정찰, 탱크 1기 램프 방어, 바이킹 2기 공중 견제",
      "Three operations over different unit families; no unit competes with another.",
      [S("마린 2기 정찰, 탱크 1기 램프 방어, 바이킹 2기 공중 견제",
         operations(op("sc", "scout_with_units", "enemy_main", 2), op("df", "defend_with_units", "ramp", 1, unit=T, role="siege_support"),
                    op("hr", "harass_with_units", "enemy_mineral_line", 2, unit=V, role="anti_air")),
         100, telemetry={"ops": {"sc": "effect", "df": "assigned", "hr": "ordered"}},
         expect={"ops.len": 3})]),
    C("par-flank-split", "왼쪽으로 3기 우회 공격, 오른쪽으로 3기 우회 공격",
      "Two attack operations with opposite flank routes.",
      [S("왼쪽으로 3기 우회 공격, 오른쪽으로 3기 우회 공격",
         operations(op("fl", "pressure_with_main_army", "enemy_natural", 3, route="flank_left"), op("fr", "pressure_with_main_army", "enemy_natural", 3, route="flank_right")),
         100, expect={"ops.len": 2})]),
    # --- upsert / reinforce / retarget / cancel -----------------------------
    C("same-id-retarget", "공격 목표를 본진으로 바꿔",
      "The existing attack operation changes target; it must stay ONE operation, generation 2.",
      [S("마린 4기로 앞마당 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100, modes=None) if False else
       S("마린 4기로 앞마당 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100, expect={"ops.0.generation": 1}),
       S("공격 목표를 본진으로 바꿔", operations(op("atk", "pressure_with_main_army", "enemy_main", 4, edit={"action": "retarget"})), 160,
         expect={"ops.len": 1, "ops.0.generation": 2, "ops.0.location": "enemy_main", "layer_action": "upsert_operations"},
         expect_production={"status": "direct_executed"})]),
    C("reinforce-count", "공격 부대에 마린 2기 더",
      "Same operation, count 4 -> 6, generation 2, no second operation.",
      [S("마린 4기로 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100),
       S("공격 부대에 마린 2기 더", operations(op("atk", "pressure_with_main_army", "enemy_natural", 6, edit={"action": "reinforce"})), 150,
         expect={"ops.len": 1, "ops.0.count": 6, "ops.0.generation": 2})]),
    C("different-id-parallel", "마린 2기 정찰 보내, 그리고 따로 4기 공격",
      "A second command with a NEW id must run alongside the first, not replace it.",
      [S("마린 2기 정찰 보내", operations(op("sc", "scout_with_units", "enemy_main", 2)), 100),
       S("그리고 따로 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 140,
         expect={"ops.len": 2, "layer_action": "upsert_operations"})]),
    C("cancel-one-keep-other", "공격만 취소하고 정찰은 계속해",
      "Cancel exactly one operation; the scout operation keeps running.",
      [S("정찰과 공격", operations(op("sc", "scout_with_units", "enemy_main", 2), op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100),
       S("공격만 취소하고 정찰은 계속해",
         operations(op("atk", "pressure_with_main_army", "enemy_natural", 4, edit={"action": "cancel"}, state="cancelled", conditions=("cancelled_by_user",))), 160,
         expect={"ops.len": 2, "layer_action": "upsert_operations"},
         expect_production={"status": "direct_failed"}),
       S("정찰 상황", operations(op("sc", "scout_with_units", "enemy_main", 2)), 220, expect={"ops.len": 1})]),
    C("restart-after-cancel", "취소했던 공격 다시 시작해",
      "Restart a cancelled operation under the same id: new generation, not generation reuse.",
      [S("공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100),
       S("공격 취소", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4, edit={"action": "cancel"}, state="cancelled", conditions=("cancelled_by_user",))), 140),
       S("취소했던 공격 다시 시작해", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4, edit={"action": "restart"})), 200,
         expect={"ops.len": 1, "ops.0.generation": 2})]),
    C("transfer-between-ops", "정찰하던 마린 1기를 공격 부대로 보내",
      "Move one marine between two named operations (before/after counts change by exactly one).",
      [S("정찰2 공격3", operations(op("sc", "scout_with_units", "enemy_main", 2), op("atk", "pressure_with_main_army", "enemy_natural", 3)), 100),
       S("정찰하던 마린 1기를 공격 부대로", operations(
           op("sc", "scout_with_units", "enemy_main", 1, edit={"action": "transfer_out", "counterpart_operation_id": "atk", "unit_selection": [{"unit_type": M, "count": 1, "role": "frontline"}]}),
           op("atk", "pressure_with_main_army", "enemy_natural", 4, edit={"action": "transfer_in", "counterpart_operation_id": "sc", "unit_selection": [{"unit_type": M, "count": 1, "role": "frontline"}]})), 160,
         expect={"compile.status": "refused", "reasons.0": "transfer operation edits require a before_composition snapshot."})]),
    # --- unit-count shortfall / downgrade --------------------------------------
    C("shortfall-assigned", "마린 4기로 공격해",
      "User asked for exactly 4; the director assigned only 1 -> shortfall must be surfaced, never 'assigned'.",
      [S("마린 4기로 공격해", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100,
         telemetry={"ops": {"atk": ("effect", 1)}},
         expect={"ui.assigned": False, "ui.effect_observed": False, "ui.operations.0.shortfall.missing": 3})]),
    C("shortfall-direct", "마린 10기 공격",
      "Only 6 marines exist in the dry-run world: the Direct path must report partial fulfilment.",
      [S("마린 10기 공격", operations(op("big", "pressure_with_main_army", "enemy_natural", 10)), 100,
         expect={"ops.0.count": 10},
         expect_production={"direct_outcome.status": "partial", "direct_outcome.shortfalls.0.issued": 6, "ui.blocked": True})]),
    C("count-string", "마린 \"둘\" 정찰", "A non-integer count is refused with a reason (not guessed as 1).",
      [S("마린 둘 정찰", operations(op("sc", "scout_with_units", "enemy_main", "2", role="scout")), 100,
         expect={"compile.status": "refused", "published": False})]),
    C("count-zero", "마린 0기 정찰", "Zero units is refused, not silently promoted to 1.",
      [S("마린 0기 정찰", operations(op("sc", "scout_with_units", "enemy_main", 0, role="scout")), 100, expect={"compile.status": "refused"})]),
    C("count-over-limit", "마린 300기 공격", "Counts above the DSL maximum (200) are refused, not clamped.",
      [S("마린 300기 공격", operations(op("big", "pressure_with_main_army", "enemy_natural", 300)), 100, expect={"compile.status": "refused"})]),
    C("count-merge-exact", "마린 2기랑 마린 3기로 공격", "Same unit+role listed twice merges to exactly 5.",
      [S("마린 2기랑 마린 3기로 공격",
         {"goal": "g", "command_layer": "operation", "operations": [
             {"operation_id": "atk", "tactical_task": {"task_type": "pressure_with_main_army", "unit_classes": [M], "location_intent": "enemy_natural"},
              "composition_requirements": [{"unit_type": M, "count": 2, "role": "frontline"}, {"unit_type": M, "count": 3, "role": "frontline"}]}]},
         100, expect={"ops.0.count": 5})]),
    C("ttl-clamp", "계속 마린 생산해 (5000초)", "A requested duration beyond the DSL limit must be reported as clamped.",
      [S("계속 마린 생산해", macro(ttl=5000), 100, expect={"ttl_seconds": 900})]),
    # --- ownership ---------------------------------------------------------------
    C("ownership-conflict-telemetry", "마린 2기 정찰, 마린 2기 공격",
      "Telemetry reports the SAME unit tags in two operations: both must be reported blocked with a reason.",
      [S("마린 2기 정찰, 2기 공격", operations(op("sc", "scout_with_units", "enemy_main", 2), op("atk", "pressure_with_main_army", "enemy_natural", 2)), 100,
         telemetry={"overlap": True, "ops": {"sc": "effect", "atk": "effect"}},
         expect={"ui.blocked": True, "ui.effect_observed": False, "ui.operations.0.state": "blocked"}, )], modes=("projection",)),
    C("ownership-direct-conflict", "마린 2기 공격해, 마린 2기 또 공격해",
      "Second Direct command over the same marines is refused with a displayable reason.",
      [S("마린 2기 공격해", operations(op("a1", "pressure_with_main_army", "enemy_natural", 2)), 100),
       S("마린 2기 또 공격해", operations(op("a2", "pressure_with_main_army", "enemy_main", 2)), 120,
         expect_production={"status": "direct_failed", "direct_outcome.status": "conflict"})], modes=("production",)),
    # --- macro persistence ----------------------------------------------------------
    C("macro-persists-through-op", "마린 계속 생산하고 2기는 정찰",
      "The standing production order survives a later scout command.",
      [S("마린 계속 생산", macro(standing=True), 100),
       S("2기는 정찰", operations(op("sc", "scout_with_units", "enemy_main", 2)), 140, expect={"layers": ["macro", "operation"], "macro_biases.0": "TERRAN_MARINE"})]),
    C("macro-supersede", "마린 말고 탱크 생산해", "A newer macro command replaces the old production focus, not merges forever.",
      [S("마린 생산", macro(M), 100), S("마린 말고 탱크 생산해", macro(T, goal="탱크 생산"), 140, expect={"macro_biases": ["TERRAN_SIEGETANK"], "layer_action": "supersede_same_layer"})]),
    C("macro-default-ttl-expiry", "마린 생산해 (이후 한참 뒤 정찰)",
      "A production macro is until_cancelled by design (no TTL): it is still merged into a command issued at frame 40000. Only an explicit cancel ends it (see macro-cancel-no-resurrection).",
      [S("마린 생산", macro(M), 100), S("정찰", operations(op("sc", "scout_with_units", "enemy_main", 2)), 40_000, expect={"layers": ["macro", "operation"]})]),
    C("macro-cancel-no-resurrection", "마린 생산 그만 (이후 정찰)",
      "A production order the user explicitly cancelled must not be merged back into the next command.",
      [S("마린 계속 생산", macro(standing=True), 100),
       S("마린 생산 그만", {"goal": "마린 생산 그만", "command_layer": "macro", "lifetime": {"mode": "ttl", "completion_state": "cancelled", "completion_conditions": ["cancelled_by_user"]}}, 140,
         expect={"layers": [], "macro_biases": []}),
       S("정찰", operations(op("sc", "scout_with_units", "enemy_main", 2)), 200, expect={"layers": ["operation"], "macro_biases": []})], modes=("projection",)),
    C("macro-standing-survives-gap", "마린 계속 생산하고 한참 뒤 정찰",
      "A standing_order macro survives past the default TTL.",
      [S("마린 계속 생산", macro(standing=True), 100), S("정찰", operations(op("sc", "scout_with_units", "enemy_main", 2)), 40_000, expect={"layers": ["macro", "operation"]})]),
    # --- emergency ------------------------------------------------------------------------
    C("emergency-retreat-ko", "즉시 후퇴", "Whole-army retreat now.",
      [S("즉시 후퇴", emergency(), 100, expect={"layers": ["emergency"], "emergency_flags.force_retreat": True}, expect_production={"status": "direct_executed"})],
      fastpath={"triggered": True, "flags": {"force_retreat": True, "cancel_attacks": True}}),
    C("emergency-cancel-attack-only", "공격 취소", "ONLY stop the attacks; do not send the army home.",
      [S("공격 취소", emergency(retreat=False, cancel=True), 100, expect={"emergency_flags.force_retreat": False})],
      fastpath={"triggered": True, "flags": {"force_retreat": False, "cancel_attacks": True}}),
    C("emergency-scoped-marines-only", "마린만 후퇴해, 탱크는 그대로",
      "Retreat only marines; tanks keep their orders. The regex fast path cannot express scope.",
      [S("마린만 후퇴", operations(op("rt", "regroup_with_units", "home", 4)), 100)],
      fastpath={"triggered": False, "flags": {}}),
    C("emergency-keeps-macro", "마린 계속 생산 중에 후퇴",
      "Retreat preempts attack/scout layers but must not erase the standing production order.",
      [S("마린 계속 생산", macro(standing=True), 100), S("공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 120),
       S("후퇴", emergency(), 160, expect={"layers": ["macro", "emergency"], "macro_biases.0": "TERRAN_MARINE", "ops.len": 0})]),
    C("emergency-preempts-direct-lease", "공격 중 후퇴", "Retreat releases the attack lease (and only combat leases).",
      [S("마린 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100),
       S("후퇴", emergency(), 140, worker_job=True,
         expect_production={"status": "direct_executed", "leases.fid-prod-0": ["cancelled", "emergency_preempted:fid-prod-1"]})], modes=("production",)),
    C("emergency-then-macro", "후퇴 뒤에 다시 마린 생산", "A macro command after an emergency publishes normally.",
      [S("후퇴", emergency(), 100), S("다시 마린 생산", macro(M), 160, expect={"macro_biases": ["TERRAN_MARINE"], "status": "published"})]),
    C("emergency-hold", "그 자리에서 버텨", "Hold position only: no retreat, no cancel.",
      [S("그 자리에서 버텨", emergency(retreat=False, cancel=False, hold=True), 100, expect={"emergency_flags.hold_position": True, "emergency_flags.force_retreat": False})]),
    C("negated-retreat", "후퇴하지 마", "Negation: no emergency fast path, nothing retreats.",
      [S("후퇴하지 마", macro(M, goal="후퇴하지 말고 유지"), 100)],
      fastpath={"triggered": False, "flags": {}}),
    C("partial-retreat-split", "탱크는 후퇴하고 마린은 계속 공격",
      "Tanks retreat while marines keep attacking: two parallel operations, NOT a global emergency.",
      [S("탱크는 후퇴하고 마린은 계속 공격",
         operations(op("tk", "regroup_with_units", "home", 2, unit=T, role="siege_support"), op("mr", "pressure_with_main_army", "enemy_natural", 4)),
         100, expect={"ops.len": 2, "layers": ["operation"]})],
      fastpath={"triggered": False, "flags": {}}),
    # --- micro / ability gating --------------------------------------------------------------
    C("micro-stim", "스팀팩 써", "One bounded stim for the marines.",
      [S("스팀팩 써", micro("stimpack"), 100, expect={"layers": ["micro"]})]),
    C("micro-siege", "탱크 시즈모드", "Siege the tanks.",
      [S("탱크 시즈모드", micro("siege_mode", unit=T, location="ramp"), 100, expect={"layers": ["micro"]})]),
    C("micro-nuke", "적 본진에 핵 써", "Tactical nuke at the enemy main (needs ghost, payload, vision).",
      [S("적 본진에 핵 써", micro("tactical_nuke", unit="TERRAN_GHOST"), 100, expect={"layers": ["micro"]})]),
    C("micro-nuke-negated", "핵은 절대 쓰지 마", "Negated nuke: refused, nothing published.",
      [S("never use a tactical nuke",
         {"goal": "nuke bias", "command_layer": "macro", "production": {"queue_biases": {"TERRAN_NUKE": 0.9}}}, 100,
         expect={"compile.status": "refused", "published": False})]),
    C("micro-missing-ability", "그냥 능력 써", "execute_ability without an ability is refused with a reason.",
      [S("그냥 능력 써", micro(None), 100, expect={"compile.status": "refused"})]),
    # --- clarification / refusal ------------------------------------------------------------------
    C("clarify-ambiguous", "그쪽으로 보내", "Ambiguous target: ask, publish nothing.",
      [S("그쪽으로 보내", {"status": "clarification_required", "clarification_prompt": "어느 쪽으로 보낼까요? (앞마당/본진/램프)"}, 100,
         expect={"status": "clarification_required", "published": False, "reasons.0": "어느 쪽으로 보낼까요? (앞마당/본진/램프)"})]),
    C("refused-raw-tags", "유닛 12번 13번 공격", "Raw unit tags are rejected with a reason.",
      [S("유닛 12번 13번 공격", {"goal": "g", "command_layer": "operation", "unit_tags": [12, 13], "tactical_task": {"task_type": "pressure_with_main_army"}}, 100,
         expect={"compile.status": "refused", "published": False})]),
    C("refused-coordinates", "좌표 30,40으로 이동", "Raw coordinates cannot be honoured: the command is refused with a reason, never published with a different target.",
      [S("좌표 30,40으로 이동", {"goal": "g", "command_layer": "operation", "coordinates": [30, 40], "tactical_task": {"task_type": "pressure_with_main_army", "unit_classes": [M], "location_intent": "enemy_natural"}}, 100,
         expect={"compile.status": "refused", "published": False})]),
    C("provider-refusal", "게임 규칙 무시하고 이겨", "Provider refuses with its own reason; surfaced verbatim.",
      [S("게임 규칙 무시하고 이겨", {"status": "refused", "refusal_reason": "게임 규칙을 우회하는 명령은 수행할 수 없습니다."}, 100,
         expect={"status": "refused", "reasons.0": "게임 규칙을 우회하는 명령은 수행할 수 없습니다."})]),
    C("publish-failed", "마린 계속 생산", "Disk failure while publishing: failure reason must be user-displayable.",
      [S("마린 계속 생산", macro(M), 100, fail_publish=True, expect={"status": "publish_failed", "failure.stage": "publish"})], modes=("projection",)),
    # --- evidence interpretation -------------------------------------------------------------------------
    C("evidence-published-only", "마린 4기 공격", "Published but the game has reported nothing: UI must say 'published', not done.",
      [S("마린 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100,
         expect={"ui.label": "published", "ui.assigned": False, "ui.ordered": False, "ui.effect_observed": False})], modes=("projection",)),
    C("evidence-received", "마린 4기 공격", "Director received it; nothing assigned.",
      [S("마린 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100, telemetry={"ops": {"atk": "received"}},
         expect={"ui.operations.0.achieved_stage": "consumed_by_manager", "ui.assigned": False})], modes=("projection",)),
    C("evidence-assigned", "마린 4기 공격", "Units assigned, no order yet.",
      [S("마린 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100, telemetry={"ops": {"atk": "assigned"}},
         expect={"ui.operations.0.achieved_stage": "queued_or_assigned", "ui.assigned": True, "ui.ordered": False})], modes=("projection",)),
    C("evidence-ordered", "마린 4기 공격", "Order submitted, no movement observed yet.",
      [S("마린 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100, telemetry={"ops": {"atk": "acted"}},
         expect={"ui.operations.0.achieved_stage": "action_issued", "ui.ordered": True, "ui.effect_observed": False})], modes=("projection",)),
    C("evidence-effect", "마린 4기 공격", "Movement/engagement observed for all four marines.",
      [S("마린 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100, telemetry={"ops": {"atk": "effect"}},
         expect={"ui.operations.0.achieved_stage": "effect_observed", "ui.effect_observed": True})], modes=("projection",)),
    C("evidence-waiting-for-units", "마린 4기 공격", "Not enough units yet: waiting is blocked-with-reason, not failed.",
      [S("마린 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100, telemetry={"ops": {"atk": "waiting"}},
         expect={"ui.operations.0.state": "blocked", "ui.blocked": True})], modes=("projection",)),
    C("evidence-objective-complete", "램프 방어", "Director closed the op because the objective was observed; no effect rows -> not claimed as effect.",
      [S("램프 방어", operations(op("df", "defend_with_units", "ramp", 2)), 100, telemetry={"ops": {"df": "objective"}},
         expect={"ui.operations.0.state": "objective_observed_complete", "ui.blocked": False, "ui.effect_observed": False})], modes=("projection",)),
    C("evidence-hard-block", "마린 4기 공격", "Invalid target blocks hard with the director's reason.",
      [S("마린 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100, telemetry={"ops": {"atk": "blocked:invalid_target"}},
         expect={"ui.blocked": True, "ui.reasons.0": "invalid_target"})], modes=("projection",)),
    C("evidence-cancelled", "공격 취소", "Director confirms cancellation.",
      [S("공격 취소", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4, edit={"action": "cancel"}, state="cancelled", conditions=("cancelled_by_user",))), 100,
         telemetry={"ops": {"atk": "cancelled"}}, expect={"ui.operations.0.state": "cancelled"})], modes=("projection",)),
    C("evidence-expired", "마린 4기 공격", "Operation expired before any effect.",
      [S("마린 4기 공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100, telemetry={"ops": {"atk": "expired"}},
         expect={"ui.blocked": True})], modes=("projection",)),
    C("evidence-stale-generation", "공격 목표 변경", "Telemetry still describes generation 1 after a retarget: must not count as progress of generation 2.",
      [S("공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 4)), 100),
       S("공격 목표 변경", operations(op("atk", "pressure_with_main_army", "enemy_main", 4, edit={"action": "retarget"})), 160, telemetry={"ops": {"atk": "stale"}},
         expect={"ops.0.generation": 2, "ui.assigned": False, "ui.effect_observed": False})], modes=("projection",)),
    C("macro-evidence-consumed", "마린 계속 생산", "Macro consumed by the manager: still no production/effect claim.",
      [S("마린 계속 생산", macro(M), 100, telemetry={"macro": "consumed"},
         expect={"ui.operations.0.achieved_stage": "consumed_by_manager", "ui.effect_observed": False})], modes=("projection",)),
    # --- Direct lifecycle ------------------------------------------------------------------------------------
    C("direct-cancel-releases", "공격 취소하고 다시 공격", "Cancel releases ownership so the same marines can be re-ordered.",
      [S("공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 2)), 100),
       S("공격 취소", operations(op("atk", "pressure_with_main_army", "enemy_natural", 2, edit={"action": "cancel"}, state="cancelled", conditions=("cancelled_by_user",))), 120,
         expect_production={"direct_outcome.status": "released", "sc2_orders_issued": 0, "owned_tags": []}),
       S("다시 공격", operations(op("atk2", "pressure_with_main_army", "enemy_main", 2)), 140, expect_production={"status": "direct_executed"})], modes=("production",)),
    C("direct-retarget-supersedes", "목표를 본진으로", "Retarget supersedes the operation's own lease and re-dispatches.",
      [S("공격", operations(op("atk", "pressure_with_main_army", "enemy_natural", 2)), 100),
       S("목표를 본진으로", operations(op("atk", "pressure_with_main_army", "enemy_main", 2, edit={"action": "retarget"})), 120,
         expect_production={"status": "direct_executed", "sc2_orders_issued": 2})], modes=("production",)),
    C("direct-cancel-unknown", "없는 작전 취소", "Cancelling an operation that owns nothing is refused, not executed.",
      [S("정찰 취소", operations(op("ghost", "scout_with_units", "enemy_main", 1, edit={"action": "cancel"}, state="cancelled", conditions=("cancelled_by_user",))), 100,
         expect_production={"status": "direct_failed", "direct_outcome.reasons.0": "No active Direct command owns operation ghost.", "sc2_orders_issued": 0})], modes=("production",)),
    C("direct-lease-ttl-release", "공격 후 한참 뒤 다시 공격", "TTL expiry releases ownership so later commands are not blocked forever.",
      [S("공격", {**operations(op("atk", "pressure_with_main_army", "enemy_natural", 2)), "ttl_seconds": 10}, 100, tick=100 + 10 * 23 + 5,
         expect_production={"owned_tags_after_tick": []}),
       S("다시 공격", operations(op("atk2", "pressure_with_main_army", "enemy_main", 2)), 600, expect_production={"status": "direct_executed"})], modes=("production",)),
    # --- recorded QA30 fixtures (evidence layer only; no compile/publish) -----------------------------------
    C("rec-14-marine-scout", "(recorded) 마린 1기 정찰", "Recorded SC2 run: scout reached order but family evidence stops at action.",
      [], kind="recorded", scenario="14-marine-scout", expect_states={"14-marine-scout": {"state": "order_issued", "achieved_stage": "order_issued"}}),
    C("rec-15-parallel", "(recorded) 공격+방어 동시", "Recorded SC2 run: attack blocked by production, defense closed by objective.",
      [], kind="recorded", scenario="15-parallel-attack-defense",
      expect_states={"15-parallel-attack-defense-attack": {"state": "blocked", "achieved_stage": "effect_observed"},
                     "15-parallel-attack-defense-defense": {"state": "objective_observed_complete", "achieved_stage": "consumed_by_manager"}}),
    C("rec-16-reinforce", "(recorded) 방어+증원", "Recorded: defense moving, reinforcement waiting for units.",
      [], kind="recorded", scenario="16-defense-reinforcement",
      expect_states={"16-defense-reinforcement-defense": {"state": "blocked", "achieved_stage": "effect_observed"},
                     "16-defense-reinforcement-reinforcement": {"state": "blocked", "achieved_stage": "consumed_by_manager"}}),
    C("rec-21-cancel", "(recorded) 공격 취소", "Recorded: director cancelled the attack.",
      [], kind="recorded", scenario="21-cancel-attack", expect_states={"21-cancel-attack": {"state": "cancelled", "achieved_stage": "consumed_by_manager"}}),
    C("rec-24-retarget", "(recorded) 목표 변경", "Recorded: generation 2 submitted but 1 of 2 marines represented.",
      [], kind="recorded", scenario="24-retarget", expect_states={"24-retarget": {"state": "blocked", "achieved_stage": "consumed_by_manager"}}),
    C("rec-27-prereq-wait", "(recorded) 전차 방어 대기", "Recorded: waiting for a tank that does not exist yet.",
      [], kind="recorded", scenario="27-prerequisite-wait", expect_states={"27-prerequisite-wait": {"state": "blocked", "achieved_stage": "consumed_by_manager"}}),
    C("rec-22-reassign", "(recorded) 마린 재배치", "Recorded: destination completed, source still moving.",
      [], kind="recorded", scenario="22-reassign-marines",
      expect_states={"22-reassign-marines-destination": {"state": "objective_observed_complete", "achieved_stage": "consumed_by_manager"},
                     "22-reassign-marines-source": {"state": "moving", "achieved_stage": "consumed_by_manager"}}),
    C("rec-06-macro-marine-wave", "(recorded) 마린 웨이브", "Recorded macro: latest-only snapshot.",
      [], kind="recorded", scenario="06-marine-wave", expect_states={"<legacy>": {"state": "effect_observed", "achieved_stage": "action_issued"}}),
]


def _sanitize_cases() -> None:
    for case in CASES:
        for step in case["steps"]:
            step.pop("modes", None)


_sanitize_cases()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def run_all() -> dict[str, Any]:
    cases = [run_case(case) for case in CASES]
    violations = [v for case in cases for v in case["violations"] if v["invariant"] != "expectation_mismatch"]
    mismatches = [v for case in cases for v in case["violations"] if v["invariant"] == "expectation_mismatch"]
    return {
        "scope": "offline_python_chain_only; SC2 not available; not live evidence",
        "direct_runtime_kind": DIRECT_RUNTIME_KIND,
        "cases": cases,
        "violations": violations,
        "expectation_mismatches": mismatches,
        "case_count": len(cases),
    }


def _fmt_ui(claim: Mapping[str, Any]) -> str:
    flags = [key for key in ("assigned", "ordered", "effect_observed", "simulated", "blocked") if claim.get(key)]
    reason = f" — {claim['reasons'][0]}" if claim.get("reasons") else ""
    return f"{claim['label']} [{','.join(flags) or 'none'}]{reason}"


def to_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Intent-fidelity chain replay (generated)",
        "",
        f"Scope: {report['scope']}. Production-mode Direct runtime: `{report['direct_runtime_kind']}` (scripted fake bot).",
        "",
        f"Cases: {report['case_count']}. Open invariant violations: {len(report['violations'])}. Expectation mismatches: {len(report['expectation_mismatches'])}.",
        "",
    ]
    for case in report["cases"]:
        lines += [f"## {case['id']}", "", f"- **Utterance:** {case['utterance']}", f"- **Semantic ask:** {case['ask']}"]
        if case["error"]:
            lines.append(f"- **ERROR:** {case['error']}")
        for step in case["steps"]:
            lines.append(f"- step {step['index']} (frame {step['frame']}): `{step['text']}`")
            if "recorded_states" in step.get("projection", {}):
                lines.append(f"  - recorded evidence: {json.dumps(step['projection']['recorded_states'], ensure_ascii=False)}")
                lines.append(f"  - UI claim: {_fmt_ui(step['projection']['ui'])}")
                continue
            for mode in ("projection", "production"):
                data = step.get(mode)
                if not data:
                    continue
                ops = ", ".join(f"{o['operation_id']}#{o['generation']}×{o['count']}" for o in data["ops"]) or "—"
                lines.append(
                    f"  - {mode}: status `{data['status']}` compile `{data['compile']['status']}` published={data['published']} layers={data['layers']} ops=[{ops}] "
                    f"direct=`{(data['direct_outcome'] or {}).get('status', '—')}` → UI: {_fmt_ui(data['ui'])}"
                )
        for violation in case["violations"]:
            lines.append(f"- ⚠ **{violation['invariant']}** ({violation['mode']}, step {violation['step']}): {violation['detail']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    report = run_all()
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n")
    if args.markdown:
        args.markdown.write_text(to_markdown(report) + "\n")
    found = sorted(f"{v['case']}::{v['invariant']}" for v in report["violations"])
    print(json.dumps({
        "cases": report["case_count"],
        "errors": [c["id"] for c in report["cases"] if c["error"]],
        "violations": found,
        "expectation_mismatches": [f"{v['case']}#{v['step']}:{v['mode']}:{v['detail']}" for v in report["expectation_mismatches"]],
        "unexpected_violations": sorted(set(found) - set(EXPECTED_OPEN_VIOLATIONS)),
        "missing_expected_violations": sorted(set(EXPECTED_OPEN_VIOLATIONS) - set(found)),
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
