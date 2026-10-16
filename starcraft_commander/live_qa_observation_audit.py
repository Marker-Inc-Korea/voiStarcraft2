"""Check raw SC2 observations without substituting manager intent for effects."""

from __future__ import annotations


def type_key(value: str) -> str:
    return (
        value.removeprefix("TERRAN_")
        .replace("_", "")
        .replace("LOWERED", "")
        .replace("SIEGED", "")
        .replace("FLYING", "")
        .upper()
    )


class ObservationAudit:
    def __init__(self):
        self.previous = None
        self.seen_tags = set()
        self.training = {}
        self.building = {}
        self.new_completed = {}
        self.evidence = {}
        self.samples = 0
        self.positions = {}
        self.expansion_resume = {}
        self.supply_recovery = {}

    def observe(
        self, raw, frame, actions, scoped_unit_tags=None,
        supply=None, supply_provider_frame=None,
    ):
        if (
            not isinstance(raw, dict)
            or raw.get("source") != "sc2_observation"
            or raw.get("schema_version") != 1
            or raw.get("frame") != frame
        ):
            return
        self.samples += 1
        units = {u["tag"]: u for u in raw.get("units", [])}
        own = {tag: u for tag, u in units.items() if u.get("alliance") == 1}
        enemy = {tag: u for tag, u in units.items() if u.get("alliance") == 4}
        scoped_unit_tags = (
            set(scoped_unit_tags) if scoped_unit_tags is not None else None
        )
        canonical_actions = {}
        for action, submitted in actions.items():
            kind, separator, name = action.partition("|")
            if separator:
                canonical_actions[(kind, type_key(name))] = submitted

        for tag, unit in own.items():
            if scoped_unit_tags is not None and tag in scoped_unit_tags:
                target_tag = unit.get("engaged_target_tag", 0)
                target = enemy.get(target_tag)
                if target is not None:
                    self.evidence.setdefault("enemy_observed", frame)
                else:
                    unit_x, unit_y = unit.get("x"), unit.get("y")
                    if isinstance(unit_x, (int, float)) and isinstance(
                        unit_y, (int, float)
                    ):
                        for candidate in enemy.values():
                            enemy_x, enemy_y = candidate.get("x"), candidate.get("y")
                            if (
                                isinstance(enemy_x, (int, float))
                                and isinstance(enemy_y, (int, float))
                                and (unit_x - enemy_x) ** 2
                                + (unit_y - enemy_y) ** 2 <= 12.0 ** 2
                            ):
                                self.evidence.setdefault("enemy_observed", frame)
                                break
            if (
                scoped_unit_tags is not None
                and tag in scoped_unit_tags
                and tag in self.positions
            ):
                previous_x, previous_y = self.positions[tag]
                x, y = unit.get("x"), unit.get("y")
                if (
                    isinstance(x, (int, float))
                    and isinstance(y, (int, float))
                    and (x - previous_x) ** 2 + (y - previous_y) ** 2 >= 1.0
                ):
                    self.evidence.setdefault("scoped_movement_observed", frame)
            if (
                scoped_unit_tags is not None
                and tag in scoped_unit_tags
                and
                isinstance(unit.get("x"), (int, float))
                and isinstance(unit.get("y"), (int, float))
            ):
                self.positions[tag] = (unit["x"], unit["y"])
            for order in unit.get("orders", []):
                ability = order.get("ability_name", "")
                if (
                    ability in {"MORPH_SIEGEMODE", "SIEGEMODE"}
                    and type_key(unit.get("type", ""))
                    in {"SIEGETANK", "SIEGETANKSIEGED"}
                    and (
                        scoped_unit_tags is None or tag in scoped_unit_tags
                    )
                ):
                    self.evidence.setdefault("siege_command", frame)
                if (
                    scoped_unit_tags is not None
                    and tag in scoped_unit_tags
                    and ability in {
                        "MOVE", "ATTACK", "ATTACK_ATTACK", "ATTACK_MOVE",
                        "MORPH_SIEGEMODE", "SIEGEMODE",
                    }
                ):
                    self.evidence.setdefault("actual_sc2_action", frame)
                if ability.startswith("TRAIN_"):
                    target_type = type_key(ability[6:])
                    submitted = canonical_actions.get(("train_command", target_type))
                    if submitted is not None and submitted <= frame:
                        self.training.setdefault(target_type, {
                            "frame": frame, "producer_tag": tag,
                            "baseline_tags": set(own) | self.seen_tags,
                            "baseline_count": sum(
                                type_key(u.get("type", "")) == target_type
                                for u in own.values()
                            ),
                        })
                if type_key(unit.get("type", "")) == "SCV":
                    target = units.get(order.get("target_tag"), {})
                    if (
                        ability in {"HARVEST_GATHER", "HARVEST_GATHER_SCV"}
                        and target.get("alliance") == 3
                        and target.get("mineral_contents", 0) > 0
                    ):
                        self.evidence.setdefault("worker_assignment_observed", frame)
                    if (
                        type_key(order.get("ability_name", "")) == "BUILDCOMMANDCENTER"
                        and canonical_actions.get(("build_command", "COMMANDCENTER"))
                        is not None
                        and canonical_actions[("build_command", "COMMANDCENTER")] <= frame
                    ):
                        self.evidence.setdefault("worker_transfer", frame)

        if self.previous is not None:
            for resource, score_key in (
                ("mineral", "collected_minerals"), ("gas", "collected_vespene")
            ):
                before = self.previous.get(score_key)
                after = raw.get(score_key)
                if (
                    isinstance(before, (int, float))
                    and isinstance(after, (int, float))
                    and after > before
                ):
                    self.evidence.setdefault(f"positive_{resource}_income", frame)
            if (
                raw.get("collection_rate_vespene", 0) > 0
                and self.previous.get("collection_rate_vespene", 0) <= 0
            ):
                self.evidence.setdefault("positive_gas_income", frame)

        for tag, unit in own.items():
            name = type_key(unit.get("type", ""))
            progress = unit.get("build_progress", 0)
            training = self.training.get(name)
            if (
                training and frame > training["frame"]
                and tag not in training["baseline_tags"]
                and progress >= 1
            ):
                count = sum(
                    type_key(u.get("type", "")) == name
                    and u.get("build_progress", 0) >= 1
                    for u in own.values()
                )
                if count > training["baseline_count"]:
                    self.new_completed.setdefault(name, frame)
            if tag not in self.building and 0 <= progress < 1:
                relevant = [
                    submitted for (kind, item), submitted in canonical_actions.items()
                    if item == name and kind in {
                        "build_command", "build_target_command", "addon_build_command"
                    } and submitted <= frame
                ]
                if relevant and tag not in self.seen_tags:
                    self.building[tag] = {
                        "type": name, "started": frame, "completed": None,
                        "command_frame": min(relevant),
                    }
            # The observation cadence can miss the construction phase and
            # first expose a newly created addon as already complete. A
            # matching scoped addon command plus a previously unseen tag is
            # still authoritative evidence of that completed construction.
            if (
                tag not in self.building
                and progress >= 1
                and tag not in self.seen_tags
                and name in {"TECHLAB", "FACTORYTECHLAB", "BARRACKSTECHLAB",
                             "STARPORTTECHLAB", "REACTOR", "FACTORYREACTOR",
                             "BARRACKSREACTOR", "STARPORTREACTOR"}
                and any(
                    item == name and kind in {"addon_build_command", "build_command"}
                    and submitted <= frame
                    for (kind, item), submitted in canonical_actions.items()
                )
            ):
                self.building[tag] = {
                    "type": name, "started": frame, "completed": frame,
                }
            if (
                tag not in self.building
                and 0 < progress < 1
                and name in {"REFINERY", "SUPPLYDEPOT", "BARRACKS",
                             "FACTORY", "TECHLAB", "FACTORYTECHLAB",
                             "COMMANDCENTER"}
            ):
                # The raw observation proves a new construction exists, but
                # does not by itself prove which policy caused its command.
                self.building[tag] = {
                    "type": name, "started": frame, "completed": None,
                }
            tracked = self.building.get(tag)
            if tracked and tracked["type"] == name and progress >= 1:
                tracked["completed"] = tracked["completed"] or frame
        self.observe_expansion_resume(own, frame)
        self.observe_supply_recovery(own, frame, supply, supply_provider_frame)
        self.seen_tags.update(own)
        self.previous = raw

    def observe_supply_recovery(self, own, frame, supply, provider_frame):
        if not isinstance(supply, dict):
            return
        used, cap = supply.get("current_supply"), supply.get("max_supply")
        if not all(isinstance(value, (int, float)) for value in (used, cap)):
            return
        record = self.supply_recovery
        if not record:
            if not 0 < cap < 200 or used < cap:
                return
            record.update({"supply_blocked": frame, "blocked_cap": cap})
        if (
            isinstance(provider_frame, (int, float))
            and record["supply_blocked"] <= provider_frame <= frame
        ):
            record.setdefault("supply_provider_command", provider_frame)
        submitted = record.get("supply_provider_command")
        if submitted is None:
            return
        if "capacity_restored" not in record:
            completed = [
                (building["completed"], tag)
                for tag, building in self.building.items()
                if building["type"] == "SUPPLYDEPOT"
                and building.get("command_frame") is not None
                and building["started"] >= submitted
                and building["completed"] is not None
                and tag in own
            ]
            # Lost army supply is not a supply-provider recovery.
            if not completed or cap <= record["blocked_cap"] or used >= cap:
                return
            completed_frame, tag = min(completed)
            record.update({
                "capacity_restored": frame,
                "depot_completed": completed_frame,
                "depot_tag": tag,
            })
        if frame <= record["capacity_restored"]:
            return
        if "training_frame" not in record:
            for tag, unit in own.items():
                if unit.get("build_progress", 0) < 1:
                    continue
                for order in unit.get("orders", []):
                    ability = order.get("ability_name", "")
                    if ability.startswith("TRAIN_"):
                        record.update({
                            "training_frame": frame,
                            "producer_tag": tag,
                            "unit_type": type_key(ability[6:]),
                            "baseline_tags": set(own) | self.seen_tags,
                        })
                        return
        elif frame > record["training_frame"]:
            for tag, unit in own.items():
                if (
                    tag not in record["baseline_tags"]
                    and type_key(unit.get("type", "")) == record["unit_type"]
                    and unit.get("build_progress", 0) >= 1
                ):
                    record.setdefault("production_resumed", frame)
                    record.setdefault("produced_tag", tag)
                    break

    def observe_expansion_resume(self, own, frame):
        # This is autonomous continuity after a scoped expansion, not proof
        # that the expansion policy directly submitted a Marine command.
        record = self.expansion_resume
        if not record:
            completed = [
                (building["completed"], tag, building)
                for tag, building in self.building.items()
                if building["type"] == "COMMANDCENTER"
                and building.get("command_frame") is not None
                and building["completed"] is not None
            ]
            if not completed:
                return
            completed_frame, tag, building = min(completed)
            record.update({
                "building_tag": tag,
                "building_started": building["started"],
                "building_completed": completed_frame,
                "command_frame": building["command_frame"],
            })
        if frame <= record["building_completed"]:
            return
        if "training_frame" not in record:
            for tag, unit in own.items():
                if (
                    type_key(unit.get("type", "")) == "BARRACKS"
                    and unit.get("build_progress", 0) >= 1
                    and not unit.get("flying", False)
                    and any(
                        order.get("ability_name") == "TRAIN_MARINE"
                        for order in unit.get("orders", [])
                    )
                ):
                    record.update({
                        "training_frame": frame,
                        "producer_tag": tag,
                        "baseline_tags": set(own) | self.seen_tags,
                    })
                    break
        elif frame > record["training_frame"] and "produced_frame" not in record:
            for tag, unit in own.items():
                if (
                    tag not in record["baseline_tags"]
                    and type_key(unit.get("type", "")) == "MARINE"
                    and unit.get("build_progress", 0) >= 1
                ):
                    record.update({"produced_frame": frame, "produced_tag": tag})
                    break

    def production_evidence(self, scenario_id):
        number = scenario_id.split("-", 1)[0]
        evidence = {}
        if number == "28":
            return {
                key: self.supply_recovery[key]
                for key in (
                    "supply_blocked", "supply_provider_command", "production_resumed"
                )
                if key in self.supply_recovery
            }
        if number == "19":
            record = self.expansion_resume
            if "building_completed" in record:
                evidence["building_completed"] = record["building_completed"]
            if "produced_frame" in record:
                evidence["train_command|Marine"] = record["training_frame"]
            return evidence
        if number == "02":
            evidence.update(self.evidence)
        if number == "03" and "positive_gas_income" in self.evidence:
            evidence["positive_gas_income"] = self.evidence["positive_gas_income"]
        if number == "26":
            factory_frames = [
                building["started"]
                for building in self.building.values()
                if building["type"] == "FACTORY"
            ]
            if factory_frames:
                evidence["factory_progress"] = min(factory_frames)
            tank_frames = [
                frame
                for frame in (
                    self.new_completed.get("SIEGETANK"),
                    self.training.get("SIEGETANK", {}).get("frame"),
                )
                if frame is not None
            ]
            if tank_frames:
                evidence["tank_progress"] = min(tank_frames)
            for income_key in ("positive_mineral_income", "positive_gas_income"):
                if income_key in self.evidence:
                    evidence["positive_income"] = self.evidence[income_key]
                    break
        unit_type = {"01": "SCV", "09": "SIEGETANK"}.get(number)
        if unit_type in self.new_completed:
            evidence["unit_count_increased"] = self.new_completed[unit_type]
        building_type = {
            "04": "SUPPLYDEPOT", "05": "BARRACKS", "07": "FACTORY",
            "08": "FACTORYTECHLAB", "17": "COMMANDCENTER",
        }.get(number)
        for tag, building in self.building.items():
            if building["type"] != building_type:
                continue
            evidence.setdefault("building_started", building["started"])
            if building["completed"] is not None:
                if number == "08":
                    # An orphaned addon does not prove a usable Factory Tech Lab.
                    owners = self.previous.get("units", [])
                    if not any(
                        u.get("alliance") == 1 and u.get("add_on_tag") == tag
                        and type_key(u.get("type", "")) == "FACTORY"
                        and not u.get("flying", False)
                        for u in owners
                    ):
                        continue
                    evidence["addon_completed"] = building["completed"]
                else:
                    evidence["building_completed"] = building["completed"]
        if number == "06":
            marine_count = 0
            for unit in (self.previous or {}).get("units", []):
                if (
                    unit.get("alliance") == 1
                    and type_key(unit.get("type", "")) == "MARINE"
                    and unit.get("build_progress", 0) >= 1
                ):
                    marine_count += 1
            if marine_count >= 4:
                evidence["unit_count_reached"] = self.previous.get("frame", 0)
        if number == "03":
            for building in self.building.values():
                if building["type"] == "REFINERY":
                    evidence.setdefault(
                        "build_command|Refinery", building["started"]
                    )
                    break
        return evidence
