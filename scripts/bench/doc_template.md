# Voice intent → PolicyModulationVector benchmark

Branch `feat/bench`. Everything below was produced by `scripts/bench/*` against the real proxy
(`proxy.nomadamas.org`, OpenAI Realtime + chat models) on **2026-11-05 KST** (screening 00:11–01:30, finalists 01:34–02:03).
5762 cached per-call result files are under `scripts/bench/runs/` (gitignored); machine-readable numbers are in
`docs/bench-results/summary.json` (with `routing_policy`), per-config tables in `docs/bench-results/tables_*.md`,
plot in `docs/bench-results/pareto.svg`.

> **What this is not.** This is *not* live-game evidence. SC2 is not runnable here. "Correct" means: the model's
> `submit_command` arguments (a) satisfy the tool JSON schema, (b) lower to a payload that
> `PolicyModulationVector.from_mapping` accepts, and (c) match the corpus gold semantic assertions. Whether MicroMachine then
> *plays* it correctly is Fidelity's question, not this benchmark's.

## 1. Question and TL;DR

Which pipeline turns a spoken Korean/English StarCraft order into the right DSL command, fast enough, per tier
(micro / operation / macro / emergency / adversarial)?

**Recommendation (`routing_policy` in `summary.json`)** — *fast path → Realtime audio proposer ‖ text veto*:

1. **Deterministic fast path first** (`fastpath.py:ext_rules`, conservative; abstains on negation / raw-control /
   unknown). Answers 41 % of the corpus (micro 82 %, emergency 91 %, macro 67 %, operation 0 %, adversarial 0 %) with
   **100 % precision on this corpus (66/66)** — but see §8.1: its first version scored 65/67 (a `스팀팩 연구해` → micro-stim
   bug and a multi-target production miss), I fixed those two after seeing the misses, so this number is tuned on the corpus.
2. Otherwise **`gpt-realtime` + `fewshot` schema, manual commit at end of speech** proposes the call from audio, and in parallel
   **`gemini-3.5-flash-lite` + `fewshot`** reads the same utterance as text and can only **veto** (turn an executable
   answer into a clarification). Veto = adversarial safety net.
3. Measured on the full corpus (160 rows × 3 repeats = 480 calls; cascade composed offline from paired measured runs):
   **92.7 % exact-semantic accuracy [95 % CI 90.0–94.7], p50 3.3 s / p95 4.7 s, 99.8 % schema-valid, 0 unsafe executions**
   (micro 100 %, emergency 98.6 %, operation 81.9 %, macro 92.6 %, adversarial 96.8 %).
4. If you refuse any transcript-dependent step (audio-native only): **`gpt-realtime` fewshot, 82.9 %, p50 2.0 s / p95 4.7 s**.
5. The highest raw accuracy is `ext_rules > gpt-5.6-sol` fewshot (93.8 %; `sol` alone 92.9 %), but at p50 **7.4 s** / p95 **19.4 s**
   (sol alone: 11.3 / 20.4 s) — within the 2 pp tie band of the recommendation, ~5 s/15 s slower, so not chosen; `sol` is useful as an off-line judge. Cheap realtime-mini is **not** viable (47–61 %).

Big caveats up front (details in §8): text-based steps were fed *gold transcripts* (real ASR errors lower them),
the corpus is author-written synthetic TTS, 160 rows, and the shared proxy/machine was heavily loaded, so latencies rank
configs but are not SLOs.

## 2. Method

### 2.1 Corpus (`corpus.jsonl`, 160 rows; `build_corpus.py`)
| tier | rows | notes |
|---|---|---|
| micro | 33 | stim, siege/unsiege, cloak, nuke, EMP, yamato, medivac, mines, turret, mode switches; colloquial (`야 스팀 ㄱㄱ`), English, mixed |
| emergency | 23 | retreat / cancel-attack / hold / evacuate workers / pull workers / stop expansion; incl. rows the shipped regex covers |
| operation | 46 | 36 army missions (parallel ops, split squad, route, target, reinforce/resize/retarget/cancel/restart) + 10 construction (`buildings[]`) |
| macro | 27 | production, expand, research, doctrine (`바이오 위주로 가자`), multi-target |
| adversarial | 31 | negation (`후퇴하지 마`), contradiction, ambiguity, smalltalk/query, unsupported, raw coordinates/unit tags/clicks/code, prompt injection |

