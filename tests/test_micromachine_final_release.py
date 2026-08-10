"""Tests for authenticated MicroMachine final release orchestration."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starcraft_commander.micromachine_final_release import (
    DEFAULT_JOURNEY_MANIFEST_PATH,
    DEFAULT_RUNBOOK_PATH,
    DEFAULT_STATUS_PATH,
    READY_FOR_LIVE_QA,
    READY_TO_MERGE,
    FinalReleaseConfig,
    InMemoryReplayStore,
    JsonReplayStore,
    build_final_release_report,
    check_structured_status_markdown,
    load_release_status,
    main,
    render_final_release_markdown,
    render_final_live_qa_runbook,
    render_structured_status_markdown,
    validate_final_live_qa_runbook,
)


REPOSITORY = "Marker-Inc-Korea/voiStarcraft2"
REPOSITORY_SHA = "a" * 40
WORKFLOW_SHA = "e" * 40
BUILD_IDENTITY = "sha256:" + ("b" * 64)
DEPENDENCY_MERGE_SHA = "c" * 40
RELEASE_PULL_NUMBER = 1420
RELEASE_CLOSURE_ISSUES = (141, 142, 128, 124)
RELEASE_PULL_BODY = "\n".join(
    f"Closes #{issue_number}" for issue_number in RELEASE_CLOSURE_ISSUES
)
RUN_ID = 9001
RUN_ATTEMPT = 2
NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


def canonical_json_bytes(value: object) -> bytes:
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


class FakeGitHubReleaseAdapter:
    def __init__(self, mode: str, status: dict[str, object]) -> None:
        self.mode = mode
        self.workflow_sha = (
            WORKFLOW_SHA if mode == READY_TO_MERGE else REPOSITORY_SHA
        )
        self.issue_requests: list[int] = []
        self.issues: dict[int, dict[str, object]] = {}
        self.closing_pulls: dict[int, list[dict[str, object]]] = {}
        self.pull_requests: dict[int, dict[str, object]] = {}
        for dependency in status["ready_to_merge_dependencies"]:
            issue_number = dependency["issue"]
            self.issues[issue_number] = {
                "number": issue_number,
                "state": "closed",
            }
            self.closing_pulls[issue_number] = [
                self.merged_pull(
                    number=1000 + issue_number,
                    merge_sha=DEPENDENCY_MERGE_SHA,
                )
            ]
        for issue_number in status["release_closure_issues"]:
            self.issues[issue_number] = {
                "number": issue_number,
                "state": "closed",
            }
            self.closing_pulls[issue_number] = [
                self.merged_pull(
                    number=RELEASE_PULL_NUMBER,
                    merge_sha=REPOSITORY_SHA,
                )
            ]
        self.pull_requests[RELEASE_PULL_NUMBER] = {
            "number": RELEASE_PULL_NUMBER,
            "state": "open" if mode == READY_TO_MERGE else "closed",
            "draft": False,
            "body": RELEASE_PULL_BODY,
            "merged": mode == READY_FOR_LIVE_QA,
            "merged_at": (
                None
                if mode == READY_TO_MERGE
                else "2026-08-09T11:00:00Z"
            ),
            "base_ref": "main",
            "base_sha": self.workflow_sha,
            "merge_commit_sha": (
                None if mode == READY_TO_MERGE else REPOSITORY_SHA
            ),
            "repository": REPOSITORY,
            "head_repository": REPOSITORY,
            "head_sha": REPOSITORY_SHA,
        }
        self.branch = {
            "name": "main",
            "commit": {"sha": REPOSITORY_SHA},
        }
        self.workflow = {
            "id": RUN_ID,
            "workflow_id": 7001,
            "path": ".github/workflows/final-pre-live.yml",
            "run_attempt": RUN_ATTEMPT,
            "head_sha": self.workflow_sha,
            "event": (
                "pull_request_target"
                if mode == READY_TO_MERGE
                else "push"
            ),
            "head_branch": "main",
            "head_repository": {"full_name": REPOSITORY},
            "pull_requests": (
                [{"number": RELEASE_PULL_NUMBER}]
                if mode == READY_TO_MERGE
                else []
            ),
        }
        self.artifacts: dict[int, dict[str, object]] = {}

    @staticmethod
    def merged_pull(*, number: int, merge_sha: str) -> dict[str, object]:
        return {
            "number": number,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-08-09T11:00:00Z",
            "base_ref": "main",
            "merge_commit_sha": merge_sha,
            "repository": REPOSITORY,
        }

    def get_issue(self, repository: str, issue_number: int) -> dict[str, object]:
        if repository != REPOSITORY:
            raise AssertionError("unexpected repository")
        self.issue_requests.append(issue_number)
        return dict(self.issues[issue_number])

    def list_issue_closing_pull_requests(
        self,
        repository: str,
        issue_number: int,
    ) -> list[dict[str, object]]:
        if repository != REPOSITORY:
            raise AssertionError("unexpected repository")
        return [dict(item) for item in self.closing_pulls[issue_number]]

    def get_branch(self, repository: str, branch: str) -> dict[str, object]:
        if repository != REPOSITORY or branch != "main":
            raise AssertionError("unexpected branch lookup")
        return json.loads(json.dumps(self.branch))

    def get_pull_request(
        self,
        repository: str,
        pull_number: int,
    ) -> dict[str, object]:
        if repository != REPOSITORY:
            raise AssertionError("unexpected pull repository")
        return json.loads(json.dumps(self.pull_requests[pull_number]))

    def get_workflow_run(
        self,
        repository: str,
        run_id: int,
    ) -> dict[str, object]:
        if repository != REPOSITORY or run_id != RUN_ID:
            raise AssertionError("unexpected workflow lookup")
        return json.loads(json.dumps(self.workflow))

    def get_artifact(
        self,
        repository: str,
        artifact_id: int,
    ) -> dict[str, object]:
        if repository != REPOSITORY:
            raise AssertionError("unexpected artifact repository")
        return json.loads(json.dumps(self.artifacts[artifact_id]))


class MicroMachineFinalReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.status = load_release_status()

    def test_checked_in_status_and_runbook_cover_exact_fourteen_journeys(
        self,
    ) -> None:
        result = validate_final_live_qa_runbook()
        manifest = json.loads(DEFAULT_JOURNEY_MANIFEST_PATH.read_text())
        document = DEFAULT_RUNBOOK_PATH.read_text()

        self.assertTrue(result["ok"], result["blockers"])
        self.assertEqual(14, result["journey_count"])
        self.assertEqual(14, len(manifest["journeys"]))
        self.assertEqual(
            render_final_live_qa_runbook(self.status, manifest),
            document,
        )
        self.assertTrue(check_structured_status_markdown(self.status, document))
        self.assertEqual(
            list(RELEASE_CLOSURE_ISSUES),
            self.status["release_closure_issues"],
        )
        self.assertTrue(
            set(RELEASE_CLOSURE_ISSUES).isdisjoint(
                self.dependency_numbers(self.status)
            )
        )

    def test_ready_to_merge_uses_github_dependencies_and_excludes_self_closure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root, adapter)

            report = self.build_report(
                mode=READY_TO_MERGE,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
            )

            self.assertTrue(report["ok"], report["blockers"])
            self.assertEqual(READY_TO_MERGE, report["status"])
            self.assertTrue(report["manual_live_qa_remaining"])
            self.assertFalse(report["live_qualified"])
            self.assertTrue(
                set(RELEASE_CLOSURE_ISSUES).isdisjoint(adapter.issue_requests)
            )
            self.assertEqual(
                self.dependency_numbers(self.status),
                {item["issue"] for item in report["dependencies"]},
            )
            self.assertEqual(
                [RELEASE_PULL_NUMBER],
                report["workflow"]["pull_request_numbers"],
            )
            self.assertIn(
                "Manual live QA remaining: `true`",
                render_final_release_markdown(report),
            )

    def test_ready_for_live_qa_requires_one_exact_pull_to_close_all_four_issues(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_FOR_LIVE_QA, self.status)
            envelopes = self.write_green_artifacts(root, adapter)

            report = self.build_report(
                mode=READY_FOR_LIVE_QA,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
            )

            self.assertTrue(report["ok"], report["blockers"])
            self.assertEqual(READY_FOR_LIVE_QA, report["status"])
            self.assertEqual(
                [
                    {
                        "issue": issue_number,
                        "state": "closed",
                        "closing_pull_numbers": [RELEASE_PULL_NUMBER],
                    }
                    for issue_number in RELEASE_CLOSURE_ISSUES
                ],
                report["dependencies"],
            )
            self.assertTrue(report["manual_live_qa_remaining"])
            self.assertFalse(report["live_qualified"])

    def test_ready_to_merge_requires_exact_four_closing_declarations_in_pr_body(
        self,
    ) -> None:
        bodies = {
            "missing": "\n".join(
                f"Closes #{issue_number}"
                for issue_number in RELEASE_CLOSURE_ISSUES[:-1]
            ),
            "duplicate": RELEASE_PULL_BODY + "\nCloses #141",
            "unexpected": RELEASE_PULL_BODY + "\nCloses #999",
            "commented": RELEASE_PULL_BODY.replace(
                "Closes #141",
                "<!-- Closes #141 -->",
            ),
            "fenced": RELEASE_PULL_BODY.replace(
                "Closes #141",
                "```\nCloses #141\n```",
            ),
        }
        for label, body in bodies.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = FakeGitHubReleaseAdapter(
                    READY_TO_MERGE,
                    self.status,
                )
                adapter.pull_requests[RELEASE_PULL_NUMBER]["body"] = body
                envelopes = self.write_green_artifacts(root, adapter)

                report = self.build_report(
                    mode=READY_TO_MERGE,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )

                self.assertBlocker(
                    report,
                    "release_pull_closing_declarations_mismatch",
                )

    def test_ready_to_merge_requires_single_workflow_bound_pr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            adapter.workflow["pull_requests"] = [
                {"number": RELEASE_PULL_NUMBER},
                {"number": RELEASE_PULL_NUMBER + 1},
            ]
            envelopes = self.write_green_artifacts(root, adapter)

            report = self.build_report(
                mode=READY_TO_MERGE,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
            )

            self.assertBlocker(report, "github_workflow_pull_request_mismatch")
            self.assertBlocker(report, "release_pull_workflow_binding_missing")

    def test_ready_to_merge_rejects_wrong_pull_identity_and_state(self) -> None:
        cases = {
            "base": (
                {"base_ref": "develop"},
                "release_pull_identity_mismatch",
            ),
            "repository": (
                {"repository": "attacker/fork"},
                "release_pull_identity_mismatch",
            ),
            "head repository": (
                {"head_repository": "attacker/fork"},
                "release_pull_identity_mismatch",
            ),
            "missing head repository": (
                {"head_repository": None},
                "release_pull_identity_mismatch",
            ),
            "head SHA": (
                {"head_sha": "d" * 40},
                "release_pull_identity_mismatch",
            ),
            "stale base SHA": (
                {"base_sha": "d" * 40},
                "release_pull_identity_mismatch",
            ),
            "draft": (
                {"draft": True},
                "release_pull_identity_mismatch",
            ),
            "closed": (
                {"state": "closed"},
                "release_pull_premerge_state_mismatch",
            ),
            "already merged": (
                {"merged": True},
                "release_pull_premerge_state_mismatch",
            ),
        }
        for label, (mutation, blocker) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = FakeGitHubReleaseAdapter(
                    READY_TO_MERGE,
                    self.status,
                )
                adapter.pull_requests[RELEASE_PULL_NUMBER].update(mutation)
                envelopes = self.write_green_artifacts(root, adapter)

                report = self.build_report(
                    mode=READY_TO_MERGE,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )

                self.assertBlocker(report, blocker)

    def test_missing_child_artifact_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root, adapter)
            envelopes.pop("browser_accessibility")

            report = self.build_report(
                mode=READY_TO_MERGE,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
            )

            self.assertBlocker(report, "missing_child_artifact")

    def test_envelope_binding_mismatches_fail_closed(self) -> None:
        mutations = {
            "schema": lambda value: value.update({"schema_version": 2}),
            "producer": lambda value: value.update({"producer": "forged"}),
            "repository SHA": lambda value: value.update(
                {"repository_sha": "d" * 40}
            ),
            "build": lambda value: value.update(
                {"build_identity": "sha256:" + ("d" * 64)}
            ),
            "run": lambda value: value.update({"workflow_run_id": RUN_ID + 1}),
            "attempt": lambda value: value.update(
                {"run_attempt": RUN_ATTEMPT + 1}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
                envelopes = self.write_green_artifacts(root, adapter)
                self.mutate_envelope(envelopes["build_identity"], mutate)

                report = self.build_report(
                    mode=READY_TO_MERGE,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )

                self.assertBlocker(report, "envelope_binding_mismatch")

    def test_member_digest_is_detached_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root, adapter)
            member = root / "build_identity" / "report.json"
            member.write_bytes(
                canonical_json_bytes(
                    {
                        "schema_version": 1,
                        "ok": True,
                        "status": "passed",
                        "producer": "tampered",
                    }
                )
            )

            report = self.build_report(
                mode=READY_TO_MERGE,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
            )

            self.assertBlocker(report, "member_digest_mismatch")

    def test_stale_future_and_replayed_artifacts_are_rejected(self) -> None:
        for label, generated_at, blocker in (
            (
                "stale",
                NOW - timedelta(days=2),
                "stale_artifact",
            ),
            (
                "future",
                NOW + timedelta(minutes=6),
                "future_artifact",
            ),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
                envelopes = self.write_green_artifacts(root, adapter)
                self.mutate_envelope(
                    envelopes["deterministic_journeys"],
                    lambda value, timestamp=generated_at: value.update(
                        {"generated_at": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")}
                    ),
                )

                report = self.build_report(
                    mode=READY_TO_MERGE,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )

                self.assertBlocker(report, blocker)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root, adapter)
            replay_store = InMemoryReplayStore()
            first = self.build_report(
                mode=READY_TO_MERGE,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
                replay_store=replay_store,
            )
            second = self.build_report(
                mode=READY_TO_MERGE,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
                replay_store=replay_store,
            )

            self.assertTrue(first["ok"], first["blockers"])
            self.assertBlocker(second, "artifact_replay_rejected")

    def test_symlink_nonregular_and_path_escape_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root, adapter)
            original = envelopes["build_identity"]
            external = root.parent / f"{root.name}-external-envelope.json"
            external.write_bytes(original.read_bytes())
            original.unlink()
            original.symlink_to(external)
            try:
                report = self.build_report(
                    mode=READY_TO_MERGE,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )
            finally:
                external.unlink(missing_ok=True)
            self.assertBlocker(report, "envelope_symlink")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root, adapter)
            member = root / "build_identity" / "report.json"
            member.unlink()
            member.mkdir()

            report = self.build_report(
                mode=READY_TO_MERGE,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
            )

            self.assertBlocker(report, "member_not_regular")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root, adapter)
            outside = root.parent / f"{root.name}-outside.json"
            outside.write_bytes(envelopes["build_identity"].read_bytes())
            envelopes["build_identity"] = outside
            try:
                report = self.build_report(
                    mode=READY_TO_MERGE,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )
            finally:
                outside.unlink(missing_ok=True)

            self.assertBlocker(report, "envelope_path_escape")

    def test_wrong_github_run_sha_attempt_and_artifact_binding_are_rejected(
        self,
    ) -> None:
        workflow_mutations = {
            "run": ({"id": RUN_ID + 1}, "github_workflow_run_id_mismatch"),
            "attempt": (
                {"run_attempt": RUN_ATTEMPT + 1},
                "github_workflow_attempt_mismatch",
            ),
            "SHA": ({"head_sha": "d" * 40}, "github_workflow_sha_mismatch"),
            "workflow id": (
                {"workflow_id": None},
                "github_workflow_identity_missing",
            ),
            "path": (
                {"path": ".github/workflows/forged.yml"},
                "github_workflow_path_mismatch",
            ),
            "repository": (
                {"head_repository": None},
                "github_workflow_repository_mismatch",
            ),
        }
        for label, (mutation, blocker) in workflow_mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
                envelopes = self.write_green_artifacts(root, adapter)
                adapter.workflow.update(mutation)

                report = self.build_report(
                    mode=READY_TO_MERGE,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )

                self.assertBlocker(report, blocker)

        artifact_mutations = {
            "name": (
                {"name": "forged-artifact"},
                "github_artifact_name_mismatch",
            ),
            "run": (
                {"workflow_run": {"id": RUN_ID + 1, "head_sha": WORKFLOW_SHA}},
                "github_artifact_run_mismatch",
            ),
            "SHA": (
                {"workflow_run": {"id": RUN_ID, "head_sha": "d" * 40}},
                "github_artifact_sha_mismatch",
            ),
            "missing SHA": (
                {"workflow_run": {"id": RUN_ID}},
                "github_artifact_sha_mismatch",
            ),
            "digest": ({"digest": None}, "github_artifact_digest_missing"),
            "digest mismatch": (
                {"digest": "sha256:" + ("d" * 64)},
                "github_artifact_digest_mismatch",
            ),
        }
        for label, (mutation, blocker) in artifact_mutations.items():
            with self.subTest(artifact=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
                envelopes = self.write_green_artifacts(root, adapter)
                artifact_id = self.read_envelope(envelopes["build_identity"])[
                    "artifact_id"
                ]
                adapter.artifacts[artifact_id].update(mutation)

                report = self.build_report(
                    mode=READY_TO_MERGE,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )

                self.assertBlocker(report, blocker)

    def test_open_dependency_and_injected_self_closure_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root, adapter)
            issue_number = min(self.dependency_numbers(self.status))
            adapter.issues[issue_number]["state"] = "open"

            report = self.build_report(
                mode=READY_TO_MERGE,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
            )

            self.assertBlocker(report, "dependency_issue_not_closed")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root, adapter)
            issue_number = min(self.dependency_numbers(self.status))
            adapter.closing_pulls[issue_number][0]["merged"] = False

            report = self.build_report(
                mode=READY_TO_MERGE,
                root=root,
                envelopes=envelopes,
                adapter=adapter,
            )

            self.assertBlocker(report, "dependency_closing_pull_not_merged")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_path = root / "status.json"
            forged_status = json.loads(DEFAULT_STATUS_PATH.read_text())
            forged_status["ready_to_merge_dependencies"].append(
                {"issue": 128, "require_merged_closing_pull": True}
            )
            status_path.write_text(json.dumps(forged_status))
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(root / "artifacts", adapter)

            report = self.build_report(
                mode=READY_TO_MERGE,
                root=root / "artifacts",
                envelopes=envelopes,
                adapter=adapter,
                status_path=status_path,
            )

            self.assertBlocker(report, "invalid_ready_to_merge_dependencies")
            self.assertBlocker(report, "ready_to_merge_self_closure_cycle")

    def test_ready_for_live_qa_rejects_incomplete_closure_or_wrong_exact_merge(
        self,
    ) -> None:
        mutations = {
            "open issue": (
                lambda adapter: adapter.issues[128].update({"state": "open"}),
                "release_closure_issue_not_closed",
            ),
            "unmerged pull": (
                lambda adapter: adapter.closing_pulls[128][0].update(
                    {"merged": False}
                ),
                "release_closure_pull_not_exact_main_merge",
            ),
            "wrong merge": (
                lambda adapter: adapter.closing_pulls[128][0].update(
                    {"merge_commit_sha": "d" * 40}
                ),
                "release_closure_pull_not_exact_main_merge",
            ),
            "different pull": (
                lambda adapter: adapter.closing_pulls[128][0].update(
                    {"number": RELEASE_PULL_NUMBER + 1}
                ),
                "release_closure_not_single_pull",
            ),
            "pull metadata merge": (
                lambda adapter: adapter.pull_requests[
                    RELEASE_PULL_NUMBER
                ].update({"merge_commit_sha": "d" * 40}),
                "release_pull_merge_state_mismatch",
            ),
            "wrong main": (
                lambda adapter: adapter.branch["commit"].update(
                    {"sha": "d" * 40}
                ),
                "main_sha_mismatch",
            ),
        }
        for label, (mutate, blocker) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = FakeGitHubReleaseAdapter(
                    READY_FOR_LIVE_QA,
                    self.status,
                )
                envelopes = self.write_green_artifacts(root, adapter)
                mutate(adapter)

                report = self.build_report(
                    mode=READY_FOR_LIVE_QA,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )

                self.assertBlocker(report, blocker)

    def test_live_qualified_claim_and_private_configuration_are_rejected_without_leak(
        self,
    ) -> None:
        cases = (
            (
                "live",
                {"live_qualified": True},
                "live_qualified_without_manual_evidence",
                None,
            ),
            (
                "secret",
                {"openai_api_key": "fixture-value"},
                "private_configuration_detected",
                "fixture-value",
            ),
        )
        for label, extra, blocker, forbidden in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
                envelopes = self.write_green_artifacts(root, adapter)
                self.rewrite_member(
                    root,
                    envelopes["pre_live_provenance"],
                    "pre_live_provenance",
                    extra,
                )

                report = self.build_report(
                    mode=READY_TO_MERGE,
                    root=root,
                    envelopes=envelopes,
                    adapter=adapter,
                )

                self.assertBlocker(report, blocker)
                self.assertTrue(report["manual_live_qa_remaining"])
                self.assertFalse(report["live_qualified"])
                if forbidden is not None:
                    self.assertNotIn(forbidden, json.dumps(report))

    def test_status_and_runbook_drift_fail_closed(self) -> None:
        rendered = render_structured_status_markdown(self.status)
        self.assertTrue(check_structured_status_markdown(self.status, rendered))
        self.assertFalse(
            check_structured_status_markdown(
                self.status,
                rendered.replace("Live qualified: `false`", "Live qualified: `true`"),
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            drifted_runbook = root / "runbook.md"
            document = DEFAULT_RUNBOOK_PATH.read_text()
            start = document.index(
                "### 14. `voice_readback_callout_identity`"
            )
            end = document.index("## Actual SC2 Visual And Audio Gate")
            drifted_runbook.write_text(document[:start] + document[end:])

            result = validate_final_live_qa_runbook(runbook_path=drifted_runbook)

            self.assertFalse(result["ok"])
            codes = {item["code"] for item in result["blockers"]}
            self.assertIn("runbook_contract_drift", codes)
            self.assertIn("runbook_journey_coverage_mismatch", codes)

    def test_outputs_and_cli_use_allowlisted_projection_and_adapter_seam(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts_root = root / "artifacts"
            adapter = FakeGitHubReleaseAdapter(READY_TO_MERGE, self.status)
            envelopes = self.write_green_artifacts(artifacts_root, adapter)
            output_json = root / "ready_to_merge.json"
            output_markdown = root / "ready_to_merge.md"
            args = [
                "report",
                "--mode",
                READY_TO_MERGE,
                "--artifact-root",
                str(artifacts_root),
            ]
            for producer, path in envelopes.items():
                args.extend(["--artifact", f"{producer}={path}"])
            args.extend(
                [
                    "--repository-sha",
                    REPOSITORY_SHA,
                    "--workflow-sha",
                    adapter.workflow_sha,
                    "--build-identity",
                    BUILD_IDENTITY,
                    "--workflow-run-id",
                    str(RUN_ID),
                    "--run-attempt",
                    str(RUN_ATTEMPT),
                    "--replay-ledger",
                    str(root / "unused-ledger.json"),
                    "--output-json",
                    str(output_json),
                    "--output-markdown",
                    str(output_markdown),
                ]
            )

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(
                    args,
                    github_adapter=adapter,
                    replay_store=InMemoryReplayStore(),
                    now=NOW,
                )

            self.assertEqual(0, exit_code)
            report = json.loads(output_json.read_text())
            self.assertTrue(report["ok"])
            self.assertTrue(report["manual_live_qa_remaining"])
            self.assertIn("Manual live QA remaining", output_markdown.read_text())
            self.assertNotIn(str(artifacts_root), output_json.read_text())
            self.assertNotIn(str(artifacts_root), output_markdown.read_text())

    def test_json_replay_store_is_atomic_and_rejects_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonReplayStore(Path(directory) / "replay.json")
            keys = ["1" * 64, "2" * 64]

            self.assertTrue(ledger.consume_many(keys))
            self.assertFalse(ledger.consume_many([keys[0]]))
            self.assertEqual(
                keys,
                json.loads(ledger.path.read_text())["replay_keys"],
            )

    def build_report(
        self,
        *,
        mode: str,
        root: Path,
        envelopes: dict[str, Path],
        adapter: FakeGitHubReleaseAdapter,
        replay_store: InMemoryReplayStore | None = None,
        status_path: Path = DEFAULT_STATUS_PATH,
        runbook_path: Path = DEFAULT_RUNBOOK_PATH,
    ) -> dict[str, object]:
        return build_final_release_report(
            FinalReleaseConfig(
                mode=mode,
                artifact_root=root,
                artifact_envelopes=envelopes,
                expected_repository_sha=REPOSITORY_SHA,
                expected_workflow_sha=adapter.workflow_sha,
                expected_build_identity=BUILD_IDENTITY,
                workflow_run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                github_adapter=adapter,
                replay_store=replay_store or InMemoryReplayStore(),
                status_path=status_path,
                journey_manifest_path=DEFAULT_JOURNEY_MANIFEST_PATH,
                runbook_path=runbook_path,
            ),
            now=NOW,
        )

    def write_green_artifacts(
        self,
        root: Path,
        adapter: FakeGitHubReleaseAdapter,
    ) -> dict[str, Path]:
        root.mkdir(parents=True, exist_ok=True)
        envelopes: dict[str, Path] = {}
        for index, spec in enumerate(self.status["required_artifacts"], start=1):
            producer = spec["producer"]
            artifact_id = 5000 + index
            member = root / spec["member"]
            member.parent.mkdir(parents=True, exist_ok=True)
            member_payload = {
                "schema_version": 1,
                "ok": True,
                "status": "passed",
                "producer": producer,
            }
            member_bytes = canonical_json_bytes(member_payload)
            member.write_bytes(member_bytes)
            envelope = {
                "schema_version": 1,
                "producer": producer,
                "repository_sha": REPOSITORY_SHA,
                "build_identity": BUILD_IDENTITY,
                "generated_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sha256": hashlib.sha256(member_bytes).hexdigest(),
                "archive_sha256": "sha256:" + hashlib.sha256(
                    f"archive-{producer}".encode()
                ).hexdigest(),
                "workflow_run_id": RUN_ID,
                "run_attempt": RUN_ATTEMPT,
                "artifact_id": artifact_id,
                "member": spec["member"],
            }
            envelope_path = root / producer / "envelope.json"
            envelope_path.parent.mkdir(parents=True, exist_ok=True)
            envelope_path.write_bytes(canonical_json_bytes(envelope))
            envelopes[producer] = envelope_path
            adapter.artifacts[artifact_id] = {
                "id": artifact_id,
                "name": spec["artifact_name"],
                "expired": False,
                "digest": "sha256:" + hashlib.sha256(
                    f"archive-{producer}".encode()
                ).hexdigest(),
                "workflow_run": {
                    "id": RUN_ID,
                    "head_sha": adapter.workflow_sha,
                },
            }
        return envelopes

    def rewrite_member(
        self,
        root: Path,
        envelope_path: Path,
        producer: str,
        extra: dict[str, object],
    ) -> None:
        envelope = self.read_envelope(envelope_path)
        payload = {
            "schema_version": 1,
            "ok": True,
            "status": "passed",
            "producer": producer,
            **extra,
        }
        member_bytes = canonical_json_bytes(payload)
        (root / envelope["member"]).write_bytes(member_bytes)
        envelope["sha256"] = hashlib.sha256(member_bytes).hexdigest()
        envelope_path.write_bytes(canonical_json_bytes(envelope))

    @staticmethod
    def mutate_envelope(
        path: Path,
        mutate: object,
    ) -> None:
        value = json.loads(path.read_text())
        mutate(value)
        path.write_bytes(canonical_json_bytes(value))

    @staticmethod
    def read_envelope(path: Path) -> dict[str, object]:
        return json.loads(path.read_text())

    @staticmethod
    def dependency_numbers(status: dict[str, object]) -> set[int]:
        return {
            dependency["issue"]
            for dependency in status["ready_to_merge_dependencies"]
        }

    def assertBlocker(self, report: dict[str, object], code: str) -> None:
        self.assertFalse(report["ok"], report)
        self.assertIn(
            code,
            {item["code"] for item in report["blockers"]},
            report["blockers"],
        )


if __name__ == "__main__":
    unittest.main()
