# MicroMachine Thunderbird Blocker

`Ladder2019Season3/ThunderbirdLE.SC2Map` is a diagnostic map, not production
sign-off evidence. It previously reached a deterministic MicroMachine macro
failure: `no_production_deadlock`.

## Blocker Code

`thunderbird_walloff_geometry_no_production_deadlock`

## Evidence

The known failed artifact is:

```text
/private/tmp/voi-mm-soak/issue-10-11-final-thunderbird-v2/soak_report.json
```

Observed signatures:

- `Depot build position fallback used`
- `Invalid setup detected`
- `Unusual ramp detected, tiles to block = 0`
- `no_production_deadlock`

The likely root-cause area is `ramp_walloff_build_placement`. The leading
candidates are ramp detection, wall-off tile calculation, and Depot/Barracks
placement. Worker path safety and production-manager state remain less likely
until a patched MicroMachine build proves otherwise.

## Reproduction

```bash
SOAK_MATRIX_RUN_ID=diagnostic-thunderbird-001 \
SOAK_MATRIX_QUALIFICATION_TIER=diagnostic \
SOAK_MATRIX_MIN_PASSES=0 \
integrations/micromachine/scripts/soak_matrix_macos_local.sh
```

## Promotion Criteria

Thunderbird can be promoted from diagnostic to required only after all of these
hold on a patched MicroMachine build:

- It passes Thunderbird at 12000 frames.
- `macro_evidence_ok=true`.
- `manager_intervention_ok=true`.
- Latest telemetry proves fresh modulation consumption.
- Matrix summary has `failed=0`.
- `SOAK_MATRIX_ALLOW_FAILURES=0`.
- `geometry_risk` and `placement_risk` are removed from the map-pool manifest
  in the same PR that moves Thunderbird to `required`.

Until then, any Thunderbird failure must remain visible as diagnostic blocker
evidence and must not be counted as production qualification.
