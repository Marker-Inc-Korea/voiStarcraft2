"""Plan-level unit binding for Direct commands.

Binding one action at a time lets two concurrent operations over the same unit
type pin the SAME first-N units ("2 marines scout, 4 marines attack" ordered
four marines twice).  ``bind_plan_actions`` binds a whole plan while carrying
the tags already reserved by earlier operation-keyed actions, so one unit
serves at most one operation.  Actions without an ``operation_id`` (emergency
stop/hold/retreat, one-shot tickets) deliberately address the whole group and
neither reserve nor are limited by the exclusion set.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence

from starcraft_commander.contracts import SC2CommandAction


def _accepts_exclude_tags(binder: Callable[..., object]) -> bool:
    try:
        parameters = inspect.signature(binder).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "exclude_tags" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def bind_plan_actions(
    binder: Callable[..., SC2CommandAction],
    actions: Sequence[SC2CommandAction],
    *,
    trust_pinned: bool,
) -> tuple[SC2CommandAction, ...]:
    """Bind every action, keeping operation-keyed unit selections disjoint.

    ``trust_pinned=False`` re-binds every action (the MCP boundary must never
    accept caller-supplied tags); ``True`` keeps already pinned actions as the
    admission step chose them and counts their tags as reserved.
    """

    accepts = _accepts_exclude_tags(binder)
    reserved: set[int] = set()
    bound: list[SC2CommandAction] = []
    for action in actions:
        operation_keyed = bool(action.metadata.get("operation_id"))
        if trust_pinned and action.metadata.get("_direct_unit_tags"):
            result = action
        elif accepts and operation_keyed:
            result = binder(action, exclude_tags=frozenset(reserved))
        else:
            result = binder(action)
        if operation_keyed:
            reserved.update(
                tag
                for tag in result.metadata.get("_direct_unit_tags", ())
                if type(tag) is int and tag > 0
            )
        bound.append(result)
    return tuple(bound)
