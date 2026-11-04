# Intent-fidelity chain replay (generated)

Scope: offline_python_chain_only; SC2 not available; not live evidence. Production-mode Direct runtime: `dry_run_fake_bot` (scripted fake bot).

Cases: 67. Open invariant violations: 15. Expectation mismatches: 0.

## par-scout-attack

- **Utterance:** 마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격
- **Semantic ask:** Two independent concurrent operations: 2 marines scout enemy main, 4 marines attack via the right flank.
- step 0 (frame 100): `마린 두 기는 정찰하고 네 기는 오른쪽 길로 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[scout-a#1×2, atk-b#1×4] direct=`—` → UI: blocked [blocked] — Terran family evidence has not reached assignment: marine
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[scout-a#1×2, atk-b#1×4] direct=`executed` → UI: simulated [simulated]
- ⚠ **one_unit_one_operation** (production, step 0): tag 13 ordered by both scout-a and atk-b

## par-attack-defend-en

- **Utterance:** Send 4 marines to attack the natural and keep 2 at the ramp
- **Semantic ask:** Attack and defend operations run concurrently with their own counts.
- step 0 (frame 100): `Send 4 marines to attack the natural and keep 2 at the ramp`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk-n#1×4, def-ramp#1×2] direct=`—` → UI: effect_observed [assigned,ordered,effect_observed]
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[atk-n#1×4, def-ramp#1×2] direct=`refused` → UI: blocked [blocked] — action 'move_group' was refused without issuing orders: unresolvable_target.
- ⚠ **one_unit_one_operation** (production, step 0): tag 13 ordered by both atk-n and def-ramp

## par-three-ops-ground

- **Utterance:** 마린 2기 정찰, 마린 3기 램프 방어, 마린 4기 압박
- **Semantic ask:** Three concurrent operations with the same unit type (needs 9 distinct marines).
- step 0 (frame 100): `마린 2기 정찰, 마린 3기 램프 방어, 마린 4기 압박`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[sc#1×2, df#1×3, pr#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[sc#1×2, df#1×3, pr#1×4] direct=`refused` → UI: blocked [blocked] — action 'move_group' was refused without issuing orders: unresolvable_target.
- ⚠ **one_unit_one_operation** (production, step 0): tag 13 ordered by both sc and df
- ⚠ **one_unit_one_operation** (production, step 0): tag 13 ordered by both sc and pr

## par-mixed-units

- **Utterance:** 마린 2기 정찰, 탱크 1기 램프 방어, 바이킹 2기 공중 견제
- **Semantic ask:** Three operations over different unit families; no unit competes with another.
- step 0 (frame 100): `마린 2기 정찰, 탱크 1기 램프 방어, 바이킹 2기 공중 견제`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[sc#1×2, df#1×1, hr#1×2] direct=`—` → UI: assigned [assigned] — Terran family evidence has not reached order: siege_tank
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[sc#1×2, df#1×1, hr#1×2] direct=`refused` → UI: blocked [blocked] — action 'move_group' was refused without issuing orders: unresolvable_target.
- ⚠ **one_unit_one_operation** (production, step 0): tag 13 ordered by both sc and hr

## par-flank-split

- **Utterance:** 왼쪽으로 3기 우회 공격, 오른쪽으로 3기 우회 공격
- **Semantic ask:** Two attack operations with opposite flank routes.
- step 0 (frame 100): `왼쪽으로 3기 우회 공격, 오른쪽으로 3기 우회 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[fl#1×3, fr#1×3] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[fl#1×3, fr#1×3] direct=`executed` → UI: simulated [simulated]
- ⚠ **one_unit_one_operation** (production, step 0): tag 13 ordered by both fl and fr

## same-id-retarget

- **Utterance:** 공격 목표를 본진으로 바꿔
- **Semantic ask:** The existing attack operation changes target; it must stay ONE operation, generation 2.
- step 0 (frame 100): `마린 4기로 앞마당 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 160): `공격 목표를 본진으로 바꿔`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#2×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`executed` → UI: simulated [simulated]

## reinforce-count

- **Utterance:** 공격 부대에 마린 2기 더
- **Semantic ask:** Same operation, count 4 -> 6, generation 2, no second operation.
- step 0 (frame 100): `마린 4기로 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 150): `공격 부대에 마린 2기 더`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#2×6] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×6] direct=`executed` → UI: simulated [simulated]

