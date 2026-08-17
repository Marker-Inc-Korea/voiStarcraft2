# MicroMachine Final Live-QA Runbook

This runbook begins only after `ready_for_live_qa` passes on the exact `main` SHA. It does not authorize human multiplayer, ladder, or Battle.net qualification.

<!-- PRE_LIVE_RELEASE_STATUS:START -->
## Structured Release Status

### Implemented

- Parallel operation ownership, transfer, reinforcement, retarget, cancellation, emergency preemption, autonomous defense restoration, all-Terran lowering, event replay, and tactical voice identity contracts are implemented.
- Hardened source, build, producer, artifact, distribution, and replay verification components are implemented.

### Automated qualified

- Automated qualification requires all five child artifacts to pass on one exact repository SHA, MicroMachine build identity, workflow run, and run attempt.
- The checked-in deterministic manifest defines exactly fourteen executable pre-live journeys.

### Live QA pending

- Actual StarCraft II movement, engagement, compact-controller and HUD consistency, and operator feel remain manual observations.
- No automated report may set live qualified or clear manual_live_qa_remaining.

### Proposed or deferred

- Human multiplayer, ladder, Battle.net qualification, and competitive balance signoff are deferred.
- Raw unit-tag control, arbitrary frame scripting, mouse automation, and direct provider SC2 actions remain excluded.

- Manual live QA remaining: `true`
- Live qualified: `false`
<!-- PRE_LIVE_RELEASE_STATUS:END -->

## Clean Build

1. Confirm `git rev-parse HEAD` equals the SHA in `ready_for_live_qa.json`.
2. Confirm `git status --porcelain=v1 --untracked-files=all` is empty.
3. Run `integrations/micromachine/scripts/build_macos_local.sh`.
4. Stop immediately if the build, CTest, embedded identity, binary digest, or source attestation differs from the readiness artifact.

## Runtime And Surfaces

1. Install and launch the macOS **voiStarcraft2** app for native runtime bootstrap.
2. Confirm that exactly one compact controller instance exists and that no second browser/cockpit window is opened.
3. Let the installed app supply the native SC2 launch receipt and auto-start the patched MicroMachine build and StarCraft II.
4. Verify the compact current-command surface before launch, then allow the app to hide it while StarCraft II is frontmost; use the in-game HUD for gameplay evidence. An ordinary browser may observe or queue commands, but it is not a runtime-start surface.
5. Capture the exact operation identity, game frame, controller/HUD screenshots, telemetry, and runtime artifacts for every journey.
6. Do not treat `queued`, compiled, `published`, assignment, or submission as gameplay success. Require current identity-matched telemetry plus the journey's production, movement, engagement, target, completion, or `effect_observed` condition.

## Fourteen Journeys

### 01. `parallel_scout_attack_defend` - Parallel scout, attack, and defend

- Input contract: `[{"command_text":"마린 정찰, 마린 공격, 탱크 입구 방어를 동시에 수행해","frame":100,"preset":"parallel_scout_attack_defend"},{"frame":140,"kind":"unit_observation","units":[{"home_distance":18.0,"unit_type":"TERRAN_MARINE"},{"engaged":true,"unit_type":"TERRAN_SIEGETANK"}]}]`
- Expected observation events: `["command_input","blackboard_update","ownership_snapshot","squad_order","submission","movement","engagement","web_projection"]`
- Stop condition: `{"count":3,"type":"all_operations_effect_observed"}`
- Timeout frames: `1200`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 02. `shortage_prerequisite_wait` - Shortage and prerequisite wait

- Input contract: `[{"command_text":"공성전차 한 기로 앞마당을 압박해","frame":200,"preset":"single_tank_attack"},{"frame":260,"kind":"resource_observation","minerals":500,"structures":["FACTORY_TECHLAB","TERRAN_FACTORY"],"units":{"TERRAN_SIEGETANK":1},"vespene":125},{"frame":300,"kind":"unit_observation","units":[{"home_distance":16.0,"unit_type":"TERRAN_SIEGETANK"}]}]`
- Expected observation events: `["production_decision","prerequisite_wait","ownership_snapshot","submission","movement"]`
- Stop condition: `{"count":1,"type":"effect_after_prerequisite_convergence"}`
- Timeout frames: `1800`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 03. `safe_partial_launch` - Safe partial launch

- Input contract: `[{"command_text":"마린 최대 네 기로 공격하되 세 기면 안전하게 먼저 출발해","frame":300,"preset":"safe_partial_attack"},{"frame":340,"kind":"unit_observation","units":[{"home_distance":14.0,"unit_type":"TERRAN_MARINE"}]}]`
- Expected observation events: `["launch_decision","ownership_snapshot","submission","movement"]`
- Stop condition: `{"count":1,"type":"matching_effect_observed"}`
- Timeout frames: `900`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 04. `protected_minimum_partial_rejection` - Protected-minimum partial-launch rejection

