"""Authenticated final release orchestration for the MicroMachine pre-live track."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Final, Protocol

try:
    import fcntl
except ImportError:  # pragma: no cover - release CI is POSIX; fail closed elsewhere.
    fcntl = None  # type: ignore[assignment]


REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_STATUS_PATH: Final[Path] = (
    REPOSITORY_ROOT
    / "integrations"
    / "micromachine"
    / "PRE_LIVE_RELEASE_STATUS.json"
)
DEFAULT_JOURNEY_MANIFEST_PATH: Final[Path] = (
    REPOSITORY_ROOT / "integrations" / "micromachine" / "PRE_LIVE_JOURNEYS.json"
)
REPOSITORY_RUNBOOK_PATH: Final[Path] = (
    REPOSITORY_ROOT / "docs" / "micromachine-final-live-qa.md"
)
DEFAULT_RUNBOOK_PATH: Final[Path | None] = (
    REPOSITORY_RUNBOOK_PATH if REPOSITORY_RUNBOOK_PATH.is_file() else None
)
READY_TO_MERGE: Final[str] = "ready_to_merge"
READY_FOR_LIVE_QA: Final[str] = "ready_for_live_qa"
EXPECTED_WORKFLOW_PATH: Final[str] = ".github/workflows/final-pre-live.yml"
RELEASE_MODES: Final[frozenset[str]] = frozenset(
    {READY_TO_MERGE, READY_FOR_LIVE_QA}
)
ENVELOPE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "producer",
        "repository_sha",
        "build_identity",
        "generated_at",
        "sha256",
        "workflow_run_id",
        "run_attempt",
        "artifact_id",
        "archive_sha256",
        "member",
    }
)
STATUS_CATEGORIES: Final[tuple[str, ...]] = (
    "implemented",
    "automated_qualified",
    "live_qa_pending",
    "proposed_deferred",
)
STATUS_LABELS: Final[Mapping[str, str]] = {
    "implemented": "Implemented",
    "automated_qualified": "Automated qualified",
    "live_qa_pending": "Live QA pending",
    "proposed_deferred": "Proposed or deferred",
}
SHA40_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")
SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
BUILD_IDENTITY_RE: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}")
UTC_RE: Final[re.Pattern[str]] = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
)
CLOSING_DECLARATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)(?<![\w])"
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
    r"[ \t]+"
    r"(?:(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?"
    r"#(?P<issue>[1-9][0-9]*)\b"
)
MAX_ENVELOPE_BYTES: Final[int] = 64 * 1024
MAX_MEMBER_BYTES: Final[int] = 32 * 1024 * 1024
MAX_CONTRACT_BYTES: Final[int] = 4 * 1024 * 1024
MAX_CLOCK_SKEW: Final[timedelta] = timedelta(minutes=5)
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "access_token",
        "anthropic_api_key",
        "api_key",
        "authorization",
        "credential",
        "credentials",
        "gh_token",
        "github_token",
        "myproxy_url",
        "openai_api_key",
        "password",
        "private_endpoint",
        "private_key",
    }
)
SENSITIVE_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\bmyproxy\b"),
    re.compile(r"(?i)\bprivate[_ -]?endpoint\b"),
)


class GitHubReleaseAdapter(Protocol):
    """Authenticated GitHub source used by the final release decision."""

    def get_issue(self, repository: str, issue_number: int) -> Mapping[str, object]:
        ...

    def list_issue_closing_pull_requests(
        self,
        repository: str,
        issue_number: int,
    ) -> Sequence[Mapping[str, object]]:
        ...

    def get_pull_request(
        self,
        repository: str,
        pull_number: int,
    ) -> Mapping[str, object]:
        ...

    def get_branch(self, repository: str, branch: str) -> Mapping[str, object]:
        ...

    def compare_commits(
        self,
        repository: str,
        base: str,
        head: str,
    ) -> Mapping[str, object]:
        ...

    def get_workflow_run(
        self,
        repository: str,
        run_id: int,
    ) -> Mapping[str, object]:
        ...

    def get_artifact(
        self,
        repository: str,
        artifact_id: int,
    ) -> Mapping[str, object]:
        ...


class ReplayStore(Protocol):
    """Atomic replay authority for accepted child artifact sets."""

    def consume_many(self, replay_keys: Sequence[str]) -> bool:
        ...


@dataclass(frozen=True)
class FinalReleaseConfig:
    """Inputs authenticated by the release workflow before report generation."""

    mode: str
    artifact_root: Path
    artifact_envelopes: Mapping[str, Path]
    expected_repository_sha: str
    expected_workflow_sha: str
    expected_build_identity: str
    workflow_run_id: int
    run_attempt: int
    github_adapter: GitHubReleaseAdapter
    replay_store: ReplayStore
    status_path: Path = DEFAULT_STATUS_PATH
    journey_manifest_path: Path = DEFAULT_JOURNEY_MANIFEST_PATH
    runbook_path: Path | None = DEFAULT_RUNBOOK_PATH
    max_artifact_age_seconds: int | None = None


class InMemoryReplayStore:
    """Deterministic replay store for unit tests and embedded callers."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def consume_many(self, replay_keys: Sequence[str]) -> bool:
        candidate = set(replay_keys)
        if len(candidate) != len(replay_keys) or candidate.intersection(self._keys):
            return False
        self._keys.update(candidate)
        return True