131 Korean, 24 English, 5 mixed. Each row has a gold of *semantic assertions* (layer, task types, unit types + exact counts, route,
target, location, ability, edit action, emergency flags, expected clarification reason, `must_not` safety assertions);
attributes the user did not state are unconstrained in `sem` and must be absent in `strict`.
`gold_check.py` converts every gold to canonical tool args → `PolicyModulationVector.from_mapping` and proves all
**160/160 are schema-legal** and that gold scores itself correct.

### 2.2 Audio (`audio.py`, `audio_manifest.json`)
`gpt-4o-mini-tts` via the proxy, 24 kHz mono pcm16, three variants per row: `v0` alloy 1.0×, `v1` onyx **1.15×**,
`v2` nova 1.0× + white noise at **18 dB SNR**. 480 files; only sha256 hashes are committed (`audio_cache/` gitignored).
A repeat *k* of a row uses variant `k mod 3`, so repeats also vary the speaker. (TTS speech is cleaner and more regular than a
real microphone — optimistic.)

### 2.3 Pipelines (`pipelines.py`; common interface `run(row, rep) → {tool_args, t_first_token_ms, t_tool_call_ms, total_ms, tokens, error}`)
* **A realtime** audio → `submit_command` tool call. Models: `gpt-realtime`, `gpt-realtime-mini`, `gpt-realtime-1.5`, `gpt-realtime-2`,
  `gpt-realtime-2025-08-28` (all accepted by the proxy even though `/models` does not list any realtime id; `gpt-4o-realtime-preview`,
  `gpt-4o-mini-realtime-preview`, `gpt-realtime-mini-2025-10-06` → `model_not_found`, skipped). Endpointing: **manual** (commit when the client says speech ended)
  vs **server_vad** (500 ms silence; trailing silence streamed at real-time pace so the VAD delay is inside the measured latency).
  Schema/instruction variants: `full` (long descriptions + rules), `compact`, `fewshot` (compact + 8 worked examples).
  `tool_choice=required`, `output_modalities=["text"]`.
* **B text** transcript → tool call through chat-completions (streaming): `gpt-5.6-sol/terra/luna`, `claude-haiku-5-5`, `gemini-3.5-flash-lite`
  (`claude-sonnet-5-5-high`: **failed** — see §7).
* **C deterministic** `prod_regex` = the regex fast path that ships in `web_gui.py` (reproduced faithfully including its
  `{force_retreat, cancel_attacks}` output); `ext_rules` = conservative extended grammar written by the corpus author.
* **D cascades** (simulated offline from measured paired results — they are *not* separate API runs): `ext_rules → X`;
  `esc0.8: cheap → strong` when cheap is invalid/clarification/confidence<0.8 (latencies add); `spec: A | B` run in parallel, first schema-valid result wins;
  `veto: realtime ‖ gemini` (latency = slower of the two) and `ext_rules > veto`.

Latency = **end of speech → complete tool-call arguments**. Realtime/manual: from the commit; realtime/vad: from the last speech sample (includes the
server's silence window); text: from request start (+ 1.59 s ASR added in the reports for transcript-fed pipelines, = measured p50 of
`conversation.item.input_audio_transcription.completed` after commit on 120 `gpt-realtime` calls; p95 2.05 s). Secret mint and WebSocket connect are excluded (a real client keeps a warm socket).