- Input contract: `[{"command_text":"마린 네 기를 공격에 보내","frame":400,"preset":"strict_marine_attack"}]`
- Expected observation events: `["launch_decision","state_snapshot","rejection"]`
- Stop condition: `{"count":0,"operation_id":"strict-assault","preserved_state_fields":["units","owners","operations","structures","base_threatened"],"type":"forbidden_submission_and_state_preserved"}`
- Timeout frames: `300`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 05. `transfer_success` - Transfer success

- Input contract: `[{"command_text":"정찰과 공격과 방어 작전을 시작해","frame":500,"preset":"transfer_baseline"},{"frame":520,"kind":"unit_observation","units":[{"home_distance":10.0,"unit_type":"TERRAN_MARINE"},{"home_distance":8.0,"unit_type":"TERRAN_SIEGETANK"}]},{"command_text":"recon-alpha 마린 한 기를 assault-bravo로 이관해","frame":540,"preset":"transfer_one_marine"}]`
- Expected observation events: `["ownership_snapshot","transfer","blackboard_update","submission","movement"]`
- Stop condition: `{"count":1,"destination_operation_id":"assault-bravo","preserved_operation_ids":["defense-charlie"],"source_operation_id":"recon-alpha","type":"transfer_applied_and_siblings_preserved"}`
- Timeout frames: `900`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 06. `transfer_rejection_preserves_active` - Transfer rejection preserving active operations

- Input contract: `[{"command_text":"정찰과 공격 작전을 시작해","frame":600,"preset":"transfer_rejection_baseline"},{"frame":620,"kind":"unit_observation","units":[{"home_distance":10.0,"unit_type":"TERRAN_MARINE"}]},{"command_text":"recon-alpha 병력을 모두 assault-bravo로 넘겨","frame":640,"preset":"unsafe_transfer_all"}]`
- Expected observation events: `["state_snapshot","rejection","ownership_snapshot"]`
- Stop condition: `{"count":0,"expected_rejection_reason":"source_operation_minimum_violation","operation_id":"recon-alpha","preserved_active_operation_ids":["recon-alpha","assault-bravo"],"preserved_state_fields":["units","owners","operations","structures","base_threatened"],"type":"forbidden_submission_and_state_preserved"}`
- Timeout frames: `300`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 07. `reinforcement_generation_update` - Reinforcement generation update

- Input contract: `[{"command_text":"정찰과 마린 네 기 공격을 시작해","frame":700,"preset":"reinforcement_baseline"},{"frame":720,"kind":"unit_observation","units":[{"home_distance":10.0,"unit_type":"TERRAN_MARINE"}]},{"command_text":"assault-bravo를 마린 여섯 기로 증원해","frame":740,"preset":"reinforce_assault"},{"frame":780,"kind":"unit_observation","units":[{"home_distance":20.0,"unit_type":"TERRAN_MARINE"}]}]`
- Expected observation events: `["blackboard_update","generation_change","ownership_snapshot","submission","movement"]`
- Stop condition: `{"count":1,"type":"updated_generation_effect_observed"}`
- Timeout frames: `900`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 08. `retarget` - Retarget active operation

- Input contract: `[{"command_text":"assault-bravo로 적 앞마당을 공격해","frame":800,"preset":"retarget_baseline"},{"frame":820,"kind":"unit_observation","units":[{"home_distance":10.0,"unit_type":"TERRAN_MARINE"}]},{"command_text":"assault-bravo 목표를 적 본진으로 바꿔","frame":840,"preset":"retarget_enemy_main"},{"frame":880,"kind":"unit_observation","units":[{"home_distance":20.0,"unit_type":"TERRAN_MARINE"}]}]`
- Expected observation events: `["blackboard_update","generation_change","squad_order","submission","movement"]`
- Stop condition: `{"count":1,"type":"updated_generation_effect_observed"}`
- Timeout frames: `900`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 09. `selective_cancellation` - Selective cancellation

- Input contract: `[{"command_text":"정찰과 공격 작전을 병렬로 시작해","frame":900,"preset":"selective_cancel_baseline"},{"frame":920,"kind":"unit_observation","units":[{"home_distance":10.0,"unit_type":"TERRAN_MARINE"},{"home_distance":8.0,"unit_type":"TERRAN_SIEGETANK"}]},{"command_text":"recon-alpha 정찰만 취소해","frame":940,"preset":"cancel_recon_only"},{"frame":980,"kind":"unit_observation","units":[{"home_distance":18.0,"unit_type":"TERRAN_SIEGETANK"}]}]`
- Expected observation events: `["blackboard_update","cancellation","state_snapshot","submission","movement"]`
- Stop condition: `{"count":1,"selected_operation_id":"recon-alpha","sibling_operation_id":"assault-bravo","type":"selected_operation_cancelled_sibling_active"}`
- Timeout frames: `600`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 10. `emergency_preemption` - Emergency preemption

