"""Issue #126 contracts for all-Terran operation admission and evidence."""

import unittest

from starcraft_commander.llm_interpreter import _lower_compact_policy_commands
from starcraft_commander.micromachine_terran_capabilities import (
    TERRAN_OPERATION_TASKS,
    TERRAN_UNIT_FAMILIES,
    all_terran_capability_matrix,
    canonical_terran_unit_family,
    lower_terran_natural_language_operation,
    lower_terran_natural_language_units,
    operation_family_evidence,
    terran_production_targets,
    validate_terran_capability_contract,
)
from starcraft_commander.policy_modulation_provider import (
    compile_policy_modulation_provider_output,
)


EXPECTED_TERRAN_FAMILIES = {
    "marine": {
        "unit_types": ("TERRAN_MARINE",),
        "aliases": ("marine", "marines", "마린", "해병"),
        "prerequisites": ("TERRAN_BARRACKS",),
        "abilities": ("marine_stimpack",),
    },
    "marauder": {
        "unit_types": ("TERRAN_MARAUDER",),
        "aliases": ("marauder", "marauders", "불곰"),
        "prerequisites": ("TERRAN_BARRACKS", "BARRACKS_TECHLAB"),
        "abilities": ("marauder_stimpack",),
    },
    "reaper": {
        "unit_types": ("TERRAN_REAPER",),
        "aliases": ("reaper", "reapers", "사신"),
        "prerequisites": ("TERRAN_BARRACKS",),
        "abilities": ("kd8_charge",),
    },
    "ghost": {
        "unit_types": ("TERRAN_GHOST",),
        "aliases": ("ghost", "ghosts", "유령"),
        "prerequisites": (
            "TERRAN_BARRACKS",
            "BARRACKS_TECHLAB",
            "TERRAN_GHOSTACADEMY",
        ),
        "abilities": (
            "emp",
            "snipe",
            "ghost_cloak",
            "ghost_decloak",
            "tactical_nuke",
        ),
    },
    "hellion_hellbat": {
        "unit_types": ("TERRAN_HELLION", "TERRAN_HELLIONTANK"),
        "aliases": (
            "hellion",
            "hellions",
            "hellbat",
            "hellbats",
            "화염차",
            "화염기갑병",
        ),
        "prerequisites": ("TERRAN_FACTORY",),
        "abilities": ("hellbat_mode", "hellion_mode"),
    },
    "widow_mine": {
        "unit_types": (
            "TERRAN_WIDOWMINE",
            "TERRAN_WIDOWMINEBURROWED",
        ),
        "aliases": (
            "widow mine",
            "widow mines",
            "widowmine",
            "widowmines",
            "지뢰",
            "땅거미지뢰",
        ),
        "prerequisites": ("TERRAN_FACTORY",),
        "abilities": ("widow_mine_burrow", "widow_mine_unburrow"),
    },
    "cyclone": {
        "unit_types": ("TERRAN_CYCLONE",),
        "aliases": ("cyclone", "cyclones", "사이클론"),
        "prerequisites": ("TERRAN_FACTORY", "FACTORY_TECHLAB"),
        "abilities": ("lock_on",),
    },
    "siege_tank": {
        "unit_types": (
            "TERRAN_SIEGETANK",
            "TERRAN_SIEGETANKSIEGED",
        ),
        "aliases": (
            "siege tank",
            "siege tanks",
            "tank",
            "tanks",
            "탱크",
            "공성전차",
        ),
        "prerequisites": ("TERRAN_FACTORY", "FACTORY_TECHLAB"),
        "abilities": ("siege_mode", "unsiege"),
    },
    "thor": {
        "unit_types": ("TERRAN_THOR", "TERRAN_THORAP"),
        "aliases": ("thor", "thors", "토르"),
        "prerequisites": (
            "TERRAN_FACTORY",
            "FACTORY_TECHLAB",
            "TERRAN_ARMORY",
        ),
        "abilities": ("thor_high_impact_mode", "thor_explosive_mode"),
    },
    "medivac": {
        "unit_types": ("TERRAN_MEDIVAC",),
        "aliases": ("medivac", "medivacs", "의료선"),
        "prerequisites": ("TERRAN_FACTORY", "TERRAN_STARPORT"),
        "abilities": (
            "medivac_afterburners",
            "medivac_heal",
            "medivac_load",
            "medivac_unload_all",
        ),
    },
    "raven": {
        "unit_types": ("TERRAN_RAVEN",),
        "aliases": ("raven", "ravens", "밤까마귀"),
        "prerequisites": (
            "TERRAN_FACTORY",
            "TERRAN_STARPORT",
            "STARPORT_TECHLAB",
        ),
        "abilities": (
            "auto_turret",
            "interference_matrix",
            "anti_armor_missile",
        ),
    },
    "viking": {
        "unit_types": (
            "TERRAN_VIKINGFIGHTER",
            "TERRAN_VIKINGASSAULT",
        ),
        "aliases": ("viking", "vikings", "바이킹"),
        "prerequisites": ("TERRAN_FACTORY", "TERRAN_STARPORT"),
        "abilities": ("viking_fighter_mode", "viking_assault_mode"),
    },
    "banshee": {
        "unit_types": ("TERRAN_BANSHEE",),
        "aliases": ("banshee", "banshees", "밴시"),
        "prerequisites": (
            "TERRAN_FACTORY",
            "TERRAN_STARPORT",
            "STARPORT_TECHLAB",
        ),
        "abilities": ("banshee_cloak", "banshee_decloak"),
    },
    "liberator": {
        "unit_types": ("TERRAN_LIBERATOR", "TERRAN_LIBERATORAG"),
        "aliases": ("liberator", "liberators", "해방선"),
        "prerequisites": ("TERRAN_FACTORY", "TERRAN_STARPORT"),
        "abilities": (
            "liberator_defender_mode",
            "liberator_fighter_mode",
        ),
    },
    "battlecruiser": {
        "unit_types": ("TERRAN_BATTLECRUISER",),
        "aliases": (
            "battlecruiser",
            "battlecruisers",
            "bc",
            "배틀크루저",
            "전투순양함",
        ),
        "prerequisites": (
            "TERRAN_FACTORY",
            "TERRAN_STARPORT",
            "STARPORT_TECHLAB",
            "TERRAN_FUSIONCORE",
        ),
        "abilities": ("yamato", "tactical_jump"),
    },
}