## different-id-parallel

- **Utterance:** 마린 2기 정찰 보내, 그리고 따로 4기 공격
- **Semantic ask:** A second command with a NEW id must run alongside the first, not replace it.
- step 0 (frame 100): `마린 2기 정찰 보내`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[sc#1×2] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[sc#1×2] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 140): `그리고 따로 4기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[sc#1×2, atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`conflict` → UI: blocked [blocked] — unit tags already owned: [13, 14]

## cancel-one-keep-other

- **Utterance:** 공격만 취소하고 정찰은 계속해
- **Semantic ask:** Cancel exactly one operation; the scout operation keeps running.
- step 0 (frame 100): `정찰과 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[sc#1×2, atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[sc#1×2, atk#1×4] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 160): `공격만 취소하고 정찰은 계속해`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[sc#1×2, atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`refused` → UI: blocked [blocked] — Direct command fid-prod-0 also owns operations ['sc']; per-operation release is not supported, so it was left untouched.
- step 2 (frame 220): `정찰 상황`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[sc#2×2] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[sc#1×2] direct=`conflict` → UI: blocked [blocked] — unit tags already owned: [13, 14]
- ⚠ **one_unit_one_operation** (production, step 0): tag 13 ordered by both sc and atk

## restart-after-cancel

- **Utterance:** 취소했던 공격 다시 시작해
- **Semantic ask:** Restart a cancelled operation under the same id: new generation, not generation reuse.
- step 0 (frame 100): `공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 140): `공격 취소`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`released` → UI: simulated [simulated]
- step 2 (frame 200): `취소했던 공격 다시 시작해`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#2×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`executed` → UI: simulated [simulated]

## transfer-between-ops

- **Utterance:** 정찰하던 마린 1기를 공격 부대로 보내
- **Semantic ask:** Move one marine between two named operations (before/after counts change by exactly one).
- step 0 (frame 100): `정찰2 공격3`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[sc#1×2, atk#1×3] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[sc#1×2, atk#1×3] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 160): `정찰하던 마린 1기를 공격 부대로`
  - projection: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — transfer operation edits require a before_composition snapshot.
  - production: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — transfer operation edits require a before_composition snapshot.
- ⚠ **one_unit_one_operation** (production, step 0): tag 13 ordered by both sc and atk

## shortfall-assigned

- **Utterance:** 마린 4기로 공격해
- **Semantic ask:** User asked for exactly 4; the director assigned only 1 -> shortfall must be surfaced, never 'assigned'.
- step 0 (frame 100): `마린 4기로 공격해`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: blocked [blocked] — Terran family evidence has not reached assignment: marine
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`executed` → UI: simulated [simulated]

## shortfall-direct

- **Utterance:** 마린 10기 공격
- **Semantic ask:** Only 6 marines exist in the dry-run world: the Direct path must report partial fulfilment.
- step 0 (frame 100): `마린 10기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[big#1×10] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[big#1×10] direct=`partial` → UI: blocked [blocked] — only 6 of 10 requested orders were issued for action 'attack_move'.

## count-string

- **Utterance:** 마린 "둘" 정찰
- **Semantic ask:** A non-integer count is refused with a reason (not guessed as 1).
- step 0 (frame 100): `마린 둘 정찰`
  - projection: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — composition_requirements[0].count must be a positive integer, got '2'.
  - production: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — composition_requirements[0].count must be a positive integer, got '2'.

## count-zero

- **Utterance:** 마린 0기 정찰
- **Semantic ask:** Zero units is refused, not silently promoted to 1.
- step 0 (frame 100): `마린 0기 정찰`
  - projection: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — composition_requirements[0].count must be at least 1, got 0.
  - production: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — composition_requirements[0].count must be at least 1, got 0.

## count-over-limit

- **Utterance:** 마린 300기 공격
- **Semantic ask:** Counts above the DSL maximum (200) are refused, not clamped.
- step 0 (frame 100): `마린 300기 공격`
  - projection: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — composition_requirements[0].count must be at most 200, got 300.
  - production: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — composition_requirements[0].count must be at most 200, got 300.

## count-merge-exact

- **Utterance:** 마린 2기랑 마린 3기로 공격
- **Semantic ask:** Same unit+role listed twice merges to exactly 5.
- step 0 (frame 100): `마린 2기랑 마린 3기로 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×5] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×5] direct=`executed` → UI: simulated [simulated]

