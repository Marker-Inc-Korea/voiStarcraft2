# MicroMachine Production Operations

This runbook covers the remaining production gates after user-facing QA:
map/game diversity, neural representation provider attachment, and CI/local
operations.

## Map And Game Diversity Gate

The production map pool is versioned in
`integrations/micromachine/MICROMACHINE_MAP_POOL.json`. That manifest is the
source of truth for required, diagnostic, and excluded maps. Production support
means the required pool in that file; it does not mean every custom StarCraft II
map is supported.
Diagnostic maps are known investigation targets and cannot count as production
signoff. Excluded maps are outside the support contract until they are promoted
to diagnostic and then to required with artifact-backed zero-failure evidence.

Use the matrix runner when validating more than one map, enemy race, or enemy
difficulty:

```bash
MICROMACHINE_DIR=/private/tmp/MicroMachine \
MICROMACHINE_BUILD_DIR=/private/tmp/MicroMachine/build-latest-api \
SOAK_MATRIX_RUN_ID=production-diversity-001 \
SOAK_MATRIX_QUALIFICATION_TIER=production \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

Use the expanded required-pool tier when validating the next ladder-style
race/difficulty matrix before user QA:

```bash
MICROMACHINE_DIR=/private/tmp/MicroMachine \
MICROMACHINE_BUILD_DIR=/private/tmp/MicroMachine/build-latest-api \
SOAK_MATRIX_RUN_ID=extended-required-pool-001 \
SOAK_MATRIX_QUALIFICATION_TIER=extended \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

The `extended` tier still includes only required maps. It expands the built-in
AI matrix to Zerg, Protoss, and Terran at difficulties 1 and 2, and it keeps
`failed=0` as the pass condition.

Explicit environment overrides such as `SOAK_MATRIX_MAP_FILES`,
`SOAK_MATRIX_ENEMY_RACES`, `SOAK_MATRIX_ENEMY_DIFFICULTIES`,
`SOAK_MATRIX_TARGET_FRAME`, and `SOAK_MATRIX_TIMEOUT_SECONDS` still take
precedence for diagnostics and one-off investigations.

Disable real SC2 execution without deleting the workflow or scripts:

```bash
SOAK_MATRIX_ENABLED=0 \
SOAK_MATRIX_RUN_ID=disabled-maintenance-window \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

Disabled mode writes `matrix_report.json`, `soak_history_dashboard.json`, and
`soak_history_dashboard.md` with `status: disabled` and exits successfully. Use
this for maintenance windows or when the self-hosted runner is intentionally
offline. A disabled run is not production sign-off evidence because
`matrix_report.json.ok == false`.

Run Thunderbird or other unqualified maps only as explicit diagnostics:

```bash
SOAK_MATRIX_RUN_ID=diagnostic-thunderbird-001 \
SOAK_MATRIX_QUALIFICATION_TIER=diagnostic \
SOAK_MATRIX_MAP_FILES="Ladder2019Season3/ThunderbirdLE.SC2Map" \
SOAK_MATRIX_ALLOW_FAILURES=1 \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

The Thunderbird blocker is tracked as
`thunderbird_walloff_geometry_no_production_deadlock` in the map-pool manifest.
See `docs/micromachine-thunderbird-blocker.md` for the artifact path,
root-cause candidates, reproduction command, and promotion criteria. Do not
move Thunderbird into the required pool until that checklist passes with
`SOAK_MATRIX_ALLOW_FAILURES=0`.

The active Base97364 host also has a required-pool blocker tracked as
`base97364_local_create_game_no_start_units`. This is not a connection failure:
SC2 opens the API listener and `WaitJoinGame` succeeds, but telemetry remains at
`CCBot.bootstrap_waiting` with `self_count=0` and `resource_depot_count=0`.
Production sign-off must remain blocked while any smoke, soak, matrix, or
release-gate evidence contains `bootstrap_no_start_units`.

The runner writes:

- Per-case artifacts under
  `/private/tmp/voi-mm-soak-matrix/<run-id>/<case-id>/`.
- A matrix summary at
  `/private/tmp/voi-mm-soak-matrix/<run-id>/matrix_report.json`.
- A recent-history JSON dashboard at
  `/private/tmp/voi-mm-soak-matrix/<run-id>/soak_history_dashboard.json`.
- A Markdown summary at
  `/private/tmp/voi-mm-soak-matrix/<run-id>/soak_history_dashboard.md`.
- A compact failure triage JSON report at
  `/private/tmp/voi-mm-soak-matrix/<run-id>/triage_report.json`.
- A GitHub-ready failure triage Markdown report at
  `/private/tmp/voi-mm-soak-matrix/<run-id>/triage_report.md`.