### 2.4 Scoring (`scoring.py`, 14 unit tests in `test_scoring.py`)
`sem` (headline), `strict`, `layer_ok`, `schema_valid` (JSON-schema **and** `from_mapping`), `safe` (nothing in `gold.must_not` executed), p50/p95, token cost proxy
(`text_in·1 + audio_in·8 + out·4`; tokens/call reported as well). A schema-invalid call is never counted correct (it would be rejected, not executed).
95 % CIs are Wilson intervals over calls; repeats of the same row are **not independent**, so the CIs are somewhat too narrow — use row count (160 / 40) as the real sample size.

### 2.5 Protocol
1. **Screen**: stratified 40-row subset (`subset.json`: 8 micro, 5 emergency, 10 operation, 6 macro, 11 adversarial), **3 repeats**, 33 configs (`run.py screen`).
2. **Finalists** on the full 160 rows × **3 repeats**: `gpt-realtime-2 compact`, `gpt-realtime fewshot`, `gemini-3.5-flash-lite fewshot`, `gpt-5.6-sol fewshot` (the four best-per-class screen results), plus
   `prod_regex`, `ext_rules`. (Rows outside the 40-row subset therefore have fresh data; subset rows accumulate their screen reps too, so finalist numbers include them.)
3. Concurrency ≤ 6, retries with backoff, `User-Agent: curl/8.7.1`, certifi CA.
4. **Selection rule** (applied per slice, no cherry-picking): eligible = unsafe ≤ 2 %, error rate ≤ 5 %, p95 ≤ tier budget (emergency 3 s, micro 5 s, operation/macro/adversarial/all 8 s);
   best = max `sem`; tie band 2 pp; pick min p95 (then p50). Unconstrained-latency and audio-native-only variants are also reported.

## 3. Screening results (40 rows × 3 reps = 120 calls per config; single pipelines)

{{SCREEN_SINGLE}}

Reading: with only 40 rows, a ±5 pp gap is noise (CI half-width ≈ 7–9 pp). Robust findings: (1) `gpt-5.6-*` text with few-shot ≥ 93 % but 11–14 s p50;
(2) realtime-mini ≤ 61 % → dropped; (3) few-shot beats compact beats full for every realtime/text model that mattered
(realtime: 82 / 71 / 73 %; gemini: 87 / 80 %); (4) **server_vad500 is as accurate as manual commit but ~0.6 s slower at p50** here;
(5) `gpt-realtime-2` compact (89 %, 0 unsafe) was the best realtime on the subset but regressed on the full corpus (79.6 %) — small-sample winner's curse (see §6).
Full-screen tables incl. all cascades: `docs/bench-results/tables_screen.md`.

## 4. Full-corpus results (160 rows × 3 reps; finalists + cascades)

Cell = exact-semantic accuracy · p50/p95 seconds. `sol` fewshot = 3 reps on all rows; `ext_rules`/`prod_regex` are deterministic (3 identical reps). Transcript-fed rows include +1.59 s ASR.

{{FULL_TABLE}}

![pareto](bench-results/pareto.svg)

Pareto frontier (accuracy vs p50, `pareto.svg`): `ext_rules>gpt-realtime fewshot` (87.9 %, 1.9 s) → `ext_rules>veto(realtime+gemini)` (92.7 %, 3.3 s) → `ext_rules>sol` (93.8 %, 7.4 s).
The composite sits at the knee: +4.8 pp for +1.4 s over the fastest accurate point; the next +1.1 pp costs +4.1 s.

## 5. Chosen optimum per tier and overall

