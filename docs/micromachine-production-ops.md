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

The runner writes:

- Per-case artifacts under
  `/private/tmp/voi-mm-soak-matrix/<run-id>/<case-id>/`.
- A matrix summary at
  `/private/tmp/voi-mm-soak-matrix/<run-id>/matrix_report.json`.
- A recent-history JSON dashboard at
  `/private/tmp/voi-mm-soak-matrix/<run-id>/soak_history_dashboard.json`.
- A Markdown summary at
  `/private/tmp/voi-mm-soak-matrix/<run-id>/soak_history_dashboard.md`.

Production qualification requires `matrix_report.json.ok == true` and
`matrix_report.json.failed == 0`. `SOAK_MATRIX_ALLOW_FAILURES=1` is only for
diagnostics or negative-control evidence; it must not be used for production
sign-off.
The matrix runner rejects `SOAK_MATRIX_QUALIFICATION_TIER=production` combined
with `SOAK_MATRIX_ALLOW_FAILURES=1`.

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
  `soak_history_dashboard.md` have been reviewed or attached to the issue/PR.

Do not weaken `soak_macos_local.sh` classifiers to make a flaky map pass. A
map/start-location failure is useful evidence only as debugging input, not as a
production-qualified case.
The final soak classifier also rejects `income_stall`: reaching the target frame
is not enough unless recent mineral and gas income evidence remains positive
near the target.

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

Real SC2 GUI soak:

- `.github/workflows/micromachine-local-soak.yml`
- Manual `workflow_dispatch`.
- Default input `enable_soak=0` writes disabled artifacts only.
- Set `enable_soak=1` to verify local inputs and run real StarCraft II.
- Requires a self-hosted macOS runner with StarCraft II, maps, and the patched
  MicroMachine build already installed.
- Uploads the matrix artifact directory from `/private/tmp/voi-mm-soak-matrix`,
  including `matrix_report.json` and the history dashboard files.

Stop condition for operations sign-off:

1. Hosted CI passes.
2. Self-hosted soak matrix produces a reviewed `matrix_report.json`.
3. The production matrix has zero failed cases.
4. Neural adapter tests pass and any real model adapter only emits bounded
   representation axes.
5. User QA is the only remaining manual gate.
