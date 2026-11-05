# Latency sweep (item 4): endpoint / prompt / tool_choice knobs + cold vs warm

## Read this first (run conditions and caveats)

- **Scope actually completed:** one interleaved sweep (2 invocations of `latency_sweep.py`, same cache dir `runs_shipped/sweep_20261105`, one shared pool of 6 workers, rep-major/row-shuffled/config-round-robin order). Only **rep 0** was completed for the six server_vad/semantic configs (E0 160, E1 160, E2 159, E3 159, E4 158, E5 158 of 160 rows; tables use the 158 rows finished by all six). **Rep 1 was not run** (time cap), so n = 158 calls per config (1 rep), not 2 x 160. E6 (`vad350:both_tcn`) has only 105 rows (it was dropped from invocation 2 after it proved non-functional, see below) and is compared on those 105 matched rows only. E7 (best silence + best prompt) was **not run**: no experiment passed ADOPT.
- **Machine load was extreme and rising:** invocation 1 (23:15:55-23:43:06) started at load average 40.75 22.71 15.00 and ended at 289.68 144.87 70.74; invocation 2 (23:43:40-23:55:51) started at 296.96 160.43 78.95 and ended at 314.39 295.97 208.80. Absolute latencies (p50 ~4.7 s, p95 9-13 s) are therefore load-inflated and noisy; all comparisons are only valid *within* this interleaved run, and nearly every latency delta CI spans 0.
- **Auth:** the proxy answered 429 `realtime_client_secret_capacity_exhausted` for secret minting, so the sweep used `--auth direct` (long-lived key + the identical `session.update` for every config; per-record `auth` field = `direct_key`). Session setup precedes the latency timer.
- **`tcn` (tool_choice = named function) is functionally broken on this proxy/model:** the session is accepted (`session.updated` echoes `tool_choice={type:function,name:submit_command}`) but the model answers with an assistant *message* whose text is the JSON arguments (`response.output[0].type == "message"`), never a `function_call`, so no tool-call event ever fires. Model-path accuracy 26.7 % (only emergencies the deterministic layer catches by transcript are correct). DROP regardless of latency.
- **Latency numbers are measured as in `pipelines.py`:** end of speech (last speech sample sent, trailing silence streamed in real time) -> `response.function_call_arguments.done`; `t_transcript_ms` = same origin -> `conversation.item.input_audio_transcription.completed`. Rule-first view = `realtime_intent.match_fast_path` or exact emergency phrase (`web_gui._micromachine_request_is_emergency`) hit on the recorded transcript -> decision from the transcript alone at `t_transcript_ms`; other rows keep the model path. Rule hits are ~16 % of calls.
- **Verdict rule applied literally with point estimates** (ADOPT iff accuracy delta > -2 pp AND unsafe <= 2 % AND micro/emergency p50 or operation/macro p95 (<= 10 s) improves). With CIs this wide none of the latency changes is distinguishable from noise; "DROP" here means "no demonstrated benefit under this load", not "proven worse".
- The semantic-VAD configs stream 8 s of trailing silence instead of 2.5 s (semantic_vad can wait longer); server_vad configs stream 2.5 s.
- Warm/cold probe (below) ran concurrently with the first sweep invocation (cold-first and warm tasks of a rep ran concurrently with each other; 12 cold starts ran before them), so it shares the same overloaded machine/proxy.

### Group 1

Group dir `sweep_20261105`; baseline `vad350:both`; matched (row, rep) pairs completed by every config: **158** (158 rows, reps [0]). Raw completed per config: `vad350:both`=160, `vad200:both`=160, `vad500:both`=159, `semhigh:both`=159, `vad350:lean1`=158, `vad350:lean2`=158.
- invocation 2026-11-05T23:15:55 -> 2026-11-05T23:43:06 (1631 s, concurrency 6, auth `direct`): uptime before: `23:15  up 18 days, 53 mins, 3 users, load averages: 40.75 22.71 15.00`; after: `23:43  up 18 days,  1:20, 3 users, load averages: 289.68 144.87 70.74`
- invocation 2026-11-05T23:43:40 -> 2026-11-05T23:55:51 (732 s, concurrency 6, auth `direct`): uptime before: `23:43  up 18 days,  1:21, 3 users, load averages: 296.96 160.43 78.95`; after: `23:55  up 18 days,  1:33, 3 users, load averages: 314.39 295.97 208.80`