| slice | selected (rule §2.5) | sem | p50 / p95 s | note |
|---|---|---|---|---|
| **all (recommended)** | `ext_rules > veto(gpt-realtime fewshot ‖ gemini fewshot)` | **92.7 %** | **3.3 / 4.7** | +9.8 pp and −0.8 % unsafe vs the audio-native config; same p95 |
| all (audio-native only) | `gpt-realtime` fewshot manual | 82.9 % | 2.0 / 4.7 | no transcript step |
| all (latency unconstrained) | rule picks the same composite (within the 2 pp tie band of the 93.8 % max, `ext_rules > gpt-5.6-sol` fewshot, p50 7.4 / p95 19.4 s) | 92.7 % | 3.3 / 4.7 | raw max is `ext_rules>sol` |
| micro | `ext_rules > gpt-realtime-2 compact` (≈ any `ext_rules >` cascade) | 100 % | 1.6 / 2.1 | fast path answers 82 %, the rest goes to realtime |
| emergency | `ext_rules > gpt-realtime fewshot` | 98.6 % | 1.6 / 2.4 | the shipped `prod_regex` answered 11 of the 23 emergency rows and got **0** flag sets right (it always sets both flags) |
| operation | `gpt-realtime` fewshot | 81.9 % | 2.3 / 5.0 | weakest tier for every pipeline; best unconstrained: `gpt-5.6-sol` 80–91 % at 12 s |
| macro | `ext_rules > gemini fewshot` | 96.3 % | 1.6 / 3.9 | 81 calls: wide CI; composite policy gets 92.6 % |
| adversarial | `veto(gpt-realtime ‖ gemini)` | 96.8 % | 3.5 / 5.2 | gemini fewshot alone 93.5 %, realtime alone 72 %, `sol` 98.9 % at 11 s |

Important: a *router cannot choose a per-tier pipeline* before something has decided the tier, so the per-tier winners are a
diagnostic upper bound. The deployable object is the single composite policy in `summary.json → routing_policy`, whose measured per-tier numbers are:
micro 100 % · emergency 98.6 % · operation 81.9 % · macro 92.6 % · adversarial 96.8 %.

### Findings that matter for the product
* **The existing regex fast path is a safety, not an accuracy, feature.** On this corpus it answers 12/160 rows and is *semantically wrong* every time it fires (`퇴각해`, `공격 중단해`, `Retreat now`, even `정찰 작전 취소해` → retreat + cancel-attacks): it always emits both flags. It is correct only in the sense "when in doubt, retreat". It has no false positive on negation (`후퇴하지 마` → no hit).
* **Adversarial rows are the realtime model's weak spot**: `gpt-realtime` fewshot alone is correct on only 72 % of adversarial calls (executes an unrelated or hallucinated command for unsupported/raw-control/injection rows); cheap text `gemini` fewshot is correct on 94 %. The veto lifts it to 96.8 % and removed every unsafe execution in 480 calls.
* **Schema discipline**: realtime emitted `command_layer="clarification"` / `"production"` (not in the enum) in ~1.5 % of calls. The backend should accept a `clarification` block regardless of the layer string.
* **Endpointing**: manual commit vs server_vad500 gave the same accuracy (81.7 % vs 80.8 % fewshot); VAD adds its silence window (`speech_stopped` fires p50 2.3 s after the last speech sample in this run, because trailing silence was paced in real time and the proxy adds jitter) → use client-side end-of-speech + commit if you can, VAD otherwise.
* **Speaker/noise sensitivity** (full corpus, `gpt-realtime` fewshot): clean 85.0 %, fast 1.15× 85.6 %, 18 dB noise **78.1 %**; `gpt-realtime-2` compact: 87.5 % / 83.1 % / **68.1 %** — realtime-2 is much less noise robust, which is why it lost on the full corpus.
* **ASR is the hidden cost of text paths**: recorded `gpt-realtime` transcripts match the gold text exactly (after normalisation) for only 62/108 calls; `gemini fewshot` fell 87 % → 76 % when fed those transcripts (40 rows × 3 reps). The realtime ‖ gemini veto on recorded ASR text (same 120 calls, no fast path): **86.7 % vs 92.5 % with gold text, 0 unsafe in both** — so ASR noise costs ~6 pp of accuracy but not safety; the `ext_rules` step on ASR text answered 20 of 108 calls (20 correct) vs 26 (26) on gold text, i.e. ASR errors cost ~23 % of its coverage. Real-ASR accuracy of the recommended policy is therefore ≈ 87–90 %, not 92.7 %.