TERRAN_NATURAL_LANGUAGE_CASES = (
    ("marine", "마린", "TERRAN_MARINE", "frontline"),
    ("marauder", "불곰", "TERRAN_MARAUDER", "frontline"),
    ("reaper", "사신", "TERRAN_REAPER", "worker_harass"),
    ("ghost", "유령", "TERRAN_GHOST", "spellcaster"),
    ("hellion_hellbat", "화염차", "TERRAN_HELLION", "worker_harass"),
    ("widow_mine", "땅거미지뢰", "TERRAN_WIDOWMINE", "ambush"),
    ("cyclone", "사이클론", "TERRAN_CYCLONE", "kite"),
    ("siege_tank", "공성전차", "TERRAN_SIEGETANK", "siege_support"),
    ("thor", "토르", "TERRAN_THOR", "anti_air"),
    ("medivac", "의료선", "TERRAN_MEDIVAC", "support"),
    ("raven", "밤까마귀", "TERRAN_RAVEN", "support"),
    ("viking", "바이킹", "TERRAN_VIKINGFIGHTER", "anti_air"),
    ("banshee", "밴시", "TERRAN_BANSHEE", "worker_harass"),
    ("liberator", "해방선", "TERRAN_LIBERATOR", "zone_control"),
    (
        "battlecruiser",
        "전투순양함",
        "TERRAN_BATTLECRUISER",
        "capital_ship",
    ),
)

TERRAN_OPERATION_LANGUAGE_CASES = (
    ("적 본진 정찰해", "scout_with_units", "scout", "enemy_main"),
    (
        "적 본진 공격해",
        "pressure_with_main_army",
        "main",
        "enemy_main",
    ),
    (
        "적 일꾼 견제해",
        "harass_with_units",
        "harass",
        "enemy_mineral_line",
    ),
    ("본진 수비해", "defend_with_units", "defense", "home"),
)


