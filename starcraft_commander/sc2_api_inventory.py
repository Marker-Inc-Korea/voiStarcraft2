"""Auditable inventory of the optional python-sc2 callable surface.

The Direct MCP boundary is semantic and finite.  This module makes that
boundary reviewable against the installed python-sc2 package without importing
the optional dependency at module import time.  Every public ``Unit`` and
``BotAI`` callable is classified as one of:

* a semantic action/query already exposed by the Direct catalog;
* an internal runtime helper/callback that is not an LLM capability; or
* an explicitly rejected raw surface (notably ``do`` and
  ``synchronous_do``), with a safety reason.

An unclassified callable is an inventory failure.  It must be assigned a
semantic schema or an explicit rejection before the package can claim that
its supported python-sc2 surface is bounded.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Final


SC2_UNIT_ACTION_METHODS: Final[frozenset[str]] = frozenset(
    {
        "attack",
        "build",
        "build_gas",
        "gather",
        "hold_position",
        "move",
        "patrol",
        "repair",
        "research",
        "return_resource",
        "smart",
        "stop",
        "train",
        "warp_in",
    }
)
SC2_UNIT_QUERY_METHODS: Final[frozenset[str]] = frozenset(
    {
        "calculate_speed",
        "distance_to",
        "distance_to_squared",
        "target_in_range",
        "in_ability_cast_range",
        "is_facing",
        "is_using_ability",
        "has_buff",
        "calculate_damage_vs_target",
        "calculate_dps_vs_target",
    }
)

SC2_BOT_SEMANTIC_ACTION_METHODS: Final[frozenset[str]] = frozenset(
    {
        "build",
        "train",
        "research",
        "distribute_workers",
        "expand_now",
    }
)
SC2_BOT_SEMANTIC_QUERY_METHODS: Final[frozenset[str]] = frozenset(
    {
        "already_pending",
        "already_pending_upgrade",
        "calculate_cost",
        "calculate_supply_cost",
        "calculate_unit_value",
        "can_afford",
        "can_feed",
        "find_placement",
        "can_place",
        "can_place_single",
        "get_available_abilities",
        "can_cast",
        "get_next_expansion",
        "get_terrain_height",
        "get_terrain_z_height",
        "has_creep",
        "in_map_bounds",
        "in_pathing_grid",
        "in_placement_grid",
        "is_visible",
        "structure_type_build_progress",
        "tech_requirement_progress",
        "worker_en_route_to_build",
    }
)

# These are callable because python-sc2 exposes them on BotAI, but they are
# runtime plumbing or selection helpers, not user-facing Direct capabilities.
SC2_BOT_INTERNAL_METHODS: Final[frozenset[str]] = frozenset(
    {
        "convert_tuple_to_numpy_array",
        "distance_math_hypot",
        "distance_math_hypot_squared",
        "issue_events",
        "prevent_double_actions",
        "select_build_worker",
        "square_to_condensed",
    }
)
SC2_BOT_CALLBACK_METHODS: Final[frozenset[str]] = frozenset(
    {
        "on_before_start", "on_start", "on_step", "on_end",
        "on_building_construction_complete", "on_building_construction_started",
        "on_enemy_unit_entered_vision", "on_enemy_unit_left_vision",
        "on_unit_created", "on_unit_destroyed", "on_unit_took_damage",
        "on_unit_type_changed", "on_upgrade_complete",
    }
)

SC2_BOT_UNIMPLEMENTED_METHODS: Final[dict[str, str]] = {
    "alert": "Game alert lookup needs a bounded Alert enum query schema and tests.",
    "chat_send": "Player chat needs a bounded recipient/text contract; it is not implemented.",
}

SC2_BOT_REJECTED_RAW_METHODS: Final[dict[str, str]] = {
    "do": (
        "rejected: arbitrary Ability/Unit orders would bypass semantic target, "
        "ownership, lifecycle, and fail-closed gates"
    ),
    "synchronous_do": (
        "rejected: arbitrary synchronous orders would bypass semantic target, "
        "ownership, lifecycle, and fail-closed gates"
    ),
}

# Client/protocol calls are intentionally audited as a separate surface. They
# are transport/debug/session controls, not semantic unit capabilities. Keeping
# an explicit disposition prevents dependency drift from being mistaken for
# an unreviewed omission.
SC2_CLIENT_EXCLUDED_METHODS: Final[dict[str, str]] = {
    "actions": "transport primitive; only semantic executor actions are exposed",
    "chat_send": "unimplemented until recipient/text policy is specified",
    "debug_*": "debug/admin control; never exposed to the command model",
    "dump_data": "raw game-data transport; observe/query schemas are required",
    "get_game_data": "raw protocol data; semantic query schemas are required",
    "get_game_info": "raw protocol data; observe/map schemas are required",
    "join_game": "session lifecycle is owned by the runtime launcher",
    "leave": "session lifecycle is owned by the runtime launcher",
    "move_camera": "camera control requires semantic map target resolution",
    "move_camera_spatial": "spatial raw camera control is rejected",
    "obs_move_camera": "spatial raw camera control is rejected",
    "observation": "raw observation transport; observe capability is the boundary",
    "ping": "transport diagnostic; not a gameplay capability",
    "query_*": "only bounded semantic query capabilities are exposed",
    "quick_*": "save/load/debug controls are not gameplay capabilities",
    "quit": "session lifecycle is owned by the runtime launcher",
    "save_replay": "artifact operation; not a command capability",
    "step": "frame stepping is runtime-owned and never user-controlled",
    "toggle_autocast": "requires a bounded ability policy before exposure",
}

SC2_PROTOCOL_EXCLUDED_SURFACE: Final[dict[str, str]] = {
    "Protocol": "transport/session implementation detail; no direct model access",
    "RequestQuery": "raw protobuf query container; use query_* semantic tools",
    "Status": "transport state; surfaced only through runtime status",
    "sc_pb": "raw protobuf namespace; never serialized into command schemas",
}


def _public_callables(cls: object) -> tuple[str, ...]:
    names: list[str] = []
    for name in dir(cls):
        if name.startswith("_"):
            continue
        # Do not invoke descriptors merely to discover a dependency's API.
        value = inspect.getattr_static(cls, name)
        if isinstance(value, (classmethod, staticmethod)):
            value = value.__func__
        if callable(value):
            names.append(name)
    return tuple(sorted(names))


def _classify_bot_method(name: str) -> str | None:
    if name in SC2_BOT_SEMANTIC_ACTION_METHODS:
        return "semantic_action"
    if name in SC2_BOT_SEMANTIC_QUERY_METHODS:
        return "semantic_query"
    if name in SC2_BOT_INTERNAL_METHODS | SC2_BOT_CALLBACK_METHODS:
        return "runtime_internal"
    if name in SC2_BOT_REJECTED_RAW_METHODS:
        return "rejected_raw"
    if name in SC2_BOT_UNIMPLEMENTED_METHODS:
        return "unimplemented"
    return None


@dataclass(frozen=True)
class SC2APISurfaceInventory:
    """Classification snapshot for one installed python-sc2 surface."""

    package_available: bool
    unit_methods: tuple[str, ...] = ()
    bot_methods: tuple[str, ...] = ()
    unit_supported: tuple[str, ...] = ()
    unit_unclassified: tuple[str, ...] = ()
    bot_supported: tuple[str, ...] = ()
    bot_internal: tuple[str, ...] = ()
    bot_rejected: tuple[str, ...] = ()
    bot_unimplemented: tuple[str, ...] = ()
    bot_unclassified: tuple[str, ...] = ()
    unavailable_reason: str = ""

    @property
    def classification_complete(self) -> bool:
        """Disposition coverage only, never all-API implementation or Live QA."""

        return (
            self.package_available
            and not self.unit_unclassified
            and not self.bot_unclassified
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "package_available": self.package_available,
            "unit_methods": list(self.unit_methods),
            "bot_methods": list(self.bot_methods),
            "unit_supported": list(self.unit_supported),
            "unit_unclassified": list(self.unit_unclassified),
            "bot_supported": list(self.bot_supported),
            "bot_internal": list(self.bot_internal),
            "bot_rejected": list(self.bot_rejected),
            "bot_unimplemented": list(self.bot_unimplemented),
            "bot_unclassified": list(self.bot_unclassified),
            "unavailable_reason": self.unavailable_reason,
            "classification_complete": self.classification_complete,
            "rejected_reasons": dict(SC2_BOT_REJECTED_RAW_METHODS),
            "unimplemented_reasons": dict(SC2_BOT_UNIMPLEMENTED_METHODS),
            "scope": "Public Unit/BotAI callables plus an explicit Client/protocol exclusion ledger; semantic mappings cover bounded subsets.",
            "excluded_client_surface": dict(SC2_CLIENT_EXCLUDED_METHODS),
            "excluded_protocol_surface": dict(SC2_PROTOCOL_EXCLUDED_SURFACE),
            "not_audited": ["Unit/BotAI properties", "ability target/caster matrix"],
        }


def inspect_python_sc2_surface() -> SC2APISurfaceInventory:
    """Inspect the installed package without making it an import requirement."""

    try:
        from sc2.bot_ai import BotAI
        from sc2.unit import Unit
    except ImportError as error:
        return SC2APISurfaceInventory(
            package_available=False,
            unavailable_reason=f"{type(error).__name__}: {error}",
        )

    return classify_python_sc2_surface(Unit, BotAI)


def classify_python_sc2_surface(unit_class: type, bot_class: type) -> SC2APISurfaceInventory:
    """Pure classification seam; unknown names (including new callbacks) stay gaps."""

    unit_methods = _public_callables(unit_class)
    bot_methods = _public_callables(bot_class)
    unit_supported = tuple(
        name
        for name in unit_methods
        if name in SC2_UNIT_ACTION_METHODS | SC2_UNIT_QUERY_METHODS
    )
    bot_supported = tuple(
        name
        for name in bot_methods
        if _classify_bot_method(name) in {"semantic_action", "semantic_query"}
    )
    bot_internal = tuple(
        name for name in bot_methods if _classify_bot_method(name) == "runtime_internal"
    )
    bot_rejected = tuple(
        name for name in bot_methods if _classify_bot_method(name) == "rejected_raw"
    )
    bot_unclassified = tuple(
        name for name in bot_methods if _classify_bot_method(name) is None
    )
    return SC2APISurfaceInventory(
        package_available=True,
        unit_methods=unit_methods,
        bot_methods=bot_methods,
        unit_supported=unit_supported,
        unit_unclassified=tuple(name for name in unit_methods if name not in unit_supported),
        bot_supported=bot_supported,
        bot_internal=bot_internal,
        bot_rejected=bot_rejected,
        bot_unimplemented=tuple(
            name for name in bot_methods if _classify_bot_method(name) == "unimplemented"
        ),
        bot_unclassified=bot_unclassified,
    )


__all__ = [
    "SC2APISurfaceInventory",
    "SC2_BOT_REJECTED_RAW_METHODS",
    "SC2_CLIENT_EXCLUDED_METHODS",
    "SC2_PROTOCOL_EXCLUDED_SURFACE",
    "SC2_BOT_SEMANTIC_ACTION_METHODS",
    "SC2_BOT_SEMANTIC_QUERY_METHODS",
    "SC2_BOT_INTERNAL_METHODS",
    "SC2_UNIT_ACTION_METHODS",
    "SC2_UNIT_QUERY_METHODS",
    "inspect_python_sc2_surface",
]
