# Parsed issue follow-up status

Generated from the GitHub issue JSON snapshot used by
`scripts/issue_parser.py`. This report separates repository evidence from
claims that require an external SC2 runtime, hosted CI, or a native C++ game
loop.

| Issue | Parsed scope | Repository evidence | Remaining gate |
| --- | --- | --- | --- |
| #188 | Live LLM interpretation latency | PR #187 includes compact schemas, uncapped output, and provider-client reuse; the focused interpreter tests pass. | Hosted/live latency evidence is external to this checkout. |
| #189 | System 1 → JEV contract | `starcraft_commander/jev_router.py` and JEV contract tests cover typed decisions, invalid responses, timeout, and fail-closed behavior. | Real endpoint/auth operations are not configured. |
| #190 | Electron desktop shell | `electron/` has loopback-only main/preload IPC, semantic target selection, runtime status, bounded history, and six Node contract tests. | Accessibility, packaging, and real SC2 parity remain unverified. |
| #191 | Real SC2 lifecycle QA | Recorded SC2 run `.local/qa30-live-r32-full-20260918-final/final_audit.json` passes all 30 scenarios, including action issuance, movement, completion, cancel, retreat, ownership release, and resume effects. | The run used structured blackboard input, so natural-language/voice-to-runtime proof remains separate. |
| #192 | C++ lease/resume bridge | Native lease/event helpers and unit tests pass; the recorded 30-scenario run includes terminal state, ownership release, autonomous owner restore, and `OnGameEnd` evidence. | A fresh upstream game-loop build/reconnect qualification is still required to claim full production integration. |
| #193 | Hosted CI blockers | PR #187 is merged; the repository status records successful post-merge Main CI and final-pre-live runs. | No local action remains unless hosted verification regresses. |
| #194 | Squad, target-pin, repair resolution | Semantic registry validation, target-pin allowlists, repair clarification contracts, and live squad ownership/retarget evidence are covered. | Live map-click coordinate conversion and repair target reselection still need a dedicated SC2 scenario. |
| #195 | Bounded SC2 API coverage | Semantic capability inventory, explicit Client/protocol exclusion ledgers, bounded schemas, and raw invocation rejection are implemented and tested. | Remaining gap is live coverage for each capability; raw APIs stay intentionally unavailable. |
| #196 | Umbrella completion | Child issue references and completion gate are parsed into `docs/issue-parser-report.json`; recorded SC2 QA now supplies 30/30 scenario evidence. | Umbrella still needs natural-language/voice runtime proof, dedicated repair/map-pin QA, and fresh upstream C++ reconnect qualification. |

The parser intentionally does not close issues or infer completion from a
passing contract test. It records issue structure and references; completion
status remains evidence-driven.
