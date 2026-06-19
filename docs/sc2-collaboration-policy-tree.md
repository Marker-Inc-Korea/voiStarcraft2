# SC2 Collaboration Policy Tree

Issue #10 asks whether there is a StarCraft II equivalent of a strong BWAPI bot
such as PurpleWave, and whether voiStarcraft2 can use that style while still
letting the human intervene.

## Short Answer

There are strong StarCraft II bots and frameworks, but there is no obvious
drop-in equivalent that gives this project all three properties at once:

1. Strong real-time SC2 play.
2. Transparent behavior-tree or policy-level control.
3. Human-in-the-loop intervention through Korean natural language.

For voiStarcraft2, the pragmatic route is to keep the existing python-sc2
executor boundary and add a human-interruptible policy tree above it. The LLM or
a future SOTA strategy selector may choose a bounded strategy profile, but the
profile can only activate deterministic policy leaves or recommend Korean
utterances that still pass the existing Intent DSL, feasibility, planner, and
executor gates.

## Bot Landscape

| Family | Useful idea | Why not directly adopt as product core |
| --- | --- | --- |
| AlphaStar-style research agents | Very strong learned SC2 play. | Not a transparent, user-interruptible product bot; not practical as a local hackable controller. |
| SC2 AI ladder bots | Mature hand-authored strategies and tactical code. | Usually built as autonomous bots, not Korean-command collaborative control surfaces. |
| MicroMachine-style SC2 bots | Strong deterministic tactical policies. | Good reference for hand-authored policy leaves, but not an intent/LLM/human intervention architecture by itself. |
| Sharpy/python-sc2 frameworks | Useful behavior managers and bot architecture ideas. | Framework adoption would be a larger migration; current repo already has python-sc2 seams and tests. |
| Issue #10 LLM + behavior-tree paper | Best architectural match: LLM chooses high-level strategy, behavior tree executes. | Needs local adaptation so LLM output cannot bypass safety gates. |

## Proposed Shape

```text
Human command / model strategy suggestion
  -> CommanderPolicyTree
     -> bounded strategy profile
     -> deterministic policy leaves
     -> standing orders or recommended utterances only
  -> existing typed Intent DSL
  -> feasibility validator
  -> SC2 action planner
  -> runtime executor
  -> python-sc2 adapter
```

The first implementation in this branch adds only the policy-tree seam:

- `manual_control`: human direct control; no autonomous policy leaves.
- `safe_macro`: continuous SCV production, supply-block prevention, scout/hold
  recommendations.
- `information_first`: macro stability plus scouting recommendations.
- `defensive_hold`: macro stability plus ramp defense recommendations.
- `pressure_when_safe`: macro stability plus pressure recommendations, still
  gated by fresh feasibility checks.

## Human Intervention Contract

The tree is designed so a user can always intervene:

- `human_override=manual`, `pause`, `hold`, or `stop` forces `manual_control`.
- `allow_autonomy=False` disables automated policy leaves.
- Unknown profiles are rejected with a reason and activate nothing.
- Model output containing raw API or python-sc2 action keys is rejected.
- The policy tree never calls python-sc2 and never mutates game state.

## Why This Fits voiStarcraft2

The project already has the right lower-level architecture:

- Korean command routing.
- Typed Intent DSL.
- Conservative feasibility validation.
- Semantic SC2 planner and executor.
- Standing orders that run in the game loop without per-frame LLM calls.
- Web dashboard state and briefing.

The policy tree is the missing middle layer between "SOTA model suggests
strategy" and "deterministic controller executes safely." It gives us a place
to plug in behavior-tree ideas without replacing the working command pipeline.

## Next Steps

1. Surface `CommanderPolicyTree.to_dict()` in `/api/state` for dashboard
   observability.
2. Add UI controls for strategy profile and manual pause.
3. Let a bounded LLM strategy selector propose only `strategy_profile`,
   `human_override`, and `allow_autonomy`.
4. Convert `recommended_utterances` into queued commands only after explicit
   human approval or a separately approved autonomous mode.
5. Add more leaves for scout, rally, defend, and production policies once each
   leaf can explain its trigger and stop condition.

