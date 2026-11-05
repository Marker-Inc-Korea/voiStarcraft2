# ASR words probe (real gpt-realtime session, server_vad 350 ms, gpt-4o-mini-transcribe)

- session: `realtime_session_body('gpt-realtime','both',vad_silence_ms=350)`, then session.update with `turn_detection.create_response=false` (no model response; ASR events only)
- audio: gpt-4o-mini-tts v0 alloy 1.0x / v1 onyx 1.15x / v2 nova 1.0x + 18 dB noise; 2 reps per (utterance, variant)
- times in ms relative to the instant the last speech sample was sent (t0, pipelines.py convention); may be negative because the audio is sent unpaced before t0
- uptime before: `23:13  up 18 days, 50 mins, 3 users, load averages: 28.46 16.55 11.89`
- uptime after: `23:49  up 18 days,  1:27, 3 users, load averages: 244.74 249.00 150.99`

## Summary

calls: 276, completed with transcript: 260, errors/no transcript: 16; auth: {'mint': 187, 'direct_key': 89}; mint 429s: 92; multi-item calls: 0

| metric (ms) | n | p50 | p95 | min | max |
|---|---|---|---|---|---|
| t_speech_stopped_ms | 260 | 2937.2 | 5872.8 | 1170.9 | 10211.7 |
| t_first_delta_ms | 260 | 3411.1 | 7295.3 | 1392.0 | 10211.8 |
| t_last_delta_ms | 260 | 3600.1 | 7305.7 | 1394.9 | 10211.8 |
| t_completed_ms | 260 | 3779.7 | 7686.3 | 1487.5 | 10211.8 |
| completed_minus_speech_stopped_ms | 260 | 564.2 | 3226.7 | 0.0 | 5560.9 |
| first_delta_minus_speech_stopped_ms | 260 | 221.2 | 2254.5 | 0.0 | 5560.800000000001 |
| first_delta_lead_over_completed_ms | 260 | 116.0 | 1794.5 | 0.0 | 3441.499999999999 |
| last_delta_lead_over_completed_ms | 260 | 0.1 | 1160.5 | 0.0 | 3441.499999999999 |

- fraction of completed calls with any delta: 1.0
- fraction where first delta arrives BEFORE completed: 0.804 (lead p50 116.0 ms, p95 1794.5 ms; last-delta lead p50 0.1 ms)
- deltas per call: p50 3.0, p95 7.0
- calls where a cumulative delta text is NOT a prefix of the final: 0
- calls where concat(deltas) != final: 0
- calls where some delta-prefix matches the emergency rule (`_micromachine_request_is_emergency`) but the final does NOT (meaning change): 13

| utterance | var | deltas | final | partial-rule first ms | completed ms |
|---|---|---|---|---|---|
| 후퇴하지 마 | v0 | 후 · 퇴 · 하지 ·  마 · . | 후퇴하지 마. | 4294.2 | 4294.3 |
| 후퇴하지 마 | v1 | 후 · 퇴 · 하지 ·  마 · . | 후퇴하지 마. | 3853.1 | 3853.1 |
| 후퇴하지 마 | v2 | 후 · 퇴 · 하지 ·  마 · . | 후퇴하지 마. | 5374.7 | 5374.8 |
| 후퇴하지 말고 공격해 | v1 | 후 · 퇴 · 하지 ·  말 · 고 ·  공격 · 해 · . | 후퇴하지 말고 공격해. | 9176.2 | 9176.3 |
| 후퇴하지 말고 공격해 | v0 | 후 · 퇴 · 하지 ·  말 · 고 ·  공격 · 해 · . | 후퇴하지 말고 공격해. | 5614.9 | 6388.2 |
| 후퇴하지 말고 공격해 | v2 | 후 · 퇴 · 하지 ·  말 · 고 ·  공격 · 해 · . | 후퇴하지 말고 공격해. | 6354.5 | 6507.8 |
| 후퇴하지 말고 공격해 | v0 | 후 · 퇴 · 하지 ·  말 · 고 ·  공격 · 해 · . | 후퇴하지 말고 공격해. | 2094.5 | 3318.2 |
| 후퇴하지 마 | v2 | 후 · 퇴 · 하지 ·  마 · . | 후퇴하지 마. | 5485.4 | 5485.4 |
| 후퇴하지 마 | v0 | 후 · 퇴 · 하지 ·  마 · . | 후퇴하지 마. | 10211.8 | 10211.8 |
| 후퇴하지 마 | v1 | 후 · 퇴 · 하지 ·  마 · . | 후퇴하지 마. | 4077.9 | 4077.9 |
| 후퇴하지 말고 공격해 | v2 | 후 · 퇴 · 하지 ·  말 · 고 ·  공격 · 해 · . | 후퇴하지 말고 공격해. | 5481.8 | 6560.5 |
| 후퇴하지 말고 공격해 | v1 | 후 · 퇴 · 하지 ·  말 · 고 ·  공격 · 해 · . | 후퇴하지 말고 공격해. | 3394.3 | 3394.4 |
| retreat | v2 | Ret · reat · s | Retreats | 2298.1 | 2298.1 |