## ttl-clamp

- **Utterance:** 계속 마린 생산해 (5000초)
- **Semantic ask:** A requested duration beyond the DSL limit must be reported as clamped.
- step 0 (frame 100): `계속 마린 생산해`
  - projection: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none]

## ownership-conflict-telemetry

- **Utterance:** 마린 2기 정찰, 마린 2기 공격
- **Semantic ask:** Telemetry reports the SAME unit tags in two operations: both must be reported blocked with a reason.
- step 0 (frame 100): `마린 2기 정찰, 2기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[sc#1×2, atk#1×2] direct=`—` → UI: blocked [blocked] — Duplicate unit ownership detected for tags: 4000000097,4000000098

## ownership-direct-conflict

- **Utterance:** 마린 2기 공격해, 마린 2기 또 공격해
- **Semantic ask:** Second Direct command over the same marines is refused with a displayable reason.
- step 0 (frame 100): `마린 2기 공격해`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[a1#1×2] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 120): `마린 2기 또 공격해`
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[a2#1×2] direct=`conflict` → UI: blocked [blocked] — unit tags already owned: [13, 14]

## macro-persists-through-op

- **Utterance:** 마린 계속 생산하고 2기는 정찰
- **Semantic ask:** The standing production order survives a later scout command.
- step 0 (frame 100): `마린 계속 생산`
  - projection: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none]
- step 1 (frame 140): `2기는 정찰`
  - projection: status `published` compile `compiled` published=True layers=['macro', 'operation'] ops=[sc#1×2] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[sc#1×2] direct=`executed` → UI: simulated [simulated]

## macro-supersede

- **Utterance:** 마린 말고 탱크 생산해
- **Semantic ask:** A newer macro command replaces the old production focus, not merges forever.
- step 0 (frame 100): `마린 생산`
  - projection: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none]
- step 1 (frame 140): `마린 말고 탱크 생산해`
  - projection: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none]

## macro-default-ttl-expiry

- **Utterance:** 마린 생산해 (이후 한참 뒤 정찰)
- **Semantic ask:** A macro order published at frame 100 expires at 19900 (900 s cap) but is still merged into a command issued at frame 40000 (open finding F-9).
- step 0 (frame 100): `마린 생산`
  - projection: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none]
- step 1 (frame 40000): `정찰`
  - projection: status `published` compile `compiled` published=True layers=['macro', 'operation'] ops=[sc#1×2] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[sc#1×2] direct=`executed` → UI: simulated [simulated]

## macro-standing-survives-gap

- **Utterance:** 마린 계속 생산하고 한참 뒤 정찰
- **Semantic ask:** A standing_order macro survives past the default TTL.
- step 0 (frame 100): `마린 계속 생산`
  - projection: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none]
- step 1 (frame 40000): `정찰`
  - projection: status `published` compile `compiled` published=True layers=['macro', 'operation'] ops=[sc#1×2] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[sc#1×2] direct=`executed` → UI: simulated [simulated]

## emergency-retreat-ko

- **Utterance:** 즉시 후퇴
- **Semantic ask:** Whole-army retreat now.
- step 0 (frame 100): `즉시 후퇴`
  - projection: status `published` compile `compiled` published=True layers=['emergency'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[—] direct=`executed` → UI: simulated [simulated]
- ⚠ **no_silent_clamp** (projection, step 0): ttl asked 45, applied 60

## emergency-cancel-attack-only

- **Utterance:** 공격 취소
- **Semantic ask:** ONLY stop the attacks; do not send the army home.
- step 0 (frame 100): `공격 취소`
  - projection: status `published` compile `compiled` published=True layers=['emergency'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[—] direct=`executed` → UI: simulated [simulated]
- ⚠ **emergency_fastpath_matches_ask** (router, step -1): force_retreat=True but the user asked False

## emergency-scoped-marines-only

- **Utterance:** 마린만 후퇴해, 탱크는 그대로
- **Semantic ask:** Retreat only marines; tanks keep their orders. The regex fast path cannot express scope.
- step 0 (frame 100): `마린만 후퇴`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[rt#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[rt#1×4] direct=`executed` → UI: simulated [simulated]

## emergency-keeps-macro

- **Utterance:** 마린 계속 생산 중에 후퇴
- **Semantic ask:** Retreat preempts attack/scout layers but must not erase the standing production order.
- step 0 (frame 100): `마린 계속 생산`
  - projection: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none]
- step 1 (frame 120): `공격`
  - projection: status `published` compile `compiled` published=True layers=['macro', 'operation'] ops=[atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`executed` → UI: simulated [simulated]
- step 2 (frame 160): `후퇴`
  - projection: status `published` compile `compiled` published=True layers=['emergency'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[—] direct=`executed` → UI: simulated [simulated]
- ⚠ **no_silent_clamp** (projection, step 2): ttl asked 45, applied 60
- ⚠ **emergency_preempts_only_affected_layers** (projection, step 2): layers lost ['macro'] (kept ['emergency'])
- ⚠ **emergency_preempts_only_affected_layers** (projection, step 2): macro standing order ['TERRAN_MARINE'] erased by emergency

## emergency-preempts-direct-lease

- **Utterance:** 공격 중 후퇴
- **Semantic ask:** Retreat releases the attack lease (and only combat leases).
- step 0 (frame 100): `마린 4기 공격`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×4] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 140): `후퇴`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[—] direct=`executed` → UI: simulated [simulated]

## emergency-then-macro

- **Utterance:** 후퇴 뒤에 다시 마린 생산
- **Semantic ask:** A macro command after an emergency publishes normally.
- step 0 (frame 100): `후퇴`
  - projection: status `published` compile `compiled` published=True layers=['emergency'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[—] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 160): `다시 마린 생산`
  - projection: status `published` compile `compiled` published=True layers=['macro', 'emergency'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none]
- ⚠ **no_silent_clamp** (projection, step 0): ttl asked 45, applied 60

## emergency-hold

- **Utterance:** 그 자리에서 버텨
- **Semantic ask:** Hold position only: no retreat, no cancel.
- step 0 (frame 100): `그 자리에서 버텨`
  - projection: status `published` compile `compiled` published=True layers=['emergency'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[—] direct=`executed` → UI: simulated [simulated]
- ⚠ **no_silent_clamp** (projection, step 0): ttl asked 45, applied 60

## negated-retreat

- **Utterance:** 후퇴하지 마
- **Semantic ask:** Negation: no emergency fast path, nothing retreats.
- step 0 (frame 100): `후퇴하지 마`
  - projection: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none]

## partial-retreat-split

- **Utterance:** 탱크는 후퇴하고 마린은 계속 공격
- **Semantic ask:** Tanks retreat while marines keep attacking: two parallel operations, NOT a global emergency.
- step 0 (frame 100): `탱크는 후퇴하고 마린은 계속 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[tk#1×2, mr#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[tk#1×2, mr#1×4] direct=`executed` → UI: simulated [simulated]

## micro-stim

- **Utterance:** 스팀팩 써
- **Semantic ask:** One bounded stim for the marines.
- step 0 (frame 100): `스팀팩 써`
  - projection: status `published` compile `compiled` published=True layers=['micro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[—] direct=`refused` → UI: blocked [blocked] — action 'execute_ability' was refused without issuing orders: insufficient_casters.

## micro-siege

- **Utterance:** 탱크 시즈모드
- **Semantic ask:** Siege the tanks.
- step 0 (frame 100): `탱크 시즈모드`
  - projection: status `published` compile `compiled` published=True layers=['micro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[—] direct=`refused` → UI: blocked [blocked] — action 'execute_ability' was refused without issuing orders: unresolvable_ability.

## micro-nuke

- **Utterance:** 적 본진에 핵 써
- **Semantic ask:** Tactical nuke at the enemy main (needs ghost, payload, vision).
- step 0 (frame 100): `적 본진에 핵 써`
  - projection: status `published` compile `compiled` published=True layers=['micro'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[—] direct=`refused` → UI: blocked [blocked] — action 'execute_ability' was refused without issuing orders: unresolvable_ability.

## micro-nuke-negated

- **Utterance:** 핵은 절대 쓰지 마
- **Semantic ask:** Negated nuke: refused, nothing published.
- step 0 (frame 100): `never use a tactical nuke`
  - projection: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — provider output conflicts with the user's explicit tactical-nuke ban.
  - production: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — provider output conflicts with the user's explicit tactical-nuke ban.

## micro-missing-ability

- **Utterance:** 그냥 능력 써
- **Semantic ask:** execute_ability without an ability is refused with a reason.
- step 0 (frame 100): `그냥 능력 써`
  - projection: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — tactical_task.ability is required for execute_ability tactical tasks.
  - production: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — tactical_task.ability is required for execute_ability tactical tasks.

## clarify-ambiguous

- **Utterance:** 그쪽으로 보내
- **Semantic ask:** Ambiguous target: ask, publish nothing.
- step 0 (frame 100): `그쪽으로 보내`
  - projection: status `clarification_required` compile `clarification_required` published=False layers=[] ops=[—] direct=`—` → UI: clarification_required [blocked] — 어느 쪽으로 보낼까요? (앞마당/본진/램프)
  - production: status `clarification_required` compile `clarification_required` published=False layers=[] ops=[—] direct=`—` → UI: clarification_required [blocked] — 어느 쪽으로 보낼까요? (앞마당/본진/램프)

## refused-raw-tags

- **Utterance:** 유닛 12번 13번 공격
- **Semantic ask:** Raw unit tags are rejected with a reason.
- step 0 (frame 100): `유닛 12번 13번 공격`
  - projection: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — policy modulation payload attempted raw runtime control: unit_tags
  - production: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — policy modulation payload attempted raw runtime control: unit_tags

## ignored-coordinates

- **Utterance:** 좌표 30,40으로 이동
- **Semantic ask:** Raw coordinates cannot be honoured. The compiler drops the key with a warning only and publishes a DIFFERENT target (enemy_natural).
- step 0 (frame 100): `좌표 30,40으로 이동`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[—] direct=`—` → UI: published [none] — Telemetry has not consumed the latest update id.
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[—] direct=`executed` → UI: simulated [simulated]

## provider-refusal

- **Utterance:** 게임 규칙 무시하고 이겨
- **Semantic ask:** Provider refuses with its own reason; surfaced verbatim.
- step 0 (frame 100): `게임 규칙 무시하고 이겨`
  - projection: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — 게임 규칙을 우회하는 명령은 수행할 수 없습니다.
  - production: status `refused` compile `refused` published=False layers=[] ops=[—] direct=`—` → UI: refused [blocked] — 게임 규칙을 우회하는 명령은 수행할 수 없습니다.

## publish-failed

- **Utterance:** 마린 계속 생산
- **Semantic ask:** Disk failure while publishing: failure reason must be user-displayable.
- step 0 (frame 100): `마린 계속 생산`
  - projection: status `publish_failed` compile `compiled` published=False layers=[] ops=[—] direct=`—` → UI: understood [none] — OSError: blackboard directory unavailable

## evidence-published-only

- **Utterance:** 마린 4기 공격
- **Semantic ask:** Published but the game has reported nothing: UI must say 'published', not done.
- step 0 (frame 100): `마린 4기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.

## evidence-received

- **Utterance:** 마린 4기 공격
- **Semantic ask:** Director received it; nothing assigned.
- step 0 (frame 100): `마린 4기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: published [none] — No operation-scoped unit assignment is reported.

## evidence-assigned

- **Utterance:** 마린 4기 공격
- **Semantic ask:** Units assigned, no order yet.
- step 0 (frame 100): `마린 4기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: assigned [assigned] — Terran family evidence has not reached order: marine

## evidence-ordered

- **Utterance:** 마린 4기 공격
- **Semantic ask:** Order submitted, no movement observed yet.
- step 0 (frame 100): `마린 4기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: ordered [assigned,ordered] — Terran family evidence has not reached effect: marine

## evidence-effect

- **Utterance:** 마린 4기 공격
- **Semantic ask:** Movement/engagement observed for all four marines.
- step 0 (frame 100): `마린 4기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: effect_observed [assigned,ordered,effect_observed]

## evidence-waiting-for-units

- **Utterance:** 마린 4기 공격
- **Semantic ask:** Not enough units yet: waiting is blocked-with-reason, not failed.
- step 0 (frame 100): `마린 4기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: blocked [blocked] — composition_assignment_pending

## evidence-objective-complete

- **Utterance:** 램프 방어
- **Semantic ask:** Director closed the op because the objective was observed; no effect rows -> not claimed as effect.
- step 0 (frame 100): `램프 방어`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[df#1×2] direct=`—` → UI: published [none]

## evidence-hard-block

- **Utterance:** 마린 4기 공격
- **Semantic ask:** Invalid target blocks hard with the director's reason.
- step 0 (frame 100): `마린 4기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: blocked [blocked] — invalid_target

## evidence-cancelled

- **Utterance:** 공격 취소
- **Semantic ask:** Director confirms cancellation.
- step 0 (frame 100): `공격 취소`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: blocked [blocked] — cancelled_by_policy

## evidence-expired

- **Utterance:** 마린 4기 공격
- **Semantic ask:** Operation expired before any effect.
- step 0 (frame 100): `마린 4기 공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: blocked [blocked] — policy_inactive_or_expired

## evidence-stale-generation

- **Utterance:** 공격 목표 변경
- **Semantic ask:** Telemetry still describes generation 1 after a retarget: must not count as progress of generation 2.
- step 0 (frame 100): `공격`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#1×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.
- step 1 (frame 160): `공격 목표 변경`
  - projection: status `published` compile `compiled` published=True layers=['operation'] ops=[atk#2×4] direct=`—` → UI: published [none] — OperationDirector has not reported receipt for this operation.

## macro-evidence-consumed

- **Utterance:** 마린 계속 생산
- **Semantic ask:** Macro consumed by the manager: still no production/effect claim.
- step 0 (frame 100): `마린 계속 생산`
  - projection: status `published` compile `compiled` published=True layers=['macro'] ops=[—] direct=`—` → UI: published [none] — No manager queue or squad assignment evidence.

## direct-cancel-releases

- **Utterance:** 공격 취소하고 다시 공격
- **Semantic ask:** Cancel releases ownership so the same marines can be re-ordered.
- step 0 (frame 100): `공격`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×2] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 120): `공격 취소`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×2] direct=`released` → UI: simulated [simulated]
- step 2 (frame 140): `다시 공격`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk2#1×2] direct=`executed` → UI: simulated [simulated]

## direct-retarget-supersedes

- **Utterance:** 목표를 본진으로
- **Semantic ask:** Retarget supersedes the operation's own lease and re-dispatches.
- step 0 (frame 100): `공격`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×2] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 120): `목표를 본진으로`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×2] direct=`executed` → UI: simulated [simulated]