## 6. Variance and sample size (be skeptical)
* 160 rows is small; per-tier cells have 23–46 rows (69–138 calls, of which only 23–46 are independent). Differences under ~5 pp between configs are not established.
* Repeat-to-repeat accuracy std (per-rep accuracy over the 3 reps): `sol` 1.6 pp, `gpt-realtime` fewshot 3.4 pp, `gpt-realtime-2` compact **8.3 pp**, gemini 0.5 pp; fraction of rows whose 3 reps agree on correctness: `sol` 90 %, realtime fewshot 82.5 %, realtime-2 70 %.
* The screen winner among realtime (`realtime-2 compact`, 89 % on 40 rows) fell to 79.6 % on 160 rows: do not trust a 40-row ranking.
* `sol` fewshot's finalist number (92.9 % over 480 calls) mixes the screen reps (40-row subset: 98 %) with fresh reps on the other 120 rows (lower). Screen-only: 98.3 %.
* The two cascades `esc`/`spec` and `veto` were composed from independent single-pipeline runs; real systems add scheduling effects (a second WebSocket/HTTP call), so their latencies are a lower bound on contention-free hardware.

## 7. Failed / skipped / degraded pipelines (nothing hidden)
| item | outcome |
|---|---|
| `claude-sonnet-5-5-high` (text) | **failed**: proxy returns HTTP 429 `RESOURCE_EXHAUSTED / model_cooldown` (upstream individual quota reached, "resets in ~4h"); 0 results; marked skipped. (A plain `claude-sonnet-5-5` also rejects `tool_choice=required` with HTTP 400.) |
| `gpt-5.6-sol` with `tool_choice=required` | proxy returns HTTP 400 ("Kiro does not support … required"); the runner learns this and falls back to `auto` + "you MUST call submit_command" (worked for 100 % of calls) |
| `gpt-4o-realtime-preview`, `gpt-4o-mini-realtime-preview`, `gpt-realtime-mini-2025-10-06` | `model_not_found` on the proxy — skipped |
| `gpt-realtime-whisper` | accepted a client secret but is an ASR model; not benchmarked as an intent compiler |
| `/audio/transcriptions` | 404 on the proxy (no separate STT path); ASR numbers come from realtime `input_audio_transcription` (`gpt-4o-mini-transcribe`) |
| Realtime ephemeral secrets | `/realtime/client_secrets` returned **HTTP 429 `realtime_client_secret_capacity_exhausted`** under load (first screening attempt: ~60–95 % of calls; run discarded and restarted). The final runs fall back to connecting with the long-lived key and sending the identical session via `session.update` (`auth: direct_key` recorded per call: 471/480 for `gpt-realtime` fewshot used it). Latency window is unaffected (session is set before audio is sent); **the production path (mint) must be re-verified when capacity allows**. |
| `gpt-realtime-mini` | works but far too inaccurate (47–61 % sem, 62–93 % schema-valid) |
| ASR-enabled `gpt-realtime-mini` | only 83/122 calls produced a transcript within the 3 s grace window |
| Stage-1 aborted once | first screening run killed after ~114/120 secret-mint 429s; all those artifacts deleted before the real run |
| Cascade pairing | where a cascade member had fewer repeats (`sol` was top-up'd to 3 later), rep 0 is reused for missing reps |

## 8. Honest limitations
1. **Corpus authored by the same people who wrote `ext_rules`, and `ext_rules` was revised once after seeing its two misses** (first pass: 67 answered, 65 correct, 37 % coverage; revised: 66 answered, 66 correct, 41 % coverage). Its precision/coverage are therefore tuned to this corpus and optimistic on real speech; the cascade numbers using it were recomputed after the revision. The model-only numbers (realtime, text) were not affected.
2. **Synthetic TTS speech.** Real microphones, accents, hesitations, game audio and room noise will lower realtime accuracy more than text; the 18 dB-noise variant already costs 7–19 pp.
3. **Gold transcripts for text pipelines** (except the 40-row `asrtxt` check). Accuracy of any transcript-fed step is therefore an upper bound.
4. Latencies were measured on a **loaded laptop (load avg 25–50)** through a shared reverse proxy; the proxy also adds its own jitter (see the 9–40 s outliers in `gpt-realtime` tails). Compare configs, do not quote absolute SLOs. Latency of the whole recommended policy ≈ max(realtime, gemini+ASR).
5. **Schema ≠ production schema.** `scripts/bench/schema.py` mirrors the cross-agent contract but uses its own field names (`operations[].units`, `micro{}`, `production{}`, `buildings[]`, `clarification{}`). `feat/voice`'s `starcraft_commander/realtime_intent.py` (build_submit_command_tool) defines a different concrete shape (`operations[].action`, `unit_type/role`, `intensity`, `doctrine`, …). **These numbers do not transfer 1:1**; `common.py` loads the schema through `BENCH_SCHEMA_MODULE` so integration can point the benchmark at the shipped schema and re-run `run.py screen/full` (~45 min). Until then treat accuracy as relative ranking evidence.
6. `exact-semantic` is strict on *what the user said* (counts, routes, flags) but tolerant of plausible additions (`sem`) — `strict` (no invented attributes) is 85–89 % for the top configs; the gap is mostly models adding an unstated location/route.
7. Not benchmarked: real MicroMachine execution (and per `direct_runtime`, non-macro vectors currently run on the scripted fake bot in dry-run mode), multi-turn context (`recent_commands`), Chinese/Japanese, noisy game-audio.

## 9. Failure analysis — top 15 misses of the recommended policy

(`misses_full_selected.md`; rows ranked by how many of their repeats were wrong. "ope" = operation, "mac" = macro, "adv" = adversarial.)

Patterns:
1. **Construction vs macro** (`병영 하나 더 지어`, `정제소 지어`, `엔지니어링 베이 지어`): realtime picks `macro/sustain_production` instead of one-shot `operation + buildings[]`. The two are genuinely close in the DSL; either fix the instruction ("지어 + count/place ⇒ buildings[]") or accept both for building rows.
2. **Doctrine utterances**: `마린 중심으로 가라` → `regroup` (realtime reads "가라" as movement). (The `스팀팩 연구해` → micro-stim bug of the first `ext_rules` version was fixed after the first full run — see §8.1 — so it no longer appears here.)
3. **Operation detail**: dropped/renamed edits (`늘려` → `reinforce`/`update` instead of `resize`), dropped `avoid_enemy_army` route, 3-way split squads, dual-route parallel ops.
4. **Negation + imperative** (`후퇴하지 말고 계속 공격해`): the veto turns it into a clarification (safe, but the user *did* give a valid command — counted as a miss). Safe-but-unhelpful.
5. **Unsupported tech** (`보병 공격력 업그레이드 해`): models guess `engineering_bay` instead of refusing as unsupported.

{{MISSES_SELECTED}}

For comparison, the 15 misses of the audio-native-only `gpt-realtime fewshot`:

{{MISSES_AUDIO}}

## 10. Reproduce
```bash
source ~/.zshenv                       # MYPROXY_API_KEY
.venv/bin/python scripts/bench/build_corpus.py
.venv/bin/python scripts/bench/gold_check.py          # 160/160 legal
.venv/bin/python -m pytest scripts/bench/test_scoring.py -q
.venv/bin/python scripts/bench/run.py synth           # TTS cache (needs proxy)
.venv/bin/python scripts/bench/run.py screen --configs "rt:gpt-realtime:manual:fewshot,tx:gemini-3.5-flash-lite:fewshot" --reps 3
.venv/bin/python scripts/bench/run.py full   --configs "rt:gpt-realtime:manual:fewshot,fp:ext_rules" --reps 3
.venv/bin/python scripts/bench/report.py --stage screen && .venv/bin/python scripts/bench/report.py --stage full
.venv/bin/python scripts/bench/make_doc.py
```