class MicroMachineTerranCapabilitiesTest(unittest.TestCase):
    def test_matrix_has_exactly_15_unique_canonical_families(self) -> None:
        matrix = all_terran_capability_matrix()

        self.assertEqual(15, len(matrix))
        self.assertEqual(
            set(EXPECTED_TERRAN_FAMILIES),
            {str(row["family"]) for row in matrix},
        )
        self.assertEqual(
            len(matrix),
            len({str(row["family"]) for row in matrix}),
        )
        self.assertEqual(15, len(TERRAN_UNIT_FAMILIES))

    def test_family_alias_prerequisite_ability_and_task_contract(self) -> None:
        matrix = {
            str(row["family"]): row
            for row in all_terran_capability_matrix()
        }

        for family, expected in EXPECTED_TERRAN_FAMILIES.items():
            with self.subTest(family=family):
                row = matrix[family]
                self.assertEqual(
                    expected["unit_types"],
                    tuple(row["unit_types"]),
                )
                self.assertEqual(
                    expected["aliases"],
                    tuple(row["aliases"]),
                )
                self.assertEqual(
                    expected["prerequisites"],
                    tuple(row["prerequisites"]),
                )
                self.assertEqual(
                    expected["abilities"],
                    tuple(row["abilities"]),
                )
                self.assertEqual(
                    set(TERRAN_OPERATION_TASKS),
                    set(row["operation_tasks"]),
                )
                self.assertIn("Squad", row["micro_managers"])

    def test_every_alias_and_transformed_unit_token_resolves_to_family(self) -> None:
        for family, expected in EXPECTED_TERRAN_FAMILIES.items():
            tokens = (
                family,
                *expected["aliases"],
                *expected["unit_types"],
                *(
                    token.removeprefix("TERRAN_").lower()
                    for token in expected["unit_types"]
                ),
            )
            for token in tokens:
                with self.subTest(family=family, token=token):
                    self.assertEqual(
                        family,
                        canonical_terran_unit_family(token),
                    )

    def test_capability_contract_has_no_internal_drift(self) -> None:
        self.assertEqual((), validate_terran_capability_contract())

    def test_all_15_families_lower_deterministically_for_every_operation(
        self,
    ) -> None:
        matrix = {
            str(row["family"]): row
            for row in all_terran_capability_matrix()
        }

        for (
            family,
            alias,
            expected_unit_type,
            expected_role,
        ) in TERRAN_NATURAL_LANGUAGE_CASES:
            for (
                operation_text,
                expected_task_type,
                expected_army_group,
                expected_location,
            ) in TERRAN_OPERATION_LANGUAGE_CASES:
                with self.subTest(
                    family=family,
                    task_type=expected_task_type,
                ):
                    command = f"{alias} 2기로 {operation_text}"
                    operation = lower_terran_natural_language_operation(command)
                    intents = lower_terran_natural_language_units(
                        command,
                        default_count=1,
                    )

                    self.assertIsNotNone(operation)
                    assert operation is not None
                    self.assertEqual(expected_task_type, operation.task_type)
                    self.assertEqual(expected_army_group, operation.army_group)
                    self.assertEqual(expected_location, operation.location_intent)
                    self.assertEqual(1, len(intents))
                    intent = intents[0]
                    self.assertEqual(family, intent.family)
                    self.assertEqual(expected_unit_type, intent.requested_unit_type)
                    self.assertEqual(expected_unit_type, intent.execution_unit_type)
                    self.assertEqual(2, intent.count)
                    self.assertEqual(expected_role, intent.role)
                    self.assertTrue(
                        set(matrix[family]["prerequisites"])
                        <= set(terran_production_targets(intents))
                    )
                    self.assertIn(
                        expected_unit_type,
                        terran_production_targets(intents),
                    )

    def test_hellbat_is_an_explicit_staged_form_not_a_hellion_alias(self) -> None:
        hellbat = lower_terran_natural_language_units(
            "화염기갑병 3기로 적 일꾼 견제해",
            default_count=1,
        )
        hellion = lower_terran_natural_language_units(
            "화염차 3기로 적 일꾼 견제해",
            default_count=1,
        )

        self.assertEqual(1, len(hellbat))
        self.assertEqual("TERRAN_HELLIONTANK", hellbat[0].requested_unit_type)
        self.assertEqual("TERRAN_HELLION", hellbat[0].execution_unit_type)
        self.assertEqual(("TERRAN_HELLION",), hellbat[0].staging_unit_types)
        self.assertEqual("hellbat_mode", hellbat[0].mode_ability)
        self.assertEqual(
            {"TERRAN_FACTORY", "TERRAN_ARMORY", "TERRAN_HELLION"},
            set(terran_production_targets(hellbat)),
        )

        self.assertEqual(1, len(hellion))
        self.assertEqual("TERRAN_HELLION", hellion[0].requested_unit_type)
        self.assertEqual("", hellion[0].mode_ability)
        self.assertNotIn(
            "TERRAN_ARMORY",
            terran_production_targets(hellion),
        )

    def test_provider_harass_aliases_lower_to_worker_harass_operation(self) -> None:
        for task_alias in ("harass", "harassment", "harass_with_units"):
            with self.subTest(task_alias=task_alias):
                result = compile_policy_modulation_provider_output(
                    {
                        "source": "llm",
                        "goal": "리퍼와 밴시로 적 일꾼을 견제해",
                        "tactical_task": {
                            "task_type": task_alias,
                            "location_intent": "enemy_mineral_line",
                            "unit_classes": ["reaper", "banshee"],
                        },
                        "composition_requirements": [
                            {
                                "unit_type": "reaper",
                                "count": 1,
                                "role": "worker_harass",
                            },
                            {
                                "unit_type": "banshee",
                                "count": 1,
                                "role": "worker_harass",
                            },
                        ],
                        "unit_roles": [
                            {
                                "unit_type": "reaper",
                                "role": "worker_harass",
                                "priority": 0.9,
                                "ability_policy": "if_available",
                            },
                            {
                                "unit_type": "banshee",
                                "role": "worker_harass",
                                "priority": 0.9,
                                "ability_policy": "if_available",
                            },
                        ],
                    }
                )

                self.assertTrue(result.ok, result.to_dict())
                assert result.vector is not None
                vector = result.vector
                self.assertEqual(
                    "harass_with_units",
                    vector.tactical_task.task_type,
                )
                self.assertEqual(
                    "enemy_mineral_line",
                    vector.tactical_task.location_intent,
                )
                self.assertEqual(
                    {"TERRAN_REAPER", "TERRAN_BANSHEE"},
                    {
                        requirement.unit_type
                        for requirement in vector.composition_requirements
                    },
                )
                self.assertEqual(
                    {"worker_harass"},
                    {role.role for role in vector.unit_roles},
                )
                self.assertGreaterEqual(
                    vector.combat.harassment_bias,
                    0.75,
                )
                self.assertGreaterEqual(
                    vector.squad.harassment_bias,
                    0.75,
                )
                queue_biases = vector.production.queue_biases.to_dict()
                for prerequisite in (
                    "TERRAN_BARRACKS",
                    "TERRAN_FACTORY",
                    "TERRAN_STARPORT",
                    "STARPORT_TECHLAB",
                    "TERRAN_REAPER",
                    "TERRAN_BANSHEE",
                ):
                    self.assertIn(prerequisite, queue_biases)

    def test_compact_harass_command_lowers_to_independent_operation(self) -> None:
        lowered = _lower_compact_policy_commands(
            {
                "status": "compiled",
                "assistant_message": "리퍼 견제 작전을 편성합니다.",
            },
            raw_commands=[
                {
                    "operation_id": "harass-alpha",
                    "goal": "리퍼 2기로 적 일꾼 견제",
                    "command_layer": "operation",
                    "operation_action": "create",
                    "task_type": "harass_with_units",
                    "unit_requests": [
                        {"unit_type": "reaper", "count": 2},
                    ],
                    "location_intent": "enemy_mineral_line",
                    "allow_partial": False,
                    "standing_order": False,
                }
            ],
            command_text="리퍼 2기로 적 일꾼을 견제해",
        )

        self.assertEqual("compiled", lowered["status"])
        modulation = lowered["modulation"]
        operation = modulation["operations"][0]
        self.assertEqual("harass-alpha", operation["operation_id"])
        self.assertEqual(
            "harass_with_units",
            operation["tactical_task"]["task_type"],
        )
        self.assertEqual(
            "enemy_mineral_line",
            operation["tactical_task"]["location_intent"],
        )
        self.assertEqual(
            "worker_harass",
            operation["composition_requirements"][0]["role"],
        )
        self.assertEqual(
            "worker_harass",
            operation["unit_roles"][0]["role"],
        )
        self.assertEqual(2, operation["scope"]["min_units"])
        self.assertEqual(2, operation["scope"]["max_units"])

    def test_operation_movement_does_not_prove_family_ability_effect(self) -> None:
        evidence = operation_family_evidence(
            {
                "update_id": "all-terran-update",
                "operation_id": "tank-harass",
                "generation": 2,
                "status": "MOVING",
                "max_home_distance": 28.0,
                "submitted_frame": 400,
                "submission_observed": True,
                "squad_order": "harass",
                "requirement_progress": [
                    {
                        "unit_type": "TERRAN_SIEGETANK",
                        "role": "siege_support",
                        "assigned_count": 1,
                        "represented_count": 1,
                    },
                    {
                        "unit_type": "TERRAN_BANSHEE",
                        "role": "worker_harass",
                        "assigned_count": 1,
                        "represented_count": 1,
                    },
                ],
            },
            expected_update_id="all-terran-update",
            expected_operation_id="tank-harass",
            expected_generation=2,
        )

        self.assertEqual(2, len(evidence))
        self.assertTrue(all(row["action"] == "squad_order:harass" for row in evidence))
        self.assertTrue(all(row["required_effect"] == "movement_or_engagement" for row in evidence))
        self.assertTrue(all(row["effect"] is False for row in evidence))
        self.assertTrue(all(row["stage"] == "assigned" for row in evidence))

    def test_explicit_ability_requires_specific_effect_evidence(self) -> None:
        evidence = operation_family_evidence(
            {
                "update_id": "ability-update",
                "operation_id": "tank-push",
                "generation": 2,
                "status": "MOVING",
                "family_evidence": [
                    {
                        "update_id": "ability-update",
                        "operation_id": "tank-push",
                        "generation": 2,
                        "family": "siege_tank",
                        "unit_type": "TERRAN_SIEGETANK",
                        "role": "siege_support",
                        "assigned": 1,
                        "represented": 1,
                        "action": "siege_mode",
                        "required_effect": "unit_type:TERRAN_SIEGETANKSIEGED",
                        "attempt_generation": 3,
                        "attempted_count": 1,
                        "attempted_frame": 410,
                        "submitted_count": 1,
                        "submitted_frame": 411,
                        "effect": True,
                    }
                ],
            },
            expected_update_id="ability-update",
            expected_operation_id="tank-push",
            expected_generation=2,
        )

        self.assertEqual(1, len(evidence))
        self.assertTrue(evidence[0]["attempted"])
        self.assertTrue(evidence[0]["executed"])
        self.assertFalse(evidence[0]["effect"])
        self.assertEqual("executed", evidence[0]["stage"])

    def test_family_evidence_rejects_stale_operation_identity(self) -> None:
        current = {
            "update_id": "ability-update",
            "operation_id": "tank-push",
            "generation": 2,
            "family": "siege_tank",
            "unit_type": "TERRAN_SIEGETANK",
            "role": "siege_support",
            "assigned": 1,
            "represented": 1,
            "action": "siege_mode",
            "required_effect": "unit_type:TERRAN_SIEGETANKSIEGED",
            "attempt_generation": 4,
            "attempted_count": 1,
            "attempted_frame": 420,
            "submitted_count": 1,
            "submitted_frame": 421,
            "effect_kind": "unit_type:TERRAN_SIEGETANKSIEGED",
            "effect_count": 1,
            "effect_frame": 422,
        }
        evidence = operation_family_evidence(
            {
                "update_id": "ability-update",
                "operation_id": "tank-push",
                "generation": 2,
                "family_evidence": [
                    current,
                    {**current, "update_id": "stale-update"},
                    {**current, "operation_id": "other-operation"},
                    {**current, "generation": 1},
                    {**current, "action": ""},
                ],
            },
            expected_update_id="ability-update",
            expected_operation_id="tank-push",
            expected_generation=2,
        )

        self.assertEqual(1, len(evidence))
        self.assertEqual("ability-update", evidence[0]["update_id"])
        self.assertEqual("tank-push", evidence[0]["operation_id"])
        self.assertEqual(2, evidence[0]["generation"])
        self.assertEqual("siege_mode", evidence[0]["action"])
        self.assertEqual(4, evidence[0]["attempt_generation"])
        self.assertTrue(evidence[0]["effect"])

    def test_newer_family_action_attempt_supersedes_stale_attempt(self) -> None:
        base = {
            "update_id": "ability-update",
            "operation_id": "tank-push",
            "generation": 2,
            "family": "siege_tank",
            "unit_type": "TERRAN_SIEGETANK",
            "role": "siege_support",
            "assigned": 1,
            "represented": 1,
            "action": "siege_mode",
            "required_effect": "unit_type:TERRAN_SIEGETANKSIEGED",
        }
        evidence = operation_family_evidence(
            {
                "update_id": "ability-update",
                "operation_id": "tank-push",
                "generation": 2,
                "family_evidence": [
                    {
                        **base,
                        "attempt_generation": 3,
                        "attempted_count": 1,
                        "attempted_frame": 410,
                        "blocker": "stale_attempt",
                    },
                    {
                        **base,
                        "attempt_generation": 4,
                        "attempted_count": 1,
                        "attempted_frame": 420,
                        "submitted_count": 1,
                        "submitted_frame": 421,
                        "effect_kind": "unit_type:TERRAN_SIEGETANKSIEGED",
                        "effect_count": 1,
                        "effect_frame": 422,
                    },
                ],
            },
            expected_update_id="ability-update",
            expected_operation_id="tank-push",
            expected_generation=2,
        )

        self.assertEqual(1, len(evidence))
        self.assertEqual(4, evidence[0]["attempt_generation"])
        self.assertEqual("", evidence[0]["blocker"])
        self.assertEqual("effect", evidence[0]["stage"])

    def test_mixed_family_rows_keep_independent_effects_and_blockers(self) -> None:
        evidence = operation_family_evidence(
            {
                "update_id": "mixed-update",
                "operation_id": "mixed-harass",
                "generation": 1,
                "family_evidence": [
                    {
                        "update_id": "mixed-update",
                        "operation_id": "mixed-harass",
                        "generation": 1,
                        "family": "reaper",
                        "unit_type": "TERRAN_REAPER",
                        "role": "worker_harass",
                        "assigned": 1,
                        "represented": 1,
                        "action": "squad_order:harass",
                        "required_effect": "movement_or_engagement",
                        "attempt_generation": 1,
                        "attempted_count": 1,
                        "attempted_frame": 100,
                        "submitted_count": 1,
                        "submitted_frame": 101,
                        "effect_kind": "movement_observed",
                        "effect_count": 1,
                        "effect_frame": 102,
                    },
                    {
                        "update_id": "mixed-update",
                        "operation_id": "mixed-harass",
                        "generation": 1,
                        "family": "banshee",
                        "unit_type": "TERRAN_BANSHEE",
                        "role": "worker_harass",
                        "assigned": 0,
                        "represented": 0,
                        "action": "banshee_cloak",
                        "required_effect": "cloak_state:cloaked",
                        "attempt_generation": 1,
                        "blocker_manager": "ProductionManager",
                        "blocker": "missing_starport_techlab",
                    },
                ],
            },
            expected_update_id="mixed-update",
            expected_operation_id="mixed-harass",
            expected_generation=1,
        )

        by_family = {row["family"]: row for row in evidence}
        self.assertEqual({"reaper", "banshee"}, set(by_family))
        self.assertEqual("effect", by_family["reaper"]["stage"])
        self.assertEqual("blocked", by_family["banshee"]["stage"])
        self.assertEqual(
            "missing_starport_techlab",
            by_family["banshee"]["blocker"],
        )

    def test_normal_production_waits_are_transient_family_blockers(self) -> None:
        for blocker in (
            "missing_producer",
            "missing_addon",
            "missing_tech",
            "producer_busy",
            "supply_blocked",
            "gas_pending",
            "minerals_pending",
        ):
            with self.subTest(blocker=blocker):
                evidence = operation_family_evidence(
                    {
                        "update_id": "production-wait",
                        "operation_id": "tank-production",
                        "generation": 1,
                        "family_evidence": [
                            {
                                "update_id": "production-wait",
                                "operation_id": "tank-production",
                                "generation": 1,
                                "family": "siege_tank",
                                "unit_type": "TERRAN_SIEGETANK",
                                "role": "siege_support",
                                "action": "squad_order:attack",
                                "required_effect": "movement_or_engagement",
                                "attempt_generation": 1,
                                "blocker_manager": "ProductionManager",
                                "blocker": blocker,
                            }
                        ],
                    },
                    expected_update_id="production-wait",
                    expected_operation_id="tank-production",
                    expected_generation=1,
                )

                self.assertEqual(1, len(evidence))
                self.assertEqual("waiting", evidence[0]["stage"])
                self.assertEqual(blocker, evidence[0]["blocker"])


if __name__ == "__main__":
    unittest.main()
