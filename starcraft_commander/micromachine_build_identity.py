"""Build identity reports for patched MicroMachine production evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final


REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_MICROMACHINE_COMMIT: Final[str] = "eb893161371dab975a0a7e600f9e250ac03ec1ef"
DEFAULT_S2CLIENT_COMMIT: Final[str] = "614acc00abb5355e4c94a1b0279b46e9d845b7ce"
DEFAULT_MICROMACHINE_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0001-macos-latest-s2client-policy-blackboard.patch"
)
DEFAULT_S2CLIENT_PATCH: Final[Path] = (
    REPO_ROOT
    / "integrations"
    / "micromachine"
    / "patches"
    / "0001-s2client-macos-launchservices.patch"
)
DEFAULT_HOOK_MANIFEST: Final[Path] = (
    REPO_ROOT / "integrations" / "micromachine" / "HOOK_MANIFEST.json"
)
DEFAULT_MAP_POOL: Final[Path] = (
    REPO_ROOT / "integrations" / "micromachine" / "MICROMACHINE_MAP_POOL.json"
)
DEFAULT_BLACKBOARD_HEADER: Final[Path] = (
    REPO_ROOT / "integrations" / "micromachine" / "voi_policy_blackboard.hpp"
)


@dataclass(frozen=True)
class MicroMachineBuildIdentityConfig:
    """Inputs needed to produce a reproducible MicroMachine build identity."""

    micromachine_dir: Path
    s2client_dir: Path
    micromachine_build_dir: Path
    micromachine_commit: str = DEFAULT_MICROMACHINE_COMMIT
    s2client_commit: str = DEFAULT_S2CLIENT_COMMIT
    micromachine_patch: Path = DEFAULT_MICROMACHINE_PATCH
    s2client_patch: Path = DEFAULT_S2CLIENT_PATCH
    hook_manifest: Path = DEFAULT_HOOK_MANIFEST
    map_pool: Path = DEFAULT_MAP_POOL
    blackboard_header: Path = DEFAULT_BLACKBOARD_HEADER

    @property
    def binary_path(self) -> Path:
        return self.micromachine_build_dir / "bin" / "MicroMachine"


def build_micromachine_build_identity(
    config: MicroMachineBuildIdentityConfig,
) -> dict[str, object]:
    """Create a machine-readable identity report without modifying worktrees."""

    observed_micro = _git_head(config.micromachine_dir)
    observed_s2 = _git_head(config.s2client_dir)
    binary_exists = config.binary_path.exists()
    binary_sha256 = _sha256_file(config.binary_path) if binary_exists else None
    failures: list[dict[str, object]] = []
    if observed_micro not in (None, config.micromachine_commit):
        failures.append(
            {
                "code": "micromachine_commit_mismatch",
                "expected": config.micromachine_commit,
                "actual": observed_micro,
            }
        )
    if observed_s2 not in (None, config.s2client_commit):
        failures.append(
            {
                "code": "s2client_commit_mismatch",
                "expected": config.s2client_commit,
                "actual": observed_s2,
            }
        )
    if not binary_exists:
        failures.append(
            {
                "code": "missing_binary",
                "path": str(config.binary_path),
            }
        )

    checksums = {
        "micromachine_patch_sha256": _sha256_file(config.micromachine_patch),
        "s2client_patch_sha256": _sha256_file(config.s2client_patch),
        "hook_manifest_sha256": _sha256_file(config.hook_manifest),
        "map_pool_sha256": _sha256_file(config.map_pool),
        "blackboard_header_sha256": _sha256_file(config.blackboard_header),
        "binary_sha256": binary_sha256,
    }
    identity_material = {
        "micromachine_commit": config.micromachine_commit,
        "s2client_commit": config.s2client_commit,
        **checksums,
    }
    identity = "sha256:" + _sha256_json(identity_material)
    return {
        "schema_version": 1,
        "identity": identity,
        "ok": not failures,
        "failures": failures,
        "expected": {
            "micromachine_commit": config.micromachine_commit,
            "s2client_commit": config.s2client_commit,
        },
        "observed": {
            "micromachine_commit": observed_micro,
            "s2client_commit": observed_s2,
        },
        "paths": {
            "micromachine_dir": str(config.micromachine_dir),
            "s2client_dir": str(config.s2client_dir),
            "micromachine_build_dir": str(config.micromachine_build_dir),
            "binary": str(config.binary_path),
            "micromachine_patch": str(config.micromachine_patch),
            "s2client_patch": str(config.s2client_patch),
            "hook_manifest": str(config.hook_manifest),
            "map_pool": str(config.map_pool),
            "blackboard_header": str(config.blackboard_header),
        },
        "checksums": checksums,
    }


def write_build_identity_report(
    report: Mapping[str, object],
    output: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def read_build_identity(path: Path | str) -> str | None:
    report_path = Path(path)
    if not report_path.exists():
        return None
    payload = json.loads(report_path.read_text())
    if not isinstance(payload, Mapping):
        return None
    identity = payload.get("identity")
    return identity if isinstance(identity, str) and identity else None


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emit MicroMachine build identity.")
    parser.add_argument("--micromachine-dir", required=True)
    parser.add_argument("--s2client-dir", required=True)
    parser.add_argument("--micromachine-build-dir", required=True)
    parser.add_argument("--micromachine-commit", default=DEFAULT_MICROMACHINE_COMMIT)
    parser.add_argument("--s2client-commit", default=DEFAULT_S2CLIENT_COMMIT)
    parser.add_argument("--micromachine-patch", default=str(DEFAULT_MICROMACHINE_PATCH))
    parser.add_argument("--s2client-patch", default=str(DEFAULT_S2CLIENT_PATCH))
    parser.add_argument("--hook-manifest", default=str(DEFAULT_HOOK_MANIFEST))
    parser.add_argument("--map-pool", default=str(DEFAULT_MAP_POOL))
    parser.add_argument("--blackboard-header", default=str(DEFAULT_BLACKBOARD_HEADER))
    parser.add_argument("--output")
    parser.add_argument("--field", choices=("identity", "ok"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    report = build_micromachine_build_identity(
        MicroMachineBuildIdentityConfig(
            micromachine_dir=Path(args.micromachine_dir),
            s2client_dir=Path(args.s2client_dir),
            micromachine_build_dir=Path(args.micromachine_build_dir),
            micromachine_commit=args.micromachine_commit,
            s2client_commit=args.s2client_commit,
            micromachine_patch=Path(args.micromachine_patch),
            s2client_patch=Path(args.s2client_patch),
            hook_manifest=Path(args.hook_manifest),
            map_pool=Path(args.map_pool),
            blackboard_header=Path(args.blackboard_header),
        )
    )
    if args.output:
        write_build_identity_report(report, Path(args.output))
    if args.field == "identity":
        print(report["identity"])
    elif args.field == "ok":
        print("1" if report["ok"] else "0")
    elif not args.output:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _git_head(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = completed.stdout.strip()
    return value or None


if __name__ == "__main__":
    raise SystemExit(main())