- Input contract: `[{"command_text":"마린으로 적 본진을 공격해","frame":1000,"preset":"emergency_attack_baseline"},{"frame":1020,"kind":"unit_observation","units":[{"home_distance":12.0,"unit_type":"TERRAN_MARINE"}]},{"command_text":"즉시 후퇴해서 병력을 보존해","frame":1040,"preset":"emergency_retreat"},{"frame":1060,"kind":"unit_observation","units":[{"home_distance":22.0,"unit_type":"TERRAN_MARINE"}]}]`
- Expected observation events: `["blackboard_update","preemption","squad_order","submission","movement"]`
- Stop condition: `{"count":1,"type":"affected_offense_preempted"}`
- Timeout frames: `300`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 11. `autonomous_defense_restoration` - Autonomous defense and restoration

- Input contract: `[{"command_text":"마린 네 기로 공격해","frame":1100,"preset":"autonomous_defense_attack"},{"base_id":"self_main","frame":1120,"kind":"base_threat_observation","required_defenders":2,"threat_strength":0.8},{"base_id":"self_main","frame":1180,"kind":"base_clear_observation","required_defenders":0,"threat_strength":0.0},{"frame":1220,"kind":"unit_observation","units":[{"home_distance":18.0,"unit_type":"TERRAN_MARINE"}]}]`
- Expected observation events: `["autonomous_defense","launch_decision","ownership_snapshot","submission","movement"]`
- Stop condition: `{"count":1,"type":"offense_restored_after_defense"}`
- Timeout frames: `900`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 12. `all_terran_family_ability_blocker_matrix` - All-Terran family, ability, and blocker matrix

- Input contract: `[{"command_text":"모든 테란 전투 유닛 계열의 작전과 능력을 검증해","frame":1200,"preset":"all_terran_matrix"},{"frame":1240,"kind":"unit_observation","matrix_observed_abilities":true}]`
- Expected observation events: `["terran_lowering","production_decision","family_action_attempt","submission","ability_effect"]`
- Stop condition: `{"count":15,"type":"all_terran_families_accounted"}`
- Timeout frames: `2400`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 13. `event_reconnect_replay` - Event reconnect and replay

- Input contract: `[{"command_text":"정찰 작전을 시작해","frame":1300,"preset":"reconnect_recon"},{"frame":1340,"kind":"unit_observation","units":[{"home_distance":14.0,"unit_type":"TERRAN_MARINE"}]},{"frame":1360,"kind":"client_reconnect"}]`
- Expected observation events: `["web_event","replay_batch","replay_deduplicated"]`
- Stop condition: `{"count":1,"type":"replayed_events_counted_once"}`
- Timeout frames: `300`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

### 14. `voice_readback_callout_identity` - Voice readback and callout identity

The identifier is retained because it is part of the checked-in deterministic pre-live journey manifest. It verifies projection identity in fixtures; it does not assert that the current compact controller ships Tactical Radio, TTS, readback, mute, or waveform UI.

- Input contract: `[{"command_text":"마린 두 기 정찰을 음성으로 확인해","frame":1400,"preset":"voice_recon"},{"frame":1460,"kind":"unit_observation","units":[{"home_distance":16.0,"unit_type":"TERRAN_MARINE"}]}]`
- Expected observation events: `["web_projection","hud_projection","voice_projection","voice_callout"]`
- Stop condition: `{"count":3,"type":"projection_identity_consistent"}`
- Timeout frames: `600`
- Artifact capture: command input, authoritative operation projection, matching submission/effect evidence, compact-controller/HUD identity where applicable, and pass/fail notes.
- Manual stop: stop on missing submission, mismatched generation or frame, duplicate ownership, unsafe launch, false success wording, stale replay, or controller/HUD inconsistency.

## Actual SC2 Visual And Audio Gate

- Verify movement and engagement are visibly caused by the matching operation submission rather than unrelated autonomous behavior.
- Verify the compact controller, HUD, and telemetry use the same `update_id + operation_id + generation + stage + game_frame`.
- Verify the controller never presents `published`, assignment, or submission as movement, engagement, completion, or another actual SC2 effect.
- Verify one controller instance is maintained, no duplicate browser/cockpit opens, and the controller may be hidden while StarCraft II is the frontmost gameplay window.
- Any failed or ambiguous observation keeps `manual_live_qa_remaining=true` and must not be reported as live qualified.

## Report Commands

Generate the post-merge readiness report with:

```bash
python3 -m starcraft_commander.micromachine_final_release report \
  --mode ready_for_live_qa \
  --artifact-root <downloaded-child-artifacts> \
  --artifact <producer>=<envelope.json> \
  --repository-sha <exact-main-sha> \
  --workflow-sha <trusted-workflow-sha> \
  --build-identity <sha256:build-identity> \
  --workflow-run-id <run-id> \
  --run-attempt <attempt> \
  --replay-ledger <private-ledger.json> \
  --output-json ready_for_live_qa.json \
  --output-markdown ready_for_live_qa.md
```

Validate this generated runbook and structured status with:

```bash
python3 -m starcraft_commander.micromachine_final_release check-status
```

## Deferred Scope

Human multiplayer, ladder, Battle.net qualification, and competitive balance signoff remain deferred. They are not implied by either automated readiness mode.
