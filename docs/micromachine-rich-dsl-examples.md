# MicroMachine Rich DSL Examples

This DSL expresses bounded manager intent. It is not a raw SC2 command layer:
MicroMachine still owns unit tags, build placement resolution, pathing, and
micro execution.

## `마린 4기랑 탱크 1기로 적진 공격해`

```json
{
  "goal": "마린 4기랑 탱크 1기로 적진 공격해",
  "composition_requirements": [
    {"unit_type": "marine", "count": 4, "role": "frontline"},
    {"unit_type": "tank", "count": 1, "role": "siege_support"}
  ],
  "route_intent": {"route_type": "direct"},
  "target_intent": {"target_type": "enemy_main", "priority": 0.9}
}
```

## `탱크 생산해`

```json
{
  "goal": "탱크 생산해",
  "production_plan": {
    "targets": ["tank"],
    "allow_prerequisite_buildings": true,
    "priority": 0.8
  }
}
```

## `앞마당 입구에 벙커 지어`

```json
{
  "goal": "앞마당 입구에 벙커 지어",
  "building_tasks": [
    {"building_type": "bunker", "placement_intent": "front_door", "count": 1}
  ]
}
```

## `바이킹으로 공중 병력 우선 잡아`

```json
{
  "goal": "바이킹으로 공중 병력 우선 잡아",
  "unit_roles": [
    {"unit_type": "viking", "role": "anti_air", "priority": 0.8}
  ],
  "target_intent": {"target_type": "air_army", "priority": 0.9}
}
```

## `밴시는 클로킹 되면 일꾼 견제해`

```json
{
  "goal": "밴시는 클로킹 되면 일꾼 견제해",
  "unit_roles": [
    {"unit_type": "banshee", "role": "worker_harass", "priority": 0.8}
  ],
  "target_intent": {"target_type": "worker_line", "priority": 0.9}
}
```

Safety boundaries:

- Counts are bounded and validated.
- Unit/building names are canonicalized to safe MicroMachine tokens.
- Roles, route intents, target intents, and building placement intents are
  whitelist enums.
- Coordinates are optional and must be bounded map positions in `0..256`.
