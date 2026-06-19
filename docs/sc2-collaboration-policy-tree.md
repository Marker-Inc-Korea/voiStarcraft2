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

For voiStarcraft2, the practical non-neural strong-bot target is MicroMachine,
not CommandCenter and not python-sc2. MicroMachine is public and historically
strong enough to study as a policy source, while Deimos and Eris are stronger
current AI Arena references but not directly adoptable because their bot logic
is not publicly downloadable. See [micromachine-adoption.md](micromachine-adoption.md).
The complete issue #10 architecture, evaluation contract, and stop conditions
are tracked in
[issue-10-policy-tree-collaboration.md](issue-10-policy-tree-collaboration.md).

The pragmatic route is to keep the existing python-sc2 executor boundary for
the current live commander while designing a MicroMachine-compatible modulation
layer above a strong autonomous bot. The LLM or a future SOTA strategy selector
may choose a bounded strategy profile or policy modulation vector, but the
profile can only activate deterministic policy leaves, constraints, or
recommended Korean utterances that still pass the existing Intent DSL,
feasibility, planner, and executor gates.

## Bot Landscape

| Family | Useful idea | Why not directly adopt as product core |
| --- | --- | --- |
| AlphaStar-style research agents | Very strong learned SC2 play. | Not a transparent, user-interruptible product bot; not practical as a local hackable controller. |
| SC2 AI ladder bots | Mature hand-authored strategies and tactical code. | Usually built as autonomous bots, not Korean-command collaborative control surfaces. |
| MicroMachine | Public historically strong deterministic SC2 bot with combat simulation, influence-map micro, build selection, and manager seams. | Best practical strong-bot adoption target, but needs a modulation layer rather than direct LLM control. |
| Deimos / Eris | Current high-ELO AI Arena references. | Bot logic is not public, so they are evidence and inspiration rather than direct adoption targets. |
| Sharpy / ares-sc2 / python-sc2 frameworks | Useful behavior managers and bot architecture ideas. | Frameworks are not the strong bot; they can inform the DSL and provider boundaries. |
| CommandCenter | Historical C++ manager architecture and MicroMachine ancestry. | Not a strong-bot candidate and not worth integrating as a product dependency. |
| Issue #10 LLM + behavior-tree paper | Best architectural match: LLM chooses high-level strategy, behavior tree executes. | Needs local adaptation so LLM output cannot bypass safety gates. |

## Proposed Shape

```text
Human command / model strategy suggestion
  -> CommanderPolicyTree / deep modulation DSL
     -> bounded strategy profile or policy modulation vector
     -> deterministic policy leaves, constraints, or manager biases
     -> standing orders, recommended utterances, or MicroMachine blackboard updates
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

## MicroMachine Modulation Direction

MicroMachine should keep playing as MicroMachine. Human or model intent should
modulate policy-level decisions such as:

- build and strategy selection in `StrategyManager`;
- production, tech, and expansion priorities in `ProductionManager` and
  `BuildOrderQueue`;
- attack, hold, retreat, and combat simulation thresholds in
  `CombatCommander` and `CombatAnalyzer`;
- squad roles and harassment allocation in `Squad`, `SquadOrder`, and
  `MicroManager`;
- scouting targets and risk tolerance in `ScoutManager`;
- worker economy, repair, and emergency defense in `WorkerManager`.

This keeps the strong non-neural bot as the executor of tactical detail while
making user intent a bounded modulation signal.

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