## direct-cancel-unknown

- **Utterance:** 없는 작전 취소
- **Semantic ask:** Cancelling an operation that owns nothing is refused, not executed.
- step 0 (frame 100): `정찰 취소`
  - production: status `direct_failed` compile `compiled` published=False layers=[] ops=[ghost#1×1] direct=`refused` → UI: blocked [blocked] — No active Direct command owns operation ghost.

## direct-lease-ttl-release

- **Utterance:** 공격 후 한참 뒤 다시 공격
- **Semantic ask:** TTL expiry releases ownership so later commands are not blocked forever.
- step 0 (frame 100): `공격`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk#1×2] direct=`executed` → UI: simulated [simulated]
- step 1 (frame 600): `다시 공격`
  - production: status `direct_executed` compile `compiled` published=False layers=[] ops=[atk2#1×2] direct=`executed` → UI: simulated [simulated]

## rec-14-marine-scout

- **Utterance:** (recorded) 마린 1기 정찰
- **Semantic ask:** Recorded SC2 run: scout reached order but family evidence stops at action.
- step 0 (frame 3634): `recorded 14-marine-scout`
  - recorded evidence: {"14-marine-scout": {"state": "order_issued", "achieved_stage": "order_issued"}}
  - UI claim: ordered [assigned,ordered] — Terran family evidence has not reached action: marine

## rec-15-parallel

- **Utterance:** (recorded) 공격+방어 동시
- **Semantic ask:** Recorded SC2 run: attack blocked by production, defense closed by objective.
- step 0 (frame 4254): `recorded 15-parallel-attack-defense`
  - recorded evidence: {"15-parallel-attack-defense-attack": {"state": "blocked", "achieved_stage": "effect_observed"}, "15-parallel-attack-defense-defense": {"state": "objective_observed_complete", "achieved_stage": "consumed_by_manager"}}
  - UI claim: blocked [blocked] — training

## rec-16-reinforce

- **Utterance:** (recorded) 방어+증원
- **Semantic ask:** Recorded: defense moving, reinforcement waiting for units.
- step 0 (frame 3477): `recorded 16-defense-reinforcement`
  - recorded evidence: {"16-defense-reinforcement-defense": {"state": "blocked", "achieved_stage": "effect_observed"}, "16-defense-reinforcement-reinforcement": {"state": "blocked", "achieved_stage": "consumed_by_manager"}}
  - UI claim: blocked [blocked] — producer_busy

## rec-21-cancel

- **Utterance:** (recorded) 공격 취소
- **Semantic ask:** Recorded: director cancelled the attack.
- step 0 (frame 4048): `recorded 21-cancel-attack`
  - recorded evidence: {"21-cancel-attack": {"state": "cancelled", "achieved_stage": "consumed_by_manager"}}
  - UI claim: blocked [blocked] — cancelled_by_policy

## rec-24-retarget

- **Utterance:** (recorded) 목표 변경
- **Semantic ask:** Recorded: generation 2 submitted but 1 of 2 marines represented.
- step 0 (frame 3677): `recorded 24-retarget`
  - recorded evidence: {"24-retarget": {"state": "blocked", "achieved_stage": "consumed_by_manager"}}
  - UI claim: blocked [blocked] — minerals_pending

## rec-27-prereq-wait

- **Utterance:** (recorded) 전차 방어 대기
- **Semantic ask:** Recorded: waiting for a tank that does not exist yet.
- step 0 (frame 4268): `recorded 27-prerequisite-wait`
  - recorded evidence: {"27-prerequisite-wait": {"state": "blocked", "achieved_stage": "consumed_by_manager"}}
  - UI claim: blocked [blocked] — composition_assignment_pending

## rec-22-reassign

- **Utterance:** (recorded) 마린 재배치
- **Semantic ask:** Recorded: destination completed, source still moving.
- step 0 (frame 4692): `recorded 22-reassign-marines`
  - recorded evidence: {"22-reassign-marines-source": {"state": "moving", "achieved_stage": "consumed_by_manager"}, "22-reassign-marines-destination": {"state": "objective_observed_complete", "achieved_stage": "consumed_by_manager"}}
  - UI claim: published [none] — No operation-scoped unit assignment is reported.

## rec-06-macro-marine-wave

- **Utterance:** (recorded) 마린 웨이브
- **Semantic ask:** Recorded macro: latest-only snapshot.
- step 0 (frame 4722): `recorded 06-marine-wave`
  - recorded evidence: {"<legacy>": {"state": "effect_observed", "achieved_stage": "action_issued"}}
  - UI claim: ordered [assigned,ordered] — No observed unit movement, tactical effect, or production effect.