#### Pure-model view (decision on the model tool call; latency = end of speech -> tool-call args)

| config | tier | n | sem acc [95% Wilson] | unsafe % | p50 s | p95 s | mean tokens |
|---|---|---|---|---|---|---|---|
| vad350:both | all | 158 | 94.3 [90-97] | 0.0 | 4.75 | 9.33 | 4050 |
| vad350:both | micro | 32 | 100.0 [89-100] | 0.0 | 4.89 | 11.06 | 4083 |
| vad350:both | emergency | 22 | 90.9 [72-97] | 0.0 | 4.33 | 9.55 | 4069 |
| vad350:both | operation | 46 | 93.5 [82-98] | 0.0 | 5.07 | 8.56 | 4146 |
| vad350:both | macro | 27 | 88.9 [72-96] | 0.0 | 4.62 | 6.87 | 3936 |
| vad350:both | adversarial | 31 | 96.8 [84-99] | 0.0 | 4.75 | 8.54 | 3960 |
| vad200:both | all | 158 | 92.4 [87-96] | 0.0 | 4.68 | 9.15 | 4096 |
| vad200:both | micro | 32 | 100.0 [89-100] | 0.0 | 5.19 | 10.79 | 4083 |
| vad200:both | emergency | 22 | 86.4 [67-95] | 0.0 | 4.77 | 8.67 | 4071 |
| vad200:both | operation | 46 | 87.0 [74-94] | 0.0 | 4.55 | 8.92 | 4134 |
| vad200:both | macro | 27 | 96.3 [82-99] | 0.0 | 4.40 | 6.95 | 4086 |
| vad200:both | adversarial | 31 | 93.5 [79-98] | 0.0 | 4.26 | 8.05 | 4083 |
| vad500:both | all | 158 | 94.9 [90-97] | 0.0 | 5.04 | 11.34 | 4102 |
| vad500:both | micro | 32 | 96.9 [84-99] | 0.0 | 5.08 | 9.00 | 4086 |
| vad500:both | emergency | 22 | 86.4 [67-95] | 0.0 | 4.60 | 11.43 | 4071 |
| vad500:both | operation | 46 | 93.5 [82-98] | 0.0 | 5.33 | 12.23 | 4148 |
| vad500:both | macro | 27 | 96.3 [82-99] | 0.0 | 5.08 | 10.69 | 4086 |
| vad500:both | adversarial | 31 | 100.0 [89-100] | 0.0 | 4.59 | 8.71 | 4087 |
| semhigh:both | all | 158 | 94.9 [90-97] | 0.0 | 5.28 | 11.98 | 4101 |
| semhigh:both | micro | 32 | 100.0 [89-100] | 0.0 | 4.90 | 13.31 | 4084 |
| semhigh:both | emergency | 22 | 90.9 [72-97] | 0.0 | 5.74 | 8.15 | 4072 |
| semhigh:both | operation | 46 | 91.3 [80-97] | 0.0 | 5.48 | 14.73 | 4145 |
| semhigh:both | macro | 27 | 92.6 [77-98] | 0.0 | 5.54 | 11.39 | 4088 |
| semhigh:both | adversarial | 31 | 100.0 [89-100] | 0.0 | 4.52 | 10.30 | 4084 |
| vad350:lean1 | all | 158 | 93.0 [88-96] | 0.0 | 4.56 | 12.86 | 3625 |
| vad350:lean1 | micro | 32 | 100.0 [89-100] | 0.0 | 5.30 | 13.36 | 3612 |
| vad350:lean1 | emergency | 22 | 95.5 [78-99] | 0.0 | 4.34 | 7.54 | 3598 |
| vad350:lean1 | operation | 46 | 93.5 [82-98] | 0.0 | 4.79 | 9.17 | 3661 |
| vad350:lean1 | macro | 27 | 77.8 [59-89] | 0.0 | 4.02 | 12.80 | 3611 |
| vad350:lean1 | adversarial | 31 | 96.8 [84-99] | 0.0 | 4.46 | 14.58 | 3617 |
| vad350:lean2 | all | 158 | 91.8 [86-95] | 0.0 | 4.61 | 9.47 | 3431 |
| vad350:lean2 | micro | 32 | 100.0 [89-100] | 0.0 | 4.60 | 12.50 | 3418 |
| vad350:lean2 | emergency | 22 | 90.9 [72-97] | 0.0 | 4.76 | 9.98 | 3403 |
| vad350:lean2 | operation | 46 | 89.1 [77-95] | 0.0 | 4.39 | 9.27 | 3476 |
| vad350:lean2 | macro | 27 | 81.5 [63-92] | 0.0 | 5.15 | 8.27 | 3417 |
| vad350:lean2 | adversarial | 31 | 96.8 [84-99] | 0.0 | 4.52 | 7.79 | 3408 |