The history dashboard includes a `production_signoff` object. It is the
recent-N production evidence gate, not just a run counter. It only counts
enabled runs from the configured signoff tier, excludes disabled and diagnostic
runs, and blocks signoff when:

- No eligible production run exists in the recent window.
- Any eligible production run has `ok != true` or `failed > 0`.
- Required map, enemy race, enemy difficulty, or strategy profile coverage is
  missing.
- `SOAK_MATRIX_SIGNOFF_REQUIRED_BUILD_IDENTITY` is set and a run was produced
  by a different MicroMachine build.

Attach both files to the final PR or issue comment:

- `soak_history_dashboard.json` for machine-readable `production_signoff`.
- `soak_history_dashboard.md` for reviewer-readable status, blockers, and
  recent run paths.

Useful signoff overrides:

```bash
BUILD_IDENTITY_REPORT=/private/tmp/MicroMachine/build-latest-api/voi_build_identity.json
SOAK_MATRIX_SIGNOFF_TIER=production \
SOAK_MATRIX_SIGNOFF_REQUIRED_BUILD_IDENTITY="$(python3 -m starcraft_commander.micromachine_build_identity --read-report "${BUILD_IDENTITY_REPORT}" --field identity)" \
SOAK_MATRIX_BUILD_IDENTITY_REPORT="${BUILD_IDENTITY_REPORT}" \
SOAK_MATRIX_RUN_ID=production-signoff-001 \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

Build identity must come from a reproducible patched build report, not an
implicit local binary path. Rebuild and emit the report with:

```bash
ROOT_DIR=/private/tmp/voi-micromachine-runtime \
MICROMACHINE_DIR=/private/tmp/MicroMachine \
MICROMACHINE_BUILD_DIR=/private/tmp/MicroMachine/build-latest-api \
MICROMACHINE_BUILD_IDENTITY_REPORT=/private/tmp/MicroMachine/build-latest-api/voi_build_identity.json \
integrations/micromachine/scripts/build_macos_local.sh
```

The report records the upstream MicroMachine commit, `s2client-api` commit,
MicroMachine patch checksum, `s2client-api` patch checksum, hook manifest
checksum, map-pool checksum, blackboard header checksum, binary path, and binary
checksum. The matrix runner reads that report by default from
`$MICROMACHINE_BUILD_DIR/voi_build_identity.json` and writes the report identity
into `matrix_report.json.build_identity`. Production signoff blocks
`unrecorded` or missing build identities and blocks mismatches when
`SOAK_MATRIX_SIGNOFF_REQUIRED_BUILD_IDENTITY` is set.

Production qualification requires `matrix_report.json.ok == true` and
`matrix_report.json.failed == 0`. `SOAK_MATRIX_ALLOW_FAILURES=1` is only for
diagnostics or negative-control evidence; it must not be used for production
sign-off.
The matrix runner rejects `SOAK_MATRIX_QUALIFICATION_TIER=production` combined
with `SOAK_MATRIX_ALLOW_FAILURES=1`.

## Final Release Gate

Issues #141, #142, #128, and #124 use one final PR followed by one exact-main
post-merge qualification. The authoritative inputs are:

- `integrations/micromachine/PRE_LIVE_RELEASE_STATUS.json`
- `integrations/micromachine/PRE_LIVE_JOURNEYS.json`
- `.github/workflows/final-pre-live.yml`
- `docs/micromachine-final-live-qa.md`

After this bootstrap PR merges, later same-repository PRs run
`ready_to_merge` from the trusted default-branch `pull_request_target`
workflow. It verifies the real GitHub state of every declared dependency and
requires each dependency issue to be closed by a merged PR. All four
release-closure issues are intentionally excluded from that dependency list
so a closing PR does not require its own merge and closure before it can pass.

The bootstrap PR that first adds `.github/workflows/final-pre-live.yml` cannot
run that workflow before merge because `pull_request_target` loads workflow
code from the default branch, where the file does not yet exist. Its
pre-merge basis is the existing trusted `ci.yml` and
`pre-live-provenance.yml` workflows plus an exact-candidate-SHA independent
review. The exact `main` push after merge is the first authoritative final
release verdict: GitHub loads the merged workflow from the exact merge SHA,
every child gate runs, and `ready_for_live_qa` must pass.

After that PR merges, the `main` push reruns the full pipeline at the exact
merge SHA in `ready_for_live_qa` mode. The final gate then verifies from GitHub
that:

- The current `main` head is exactly the workflow SHA.
- Issues #141, #142, #128, and #124 are closed.
- Exactly one accepted closing PR for all four issues merged to that same
  `main` SHA.
- The workflow event, run ID, run attempt, repository, and child artifacts all
  match the report inputs.

Both modes keep `manual_live_qa_remaining=true` and `live_qualified=false`.
Automated success authorizes the final live-QA runbook; it never claims that
actual StarCraft II gameplay, visuals, HUD consistency, captions, or tactical
audio have been observed.

### Child Artifact Contract

The workflow produces these exact raw artifacts:

| Producer | GitHub artifact | Canonical member |
| --- | --- | --- |
| Build identity | `micromachine-build-identity` | `build_identity/report.json` |
| Deterministic journeys | `micromachine-deterministic-journeys` | `deterministic_journeys/report.json` |
| Browser/accessibility | `micromachine-browser-accessibility` | `browser_accessibility/report.json` |
| Distribution compliance | `micromachine-distribution-compliance` | `distribution_compliance/report.json` |
| Pre-live provenance | `micromachine-pre-live-provenance` | `pre_live_provenance/report.json` |

Each raw artifact is uploaded before its numeric GitHub artifact ID is known.
A separate sealing job downloads those exact IDs and creates a canonical
envelope containing `schema_version`, `producer`, `repository_sha`,
`build_identity`, `generated_at`, detached member `sha256`,
`workflow_run_id`, `run_attempt`, `artifact_id`, `archive_sha256`, and
`member`.

The final release verifier rejects missing or extra producers, stale or future
evidence, replayed sets, wrong run/attempt/SHA/build bindings, wrong GitHub
artifact names or IDs, expired artifacts, non-canonical JSON, self-digests,
symlinks, non-regular files, path traversal, member digest changes, failed
child status, and private configuration or secret-shaped values.

### Generated Reports

The PR workflow artifact `micromachine-ready_to_merge` contains:

- `ready_to_merge.json`
- `ready_to_merge.md`

The exact `main` push artifact `micromachine-ready_for_live_qa` contains:

- `ready_for_live_qa.json`
- `ready_for_live_qa.md`

These generated reports and the checked-in structured status are the release
source of truth. README test counts, local hashes, browser versions, run IDs,
and dated observations are non-authoritative unless they are present in the
exact-SHA generated artifact.

The only remaining manual gate after `ready_for_live_qa` passes is
`docs/micromachine-final-live-qa.md`. It covers the fourteen deterministic
journeys one-to-one and records actual SC2 visual, movement, engagement, HUD,
caption, and tactical-audio observations. Human multiplayer, ladder,
Battle.net qualification, and competitive balance signoff remain deferred.

Before launching a case, the matrix runner now writes
`preflight_report.json`. Preflight distinguishes:

- `unsupported_map`: the map is unknown, excluded, or outside the selected tier.
- `missing_map`: a configured `SOAK_MATRIX_MAP_ROOTS` lookup could not find the map.
- `geometry_risk`: manifest metadata indicates a ramp/start-location risk.
- `placement_risk`: manifest metadata indicates a wall-off/build-placement risk.
- `production_runtime_failure`: preflight passed, but the later soak report failed.

Required production cases fail closed on preflight errors. Diagnostic cases can
collect preflight blockers as evidence, but they still do not count as
production signoff.
`SOAK_MATRIX_MAP_ROOTS` is a colon-separated list so macOS paths with spaces,
such as a `StarCraft II` install directory, remain valid.

Artifact retention:

- GitHub Actions uploads from `.github/workflows/micromachine-local-soak.yml`
  are pinned to `retention-days: 30`.
- Local self-hosted artifacts remain under `/private/tmp/voi-mm-soak-matrix`
  until the operator deletes old run directories.
- Keep the most recent passing production run and any recent failed diagnostic
  run needed for triage before cleaning old directories.
- Never delete a failed run before `failure_codes`, `matrix_report.json`, and
  `triage_report.md` have been reviewed or attached to the issue/PR.

Do not weaken `soak_macos_local.sh` classifiers to make a flaky map pass. A
map/start-location failure is useful evidence only as debugging input, not as a
production-qualified case.
The final soak classifier also rejects `income_stall`: reaching the target frame
is not enough unless recent mineral and gas income evidence remains positive
near the target.

Long-horizon strategy profile soak uses `SOAK_PROFILE_SEQUENCE`. The default
`default_defensive_to_aggressive` schedule publishes `defensive_hold` at frame
0 and delays `aggressive_pressure` until the configured aggressive frame and
macro evidence are both present. For deeper DSL QA, use an explicit sequence:

```bash
SOAK_PROFILE_SEQUENCE="defensive_hold@0,economic_expansion@6000,scouting_map_control@9000,tech_transition@13000" \
integrations/micromachine/scripts/soak_macos_local.sh
```

Profiles are bounded manager bias vectors, not raw commands. The current
catalog is `defensive_hold`, `economic_expansion`, `aggressive_pressure`,
`scouting_map_control`, `tech_transition`, and `emergency_recovery`. The final
classifier records expected profile tags in `soak_report.json` and fails with
`strategy_profile_missing` if `modulation_updates.jsonl` does not prove the
scheduled profiles were published.

Tactical-effect evidence can be required independently from profile publishing:

```bash
SOAK_EXPECTED_TACTICAL_EFFECTS="pressure contain target_priority" \
integrations/micromachine/scripts/soak_macos_local.sh
```

When this variable is non-empty, the final classifier requires behavior-level
MicroMachine telemetry/log evidence, not just published or consumed blackboard
axes. Missing evidence fails with `tactical_effect_missing`; leaving the
variable empty keeps the soak optional for environments where tactical evidence
is only diagnostic.

Verified local matrix evidence:

| Run | Evidence |
| --- | --- |
| `issue-10-13-acropolis-races-zero-v4` | `/private/tmp/voi-mm-soak-matrix/issue-10-13-acropolis-races-zero-v4/matrix_report.json` passed with `SOAK_MAX_ATTEMPTS=1`, `passed=3`, `failed=0` for `AcropolisLE.SC2Map` against `Zerg`, `Protoss`, and `Terran` difficulty 1. |
| Thunderbird blocker | `Ladder2019Season3/ThunderbirdLE.SC2Map` emitted `Depot build position fallback used`, `Invalid setup detected`, and `Unusual ramp detected, tiles to block = 0`; this is `thunderbird_walloff_geometry_no_production_deadlock`, a MicroMachine map-support blocker, not production evidence. |

## Neural/SOTA Representation Attachment

`starcraft_commander.neural_representation` is the model attachment surface.
SOTA or AlphaStar-like components implement `NeuralRepresentationModelAdapter`
and return bounded semantic `representation_axes`.

```python
from starcraft_commander import (
    MicroMachineFilesystemBlackboard,
    PolicyModulationProviderRequest,
    PolicyModulationSource,
    publish_neural_representation_modulation,
)