### By category (p50 / p95 ms)

| category | n | speech_stopped | first_delta | completed | final matches emergency rule | partial-only rule match |
|---|---|---|---|---|---|---|
| positive | 130 | 2756 / 5391 | 3229 / 7073 | 3398 / 7433 | 36 | 1 |
| negated | 85 | 3303 / 7001 | 3633 / 7636 | 4021 / 8564 | 5 | 12 |
| micro | 45 | 3329 / 5864 | 3584 / 6528 | 4055 / 6682 | 0 | 0 |

## Per utterance: distinct ASR transcripts (count)

| cat | utterance | n | transcripts (count) | final→emergency rule | partial-only rule | completed p50 |
|---|---|---|---|---|---|---|
| positive | 후퇴 | 6 | `후퇴` ×6 | 6/6 | 0 | 3531.2 |
| positive | 후퇴해 | 6 | `후퇴해.` ×5; `<none>` ×1 | 5/6 | 0 | 3211.0 |
| positive | 퇴각 | 6 | `퇴각` ×2; `체학` ×1; `책각` ×1; `체감` ×1; `체각` ×1 | 2/6 | 0 | 3057.3 |
| positive | 철수 | 6 | `철수` ×6 | 6/6 | 0 | 2825.1 |
| positive | 전원 철수 | 6 | `전원 차출` ×2; `전원 철수.` ×2; `<none>` ×1; `전원 철수` ×1 | 3/6 | 0 | 4500.6 |
| positive | 긴급 후퇴 | 6 | `긴급 후퇴` ×4; `긴급 호출` ×1; `キングプテ` ×1 | 4/6 | 0 | 3139.1 |
| positive | retreat | 6 | `Retreat` ×3; `退隠` ×1; `retrait` ×1; `Retreats` ×1 | 3/6 | 1 | 4435.2 |
| positive | Retreat now | 6 | `Retreat now.` ×6 | 6/6 | 0 | 3344.0 |
| positive | fall back | 6 | `Fallback` ×4; `Fall back.` ×1; `fallback` ×1 | 1/6 | 0 | 3699.3 |
| positive | 홀드 | 6 | `홀드` ×2; `Horde` ×2; `<none>` ×1; `Hold us.` ×1 | 0/6 | 0 | 2553.5 |
| positive | 홀드해 | 6 | `홀드해.` ×4; `<none>` ×1; `폴드해.` ×1 | 0/6 | 0 | 3802.5 |
| positive | 제자리 | 6 | `제자리` ×4; `제자리.` ×1; `<none>` ×1 | 0/6 | 0 | 4930.2 |
| positive | 제자리 홀드 | 6 | `제자리 홀드` ×5; `제다리 홀드` ×1 | 0/6 | 0 | 3655.0 |
| positive | 멈춰 서 | 6 | `멈춰서` ×6 | 0/6 | 0 | 3497.2 |
| positive | hold | 6 | `Hold` ×2; `Hold.` ×2; `hold` ×1; `<none>` ×1 | 0/6 | 0 | 2673.1 |
| positive | hold position | 6 | `Hold position.` ×6 | 0/6 | 0 | 4158.3 |
| positive | 멈춰 | 6 | `멈춰.` ×6 | 0/6 | 0 | 3025.9 |
| positive | 멈춰! | 6 | `멈춰!` ×3; `멈춰.` ×2; `<none>` ×1 | 0/6 | 0 | 3961.1 |
| positive | 정지 | 6 | `정지` ×5; `정지.` ×1 | 0/6 | 0 | 4704.3 |
| positive | 그만 | 6 | `그만.` ×3; `Come on.` ×2; `<none>` ×1 | 0/6 | 0 | 3114.0 |
| positive | 스톱 | 6 | `Stop.` ×3; `스톱.` ×1; `スト` ×1; `停止` ×1 | 0/6 | 0 | 3996.8 |
| positive | stop | 6 | `Stop.` ×6 | 0/6 | 0 | 2667.3 |
| positive | Stop! | 6 | `Stop!` ×4; `stop` ×1; `Stop.` ×1 | 0/6 | 0 | 2974.4 |
| negated | 후퇴하지 마 | 6 | `후퇴하지 마.` ×6 | 0/6 | 6 | 4834.6 |
| negated | 후퇴하지 말고 공격해 | 6 | `후퇴하지 말고 공격해.` ×6 | 0/6 | 6 | 6448.0 |
| negated | 멈추지 마 | 6 | `멈추지 마.` ×6 | 0/6 | 0 | 4188.9 |
| negated | 멈추지 말고 계속 가 | 6 | `멈추지 말고 계속 가.` ×5; `<none>` ×1 | 0/6 | 0 | 4830.1 |
| negated | 정지하지 마 | 6 | `정지하지 마.` ×6 | 0/6 | 0 | 2647.1 |
| negated | 그만하지 마 | 6 | `그만하지 마.` ×5; `그만 하지 마.` ×1 | 0/6 | 0 | 3084.1 |
| negated | 홀드하지 마 | 6 | `홀드 하지 마.` ×2; `홀드하지 마.` ×2; `볼드하지 마.` ×1; `벌도 하지 마.` ×1 | 0/6 | 0 | 3871.5 |
| negated | don't stop | 6 | `Don't stop.` ×6 | 0/6 | 0 | 4035.2 |
| negated | don't retreat | 6 | `Don't retreat.` ×4; `<none>` ×2 | 0/6 | 0 | 4259.2 |
| negated | don't hold | 6 | `Don't hold.` ×6 | 0/6 | 0 | 3050.4 |
| negated | 스팀팩 멈춰 | 6 | `스팀팩 멈춰.` ×5; `스팀팩 멈춰!` ×1 | 0/6 | 0 | 4279.8 |
| negated | 시즈모드 멈춰 | 6 | `시지 모드 멈춰.` ×2; `시즈 모드 멈춰.` ×2; `시즈모드 멈춰.` ×1; `<none>` ×1 | 0/6 | 0 | 5069.2 |
| negated | 공격 멈춰 | 6 | `공격 멈춰.` ×3; `공격 멈춰!` ×2; `<none>` ×1 | 5/6 | 0 | 5085.6 |
| negated | 일꾼 후퇴 | 6 | `일꾼 후퇴` ×4; `일권 호텔` ×1; `일곤 호스텔` ×1 | 0/6 | 0 | 3160.2 |
| negated | 일꾼 멈춰 | 6 | `일꾼 멈춰.` ×4; `일본 멈춰.` ×1; `일권 멈춰.` ×1 | 0/6 | 0 | 4466.0 |
| micro | 스팀팩 | 6 | `SteamPec` ×1; `steampack` ×1; `SteamPac` ×1; `Steampec` ×1; `Steampack` ×1; `Steampek` ×1 | 0/6 | 0 | 4991.9 |
| micro | 마린 스팀팩 | 6 | `마린 스팀팩` ×4; `마리의 실패.` ×1; `마리 스핀테.` ×1 | 0/6 | 0 | 5002.6 |
| micro | 불곰 스팀팩 | 6 | `불곰 스팀팩` ×3; `<none>` ×1; `불곰 스킨팩` ×1; `둘곰 스팀팩` ×1 | 0/6 | 0 | 3586.0 |
| micro | 스팀팩 써 | 6 | `스팀팩 써.` ×2; `스팀펙서` ×1; `스팀팩서` ×1; `Steampexos` ×1; `steampexo` ×1 | 0/6 | 0 | 3589.9 |
| micro | 시즈모드 | 6 | `시즈 모드` ×2; `시지 모드.` ×1; `시재 모드` ×1; `시재모드` ×1; `시지 모드` ×1 | 0/6 | 0 | 3607.2 |
| micro | 탱크 시즈모드 | 6 | `탱크 시지 모드` ×2; `탱크 시즈 모드` ×2; `탱크 시지 모드.` ×1; `<none>` ×1 | 0/6 | 0 | 3728.5 |
| micro | 시즈 풀어 | 6 | `시즈프러` ×3; `치즈 프로` ×2; `시즈프르` ×1 | 0/6 | 0 | 3659.4 |
| micro | 언시즈 | 6 | `언시즈` ×4; `<none>` ×1; `안시주` ×1 | 0/6 | 0 | 5701.8 |