#### Rule-first blended view (rule hit -> decision on transcript at t_transcript; else model)

| config | tier | n | sem acc [95% Wilson] | unsafe % | p50 s | p95 s | mean tokens |
|---|---|---|---|---|---|---|---|
| vad350:both | all | 158 | 94.9 [90-97] | 0.0 | 4.73 | 9.33 | 4050 |
| vad350:both | micro | 32 | 100.0 [89-100] | 0.0 | 4.76 | 11.60 | 4083 |
| vad350:both | emergency | 22 | 100.0 [85-100] | 0.0 | 4.19 | 7.58 | 4069 |
| vad350:both | operation | 46 | 91.3 [80-97] | 0.0 | 5.07 | 8.56 | 4146 |
| vad350:both | macro | 27 | 88.9 [72-96] | 0.0 | 4.62 | 6.87 | 3936 |
| vad350:both | adversarial | 31 | 96.8 [84-99] | 0.0 | 4.75 | 8.54 | 3960 |
| vad200:both | all | 158 | 92.4 [87-96] | 0.0 | 4.60 | 9.84 | 4096 |
| vad200:both | micro | 32 | 100.0 [89-100] | 0.0 | 5.06 | 11.37 | 4083 |
| vad200:both | emergency | 22 | 90.9 [72-97] | 0.0 | 4.27 | 8.67 | 4071 |
| vad200:both | operation | 46 | 84.8 [72-92] | 0.0 | 4.53 | 8.92 | 4134 |
| vad200:both | macro | 27 | 96.3 [82-99] | 0.0 | 4.40 | 6.95 | 4086 |
| vad200:both | adversarial | 31 | 93.5 [79-98] | 0.0 | 4.26 | 8.05 | 4083 |
| vad500:both | all | 158 | 95.6 [91-98] | 0.0 | 4.88 | 11.19 | 4102 |
| vad500:both | micro | 32 | 96.9 [84-99] | 0.0 | 4.84 | 9.00 | 4086 |
| vad500:both | emergency | 22 | 95.5 [78-99] | 0.0 | 3.76 | 10.47 | 4071 |
| vad500:both | operation | 46 | 91.3 [80-97] | 0.0 | 5.33 | 12.23 | 4148 |
| vad500:both | macro | 27 | 96.3 [82-99] | 0.0 | 5.08 | 10.69 | 4086 |
| vad500:both | adversarial | 31 | 100.0 [89-100] | 0.0 | 4.59 | 8.71 | 4087 |
| semhigh:both | all | 158 | 94.9 [90-97] | 0.0 | 4.92 | 11.68 | 4101 |
| semhigh:both | micro | 32 | 100.0 [89-100] | 0.0 | 4.59 | 10.18 | 4084 |
| semhigh:both | emergency | 22 | 95.5 [78-99] | 0.0 | 4.48 | 8.16 | 4072 |
| semhigh:both | operation | 46 | 89.1 [77-95] | 0.0 | 5.38 | 14.73 | 4145 |
| semhigh:both | macro | 27 | 92.6 [77-98] | 0.0 | 5.54 | 11.39 | 4088 |
| semhigh:both | adversarial | 31 | 100.0 [89-100] | 0.0 | 4.52 | 10.30 | 4084 |
| vad350:lean1 | all | 158 | 93.0 [88-96] | 0.0 | 4.56 | 12.86 | 3625 |
| vad350:lean1 | micro | 32 | 100.0 [89-100] | 0.0 | 5.20 | 13.36 | 3612 |
| vad350:lean1 | emergency | 22 | 100.0 [85-100] | 0.0 | 4.26 | 6.23 | 3598 |
| vad350:lean1 | operation | 46 | 91.3 [80-97] | 0.0 | 4.79 | 9.17 | 3661 |
| vad350:lean1 | macro | 27 | 77.8 [59-89] | 0.0 | 4.02 | 12.80 | 3611 |
| vad350:lean1 | adversarial | 31 | 96.8 [84-99] | 0.0 | 4.46 | 14.58 | 3617 |
| vad350:lean2 | all | 158 | 92.4 [87-96] | 0.0 | 4.61 | 10.09 | 3431 |
| vad350:lean2 | micro | 32 | 100.0 [89-100] | 0.0 | 4.60 | 12.22 | 3418 |
| vad350:lean2 | emergency | 22 | 100.0 [85-100] | 0.0 | 4.31 | 10.45 | 3403 |
| vad350:lean2 | operation | 46 | 87.0 [74-94] | 0.0 | 4.39 | 9.27 | 3476 |
| vad350:lean2 | macro | 27 | 81.5 [63-92] | 0.0 | 5.15 | 8.27 | 3417 |
| vad350:lean2 | adversarial | 31 | 96.8 [84-99] | 0.0 | 4.52 | 7.79 | 3408 |

