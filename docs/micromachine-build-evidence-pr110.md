# MicroMachine Build Evidence for PR #110

## Command

```sh
integrations/micromachine/scripts/build_macos_local.sh
```

## Result

The local MicroMachine runtime build completed successfully.

```text
[100%] Built target MicroMachine
MicroMachine executable: /private/tmp/voi-micromachine-runtime/MicroMachine/build-latest-api/bin/MicroMachine
MicroMachine build identity report: /private/tmp/voi-micromachine-runtime/MicroMachine/build-latest-api/voi_build_identity.json
```

## Build Identity

Extracted from `/private/tmp/voi-micromachine-runtime/MicroMachine/build-latest-api/voi_build_identity.json` after the successful build:

```json
{
  "ok": true,
  "identity": "sha256:1838077e91cbfb5270b741df9b3f327e90a70dc8fcab5c941384577e10df178e",
  "expected": {
    "micromachine_commit": "eb893161371dab975a0a7e600f9e250ac03ec1ef",
    "s2client_commit": "614acc00abb5355e4c94a1b0279b46e9d845b7ce"
  },
  "observed": {
    "micromachine_commit": "eb893161371dab975a0a7e600f9e250ac03ec1ef",
    "s2client_commit": "614acc00abb5355e4c94a1b0279b46e9d845b7ce"
  },
  "checksums": {
    "binary_sha256": "a2b6fcf04010a27a593bd3d2d05ac8986d0d2309a4849164a5da2728ceb2a9af",
    "micromachine_patch_sha256": "a9806b255a0291a24e0747fcfd9bf83e185611733dfd60cefe3fbd765a06f6cd",
    "s2client_patch_sha256": "e48d175770b3dbeb91d02a2e74e6ad5e878c1135ff228f120d8b2ca6368bf9f8"
  },
  "failures": []
}
```

## Python Validation

```sh
pytest tests/test_micromachine_live_session.py tests/test_web_gui.py tests/test_policy_modulation_provider.py tests/test_micromachine_tactical_evidence.py tests/test_micromachine_soak.py tests/test_micromachine_integration_kit.py -q
```

Result:

```text
252 passed, 870 subtests passed in 16.21s
```