class JsonReplayStore:
    """Small atomic JSON replay ledger for the CLI."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def consume_many(self, replay_keys: Sequence[str]) -> bool:
        candidate = set(replay_keys)
        if len(candidate) != len(replay_keys):
            return False
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        if self.path.is_symlink() or lock_path.is_symlink():
            return False
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow or fcntl is None:
            return False
        try:
            lock_descriptor = os.open(
                lock_path,
                os.O_RDWR
                | os.O_CREAT
                | nofollow
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except OSError:
            return False
        try:
            if not stat.S_ISREG(os.fstat(lock_descriptor).st_mode):
                return False
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            return self._consume_locked(candidate)
        except OSError:
            return False
        finally:
            os.close(lock_descriptor)

    def _consume_locked(self, candidate: set[str]) -> bool:
        existing: set[str] = set()
        if self.path.exists():
            try:
                state = self.path.stat(follow_symlinks=False)
                if not stat.S_ISREG(state.st_mode):
                    return False
                payload = _load_json_object(self.path.read_bytes())
                keys = payload.get("replay_keys")
                if not isinstance(keys, list) or not all(
                    isinstance(item, str) and SHA256_RE.fullmatch(item)
                    for item in keys
                ):
                    return False
                existing = set(keys)
            except (OSError, ValueError):
                return False
        if candidate.intersection(existing):
            return False
        output = _canonical_json_bytes(
            {
                "schema_version": 1,
                "replay_keys": sorted(existing | candidate),
            }
        )
        try:
            _atomic_write_bytes(self.path, output, mode=0o600)
        except OSError:
            return False
        return True


class StdlibGitHubReleaseAdapter:
    """Minimal GitHub REST/GraphQL adapter with no caller-supplied state rows."""

    def __init__(
        self,
        token: str,
        *,
        api_base_url: str = "https://api.github.com",
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("a GitHub token is required")
        self._token = token.strip()
        self._api_base_url = api_base_url.rstrip("/")

    def get_issue(self, repository: str, issue_number: int) -> Mapping[str, object]:
        return self._rest_json(f"/repos/{repository}/issues/{issue_number}")

    def list_issue_closing_pull_requests(
        self,
        repository: str,
        issue_number: int,
    ) -> Sequence[Mapping[str, object]]:
        owner, name = _split_repository(repository)
        query = """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            issue(number: $number) {
              closedByPullRequestsReferences(first: 100) {
                nodes {
                  number
                  state
                  merged
                  mergedAt
                  baseRefName
                  mergeCommit { oid }
                  repository { nameWithOwner }
                }
                pageInfo { hasNextPage }
              }
            }
          }
        }
        """
        payload = self._graphql_json(
            query,
            {"owner": owner, "name": name, "number": issue_number},
        )
        repository_row = _mapping(_mapping(payload.get("data")).get("repository"))
        issue = _mapping(repository_row.get("issue"))
        references = _mapping(issue.get("closedByPullRequestsReferences"))
        page_info = _mapping(references.get("pageInfo"))
        if page_info.get("hasNextPage") is True:
            raise RuntimeError("closing pull request set exceeds the bounded query")
        nodes = references.get("nodes")
        if not isinstance(nodes, list):
            raise RuntimeError("closing pull request nodes are missing")
        results: list[dict[str, object]] = []
        for item in nodes:
            row = _mapping(item)
            results.append(
                {
                    "number": row.get("number"),
                    "state": str(row.get("state", "")).lower(),
                    "merged": row.get("merged"),
                    "merged_at": row.get("mergedAt"),
                    "base_ref": row.get("baseRefName"),
                    "merge_commit_sha": _mapping(row.get("mergeCommit")).get("oid"),
                    "repository": _mapping(row.get("repository")).get(
                        "nameWithOwner"
                    ),
                }
            )
        return results

    def get_branch(self, repository: str, branch: str) -> Mapping[str, object]:
        return self._rest_json(f"/repos/{repository}/branches/{branch}")

    def compare_commits(
        self,
        repository: str,
        base: str,
        head: str,
    ) -> Mapping[str, object]:
        payload = self._rest_json(
            f"/repos/{repository}/compare/{base}...{head}"
        )
        return {
            "status": payload.get("status"),
            "merge_base_sha": _mapping(payload.get("merge_base_commit")).get(
                "sha"
            ),
        }

    def get_pull_request(
        self,
        repository: str,
        pull_number: int,
    ) -> Mapping[str, object]:
        payload = self._rest_json(f"/repos/{repository}/pulls/{pull_number}")
        base = _mapping(payload.get("base"))
        head = _mapping(payload.get("head"))
        return {
            "number": payload.get("number"),
            "state": str(payload.get("state", "")).lower(),
            "draft": payload.get("draft"),
            "body": payload.get("body"),
            "merged": payload.get("merged"),
            "merged_at": payload.get("merged_at"),
            "base_ref": base.get("ref"),
            "base_sha": base.get("sha"),
            "merge_commit_sha": payload.get("merge_commit_sha"),
            "repository": _mapping(base.get("repo")).get("full_name"),
            "head_repository": _mapping(head.get("repo")).get("full_name"),
            "head_sha": head.get("sha"),
        }

    def get_workflow_run(
        self,
        repository: str,
        run_id: int,
    ) -> Mapping[str, object]:
        return self._rest_json(f"/repos/{repository}/actions/runs/{run_id}")

    def get_artifact(
        self,
        repository: str,
        artifact_id: int,
    ) -> Mapping[str, object]:
        return self._rest_json(
            f"/repos/{repository}/actions/artifacts/{artifact_id}"
        )

    def _rest_json(self, path: str) -> Mapping[str, object]:
        value = self._request_json(
            f"{self._api_base_url}{path}",
            method="GET",
            payload=None,
        )
        if not isinstance(value, Mapping):
            raise RuntimeError("GitHub REST response must be an object")
        return dict(value)

    def _graphql_json(
        self,
        query: str,
        variables: Mapping[str, object],
    ) -> Mapping[str, object]:
        value = self._request_json(
            f"{self._api_base_url}/graphql",
            method="POST",
            payload={"query": query, "variables": dict(variables)},
        )
        if not isinstance(value, Mapping) or value.get("errors"):
            raise RuntimeError("GitHub GraphQL response is invalid")
        return dict(value)

    def _request_json(
        self,
        url: str,
        *,
        method: str,
        payload: Mapping[str, object] | None,
    ) -> object:
        body = _canonical_json_bytes(payload) if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "voiStarcraft2-final-release",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response_body = response.read(MAX_CONTRACT_BYTES + 1)
        except (OSError, urllib.error.HTTPError) as exc:
            raise RuntimeError("GitHub source request failed") from exc
        if len(response_body) > MAX_CONTRACT_BYTES:
            raise RuntimeError("GitHub source response exceeds the size limit")
        return json.loads(
            response_body,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )


def build_final_release_report(
    config: FinalReleaseConfig,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Build a fail-closed final release report from authenticated sources."""

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current_time = current_time.astimezone(timezone.utc)
    blockers: list[dict[str, object]] = []
    status = _load_release_status(config.status_path, blockers)
    manifest = _load_journey_manifest(config.journey_manifest_path, blockers)
    if status.get("journey_suite_id") != manifest.get("suite_id"):
        blockers.append({"code": "journey_suite_identity_mismatch"})
    runbook = _validate_runbook(
        config.runbook_path,
        status=status,
        manifest=manifest,
        blockers=blockers,
    )
    _validate_config(config, status=status, blockers=blockers)

    workflow = _verify_workflow_source(config, status=status, blockers=blockers)
    artifacts, replay_keys = _verify_child_artifacts(
        config,
        status=status,
        now=current_time,
        blockers=blockers,
    )
    dependencies = _verify_release_mode_authority(
        config,
        status=status,
        workflow=workflow,
        blockers=blockers,
    )
    if not blockers:
        try:
            replay_consumed = config.replay_store.consume_many(replay_keys)
        except Exception:
            replay_consumed = False
        if not replay_consumed:
            blockers.append({"code": "artifact_replay_rejected"})

    mode = config.mode if config.mode in RELEASE_MODES else "invalid"
    ok = not blockers
    report: dict[str, object] = {
        "schema_version": 1,
        "mode": mode,
        "status": mode if ok else "blocked",
        "ok": ok,
        "repository": status.get("repository"),
        "release_candidate": {
            "repository_sha": config.expected_repository_sha,
            "build_identity": config.expected_build_identity,
            "workflow_run_id": config.workflow_run_id,
            "run_attempt": config.run_attempt,
        },
        "workflow": workflow,
        "dependencies": dependencies,
        "artifacts": artifacts,
        "structured_status": {
            "schema_version": status.get("schema_version"),
            "categories": {
                category: len(_string_list(_mapping(status.get("status")).get(category)))
                for category in STATUS_CATEGORIES
            },
        },
        "runbook": runbook,
        "blockers": blockers,
        "manual_live_qa_remaining": True,
        "live_qualified": False,
    }
    if _private_configuration_findings(report):
        report = {
            "schema_version": 1,
            "mode": mode,
            "status": "blocked",
            "ok": False,
            "repository": status.get("repository"),
            "release_candidate": {
                "repository_sha": config.expected_repository_sha,
                "build_identity": config.expected_build_identity,
                "workflow_run_id": config.workflow_run_id,
                "run_attempt": config.run_attempt,
            },
            "workflow": {},
            "dependencies": [],
            "artifacts": [],
            "structured_status": {},
            "runbook": {},
            "blockers": [{"code": "private_configuration_projection_detected"}],
            "manual_live_qa_remaining": True,
            "live_qualified": False,
        }
    return report