#### Transcript-complete latency (t_transcript_ms, end of speech -> input transcription completed)

| config | all p50/p95 s | micro p50/p95 s | emergency p50/p95 s | operation p50/p95 s | macro p50/p95 s | adversarial p50/p95 s | rule hits |
|---|---|---|---|---|---|---|---|
| vad350:both | 4.28/10.53 | 4.02/10.25 | 3.73/7.64 | 4.65/11.14 | 4.34/8.84 | 4.13/9.64 | 25/158 (16%) |
| vad200:both | 4.29/11.17 | 4.68/11.22 | 4.18/9.18 | 3.60/10.86 | 3.92/10.68 | 5.19/10.56 | 26/158 (16%) |
| vad500:both | 4.43/11.45 | 4.64/8.93 | 3.89/10.59 | 4.46/11.26 | 4.59/10.81 | 3.82/12.13 | 25/158 (16%) |
| semhigh:both | 4.36/14.19 | 4.22/11.78 | 4.48/7.76 | 4.36/16.02 | 4.84/14.97 | 3.54/10.67 | 26/158 (16%) |
| vad350:lean1 | 4.09/12.91 | 5.11/13.59 | 4.26/8.32 | 3.99/7.85 | 3.36/9.73 | 3.70/14.71 | 27/158 (17%) |
| vad350:lean2 | 4.20/10.63 | 4.39/11.36 | 4.11/10.72 | 3.52/7.52 | 4.67/8.66 | 4.42/9.59 | 27/158 (17%) |

#### Paired comparison vs baseline and verdict

Rule: ADOPT iff accuracy delta > -2 pp AND unsafe <= 2 % AND (micro/emergency p50 or operation/macro p95 (<= 10 s) improves, point estimates). Accuracy/unsafe/latency checks use the pure-model view; deltas are experiment minus baseline, 95% bootstrap CI over rows.

