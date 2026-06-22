# MicroMachine Production Operations

This runbook covers the remaining production gates after user-facing QA:
map/game diversity, neural representation provider attachment, and CI/local
operations.

## Map And Game Diversity Gate

Use the matrix runner when validating more than one map, enemy race, or enemy
difficulty:

```bash
MICROMACHINE_DIR=/private/tmp/MicroMachine \
MICROMACHINE_BUILD_DIR=/private/tmp/MicroMachine/build-latest-api \
SOAK_MATRIX_RUN_ID=production-diversity-001 \
SOAK_MATRIX_MAP_FILES="AcropolisLE.SC2Map Ladder2019Season3/ThunderbirdLE.SC2Map" \
SOAK_MATRIX_ENEMY_RACES="Zerg Protoss Terran" \
SOAK_MATRIX_ENEMY_DIFFICULTIES="1 2" \
SOAK_MATRIX_TARGET_FRAME=12000 \
SOAK_MATRIX_TIMEOUT_SECONDS=1200 \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

The runner writes:

- Per-case artifacts under
  `/private/tmp/voi-mm-soak-matrix/<run-id>/<case-id>/`.
- A matrix summary at
  `/private/tmp/voi-mm-soak-matrix/<run-id>/matrix_report.json`.

Production qualification requires either:

- `matrix_report.json.ok == true` for a pure pass matrix, or
- an explicitly documented mixed matrix with `SOAK_MATRIX_ALLOW_FAILURES=1`,
  where failures are expected negative controls such as unsupported map
  geometry producing `no_production_deadlock`; mixed matrices still require at
  least `SOAK_MATRIX_MIN_PASSES` passing case, defaulting to 1.

Do not weaken `soak_macos_local.sh` classifiers to make a flaky map pass. A
map/start-location failure is useful evidence only if it is preserved as a
failed case report.

Verified local matrix evidence:

| Run | Evidence |
| --- | --- |
| `issue-10-12-diversity-v1` | `/private/tmp/voi-mm-soak-matrix/issue-10-12-diversity-v1/matrix_report.json` covered `AcropolisLE.SC2Map` and `Ladder2019Season3/ThunderbirdLE.SC2Map` against `Zerg`, `Protoss`, and `Terran` difficulty 1. The matrix completed with `passed=1`, `failed=5`, and preserved failure codes including `no_production_deadlock`, `micromachine_crash`, `micromachine_process_stopped`, and `telemetry_missing`. |
| `02-AcropolisLE-SC2Map-Protoss-d1` | Passed to frame 12042 with `macro_evidence_ok=true` and `manager_intervention_ok=true`. This is the currently qualified diversity case from the matrix. |
| Thunderbird cases | Failed instead of being silently retried or promoted, preserving unsupported-map/startup evidence as production gap data. |

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
- Requires a self-hosted macOS runner with StarCraft II, maps, and the patched
  MicroMachine build already installed.
- Uploads the matrix artifact directory from `/private/tmp/voi-mm-soak-matrix`.

Stop condition for operations sign-off:

1. Hosted CI passes.
2. Self-hosted soak matrix produces a reviewed `matrix_report.json`.
3. The matrix contains at least one explicit qualified case for user QA and
   preserves every deterministic bot/map failure as a failed report.
4. Neural adapter tests pass and any real model adapter only emits bounded
   representation axes.
5. User QA is the only remaining manual gate.