def render_final_release_markdown(report: Mapping[str, object]) -> str:
    """Render the allowlisted final readiness report."""

    candidate = _mapping(report.get("release_candidate"))
    lines = [
        "# MicroMachine Final Release Readiness",
        "",
        f"- Mode: `{report.get('mode', 'invalid')}`",
        f"- Status: `{report.get('status', 'blocked')}`",
        f"- Repository SHA: `{candidate.get('repository_sha', 'invalid')}`",
        f"- Build identity: `{candidate.get('build_identity', 'invalid')}`",
        f"- Workflow run: `{candidate.get('workflow_run_id', 'invalid')}`",
        f"- Run attempt: `{candidate.get('run_attempt', 'invalid')}`",
        "- Manual live QA remaining: `true`",
        "- Live qualified: `false`",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers")
    if isinstance(blockers, list) and blockers:
        for blocker in blockers:
            row = _mapping(blocker)
            details = ", ".join(
                f"{key}={value}"
                for key, value in row.items()
                if key != "code"
            )
            suffix = f" ({details})" if details else ""
            lines.append(f"- `{row.get('code', 'blocked')}`{suffix}")
    else:
        lines.append("- None. Automated gates passed; manual live QA remains.")
    lines.extend(["", "## Child Artifacts", ""])
    for item in _mapping_list(report.get("artifacts")):
        lines.append(
            "- "
            f"`{item.get('producer', 'unknown')}`: "
            f"status=`{item.get('status', 'blocked')}`, "
            f"artifact_id=`{item.get('artifact_id', 'invalid')}`, "
            f"member_sha256=`{item.get('member_sha256', 'invalid')}`"
        )
    lines.extend(["", "## Dependency Authority", ""])
    dependencies = _mapping_list(report.get("dependencies"))
    if not dependencies:
        lines.append("- No dependency rows were accepted.")
    for item in dependencies:
        pull_numbers = item.get("closing_pull_numbers")
        lines.append(
            f"- Issue `#{item.get('issue', 'invalid')}`: "
            f"state=`{item.get('state', 'invalid')}`, "
            f"closing_pulls=`{pull_numbers if isinstance(pull_numbers, list) else []}`"
        )
    lines.extend(
        [
            "",
            "## Manual Boundary",
            "",
            "This report never claims live qualification. Actual StarCraft II "
            "visual movement, engagement, HUD consistency, tactical voice audio, "
            "and operator feel remain mandatory manual observations.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_final_release_outputs(
    report: Mapping[str, object],
    *,
    output_json: Path | None,
    output_markdown: Path | None,
) -> None:
    """Write deterministic JSON and Markdown outputs without following symlinks."""

    if output_json is not None:
        _atomic_write_bytes(
            output_json,
            (
                json.dumps(
                    report,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8"),
            mode=0o644,
        )
    if output_markdown is not None:
        _atomic_write_bytes(
            output_markdown,
            render_final_release_markdown(report).encode("utf-8"),
            mode=0o644,
        )


def load_release_status(path: Path | str = DEFAULT_STATUS_PATH) -> dict[str, object]:
    """Load and validate the checked-in structured release status."""

    blockers: list[dict[str, object]] = []
    status = _load_release_status(Path(path), blockers)
    if blockers:
        raise ValueError(
            "invalid release status: "
            + ", ".join(str(item.get("code")) for item in blockers)
        )
    return status


def render_structured_status_markdown(status: Mapping[str, object]) -> str:
    """Render the generated structured status block used by the runbook."""

    lines = [
        "<!-- PRE_LIVE_RELEASE_STATUS:START -->",
        "## Structured Release Status",
        "",
    ]
    status_map = _mapping(status.get("status"))
    for category in STATUS_CATEGORIES:
        lines.append(f"### {STATUS_LABELS[category]}")
        lines.append("")
        for item in _string_list(status_map.get(category)):
            lines.append(f"- {item}")
        lines.append("")
    lines.extend(
        [
            "- Manual live QA remaining: `true`",
            "- Live qualified: `false`",
            "<!-- PRE_LIVE_RELEASE_STATUS:END -->",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def check_structured_status_markdown(
    status: Mapping[str, object],
    document: str,
) -> bool:
    """Return whether a document contains the exact generated status block."""

    expected = render_structured_status_markdown(status).rstrip()
    start = "<!-- PRE_LIVE_RELEASE_STATUS:START -->"
    end = "<!-- PRE_LIVE_RELEASE_STATUS:END -->"
    start_index = document.find(start)
    end_index = document.find(end)
    if start_index < 0 or end_index < start_index:
        return False
    end_index += len(end)
    return document[start_index:end_index].rstrip() == expected


def render_final_live_qa_runbook(
    status: Mapping[str, object],
    journey_manifest: Mapping[str, object],
) -> str:
    """Render the exact 14-journey final manual live-QA runbook."""

    journeys = _mapping_list(journey_manifest.get("journeys"))
    lines = [
        "# MicroMachine Final Live-QA Runbook",
        "",
        "This runbook begins only after `ready_for_live_qa` passes on the exact "
        "`main` SHA. It does not authorize human multiplayer, ladder, or "
        "Battle.net qualification.",
        "",
        render_structured_status_markdown(status).rstrip(),
        "",
        "## Clean Build",
        "",
        "1. Confirm `git rev-parse HEAD` equals the SHA in "
        "`ready_for_live_qa.json`.",
        "2. Confirm `git status --porcelain=v1 --untracked-files=all` is empty.",
        "3. Run `integrations/micromachine/scripts/build_macos_local.sh`.",
        "4. Stop immediately if the build, CTest, embedded identity, binary "
        "digest, or source attestation differs from the readiness artifact.",
        "",
        "## Runtime And Surfaces",
        "",
        "1. Start `python3 -m starcraft_commander.web_gui --dry-run` and open "
        "the localhost cockpit.",
        "2. Use the cockpit runtime-start action to launch the patched "
        "MicroMachine build and StarCraft II.",
        "3. Keep browser operation cards, in-game HUD, tactical captions, and "
        "voice readback visible or audible.",
        "4. Capture the exact operation identity, game frame, screenshots, "
        "captions, audio notes, and runtime artifacts for every journey.",
        "",
        "## Fourteen Journeys",
        "",
    ]
    for index, journey in enumerate(journeys, start=1):
        journey_id = journey.get("id")
        title = journey.get("title")
        ordered_inputs = journey.get("ordered_inputs")
        expected_events = journey.get("expected_raw_event_types")
        stop_condition = journey.get("stop_condition")
        timeout_frames = journey.get("timeout_frames")
        lines.extend(
            [
                f"### {index:02d}. `{journey_id}` - {title}",
                "",
                "- Input contract: "
                f"`{_inline_json(ordered_inputs)}`",
                "- Expected observation events: "
                f"`{_inline_json(expected_events)}`",
                f"- Stop condition: `{_inline_json(stop_condition)}`",
                f"- Timeout frames: `{timeout_frames}`",
                "- Artifact capture: command input, authoritative operation "
                "projection, matching submission/effect evidence, browser/HUD/"
                "voice identity where applicable, and pass/fail notes.",
                "- Manual stop: stop on missing submission, mismatched generation "
                "or frame, duplicate ownership, unsafe launch, false success "
                "wording, stale replay, visual inconsistency, or incorrect audio.",
                "",
            ]
        )
    lines.extend(
        [
            "## Actual SC2 Visual And Audio Gate",
            "",
            "- Verify movement and engagement are visibly caused by the matching "
            "operation submission rather than unrelated autonomous behavior.",
            "- Verify the browser, HUD, caption, and voice callout use the same "
            "`update_id + operation_id + generation + stage + game_frame`.",
            "- Verify tactical audio is understandable, correctly prioritized, "
            "deduplicated, interruptible, and honest about pending versus observed "
            "effects.",
            "- Any failed or ambiguous observation keeps "
            "`manual_live_qa_remaining=true` and must not be reported as live "
            "qualified.",
            "",
            "## Report Commands",
            "",
            "Generate the post-merge readiness report with:",
            "",
            "```bash",
            "python3 -m starcraft_commander.micromachine_final_release report \\",
            "  --mode ready_for_live_qa \\",
            "  --artifact-root <downloaded-child-artifacts> \\",
            "  --artifact <producer>=<envelope.json> \\",
            "  --repository-sha <exact-main-sha> \\",
            "  --workflow-sha <trusted-workflow-sha> \\",
            "  --build-identity <sha256:build-identity> \\",
            "  --workflow-run-id <run-id> \\",
            "  --run-attempt <attempt> \\",
            "  --replay-ledger <private-ledger.json> \\",
            "  --output-json ready_for_live_qa.json \\",
            "  --output-markdown ready_for_live_qa.md",
            "```",
            "",
            "Validate this generated runbook and structured status with:",
            "",
            "```bash",
            "python3 -m starcraft_commander.micromachine_final_release check-status",
            "```",
            "",
            "## Deferred Scope",
            "",
            "Human multiplayer, ladder, Battle.net qualification, and competitive "
            "balance signoff remain deferred. They are not implied by either "
            "automated readiness mode.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def validate_final_live_qa_runbook(
    *,
    status_path: Path | str = DEFAULT_STATUS_PATH,
    journey_manifest_path: Path | str = DEFAULT_JOURNEY_MANIFEST_PATH,
    runbook_path: Path | str | None = DEFAULT_RUNBOOK_PATH,
) -> dict[str, object]:
    """Validate exact structured status and one-to-one 14-journey coverage."""

    blockers: list[dict[str, object]] = []
    status = _load_release_status(Path(status_path), blockers)
    manifest = _load_journey_manifest(Path(journey_manifest_path), blockers)
    result = _validate_runbook(
        Path(runbook_path) if runbook_path is not None else None,
        status=status,
        manifest=manifest,
        blockers=blockers,
    )
    return {
        "schema_version": 1,
        "ok": not blockers,
        "status": "passed" if not blockers else "blocked",
        "journey_count": result.get("journey_count", 0),
        "source": result.get("source"),
        "blockers": blockers,
    }


def _validate_config(
    config: FinalReleaseConfig,
    *,
    status: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> None:
    if config.mode not in RELEASE_MODES:
        blockers.append({"code": "invalid_release_mode"})
    if SHA40_RE.fullmatch(config.expected_repository_sha) is None:
        blockers.append({"code": "invalid_repository_sha"})
    if SHA40_RE.fullmatch(config.expected_workflow_sha) is None:
        blockers.append({"code": "invalid_workflow_sha"})
    if BUILD_IDENTITY_RE.fullmatch(config.expected_build_identity) is None:
        blockers.append({"code": "invalid_build_identity"})
    if type(config.workflow_run_id) is not int or config.workflow_run_id <= 0:
        blockers.append({"code": "invalid_workflow_run_id"})
    if type(config.run_attempt) is not int or config.run_attempt <= 0:
        blockers.append({"code": "invalid_run_attempt"})
    max_age = (
        config.max_artifact_age_seconds
        if config.max_artifact_age_seconds is not None
        else status.get("max_artifact_age_seconds")
    )
    if type(max_age) is not int or max_age <= 0:
        blockers.append({"code": "invalid_max_artifact_age"})
    root = config.artifact_root
    try:
        root_state = root.lstat()
    except OSError:
        blockers.append({"code": "artifact_root_missing"})
    else:
        if root.is_symlink() or not stat.S_ISDIR(root_state.st_mode):
            blockers.append({"code": "artifact_root_invalid"})


def _verify_workflow_source(
    config: FinalReleaseConfig,
    *,
    status: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    repository = str(status.get("repository", ""))
    try:
        workflow = config.github_adapter.get_workflow_run(
            repository,
            config.workflow_run_id,
        )
    except Exception:
        blockers.append({"code": "github_workflow_lookup_failed"})
        return {}
    if workflow.get("id") != config.workflow_run_id:
        blockers.append({"code": "github_workflow_run_id_mismatch"})
    if workflow.get("run_attempt") != config.run_attempt:
        blockers.append({"code": "github_workflow_attempt_mismatch"})
    if workflow.get("head_sha") != config.expected_workflow_sha:
        blockers.append({"code": "github_workflow_sha_mismatch"})
    expected_event = (
        "pull_request_target" if config.mode == READY_TO_MERGE else "push"
    )
    if workflow.get("event") != expected_event:
        blockers.append({"code": "github_workflow_event_mismatch"})
    workflow_id = workflow.get("workflow_id")
    if type(workflow_id) is not int or workflow_id <= 0:
        blockers.append({"code": "github_workflow_identity_missing"})
    if workflow.get("path") != EXPECTED_WORKFLOW_PATH:
        blockers.append({"code": "github_workflow_path_mismatch"})
    head_repository = _mapping(workflow.get("head_repository"))
    full_name = head_repository.get("full_name")
    if full_name != repository:
        blockers.append({"code": "github_workflow_repository_mismatch"})
    if workflow.get("head_branch") != status.get("main_branch"):
        blockers.append({"code": "github_workflow_main_branch_mismatch"})
    pull_request_numbers: list[int] = []
    if config.mode == READY_TO_MERGE:
        pull_requests = workflow.get("pull_requests")
        if isinstance(pull_requests, list):
            pull_request_numbers = [
                number
                for item in pull_requests
                if isinstance(item, Mapping)
                and type(number := item.get("number")) is int
                and number > 0
            ]
        if len(pull_request_numbers) != 1 or len(pull_request_numbers) != len(
            pull_requests if isinstance(pull_requests, list) else []
        ):
            blockers.append({"code": "github_workflow_pull_request_mismatch"})
    return {
        "id": workflow.get("id"),
        "workflow_id": workflow_id,
        "path": workflow.get("path"),
        "run_attempt": workflow.get("run_attempt"),
        "head_sha": workflow.get("head_sha"),
        "event": workflow.get("event"),
        "head_branch": workflow.get("head_branch"),
        "pull_request_numbers": pull_request_numbers,
    }


def _verify_child_artifacts(
    config: FinalReleaseConfig,
    *,
    status: Mapping[str, object],
    now: datetime,
    blockers: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[str]]:
    specs = _mapping_list(status.get("required_artifacts"))
    expected_producers = {str(spec.get("producer")) for spec in specs}
    provided_producers = set(config.artifact_envelopes)
    for missing in sorted(expected_producers - provided_producers):
        blockers.append({"code": "missing_child_artifact", "producer": missing})
    for unexpected in sorted(provided_producers - expected_producers):
        blockers.append(
            {"code": "unexpected_child_artifact", "producer": unexpected}
        )

    projections: list[dict[str, object]] = []
    replay_keys: list[str] = []
    artifact_ids: set[int] = set()
    max_age_value = (
        config.max_artifact_age_seconds
        if config.max_artifact_age_seconds is not None
        else status.get("max_artifact_age_seconds")
    )
    max_age_seconds = max_age_value if type(max_age_value) is int else 0
    for spec in specs:
        producer = str(spec.get("producer", ""))
        envelope_path = config.artifact_envelopes.get(producer)
        if envelope_path is None:
            continue
        result = _verify_child_artifact(
            config,
            spec=spec,
            envelope_path=envelope_path,
            now=now,
            max_age_seconds=max_age_seconds,
            blockers=blockers,
        )
        projections.append(result["projection"])
        replay_key = result.get("replay_key")
        artifact_id = result.get("artifact_id")
        if isinstance(artifact_id, int):
            if artifact_id in artifact_ids:
                blockers.append(
                    {"code": "duplicate_artifact_id", "producer": producer}
                )
            artifact_ids.add(artifact_id)
        if isinstance(replay_key, str):
            replay_keys.append(replay_key)
    return projections, replay_keys


def _verify_child_artifact(
    config: FinalReleaseConfig,
    *,
    spec: Mapping[str, object],
    envelope_path: Path,
    now: datetime,
    max_age_seconds: int,
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    producer = str(spec.get("producer", ""))
    local_blockers: list[dict[str, object]] = []
    envelope_bytes = _read_contained_regular_file(
        config.artifact_root,
        envelope_path,
        max_bytes=MAX_ENVELOPE_BYTES,
        code_prefix="envelope",
        producer=producer,
        blockers=local_blockers,
    )
    envelope: dict[str, object] = {}
    if envelope_bytes is not None:
        try:
            envelope = _load_json_object(envelope_bytes)
        except ValueError:
            local_blockers.append(
                {"code": "invalid_envelope_json", "producer": producer}
            )
        else:
            if envelope_bytes != _canonical_json_bytes(envelope):
                local_blockers.append(
                    {"code": "noncanonical_envelope", "producer": producer}
                )
            if set(envelope) != ENVELOPE_FIELDS:
                local_blockers.append(
                    {"code": "invalid_envelope_fields", "producer": producer}
                )
    if envelope:
        _validate_envelope_fields(
            envelope,
            config=config,
            spec=spec,
            now=now,
            max_age_seconds=max_age_seconds,
            blockers=local_blockers,
        )

    member_bytes: bytes | None = None
    member_name = envelope.get("member")
    if isinstance(member_name, str) and _safe_member_name(member_name):
        member_bytes = _read_contained_regular_file(
            config.artifact_root,
            config.artifact_root / PurePosixPath(member_name),
            max_bytes=MAX_MEMBER_BYTES,
            code_prefix="member",
            producer=producer,
            blockers=local_blockers,
        )
    elif envelope:
        local_blockers.append({"code": "invalid_member_path", "producer": producer})

    member_digest: str | None = None
    payload: dict[str, object] = {}
    if member_bytes is not None:
        member_digest = hashlib.sha256(member_bytes).hexdigest()
        if member_digest != envelope.get("sha256"):
            local_blockers.append(
                {"code": "member_digest_mismatch", "producer": producer}
            )
        try:
            payload = _load_json_object(member_bytes)
        except ValueError:
            local_blockers.append(
                {"code": "invalid_member_json", "producer": producer}
            )
        else:
            if payload.get("ok") is not True or payload.get("status") not in {
                "passed",
                "success",
                "qualified",
            }:
                local_blockers.append(
                    {"code": "child_artifact_failed", "producer": producer}
                )
            if payload.get("live_qualified") is True:
                local_blockers.append(
                    {
                        "code": "live_qualified_without_manual_evidence",
                        "producer": producer,
                    }
                )
            if _private_configuration_findings(payload):
                local_blockers.append(
                    {
                        "code": "private_configuration_detected",
                        "producer": producer,
                    }
                )

    artifact_id = envelope.get("artifact_id")
    artifact_metadata: Mapping[str, object] = {}
    if type(artifact_id) is int and artifact_id > 0:
        try:
            artifact_metadata = config.github_adapter.get_artifact(
                str(spec.get("repository") or ""),
                artifact_id,
            )
        except Exception:
            local_blockers.append(
                {"code": "github_artifact_lookup_failed", "producer": producer}
            )
        else:
            _validate_github_artifact_metadata(
                artifact_metadata,
                config=config,
                spec=spec,
                artifact_id=artifact_id,
                producer=producer,
                blockers=local_blockers,
            )

    archive_digest = artifact_metadata.get("digest")
    if envelope and archive_digest != envelope.get("archive_sha256"):
        local_blockers.append(
            {"code": "github_artifact_digest_mismatch", "producer": producer}
        )
    projection = {
        "producer": producer,
        "status": "passed" if not local_blockers else "blocked",
        "artifact_id": artifact_id if type(artifact_id) is int else None,
        "workflow_run_id": envelope.get("workflow_run_id"),
        "run_attempt": envelope.get("run_attempt"),
        "generated_at": envelope.get("generated_at"),
        "member_sha256": member_digest,
        "archive_sha256": archive_digest,
    }
    blockers.extend(local_blockers)
    replay_key: str | None = None
    if not local_blockers:
        replay_key = hashlib.sha256(
            _canonical_json_bytes(
                {
                    "mode": config.mode,
                    "producer": producer,
                    "repository_sha": envelope["repository_sha"],
                    "build_identity": envelope["build_identity"],
                    "workflow_run_id": envelope["workflow_run_id"],
                    "run_attempt": envelope["run_attempt"],
                    "artifact_id": envelope["artifact_id"],
                    "member_sha256": envelope["sha256"],
                    "archive_sha256": envelope["archive_sha256"],
                }
            )
        ).hexdigest()
    return {
        "projection": projection,
        "replay_key": replay_key,
        "artifact_id": artifact_id,
    }


def _validate_envelope_fields(
    envelope: Mapping[str, object],
    *,
    config: FinalReleaseConfig,
    spec: Mapping[str, object],
    now: datetime,
    max_age_seconds: int,
    blockers: list[dict[str, object]],
) -> None:
    producer = str(spec.get("producer", ""))
    checks = (
        ("schema_version", envelope.get("schema_version"), 1),
        ("producer", envelope.get("producer"), producer),
        (
            "repository_sha",
            envelope.get("repository_sha"),
            config.expected_repository_sha,
        ),
        (
            "build_identity",
            envelope.get("build_identity"),
            config.expected_build_identity,
        ),
        (
            "workflow_run_id",
            envelope.get("workflow_run_id"),
            config.workflow_run_id,
        ),
        ("run_attempt", envelope.get("run_attempt"), config.run_attempt),
        ("member", envelope.get("member"), spec.get("member")),
    )
    for field, actual, expected in checks:
        if type(actual) is not type(expected) or actual != expected:
            blockers.append(
                {
                    "code": "envelope_binding_mismatch",
                    "producer": producer,
                    "field": field,
                }
            )
    digest = envelope.get("sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        blockers.append(
            {"code": "invalid_member_digest", "producer": producer}
        )
    artifact_id = envelope.get("artifact_id")
    if type(artifact_id) is not int or artifact_id <= 0:
        blockers.append({"code": "invalid_artifact_id", "producer": producer})
    archive_sha256 = envelope.get("archive_sha256")
    if (
        not isinstance(archive_sha256, str)
        or not archive_sha256.startswith("sha256:")
        or SHA256_RE.fullmatch(archive_sha256.removeprefix("sha256:")) is None
    ):
        blockers.append(
            {"code": "invalid_archive_digest", "producer": producer}
        )
    generated_at = _parse_utc(envelope.get("generated_at"))
    if generated_at is None:
        blockers.append({"code": "invalid_generated_at", "producer": producer})
    else:
        age = now - generated_at
        if generated_at - now > MAX_CLOCK_SKEW:
            blockers.append({"code": "future_artifact", "producer": producer})
        elif age.total_seconds() > max_age_seconds:
            blockers.append({"code": "stale_artifact", "producer": producer})


def _validate_github_artifact_metadata(
    metadata: Mapping[str, object],
    *,
    config: FinalReleaseConfig,
    spec: Mapping[str, object],
    artifact_id: int,
    producer: str,
    blockers: list[dict[str, object]],
) -> None:
    if metadata.get("id") != artifact_id:
        blockers.append(
            {"code": "github_artifact_id_mismatch", "producer": producer}
        )
    if metadata.get("name") != spec.get("artifact_name"):
        blockers.append(
            {"code": "github_artifact_name_mismatch", "producer": producer}
        )
    if metadata.get("expired") is True:
        blockers.append({"code": "github_artifact_expired", "producer": producer})
    workflow_run = _mapping(metadata.get("workflow_run"))
    if workflow_run.get("id") != config.workflow_run_id:
        blockers.append(
            {"code": "github_artifact_run_mismatch", "producer": producer}
        )
    if workflow_run.get("head_sha") != config.expected_workflow_sha:
        blockers.append(
            {"code": "github_artifact_sha_mismatch", "producer": producer}
        )
    archive_digest = metadata.get("digest")
    if (
        not isinstance(archive_digest, str)
        or not archive_digest.startswith("sha256:")
        or SHA256_RE.fullmatch(archive_digest.removeprefix("sha256:")) is None
    ):
        blockers.append(
            {"code": "github_artifact_digest_missing", "producer": producer}
        )


def _verify_release_mode_authority(
    config: FinalReleaseConfig,
    *,
    status: Mapping[str, object],
    workflow: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> list[dict[str, object]]:
    if config.mode == READY_TO_MERGE:
        return _verify_ready_to_merge(
            config,
            status=status,
            workflow=workflow,
            blockers=blockers,
        )
    if config.mode == READY_FOR_LIVE_QA:
        return _verify_ready_for_live_qa(
            config,
            status=status,
            blockers=blockers,
        )
    return []


def _verify_ready_to_merge(
    config: FinalReleaseConfig,
    *,
    status: Mapping[str, object],
    workflow: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> list[dict[str, object]]:
    repository = str(status.get("repository", ""))
    release_completion_issues = _release_completion_issues(status)
    results: list[dict[str, object]] = []
    for dependency in _mapping_list(status.get("ready_to_merge_dependencies")):
        issue_number = dependency.get("issue")
        if issue_number in release_completion_issues:
            blockers.append({"code": "ready_to_merge_self_closure_cycle"})
            continue
        if type(issue_number) is not int or issue_number <= 0:
            blockers.append({"code": "invalid_dependency_issue"})
            continue
        try:
            issue = config.github_adapter.get_issue(repository, issue_number)
            pulls = config.github_adapter.list_issue_closing_pull_requests(
                repository,
                issue_number,
            )
        except Exception:
            blockers.append(
                {"code": "github_dependency_lookup_failed", "issue": issue_number}
            )
            continue
        state = str(issue.get("state", "")).lower()
        if issue.get("number") != issue_number or state != "closed":
            blockers.append(
                {"code": "dependency_issue_not_closed", "issue": issue_number}
            )
        merged_pulls = _accepted_merged_pulls(
            pulls,
            repository=repository,
            main_branch=str(status.get("main_branch", "")),
        )
        if dependency.get("require_merged_closing_pull") is True and not merged_pulls:
            blockers.append(
                {
                    "code": "dependency_closing_pull_not_merged",
                    "issue": issue_number,
                }
            )
        results.append(
            {
                "issue": issue_number,
                "state": state,
                "closing_pull_numbers": [
                    pull["number"] for pull in merged_pulls
                ],
            }
        )
    pull_request_numbers = workflow.get("pull_request_numbers")
    if (
        not isinstance(pull_request_numbers, list)
        or len(pull_request_numbers) != 1
        or type(pull_request_numbers[0]) is not int
    ):
        blockers.append({"code": "release_pull_workflow_binding_missing"})
        return results
    _verify_release_pull_contract(
        config,
        status=status,
        pull_number=pull_request_numbers[0],
        require_merged=False,
        blockers=blockers,
    )
    return results


def _verify_ready_for_live_qa(
    config: FinalReleaseConfig,
    *,
    status: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> list[dict[str, object]]:
    repository = str(status.get("repository", ""))
    main_branch = str(status.get("main_branch", ""))
    release_closing_issue = _release_closing_issue(status)
    release_completion_issues = _release_completion_issues(status)
    release_pull_number = _release_pull_number(status)
    try:
        branch = config.github_adapter.get_branch(repository, main_branch)
        branch_sha = _mapping(branch.get("commit")).get("sha")
        comparison = config.github_adapter.compare_commits(
            repository,
            config.expected_repository_sha,
            str(branch_sha),
        )
    except Exception:
        blockers.append({"code": "github_main_branch_lookup_failed"})
    else:
        if (
            branch.get("name") != main_branch
            or not isinstance(branch_sha, str)
            or SHA40_RE.fullmatch(branch_sha) is None
            or comparison.get("status") not in {"ahead", "identical"}
            or comparison.get("merge_base_sha")
            != config.expected_repository_sha
        ):
            blockers.append({"code": "release_commit_not_on_main"})
    if (
        release_closing_issue is None
        or len(release_completion_issues) != 4
        or release_closing_issue not in release_completion_issues
        or release_pull_number is None
    ):
        blockers.append({"code": "invalid_release_completion_contract"})
        return []
    _verify_release_pull_contract(
        config,
        status=status,
        pull_number=release_pull_number,
        require_merged=True,
        blockers=blockers,
    )
    results: list[dict[str, object]] = []
    for issue_number in release_completion_issues:
        try:
            issue = config.github_adapter.get_issue(repository, issue_number)
            pulls = config.github_adapter.list_issue_closing_pull_requests(
                repository,
                issue_number,
            )
        except Exception:
            blockers.append(
                {
                    "code": "github_release_closure_lookup_failed",
                    "issue": issue_number,
                }
            )
            continue
        state = str(issue.get("state", "")).lower()
        state_reason = str(issue.get("state_reason", "")).lower()
        if (
            issue.get("number") != issue_number
            or state != "closed"
            or state_reason != "completed"
        ):
            blockers.append(
                {
                    "code": "release_completion_issue_not_completed",
                    "issue": issue_number,
                }
            )
        accepted_pulls = _accepted_merged_pulls(
            pulls,
            repository=repository,
            main_branch=main_branch,
        )
        if issue_number == release_closing_issue:
            exact_pulls = [
                pull
                for pull in accepted_pulls
                if pull.get("number") == release_pull_number
                and pull.get("merge_commit_sha")
                == config.expected_repository_sha
            ]
            if len(exact_pulls) != 1:
                blockers.append(
                    {
                        "code": "release_closing_pull_not_exact_main_merge",
                        "issue": issue_number,
                    }
                )
            closure_kind = "release_pull"
        else:
            exact_pulls = accepted_pulls
            if exact_pulls:
                blockers.append(
                    {
                        "code": "explicit_completion_has_closing_pull",
                        "issue": issue_number,
                    }
                )
            closure_kind = "explicit_completion"
        results.append(
            {
                "issue": issue_number,
                "state": state,
                "state_reason": state_reason,
                "closure_kind": closure_kind,
                "closing_pull_numbers": [
                    pull["number"] for pull in exact_pulls
                ],
            }
        )
    return results


def _verify_release_pull_contract(
    config: FinalReleaseConfig,
    *,
    status: Mapping[str, object],
    pull_number: int,
    require_merged: bool,
    blockers: list[dict[str, object]],
) -> None:
    repository = str(status.get("repository", ""))
    main_branch = str(status.get("main_branch", ""))
    try:
        pull = config.github_adapter.get_pull_request(repository, pull_number)
    except Exception:
        blockers.append({"code": "github_release_pull_lookup_failed"})
        return
    identity_valid = (
        pull_number == _release_pull_number(status)
        and pull.get("number") == pull_number
        and pull.get("base_ref") == main_branch
        and (
            require_merged
            or pull.get("base_sha") == config.expected_workflow_sha
        )
        and pull.get("repository") == repository
        and pull.get("head_repository") == repository
        and (
            require_merged
            or pull.get("head_sha") == config.expected_repository_sha
        )
        and pull.get("draft") is False
    )
    if not identity_valid:
        blockers.append({"code": "release_pull_identity_mismatch"})
    if require_merged:
        if (
            str(pull.get("state", "")).lower() != "closed"
            or pull.get("merged") is not True
            or not isinstance(pull.get("merged_at"), str)
            or pull.get("merge_commit_sha") != config.expected_repository_sha
        ):
            blockers.append({"code": "release_pull_merge_state_mismatch"})
    elif (
        str(pull.get("state", "")).lower() != "open"
        or pull.get("merged") is not False
    ):
        blockers.append({"code": "release_pull_premerge_state_mismatch"})
    body = pull.get("body")
    release_closing_issue = _release_closing_issue(status)
    expected_declarations = (
        [(repository.casefold(), release_closing_issue)]
        if release_closing_issue is not None
        else []
    )
    observed_declarations = (
        sorted(_closing_declarations(body, repository))
        if isinstance(body, str)
        else []
    )
    if observed_declarations != expected_declarations:
        blockers.append(
            {
                "code": "release_pull_closing_declarations_mismatch",
                "expected_issues": [
                    issue_number for _, issue_number in expected_declarations
                ],
                "observed_issues": [
                    issue_number for _, issue_number in observed_declarations
                ],
            }
        )


def _closing_declarations(
    body: str,
    repository: str,
) -> list[tuple[str, int]]:
    visible_body = re.sub(r"(?s)<!--.*?-->", "", body)
    visible_body = re.sub(
        r"(?ms)^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[ \t]*$",
        "",
        visible_body,
    )
    visible_body = re.sub(r"`[^`\n]*`", "", visible_body)
    declarations: list[tuple[str, int]] = []
    for match in CLOSING_DECLARATION_RE.finditer(visible_body):
        declared_repository = match.group("repository") or repository
        declarations.append(
            (
                declared_repository.casefold(),
                int(match.group("issue")),
            )
        )
    return declarations


def _release_pull_number(status: Mapping[str, object]) -> int | None:
    value = status.get("release_pull_number")
    return value if type(value) is int and value > 0 else None


def _release_closing_issue(status: Mapping[str, object]) -> int | None:
    value = status.get("release_closing_issue")
    return value if type(value) is int and value > 0 else None


def _release_completion_issues(status: Mapping[str, object]) -> list[int]:
    value = status.get("release_completion_issues")
    if not isinstance(value, list):
        return []
    return [item for item in value if type(item) is int and item > 0]


def _accepted_merged_pulls(
    pulls: Sequence[Mapping[str, object]],
    *,
    repository: str,
    main_branch: str,
) -> list[dict[str, object]]:
    accepted: list[dict[str, object]] = []
    for pull in pulls:
        number = pull.get("number")
        merge_sha = pull.get("merge_commit_sha")
        if (
            type(number) is int
            and number > 0
            and str(pull.get("state", "")).lower() == "closed"
            and pull.get("merged") is True
            and isinstance(pull.get("merged_at"), str)
            and pull.get("base_ref") == main_branch
            and pull.get("repository") == repository
            and isinstance(merge_sha, str)
            and SHA40_RE.fullmatch(merge_sha)
        ):
            accepted.append(
                {
                    "number": number,
                    "merge_commit_sha": merge_sha,
                }
            )
    return accepted


def _load_release_status(
    path: Path,
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    payload = _load_contract_file(path, "release_status", blockers)
    expected_fields = {
        "schema_version",
        "repository",
        "release_issue",
        "parent_issue",
        "master_issue",
        "release_pull_number",
        "release_closing_issue",
        "release_completion_issues",
        "main_branch",
        "manual_live_qa_remaining",
        "max_artifact_age_seconds",
        "journey_suite_id",
        "ready_to_merge_dependencies",
        "required_artifacts",
        "status",
    }
    if payload and set(payload) != expected_fields:
        blockers.append({"code": "invalid_release_status_fields"})
    if payload.get("schema_version") != 2:
        blockers.append({"code": "unsupported_release_status_schema"})
    if payload.get("repository") != "Marker-Inc-Korea/voiStarcraft2":
        blockers.append({"code": "invalid_release_repository"})
    if payload.get("release_issue") != 142:
        blockers.append({"code": "invalid_release_issue"})
    if payload.get("parent_issue") != 128 or payload.get("master_issue") != 124:
        blockers.append({"code": "invalid_release_issue_hierarchy"})
    if payload.get("release_pull_number") != 168:
        blockers.append({"code": "invalid_release_pull_number"})
    if payload.get("release_closing_issue") != 141:
        blockers.append({"code": "invalid_release_closing_issue"})
    release_completion_issues = payload.get("release_completion_issues")
    if release_completion_issues != [141, 142, 128, 124]:
        blockers.append({"code": "invalid_release_completion_issues"})
    if payload.get("main_branch") != "main":
        blockers.append({"code": "invalid_main_branch"})
    if payload.get("manual_live_qa_remaining") is not True:
        blockers.append({"code": "manual_live_qa_must_remain"})
    if (
        type(payload.get("max_artifact_age_seconds")) is not int
        or int(payload.get("max_artifact_age_seconds", 0)) <= 0
    ):
        blockers.append({"code": "invalid_release_status_freshness"})

    dependencies = _mapping_list(payload.get("ready_to_merge_dependencies"))
    dependency_numbers = [item.get("issue") for item in dependencies]
    if (
        not dependencies
        or any(type(number) is not int or number <= 0 for number in dependency_numbers)
        or len(set(dependency_numbers)) != len(dependency_numbers)
        or any(
            number in _release_completion_issues(payload)
            for number in dependency_numbers
        )
        or any(
            item.get("require_merged_closing_pull") is not True
            for item in dependencies
        )
    ):
        blockers.append({"code": "invalid_ready_to_merge_dependencies"})

    artifact_specs = _mapping_list(payload.get("required_artifacts"))
    producers = [item.get("producer") for item in artifact_specs]
    artifact_names = [item.get("artifact_name") for item in artifact_specs]
    members = [item.get("member") for item in artifact_specs]
    if (
        len(artifact_specs) < 5
        or not all(isinstance(item, str) and item for item in producers)
        or not all(isinstance(item, str) and item for item in artifact_names)
        or not all(isinstance(item, str) and _safe_member_name(item) for item in members)
        or len(set(producers)) != len(producers)
        or len(set(artifact_names)) != len(artifact_names)
        or len(set(members)) != len(members)
    ):
        blockers.append({"code": "invalid_required_artifacts"})
    for item in artifact_specs:
        item["repository"] = payload.get("repository")
    payload["required_artifacts"] = artifact_specs

    status_map = _mapping(payload.get("status"))
    if set(status_map) != set(STATUS_CATEGORIES) or any(
        not _string_list(status_map.get(category)) for category in STATUS_CATEGORIES
    ):
        blockers.append({"code": "invalid_structured_status"})
    return payload


def _load_journey_manifest(
    path: Path,
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    payload = _load_contract_file(path, "journey_manifest", blockers)
    journeys = _mapping_list(payload.get("journeys"))
    ids = [item.get("id") for item in journeys]
    if (
        payload.get("schema_version") != 1
        or payload.get("suite_id") != "micromachine_deterministic_pre_live_v1"
        or len(journeys) != 14
        or len(set(ids)) != 14
        or not all(isinstance(item, str) and item for item in ids)
    ):
        blockers.append({"code": "invalid_fourteen_journey_manifest"})
    for journey in journeys:
        if (
            not isinstance(journey.get("title"), str)
            or not isinstance(journey.get("ordered_inputs"), list)
            or not journey.get("ordered_inputs")
            or not isinstance(journey.get("expected_raw_event_types"), list)
            or not journey.get("expected_raw_event_types")
            or not isinstance(journey.get("stop_condition"), Mapping)
            or type(journey.get("timeout_frames")) is not int
        ):
            blockers.append(
                {
                    "code": "invalid_journey_contract",
                    "journey": journey.get("id"),
                }
            )
    return payload


def _validate_runbook(
    path: Path | None,
    *,
    status: Mapping[str, object],
    manifest: Mapping[str, object],
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    expected = render_final_live_qa_runbook(status, manifest)
    if path is None:
        document = expected
        source = "generated"
    else:
        try:
            path_state = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(path_state.st_mode):
                raise OSError
            document = path.read_text(encoding="utf-8")
        except OSError:
            blockers.append({"code": "runbook_missing_or_unsafe"})
            return {"status": "blocked", "journey_count": 0}
        source = "file"
    if document != expected:
        blockers.append({"code": "runbook_contract_drift"})
    if not check_structured_status_markdown(status, document):
        blockers.append({"code": "structured_status_drift"})
    journeys = _mapping_list(manifest.get("journeys"))
    observed_ids = re.findall(r"^### \d{2}\. `([^`]+)` - ", document, re.MULTILINE)
    expected_ids = [str(item.get("id")) for item in journeys]
    if observed_ids != expected_ids:
        blockers.append({"code": "runbook_journey_coverage_mismatch"})
    return {
        "status": "passed" if document == expected else "blocked",
        "suite_id": manifest.get("suite_id"),
        "journey_count": len(observed_ids),
        "source": source,
    }


def _load_contract_file(
    path: Path,
    label: str,
    blockers: list[dict[str, object]],
) -> dict[str, object]:
    try:
        state = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(state.st_mode):
            raise OSError
        payload = path.read_bytes()
        if len(payload) > MAX_CONTRACT_BYTES:
            raise OSError
        return _load_json_object(payload)
    except (OSError, ValueError):
        blockers.append({"code": f"invalid_{label}"})
        return {}


def _read_contained_regular_file(
    root: Path,
    path: Path,
    *,
    max_bytes: int,
    code_prefix: str,
    producer: str,
    blockers: list[dict[str, object]],
) -> bytes | None:
    root_absolute = Path(os.path.abspath(root))
    candidate = path if path.is_absolute() else root_absolute / path
    candidate_absolute = Path(os.path.abspath(candidate))
    try:
        relative = candidate_absolute.relative_to(root_absolute)
    except ValueError:
        blockers.append(
            {"code": f"{code_prefix}_path_escape", "producer": producer}
        )
        return None
    if not relative.parts:
        blockers.append(
            {"code": f"{code_prefix}_not_regular", "producer": producer}
        )
        return None
    opened_directories: list[int] = []
    file_descriptor = -1
    try:
        root_state = os.lstat(root_absolute)
        if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode):
            raise OSError
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        if not nofollow or not directory_flag:
            raise OSError
        directory_descriptor = os.open(
            root_absolute,
            os.O_RDONLY
            | directory_flag
            | nofollow
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened_directories.append(directory_descriptor)
        for part in relative.parts[:-1]:
            if part in {"", ".", ".."}:
                raise OSError
            state = os.stat(
                part,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(state.st_mode):
                blockers.append(
                    {"code": f"{code_prefix}_symlink", "producer": producer}
                )
                return None
            if not stat.S_ISDIR(state.st_mode):
                raise OSError
            directory_descriptor = os.open(
                part,
                os.O_RDONLY
                | directory_flag
                | nofollow
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_descriptor,
            )
            opened_directories.append(directory_descriptor)
        final_name = relative.parts[-1]
        final_state = os.stat(
            final_name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISLNK(final_state.st_mode):
            blockers.append(
                {"code": f"{code_prefix}_symlink", "producer": producer}
            )
            return None
        if not stat.S_ISREG(final_state.st_mode):
            blockers.append(
                {
                    "code": f"{code_prefix}_not_regular",
                    "producer": producer,
                }
            )
            return None
        file_descriptor = os.open(
            final_name,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_descriptor,
        )
        try:
            before = os.fstat(file_descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
                raise OSError
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(file_descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if remaining == 0 and os.read(file_descriptor, 1):
                raise OSError
            after = os.fstat(file_descriptor)
            def identity(item: os.stat_result) -> tuple[int, int, int, int, int]:
                return (
                    item.st_dev,
                    item.st_ino,
                    item.st_size,
                    item.st_mtime_ns,
                    item.st_ctime_ns,
                )

            if identity(before) != identity(after):
                raise OSError
            return b"".join(chunks)
        finally:
            os.close(file_descriptor)
            file_descriptor = -1
    except OSError:
        blockers.append(
            {"code": f"{code_prefix}_unreadable", "producer": producer}
        )
        return None
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        for descriptor in reversed(opened_directories):
            os.close(descriptor)


def _private_configuration_findings(value: object) -> list[str]:
    findings: list[str] = []

    def visit(item: object, path: str) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                key_text = str(key)
                child_path = f"{path}.{key_text}" if path else key_text
                if key_text.casefold() in SENSITIVE_KEYS:
                    findings.append(child_path)
                visit(child, child_path)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")
        elif isinstance(item, str):
            if any(pattern.search(item) for pattern in SENSITIVE_VALUE_PATTERNS):
                findings.append(path)

    visit(value, "")
    return findings


def _safe_member_name(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.as_posix() == value
    )


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _load_json_object(payload: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON value must be an object")
    return value


def _reject_duplicate_pairs(
    pairs: Sequence[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise ValueError(f"non-finite JSON value: {value}")


def _inline_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).replace("`", "\\`")


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _split_repository(repository: str) -> tuple[str, str]:
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("repository must be owner/name")
    return parts[0], parts[1]


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("refusing to replace a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or validate MicroMachine final release readiness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    report = subparsers.add_parser("report")
    report.add_argument("--mode", choices=sorted(RELEASE_MODES), required=True)
    report.add_argument("--artifact-root", type=Path, required=True)
    report.add_argument("--artifact", action="append", default=[], required=True)
    report.add_argument("--repository-sha", required=True)
    report.add_argument("--workflow-sha", required=True)
    report.add_argument("--build-identity", required=True)
    report.add_argument("--workflow-run-id", type=int, required=True)
    report.add_argument("--run-attempt", type=int, required=True)
    report.add_argument("--replay-ledger", type=Path, required=True)
    report.add_argument("--status", type=Path, default=DEFAULT_STATUS_PATH)
    report.add_argument(
        "--journey-manifest",
        type=Path,
        default=DEFAULT_JOURNEY_MANIFEST_PATH,
    )
    report.add_argument("--runbook", type=Path, default=DEFAULT_RUNBOOK_PATH)
    report.add_argument("--max-artifact-age-seconds", type=int)
    report.add_argument("--output-json", type=Path, required=True)
    report.add_argument("--output-markdown", type=Path, required=True)

    render_status = subparsers.add_parser("render-status")
    render_status.add_argument("--status", type=Path, default=DEFAULT_STATUS_PATH)
    render_status.add_argument("--output", type=Path)

    check_status = subparsers.add_parser("check-status")
    check_status.add_argument("--status", type=Path, default=DEFAULT_STATUS_PATH)
    check_status.add_argument(
        "--journey-manifest",
        type=Path,
        default=DEFAULT_JOURNEY_MANIFEST_PATH,
    )
    check_status.add_argument("--runbook", type=Path, default=DEFAULT_RUNBOOK_PATH)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    github_adapter: GitHubReleaseAdapter | None = None,
    replay_store: ReplayStore | None = None,
    now: datetime | None = None,
) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.command == "render-status":
        status = load_release_status(args.status)
        rendered = render_structured_status_markdown(status)
        if args.output is None:
            print(rendered, end="")
        else:
            _atomic_write_bytes(args.output, rendered.encode("utf-8"), mode=0o644)
        return 0
    if args.command == "check-status":
        result = validate_final_live_qa_runbook(
            status_path=args.status,
            journey_manifest_path=args.journey_manifest,
            runbook_path=args.runbook,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1

    artifact_envelopes: dict[str, Path] = {}
    for assignment in args.artifact:
        if "=" not in assignment:
            raise SystemExit("--artifact must use producer=path")
        producer, raw_path = assignment.split("=", 1)
        if not producer or not raw_path or producer in artifact_envelopes:
            raise SystemExit("--artifact assignments must be unique and non-empty")
        artifact_envelopes[producer] = Path(raw_path)
    if github_adapter is None:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        github_adapter = StdlibGitHubReleaseAdapter(token or "")
    if replay_store is None:
        replay_store = JsonReplayStore(args.replay_ledger)
    report = build_final_release_report(
        FinalReleaseConfig(
            mode=args.mode,
            artifact_root=args.artifact_root,
            artifact_envelopes=artifact_envelopes,
            expected_repository_sha=args.repository_sha,
            expected_workflow_sha=args.workflow_sha,
            expected_build_identity=args.build_identity,
            workflow_run_id=args.workflow_run_id,
            run_attempt=args.run_attempt,
            github_adapter=github_adapter,
            replay_store=replay_store,
            status_path=args.status,
            journey_manifest_path=args.journey_manifest,
            runbook_path=args.runbook,
            max_artifact_age_seconds=args.max_artifact_age_seconds,
        ),
        now=now,
    )
    write_final_release_outputs(
        report,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