| experiment | acc delta pp (model) [CI] | acc delta pp (rule-first) [CI] | unsafe % | micro p50 ms | emerg p50 ms | op p95 ms | macro p95 ms | all p50 ms | all p95 ms | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| vad200:both | -1.9 [-5.7,+1.9] | -2.5 [-6.3,+1.3] | 0.0 | +304 [-901,+1654] | +441 [-1582,+1435] | +356 [-13486,+4306] | +78 [-2930,+4526] | -66 [-592,+306] | -178 [-2092,+2614] | **DROP** |
| vad500:both | +0.6 [-1.9,+3.2] | +0.6 [-1.9,+3.2] | 0.0 | +187 [-845,+1179] | +275 [-1470,+1483] | +3675 [-10001,+17861] | +3822 [+157,+4852] | +290 [-161,+717] | +2015 [-1063,+4263] | **DROP** |
| semhigh:both | +0.6 [-1.9,+3.2] | +0.0 [-2.5,+2.5] | 0.0 | +8 [-966,+1512] | +1407 [-909,+2337] | +6166 [-9283,+10599] | +4517 [-1016,+9349] | +533 [-66,+915] | +2656 [-47,+7791] | **DROP** |
| vad350:lean1 | -1.3 [-5.1,+2.5] | -1.9 [-5.7,+1.9] | 0.0 | +415 [-735,+1518] | +12 [-1449,+899] | +607 [-12919,+2345] | +5930 [-1334,+12920] | -189 [-603,+242] | +3534 [-332,+6051] | **DROP** |
| vad350:lean2 | -2.5 [-7.0,+1.3] | -2.5 [-7.0,+1.3] | 0.0 | -287 [-1277,+705] | +433 [-1079,+2088] | +710 [-12006,+9059] | +1398 [-1708,+2543] | -137 [-521,+276] | +141 [-1422,+2674] | **DROP** |

### Group 2

Group dir `sweep_20261105`; baseline `vad350:both`; matched (row, rep) pairs completed by every config: **105** (105 rows, reps [0]). Raw completed per config: `vad350:both`=160, `vad350:both_tcn`=105.
- invocation 2026-11-05T23:15:55 -> 2026-11-05T23:43:06 (1631 s, concurrency 6, auth `direct`): uptime before: `23:15  up 18 days, 53 mins, 3 users, load averages: 40.75 22.71 15.00`; after: `23:43  up 18 days,  1:20, 3 users, load averages: 289.68 144.87 70.74`
- invocation 2026-11-05T23:43:40 -> 2026-11-05T23:55:51 (732 s, concurrency 6, auth `direct`): uptime before: `23:43  up 18 days,  1:21, 3 users, load averages: 296.96 160.43 78.95`; after: `23:55  up 18 days,  1:33, 3 users, load averages: 314.39 295.97 208.80`

#### Pure-model view (decision on the model tool call; latency = end of speech -> tool-call args)

| config | tier | n | sem acc [95% Wilson] | unsafe % | p50 s | p95 s | mean tokens |
|---|---|---|---|---|---|---|---|
| vad350:both | all | 105 | 93.3 [87-97] | 0.0 | 4.75 | 8.70 | 4026 |
| vad350:both | micro | 20 | 100.0 [84-100] | 0.0 | 4.62 | 10.67 | 4085 |
| vad350:both | emergency | 17 | 94.1 [73-99] | 0.0 | 4.32 | 7.32 | 4068 |
| vad350:both | operation | 34 | 94.1 [81-98] | 0.0 | 5.11 | 8.26 | 4152 |
| vad350:both | macro | 13 | 76.9 [50-92] | 0.0 | 4.66 | 7.99 | 3772 |
| vad350:both | adversarial | 21 | 95.2 [77-99] | 0.0 | 4.75 | 8.16 | 3890 |
| vad350:both_tcn | all | 105 | 26.7 [19-36] | 0.0 | - | - | 4104 |
| vad350:both_tcn | micro | 20 | 35.0 [18-57] | 0.0 | - | - | 4084 |
| vad350:both_tcn | emergency | 17 | 76.5 [53-90] | 0.0 | - | - | 4069 |
| vad350:both_tcn | operation | 34 | 0.0 [0-10] | 0.0 | - | - | 4153 |
| vad350:both_tcn | macro | 13 | 0.0 [0-23] | 0.0 | - | - | 4085 |
| vad350:both_tcn | adversarial | 21 | 38.1 [21-59] | 0.0 | - | - | 4081 |

#### Rule-first blended view (rule hit -> decision on transcript at t_transcript; else model)

