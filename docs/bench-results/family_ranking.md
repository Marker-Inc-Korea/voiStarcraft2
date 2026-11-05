### Candidate families on 1396 recorded real-ASR calls (4 prompt variants x 160 rows x reps)

| family | answers | correct | precision | coverage of tier calls | median saved ms | total saved s | in product |
|---|---|---|---|---|---|---|---|
| `macro:production` | 71 | 71 | 100% | 30.1% | 355 | 43.0 |  |
| `micro:siege_mode` | 34 | 34 | 100% | 12.2% | 314 | 26.9 | **yes** |
| `micro:stimpack` | 31 | 31 | 100% | 11.1% | 568 | 25.0 | **yes** |
| `macro:research_stimpack` | 14 | 14 | 100% | 5.9% | 390 | 22.8 |  |
| `macro:expand` | 26 | 26 | 100% | 11.0% | 340 | 20.8 |  |
| `micro:auto_turret` | 8 | 8 | 100% | 2.9% | 620 | 13.1 |  |
| `emergency:cancel_attacks` | 37 | 37 | 100% | 19.1% | 37 | 12.3 | **yes** |
| `micro:tactical_nuke` | 14 | 14 | 100% | 5.0% | 424 | 10.7 |  |
| `emergency:stop_expansion` | 8 | 8 | 100% | 4.1% | 303 | 9.8 |  |
| `micro:banshee_cloak` | 18 | 18 | 100% | 6.5% | 348 | 8.1 |  |
| `emergency:hold_position` | 21 | 21 | 100% | 10.8% | 56 | 8.0 | **yes** |
| `micro:marauder_stimpack` | 6 | 6 | 100% | 2.2% | 695 | 7.8 |  |
| `micro:medivac_load` | 8 | 8 | 100% | 2.9% | 158 | 6.4 |  |
| `micro:yamato` | 8 | 8 | 100% | 2.9% | 274 | 6.3 |  |
| `emergency:evacuate_workers` | 21 | 21 | 100% | 10.8% | 0 | 5.4 |  |
| `micro:unsiege` | 8 | 8 | 100% | 2.9% | 410 | 5.4 |  |
| `micro:banshee_decloak` | 6 | 6 | 100% | 2.2% | 343 | 5.0 |  |
| `micro:medivac_unload_all` | 6 | 6 | 100% | 2.2% | 650 | 4.8 |  |
| `micro:emp` | 5 | 5 | 100% | 1.8% | 343 | 4.6 |  |
| `macro:research_combatshield` | 9 | 9 | 100% | 3.8% | 127 | 3.4 |  |
| `micro:widow_mine_unburrow` | 5 | 5 | 100% | 1.8% | 240 | 3.3 |  |
| `micro:ghost_cloak` | 6 | 6 | 100% | 2.2% | 235 | 2.9 |  |
| `micro:marine_stimpack` | 5 | 5 | 100% | 1.8% | 219 | 2.6 |  |
| `emergency:force_retreat` | 79 | 75 | 95% | 40.7% | 344 | 55.4 | **yes** |

### Rule-first blend of the shipped five families (offline, same recorded calls)

| tier | n | model-only acc | rule-first acc | model-only p50 / p95 s | rule-first p50 / p95 s | unsafe before -> after |
|---|---|---|---|---|---|---|
| all | 1396 | 89.3% | 90.3% | 4.73 / 8.93 | 4.63 / 8.62 | 0.14% -> 0.14% |
| micro | 279 | 94.3% | 94.3% | 4.81 / 8.76 | 4.60 / 8.36 | 0.00% -> 0.00% |
| emergency | 194 | 89.2% | 96.4% | 4.63 / 7.78 | 4.15 / 7.30 | 0.00% -> 0.00% |
| operation | 427 | 88.1% | 88.1% | 5.04 / 9.14 | 5.04 / 9.14 | 0.00% -> 0.00% |
| macro | 236 | 85.2% | 85.2% | 4.64 / 9.28 | 4.64 / 9.28 | 0.00% -> 0.00% |
| adversarial | 260 | 89.6% | 89.6% | 4.36 / 8.64 | 4.17 / 8.31 | 0.77% -> 0.77% |

Rule hits by family: `emergency:cancel_attacks` 37, `emergency:force_retreat` 75, `emergency:hold_position` 21, `micro:siege_mode` 47, `micro:stimpack` 38, `raw_control_guard` 84