backend = MicroMachineFilesystemBlackboard("/private/tmp/voi-mm-live")
request = PolicyModulationProviderRequest(
    command_text="탱크 중심으로 안전하게 버텨",
    source=PolicyModulationSource.NEURAL_REPRESENTATION,
    game_state={"frame": 6400},
)

result = publish_neural_representation_modulation(
    adapter=my_model_adapter,
    request=request,
    backend=backend,
    current_frame=6400,
    update_id="neural-6400",
)
```

The adapter cannot publish directly to MicroMachine. Its output must pass
through the provider compiler and `MicroMachineModulationBackend`, so raw keys
such as `raw_action`, `python_sc2`, `unit_tag`, or direct s2client actions are
rejected before reaching the C++ bridge.

## CI And Self-Hosted Soak

Hosted CI:

- `.github/workflows/ci.yml`
- Runs `uv run pytest -q` on Python 3.10, 3.11, and 3.12.
- Runs `bash -n` on MicroMachine smoke/soak/matrix scripts.
- `.github/workflows/final-pre-live.yml`
- On the final PR, builds one admitted MicroMachine identity and runs the
  deterministic fourteen journeys, browser/accessibility gate, distribution
  compliance, pre-live provenance, and `ready_to_merge`.
- On the exact `main` merge SHA, reruns every child gate and emits
  `ready_for_live_qa`.

Real SC2 GUI soak:

- `.github/workflows/micromachine-local-soak.yml`
- Manual `workflow_dispatch`.
- Default input `enable_soak=0` writes disabled artifacts only.
- Set `enable_soak=1` to verify local inputs and run real StarCraft II.
- Requires a self-hosted macOS runner with StarCraft II, maps, and the patched
  MicroMachine build already installed.
- Uploads the matrix artifact directory from `/private/tmp/voi-mm-soak-matrix`,
  including `matrix_report.json` and the history dashboard files.

Stop condition for final pre-live sign-off:

1. The final PR's hosted CI and all five final-pre-live child artifacts pass on
   one exact SHA, build identity, run, and attempt.
2. `ready_to_merge.json` reports no blockers while excluding the four
   release-closure issues from dependency checks.
3. The PR merges to `main` and closes #141, #142, #128, and #124.
4. The exact merge SHA reruns every child gate and
   `ready_for_live_qa.json` reports no blockers.
5. The fourteen-journey live-QA runbook is completed against that exact SHA and
   build identity.
6. Any ambiguous gameplay, visual, HUD, caption, or audio observation leaves
   `manual_live_qa_remaining=true`; human multiplayer remains deferred.