| config | tier | n | sem acc [95% Wilson] | unsafe % | p50 s | p95 s | mean tokens |
|---|---|---|---|---|---|---|---|
| vad350:both | all | 105 | 94.3 [88-97] | 0.0 | 4.73 | 8.68 | 4026 |
| vad350:both | micro | 20 | 100.0 [84-100] | 0.0 | 4.62 | 10.67 | 4085 |
| vad350:both | emergency | 17 | 100.0 [82-100] | 0.0 | 3.73 | 6.34 | 4068 |
| vad350:both | operation | 34 | 94.1 [81-98] | 0.0 | 5.11 | 8.26 | 4152 |
| vad350:both | macro | 13 | 76.9 [50-92] | 0.0 | 4.66 | 7.99 | 3772 |
| vad350:both | adversarial | 21 | 95.2 [77-99] | 0.0 | 4.75 | 8.16 | 3890 |
| vad350:both_tcn | all | 105 | 26.7 [19-36] | 0.0 | 3.68 | 5.77 | 4104 |
| vad350:both_tcn | micro | 20 | 35.0 [18-57] | 0.0 | 3.22 | 4.79 | 4084 |
| vad350:both_tcn | emergency | 17 | 76.5 [53-90] | 0.0 | 3.81 | 5.97 | 4069 |
| vad350:both_tcn | operation | 34 | 0.0 [0-10] | 0.0 | - | - | 4153 |
| vad350:both_tcn | macro | 13 | 0.0 [0-23] | 0.0 | - | - | 4085 |
| vad350:both_tcn | adversarial | 21 | 38.1 [21-59] | 0.0 | - | - | 4081 |

#### Transcript-complete latency (t_transcript_ms, end of speech -> input transcription completed)

| config | all p50/p95 s | micro p50/p95 s | emergency p50/p95 s | operation p50/p95 s | macro p50/p95 s | adversarial p50/p95 s | rule hits |
|---|---|---|---|---|---|---|---|
| vad350:both | 4.05/10.12 | 4.02/8.44 | 3.57/6.25 | 4.92/10.90 | 4.66/9.61 | 3.83/8.78 | 18/105 (17%) |
| vad350:both_tcn | 3.81/9.31 | 3.86/5.33 | 4.38/5.85 | 3.99/10.90 | 3.24/8.47 | 3.66/17.58 | 18/105 (17%) |

#### Paired comparison vs baseline and verdict

Rule: ADOPT iff accuracy delta > -2 pp AND unsafe <= 2 % AND (micro/emergency p50 or operation/macro p95 (<= 10 s) improves, point estimates). Accuracy/unsafe/latency checks use the pure-model view; deltas are experiment minus baseline, 95% bootstrap CI over rows.

| experiment | acc delta pp (model) [CI] | acc delta pp (rule-first) [CI] | unsafe % | micro p50 ms | emerg p50 ms | op p95 ms | macro p95 ms | all p50 ms | all p95 ms | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| vad350:both_tcn | -66.7 [-76.2,-57.1] | -67.6 [-77.1,-58.1] | 0.0 | - | - | - | - | - | - | **DROP** |

## Cold vs warm probe

Run 2026-11-05T23:17:51 -> 2026-11-05T23:26:06; uptime before `23:17  up 18 days, 55 mins, 3 users, load averages: 16.07 19.34 14.64`; after `23:26  up 18 days,  1:03, 3 users, load averages: 14.14 16.33 14.96`. Rows: mic-008, eme-011, ope-034, mac-005, adv-017, mic-024, eme-022, ope-026, mac-007, adv-021, mic-026, eme-007 (v0 audio).

### (a) Cold start components (sequential, product session body)

- POST /realtime/client_secrets: p50 1.92 s / p95 5.51 s (n=12); HTTP status counts {'200': 6, '429': 6} (non-200 -> that socket used the direct key + session.update)
- websocket open: p50 2.84 s / p95 4.68 s (n=12)
- session.update -> session.updated: p50 3.20 s / p95 14.74 s (n=12)

### (b) First utterance on a FRESH session vs n-th utterance on ONE warm session (items pruned after each response)

| kind | ok/total | t_tool_call p50 / p95 / mean s | t_transcript p50 / p95 s |
|---|---|---|---|
| cold_first | 24/24 | 4.16 / 9.22 / 5.57 | 3.81 / 9.29 |
| warm | 24/24 | 3.23 / 8.38 / 3.86 | 1.98 / 3.71 |

Paired (same row+rep) warm minus cold-first t_tool_call: median -481.90000000000055 ms, mean -1703.5 ms (n=24). Warm session 1st utterance p50 7.15 s (n=2) vs later utterances p50 3.13 s (n=22).

