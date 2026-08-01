"""Tests for authenticated MicroMachine pre-live provenance."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from pathlib import Path
from typing import Any
from unittest import mock

from starcraft_commander import micromachine_pre_live_provenance as provenance_module
from starcraft_commander.micromachine_build_identity import (
    MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
    MICROMACHINE_REQUIRED_NATIVE_TESTS,
    REPO_ROOT as BUILD_IDENTITY_REPO_ROOT,
    MicroMachineBuildIdentityConfig,
    build_micromachine_build_identity,
    canonical_micromachine_ctest_registry,
    write_micromachine_build_attestation,
    write_micromachine_embedded_build_identity_header,
    write_micromachine_source_attestation,
)
from starcraft_commander.micromachine_pre_live_provenance import (
    AUTHORITATIVE_PROVENANCE_JOB_NAME,
    AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH,
    AUTHORITATIVE_REPOSITORY_ID,
    AUTHORITATIVE_REPLAY_CREATE_RULESET_NAME,
    AUTHORITATIVE_REPLAY_CLAIMER_USER_ID,
    AUTHORITATIVE_REPLAY_IMMUTABLE_RULESET_NAME,
    AUTHORITATIVE_REPLAY_REF_PREFIX,
    AUTHORITATIVE_REPLAY_REF_PATTERN,
    ISOLATED_PYTHON_BOOTSTRAP,
    PRODUCER_POLICY_RELATIVE_PATH,
    SANITIZED_PRODUCER_ENV,
    GitHubHTTPError,
    GitHubRefReplayStore,
    GitHubSourceError,
    StdlibGitHubRESTAdapter,
    attest_build_binding,
    attest_github_actions_emission_context,
    attest_github_replay_rulesets,
    attest_github_source,
    attest_pre_live_provenance,
    attest_repository,
    canonical_global_replay_state_dir,
    canonical_replay_digest,
    consume_github_replay_reference,
    consume_replay_ledger,
    canonical_pre_live_state_dir,
    emit_github_actions_pre_live_bundle,
    normalize_github_repository,
    require_release_authority,
    resolve_local_producer_policy,
    run_local_producer,
)
from starcraft_commander.micromachine_pre_live_artifact import (
    GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
    PRE_LIVE_CTEST_EVIDENCE_SCHEMA_VERSION,
    PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID,
    PreLiveArtifactMetadata,
    build_pre_live_artifact_bundle,
    canonical_ctest_evidence_bytes,
    canonical_json_bytes,
    verify_pre_live_artifact_bundle,
)


REPOSITORY = "Marker-Inc-Korea/voiStarcraft2"
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
RUN_ID = 101
RUN_ATTEMPT = 2
JOB_ID = 201
ARTIFACT_ID = 301
WORKFLOW_ID = 401
WORKFLOW_PATH = AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH
WORKFLOW_REF = (
    f"{REPOSITORY}/{WORKFLOW_PATH}@refs/pull/137/merge"
)
WORKFLOW_SHA = "c" * 40
REQUIRED_CTEST_COUNT = len(MICROMACHINE_REQUIRED_NATIVE_TESTS)


def candidate_authority(
    head_sha: str,
    *,
    pull_id: int = 3,
    pull_number: int = 137,
    head_ref: str = "issue-138-authenticated-prelive-provenance",
    head_repository_id: int = AUTHORITATIVE_REPOSITORY_ID,
    issue_id: int = 2,
    issue_number: int = 138,
) -> dict[str, object]:
    return {
        "scope": "candidate_pr",
        "release_authoritative": False,
        "event": "pull_request",
        "pull_request": {
            "database_id": pull_id,
            "number": pull_number,
            "head_sha": head_sha,
            "head_ref": head_ref,
            "head_repository_id": head_repository_id,
        },
        "closing_issue": {
            "repository_full_name": REPOSITORY,
            "repository_database_id": AUTHORITATIVE_REPOSITORY_ID,
            "database_id": issue_id,
            "number": issue_number,
        },
    }


def make_replay_ruleset_fixtures() -> tuple[
    list[dict[str, object]],
    dict[int, dict[str, object]],
]:
    create_id = 501
    immutable_id = 502
    details = {
        create_id: {
            "id": create_id,
            "name": AUTHORITATIVE_REPLAY_CREATE_RULESET_NAME,
            "target": "tag",
            "source_type": "Repository",
            "enforcement": "active",
            "conditions": {
                "ref_name": {
                    "include": [AUTHORITATIVE_REPLAY_REF_PATTERN],
                    "exclude": [],
                }
            },
            "rules": [{"type": "creation"}],
            "bypass_actors": [
                {
                    "actor_id": AUTHORITATIVE_REPLAY_CLAIMER_USER_ID,
                    "actor_type": "User",
                    "bypass_mode": "always",
                }
            ],
        },
        immutable_id: {
            "id": immutable_id,
            "name": AUTHORITATIVE_REPLAY_IMMUTABLE_RULESET_NAME,
            "target": "tag",
            "source_type": "Repository",
            "enforcement": "active",
            "conditions": {
                "ref_name": {
                    "include": [AUTHORITATIVE_REPLAY_REF_PATTERN],
                    "exclude": [],
                }
            },
            "rules": [{"type": "update"}, {"type": "deletion"}],
            "bypass_actors": [],
        },
    }
    summaries = [
        {
            "id": ruleset_id,
            "name": record["name"],
            "target": record["target"],
            "source_type": record["source_type"],
            "enforcement": record["enforcement"],
        }
        for ruleset_id, record in details.items()
    ]
    return summaries, details


class FakeHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, maximum: int) -> bytes:
        return self.payload[:maximum]


class FakeGitHubAdapter:
    def __init__(self, *, head_sha: str = HEAD_SHA) -> None:
        self.artifact_bytes = b"server-downloaded-artifact"
        self.repository = {
            "id": AUTHORITATIVE_REPOSITORY_ID,
            "full_name": REPOSITORY,
            "archived": False,
            "disabled": False,
        }
        self.issue = {"id": 2, "number": 138, "state": "open"}
        self.pull_request = {
            "id": 3,
            "number": 137,
            "state": "open",
            "body": "Closes #138",
            "head": {
                "sha": head_sha,
                "ref": "issue-138-authenticated-prelive-provenance",
                "repo": {
                    "id": AUTHORITATIVE_REPOSITORY_ID,
                    "full_name": REPOSITORY,
                },
            },
            "base": {
                "sha": BASE_SHA,
                "ref": "main",
                "repo": {
                    "id": AUTHORITATIVE_REPOSITORY_ID,
                    "full_name": REPOSITORY,
                },
            },
            "merged_at": None,
        }
        self.closing_issues = [
            {
                "databaseId": 2,
                "number": 138,
                "repository": {
                    "databaseId": AUTHORITATIVE_REPOSITORY_ID,
                    "nameWithOwner": REPOSITORY,
                },
            }
        ]
        self.comparison = {
            "status": "ahead",
            "ahead_by": 1,
            "behind_by": 0,
            "base_commit": {"sha": BASE_SHA},
            "merge_base_commit": {"sha": BASE_SHA},
            "commits": [{"sha": head_sha}],
        }
        self.workflow_run = {
            "id": RUN_ID,
            "workflow_id": WORKFLOW_ID,
            "run_number": 17,
            "run_attempt": RUN_ATTEMPT,
            "head_sha": head_sha,
            "head_branch": "issue-138-authenticated-prelive-provenance",
            "head_repository": {
                "id": AUTHORITATIVE_REPOSITORY_ID,
                "full_name": REPOSITORY,
            },
            "event": "pull_request",
            "pull_requests": [
                {
                    "id": 3,
                    "number": 137,
                    "head": {
                        "sha": head_sha,
                        "repo": {"id": AUTHORITATIVE_REPOSITORY_ID},
                    },
                }
            ],
            "path": WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "success",
        }
        self.workflow_runs = [dict(self.workflow_run)]
        self.workflow_run_details = {RUN_ID: self.workflow_run}
        self.workflow = {
            "id": WORKFLOW_ID,
            "path": WORKFLOW_PATH,
            "state": "active",
        }
        self.attempt = dict(self.workflow_run)
        self.attempt.update(
            {
                "run_started_at": "2026-07-30T00:00:00Z",
                "updated_at": "2026-07-30T00:10:00Z",
            }
        )
        self.jobs = [
            {
                "id": JOB_ID,
                "run_id": RUN_ID,
                "run_attempt": RUN_ATTEMPT,
                "name": AUTHORITATIVE_PROVENANCE_JOB_NAME,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-07-30T00:01:00Z",
                "completed_at": "2026-07-30T00:09:00Z",
            }
        ]
        self.job = {
            "id": JOB_ID,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "head_sha": head_sha,
            "name": "pre-live-provenance",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-07-30T00:01:00Z",
            "completed_at": "2026-07-30T00:09:00Z",
        }
        self.artifacts = [{"id": ARTIFACT_ID, "name": "pre-live"}]
        self.artifact = {
            "id": ARTIFACT_ID,
            "name": "pre-live",
            "expired": False,
            "created_at": "2026-07-30T00:05:00Z",
            "updated_at": "2026-07-30T00:05:00Z",
            "digest": "sha256:" + hashlib.sha256(self.artifact_bytes).hexdigest(),
            "workflow_run": {
                "id": RUN_ID,
                "head_sha": head_sha,
            },
        }
        self.artifact_bytes = make_source_artifact_bundle(head_sha)
        self.artifact["digest"] = (
            "sha256:" + hashlib.sha256(self.artifact_bytes).hexdigest()
        )
        self.fail_at: str | None = None
        self.download_calls = 0
        self.workflow_references = {
            "refs/pull/137/merge": {
                "ref": "refs/pull/137/merge",
                "object": {"type": "commit", "sha": WORKFLOW_SHA},
            },
            (
                "refs/heads/issue-138-authenticated-prelive-provenance"
            ): {
                "ref": (
                    "refs/heads/"
                    "issue-138-authenticated-prelive-provenance"
                ),
                "object": {"type": "commit", "sha": head_sha},
            },
            "refs/heads/main": {
                "ref": "refs/heads/main",
                "object": {"type": "commit", "sha": BASE_SHA},
            },
        }
        self.references: dict[str, dict[str, object]] = {}
        self.rulesets, self.ruleset_details = make_replay_ruleset_fixtures()

    def _result(self, name: str, value: Any) -> Any:
        if self.fail_at == name:
            raise GitHubSourceError("fixture API failure")
        return value

    def get_repository(self, repository: str) -> dict[str, object]:
        return self._result("repository", self.repository)

    def get_issue(
        self,
        repository: str,
        issue_number: int,
    ) -> dict[str, object]:
        return self._result("issue", self.issue)

    def get_pull_request(
        self,
        repository: str,
        pull_number: int,
    ) -> dict[str, object]:
        return self._result("pull_request", self.pull_request)

    def list_pull_request_closing_issues(
        self,
        repository: str,
        pull_number: int,
    ) -> list[dict[str, object]]:
        return self._result("closing_issues", self.closing_issues)

    def compare_commits(
        self,
        repository: str,
        *,
        base: str,
        head: str,
    ) -> dict[str, object]:
        return self._result("comparison", self.comparison)

    def get_workflow_run(
        self,
        repository: str,
        run_id: int,
    ) -> dict[str, object]:
        record = self.workflow_run_details.get(run_id)
        if record is None:
            raise GitHubSourceError(f"unknown workflow run: {run_id}")
        return self._result(
            "workflow_run",
            record,
        )

    def list_workflow_runs(
        self,
        repository: str,
        workflow_id: int,
        *,
        branch: str,
        event: str,
    ) -> list[dict[str, object]]:
        return self._result("workflow_runs", self.workflow_runs)

    def get_workflow(
        self,
        repository: str,
        workflow_id: int,
    ) -> dict[str, object]:
        return self._result("workflow", self.workflow)

    def get_workflow_run_attempt(
        self,
        repository: str,
        run_id: int,
        run_attempt: int,
    ) -> dict[str, object]:
        return self._result("attempt", self.attempt)

    def list_workflow_run_attempt_jobs(
        self,
        repository: str,
        run_id: int,
        run_attempt: int,
    ) -> list[dict[str, object]]:
        return self._result("jobs", self.jobs)

    def get_job(
        self,
        repository: str,
        job_id: int,
    ) -> dict[str, object]:
        return self._result("job", self.job)

    def list_workflow_run_artifacts(
        self,
        repository: str,
        run_id: int,
    ) -> list[dict[str, object]]:
        return self._result("artifacts", self.artifacts)

    def get_artifact(
        self,
        repository: str,
        artifact_id: int,
    ) -> dict[str, object]:
        return self._result("artifact", self.artifact)

    def download_artifact(self, repository: str, artifact_id: int) -> bytes:
        self.download_calls += 1
        return self._result("download", self.artifact_bytes)

    def create_git_reference(
        self,
        repository: str,
        *,
        ref: str,
        sha: str,
    ) -> dict[str, object]:
        if ref in self.references:
            raise GitHubHTTPError(
                path=f"/repos/{repository}/git/refs",
                status=422,
                body=b'{"message":"Reference already exists"}',
            )
        result = {
            "ref": ref,
            "object": {"type": "commit", "sha": sha},
        }
        self.references[ref] = result
        return result

    def get_git_reference(
        self,
        repository: str,
        *,
        ref: str,
    ) -> dict[str, object]:
        if ref in self.workflow_references:
            return self._result(
                "workflow_reference",
                self.workflow_references[ref],
            )
        try:
            return self.references[ref]
        except KeyError as exc:
            raise GitHubHTTPError(
                path=f"/repos/{repository}/git/ref/{ref}",
                status=404,
                body=b'{"message":"Not Found"}',
            ) from exc

    def list_repository_rulesets(
        self,
        repository: str,
    ) -> list[dict[str, object]]:
        return self._result("rulesets", self.rulesets)

    def get_repository_ruleset(
        self,
        repository: str,
        ruleset_id: int,
    ) -> dict[str, object]:
        return self._result(
            f"ruleset_{ruleset_id}",
            self.ruleset_details[ruleset_id],
        )


class RepositoryAttestationTest(unittest.TestCase):
    def test_normalizes_supported_github_remotes(self) -> None:
        for remote in (
            "https://github.com/Marker-Inc-Korea/voiStarcraft2.git",
            "ssh://git@github.com/Marker-Inc-Korea/voiStarcraft2.git",
            "git@github.com:Marker-Inc-Korea/voiStarcraft2.git",
        ):
            with self.subTest(remote=remote):
                self.assertEqual(REPOSITORY, normalize_github_repository(remote))

    def test_rejects_non_authoritative_remote_forms(self) -> None:
        for remote in (
            REPOSITORY,
            "file://github.com/Marker-Inc-Korea/voiStarcraft2.git",
            "git://github.com/Marker-Inc-Korea/voiStarcraft2.git",
            "https://user:token@github.com/Marker-Inc-Korea/voiStarcraft2.git",
            "ssh://root@github.com/Marker-Inc-Korea/voiStarcraft2.git",
            "ssh://git@github.com:2222/Marker-Inc-Korea/voiStarcraft2.git",
        ):
            with self.subTest(remote=remote):
                with self.assertRaises(ValueError):
                    normalize_github_repository(remote)

    def test_attests_exact_clean_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = init_git_repo(root)

            report = attest_repository(
                root,
                expected_repository=REPOSITORY,
                expected_commit=commit,
            )

            self.assertTrue(report["ok"], report)
            self.assertEqual(commit, report["observed_commit"])
            self.assertEqual(REPOSITORY, report["observed_repository"])

    def test_rejects_wrong_repository_remote_and_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = init_git_repo(root)
            for kwargs in (
                {
                    "expected_repository": "Other/Repository",
                    "expected_commit": commit,
                },
                {
                    "expected_repository": REPOSITORY,
                    "expected_commit": "b" * 40,
                },
                {
                    "expected_repository": REPOSITORY,
                    "expected_commit": commit.upper(),
                },
            ):
                with self.subTest(kwargs=kwargs):
                    report = attest_repository(root, **kwargs)
                    self.assertFalse(report["ok"], report)

            git(root, "remote", "set-url", "origin", "https://example.com/x/y")
            report = attest_repository(
                root,
                expected_repository=REPOSITORY,
                expected_commit=commit,
            )
            self.assertFalse(report["ok"], report)
            self.assertIn(
                "unsupported GitHub remote host", " ".join(report["blockers"])
            )

    def test_rejects_dirty_tracked_and_untracked_files(self) -> None:
        for dirty_kind in ("tracked", "untracked"):
            with self.subTest(dirty_kind=dirty_kind):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    commit = init_git_repo(root)
                    if dirty_kind == "tracked":
                        (root / "tracked.txt").write_text("changed\n")
                    else:
                        (root / "untracked.txt").write_text("new\n")

                    report = attest_repository(
                        root,
                        expected_repository=REPOSITORY,
                        expected_commit=commit,
                    )

                    self.assertFalse(report["ok"], report)
                    self.assertTrue(report["dirty_entries"])

    def test_rejects_ignored_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit = init_git_repo(root)
            (root / ".gitignore").write_text("private.env\n")
            git(root, "add", ".gitignore")
            git(
                root,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "ignore fixture",
            )
            commit = git(root, "rev-parse", "HEAD").stdout.strip()
            (root / "private.env").write_text("SECRET=ignored-but-dirty\n")

            report = attest_repository(
                root,
                expected_repository=REPOSITORY,
                expected_commit=commit,
            )

            self.assertFalse(report["ok"], report)
            self.assertTrue(
                any("private.env" in entry for entry in report["dirty_entries"]),
                report,
            )

    def test_rejects_dirty_files_hidden_by_git_index_flags(self) -> None:
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    commit = init_git_repo(root)
                    git(root, "update-index", flag, "tracked.txt")
                    (root / "tracked.txt").write_text("hidden dirty change\n")

                    report = attest_repository(
                        root,
                        expected_repository=REPOSITORY,
                        expected_commit=commit,
                    )

                    self.assertFalse(report["ok"], report)
                    self.assertIn(
                        "direct blob comparison",
                        " ".join(report["blockers"]),
                    )
                    self.assertTrue(report["index_override_entries"])

    def test_ignores_hostile_git_environment_and_path_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "release"
            repository.mkdir()
            commit = init_git_repo(repository)
            foreign = root / "foreign"
            foreign.mkdir()
            init_git_repo(foreign, add_origin=False)
            shadow = root / "shadow"
            shadow.mkdir()
            fake_git = shadow / "git"
            fake_git.write_text("#!/bin/sh\nexit 97\n")
            fake_git.chmod(0o755)

            with mock.patch.dict(
                os.environ,
                {
                    "GIT_DIR": str(foreign / ".git"),
                    "GIT_WORK_TREE": str(foreign),
                    "PATH": str(shadow),
                },
                clear=False,
            ):
                report = attest_repository(
                    repository,
                    expected_repository=REPOSITORY,
                    expected_commit=commit,
                )

            self.assertTrue(report["ok"], report)

    def test_git_replace_ref_cannot_substitute_the_attested_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_commit = init_git_repo(root)
            (root / "tracked.txt").write_text("replacement tree\n")
            git(root, "add", "tracked.txt")
            git(
                root,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "replacement",
            )
            replacement_commit = git(root, "rev-parse", "HEAD").stdout.strip()
            git(root, "replace", original_commit, replacement_commit)
            git(root, "checkout", "--detach", original_commit)

            report = attest_repository(
                root,
                expected_repository=REPOSITORY,
                expected_commit=original_commit,
            )

            self.assertFalse(report["ok"], report)
            self.assertTrue(report["dirty_entries"], report)


class GitHubSourceAttestationTest(unittest.TestCase):
    def test_authoritative_workflow_is_read_only_and_declares_exact_job(self) -> None:
        workflow_path = (
            BUILD_IDENTITY_REPO_ROOT / AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH
        )
        workflow = workflow_path.read_text()

        self.assertIn("permissions:\n  contents: read\n", workflow)
        self.assertIn("  pre-live-build:\n", workflow)
        self.assertIn("  pre-live-producer-isolation:\n", workflow)
        self.assertIn(f"  {AUTHORITATIVE_PROVENANCE_JOB_NAME}:\n", workflow)
        build_job = workflow.split(
            "  pre-live-build:\n",
            1,
        )[1].split(
            "\n  pre-live-producer-isolation:\n",
            1,
        )[0]
        isolation_job = workflow.split(
            "  pre-live-producer-isolation:\n",
            1,
        )[1].split(
            f"\n  {AUTHORITATIVE_PROVENANCE_JOB_NAME}:\n",
            1,
        )[0]
        provenance_job = workflow.split(
            f"  {AUTHORITATIVE_PROVENANCE_JOB_NAME}:\n",
            1,
        )[1].split("\n  micromachine-macos-contracts:\n", 1)[0]
        trusted_verifier_commit = "a0020cccecaa247406263bf61dabee74d6c683a7"
        self.assertIn(
            "    if: >-\n"
            "      github.event_name == 'pull_request' &&\n"
            "      github.event.pull_request.head.repo.id == "
            "github.event.repository.id\n",
            provenance_job,
        )
        self.assertIn("      actions: read\n", provenance_job)
        self.assertIn("      contents: read\n", provenance_job)
        self.assertIn("          persist-credentials: false\n", provenance_job)
        self.assertNotIn(
            "      ROOT_DIR: ${{ runner.temp }}/voi-micromachine-runtime\n",
            workflow,
        )
        self.assertEqual(
            3,
            workflow.count(
                "      ROOT_DIR: /private/tmp/voi-micromachine-runtime\n"
            ),
        )
        job_blocks = [
            match.group(0)
            for match in re.finditer(
                r"(?ms)^  [A-Za-z0-9_-]+:\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)",
                workflow.split("jobs:\n", 1)[1],
            )
            if "\n      - uses: actions/checkout@" in match.group(0)
        ]
        pull_request_job_blocks = [
            block
            for block in job_blocks
            if "if: github.event_name == 'push'" not in block
        ]
        self.assertGreaterEqual(len(pull_request_job_blocks), 4)
        for job_block in pull_request_job_blocks:
            with self.subTest(job=job_block.split(":\n", 1)[0].strip()):
                checkout_block = job_block.split(
                    "      - uses: actions/checkout@",
                    1,
                )[1].split("\n      - ", 1)[0]
                self.assertIn(
                    "          persist-credentials: false",
                    checkout_block,
                )
        self.assertIn(
            "      - name: Build exact MicroMachine integration "
            "without credentials\n"
            "        working-directory: candidate\n",
            build_job,
        )
        self.assertIn(
            "          path: candidate\n"
            "          persist-credentials: false\n"
            "          ref: "
            "${{ github.event.pull_request.head.sha || github.sha }}\n",
            build_job,
        )
        self.assertIn(
            "      - name: Archive exact MicroMachine runtime\n",
            build_job,
        )
        self.assertIn(
            "      - name: Upload exact MicroMachine runtime\n",
            build_job,
        )
        self.assertIn(
            "          name: pre-live-build-runtime\n",
            build_job,
        )
        self.assertNotIn("GITHUB_TOKEN", build_job)
        self.assertNotIn("github.token", build_job)
        self.assertIn(
            "      - name: Verify immutable dedicated producer isolation\n"
            "        working-directory: trusted-verifier\n",
            isolation_job,
        )
        self.assertIn(
            f"      VOI_TRUSTED_VERIFIER_COMMIT: {trusted_verifier_commit}\n",
            isolation_job,
        )
        self.assertIn(
            "          path: trusted-verifier\n"
            "          persist-credentials: false\n"
            f"          ref: {trusted_verifier_commit}\n",
            isolation_job,
        )
        self.assertIn(
            "          sudo env -u GITHUB_TOKEN -u GH_TOKEN \\\n",
            isolation_job,
        )
        self.assertIn("-k dedicated_producer_uid", isolation_job)
        self.assertNotIn("github.token", isolation_job)
        self.assertNotIn("actions: read", isolation_job)
        self.assertNotIn("path: candidate", isolation_job)
        self.assertNotIn("github.event.pull_request.head.sha", isolation_job)
        self.assertIn(
            "      - pre-live-producer-isolation\n",
            provenance_job,
        )
        self.assertNotIn(
            "Build exact MicroMachine integration",
            provenance_job,
        )
        self.assertIn(
            "      - name: Download exact MicroMachine runtime\n",
            provenance_job,
        )
        self.assertIn(
            "        uses: actions/download-artifact@"
            "d3f86a106a0bac45b974a628896c90dbdf5c8093\n",
            provenance_job,
        )
        self.assertIn(
            "      - name: Restore exact MicroMachine runtime\n",
            provenance_job,
        )
        self.assertNotIn("GITHUB_WORKFLOW_REF:", provenance_job)
        self.assertNotIn("GITHUB_WORKFLOW_SHA:", provenance_job)
        self.assertIn(
            'VOI_NODE_EXECUTABLE="$(python3 -c ',
            provenance_job,
        )
        self.assertIn('      VOI_PRODUCER_UID: "65001"\n', provenance_job)
        self.assertIn('      VOI_PRODUCER_GID: "65001"\n', provenance_job)
        self.assertIn(
            "      VOI_CANDIDATE_WORKSPACE: "
            "${{ github.workspace }}/candidate\n",
            provenance_job,
        )
        self.assertIn(
            "      VOI_TRUSTED_VERIFIER_WORKSPACE: "
            "${{ github.workspace }}/trusted-verifier\n",
            provenance_job,
        )
        self.assertIn(
            f"      VOI_TRUSTED_VERIFIER_COMMIT: {trusted_verifier_commit}\n",
            provenance_job,
        )
        self.assertEqual(
            2,
            provenance_job.count(
                "      - uses: actions/checkout@"
                "11d5960a326750d5838078e36cf38b85af677262\n"
            ),
        )
        self.assertIn(
            "          path: trusted-verifier\n"
            "          persist-credentials: false\n"
            f"          ref: {trusted_verifier_commit}\n",
            provenance_job,
        )
        self.assertIn(
            "          path: candidate\n"
            "          persist-credentials: false\n"
            "          ref: "
            "${{ github.event.pull_request.head.sha || github.sha }}\n",
            provenance_job,
        )
        ownership_step = provenance_job.split(
            "      - name: Transfer verifier inputs to root ownership\n",
            1,
        )[1].split(
            "      - name: Emit canonical authenticated provenance bundle\n",
            1,
        )[0]
        self.assertIn(
            "          sudo chown -RP 0:0 \\\n"
            '            "${VOI_CANDIDATE_WORKSPACE}" \\\n'
            '            "${VOI_TRUSTED_VERIFIER_WORKSPACE}" \\\n'
            '            "${ROOT_DIR}"\n',
            ownership_step,
        )
        self.assertIn(
            "          sudo chmod 0755 \\\n"
            '            "${VOI_CANDIDATE_WORKSPACE}" \\\n'
            '            "${VOI_TRUSTED_VERIFIER_WORKSPACE}" \\\n'
            '            "${ROOT_DIR}"\n',
            ownership_step,
        )
        self.assertNotIn("-m unittest", provenance_job)
        self.assertNotIn("-k dedicated_producer_uid", provenance_job)
        emission_step = provenance_job.split(
            "      - name: Emit canonical authenticated provenance bundle\n",
            1,
        )[1].split(
            "      - name: Upload canonical authenticated provenance bundle\n",
            1,
        )[0]
        self.assertIn(
            "          printf '%s' \"${GITHUB_TOKEN}\" | "
            "sudo env -u GITHUB_TOKEN \\\n",
            emission_step,
        )
        self.assertIn(
            '          cd "${VOI_TRUSTED_VERIFIER_WORKSPACE}"\n',
            emission_step,
        )
        self.assertNotIn(
            'GITHUB_TOKEN="${GITHUB_TOKEN}"',
            emission_step,
        )
        self.assertIn(
            '            VOI_CANDIDATE_WORKSPACE="${VOI_CANDIDATE_WORKSPACE}" \\\n',
            emission_step,
        )
        self.assertIn(
            '            VOI_PRODUCER_UID="${VOI_PRODUCER_UID}" \\\n',
            emission_step,
        )
        self.assertIn(
            '            VOI_PRODUCER_GID="${VOI_PRODUCER_GID}" \\\n',
            emission_step,
        )
        self.assertIn(
            "            VOI_TRUSTED_VERIFIER_COMMIT="
            '"${VOI_TRUSTED_VERIFIER_COMMIT}" \\\n',
            emission_step,
        )
        self.assertIn(
            "            VOI_TRUSTED_VERIFIER_WORKSPACE="
            '"${VOI_TRUSTED_VERIFIER_WORKSPACE}" \\\n',
            emission_step,
        )
        self.assertIn(
            '            "${PYTHON_EXECUTABLE}" -B -m '
            "starcraft_commander.micromachine_pre_live_provenance \\\n",
            emission_step,
        )
        self.assertIn(
            '          sudo chown "$(id -u):$(id -g)" \\\n',
            emission_step,
        )
        self.assertIn(
            "      - name: Restore checkout ownership to the runner\n"
            "        if: always()\n"
            "        run: >-\n"
            '          sudo chown -RP "$(id -u):$(id -g)"\n'
            '          "${GITHUB_WORKSPACE}"\n',
            provenance_job,
        )
        self.assertIn(
            "  micromachine-macos-contracts:\n    if: github.event_name == 'push'\n",
            workflow,
        )
        self.assertNotIn("contents: write", workflow)

    def attest(self, adapter: FakeGitHubAdapter) -> dict[str, object]:
        return attest_github_source(
            adapter,
            repository=REPOSITORY,
            expected_repository_id=AUTHORITATIVE_REPOSITORY_ID,
            issue_number=138,
            pull_number=137,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            job_id=JOB_ID,
            artifact_id=ARTIFACT_ID,
            expected_head_sha=HEAD_SHA,
        )

    def test_preserves_server_ids_states_and_download_digest(self) -> None:
        adapter = FakeGitHubAdapter()

        report = self.attest(adapter)

        self.assertTrue(report["ok"], report)
        self.assertEqual(
            {
                "repository_id": AUTHORITATIVE_REPOSITORY_ID,
                "issue_id": 2,
                "issue_number": 138,
                "pull_request_id": 3,
                "pull_number": 137,
                "workflow_run_id": RUN_ID,
                "workflow_id": WORKFLOW_ID,
                "run_attempt": RUN_ATTEMPT,
                "job_id": JOB_ID,
                "artifact_database_id": ARTIFACT_ID,
            },
            report["source_ids"],
        )
        self.assertEqual("open", report["issue_state"])
        self.assertEqual("success", report["run_conclusion"])
        self.assertEqual(
            hashlib.sha256(adapter.artifact_bytes).hexdigest(),
            report["artifact_sha256"],
        )

    def test_accepts_github_one_file_artifact_delivery_wrapper(self) -> None:
        adapter = FakeGitHubAdapter()
        wrapper = io.BytesIO()
        with zipfile.ZipFile(wrapper, mode="w") as archive:
            archive.writestr(
                GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
                adapter.artifact_bytes,
            )
        adapter.artifact_bytes = wrapper.getvalue()
        adapter.artifact["digest"] = (
            "sha256:" + hashlib.sha256(adapter.artifact_bytes).hexdigest()
        )

        report = self.attest(adapter)

        self.assertTrue(report["ok"], report)
        self.assertEqual(
            "github_artifact_zip",
            report["artifact_bundle"]["delivery"]["kind"],
        )

    def test_threads_node_descriptor_into_downloaded_bundle_replay(
        self,
    ) -> None:
        adapter = FakeGitHubAdapter()
        descriptor = "/dev/fd/123"

        with mock.patch.object(
            provenance_module,
            "verify_downloaded_pre_live_artifact",
            wraps=provenance_module.verify_downloaded_pre_live_artifact,
        ) as verifier:
            report = attest_github_source(
                adapter,
                repository=REPOSITORY,
                expected_repository_id=AUTHORITATIVE_REPOSITORY_ID,
                issue_number=138,
                pull_number=137,
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                job_id=JOB_ID,
                artifact_id=ARTIFACT_ID,
                expected_head_sha=HEAD_SHA,
                node_executable=descriptor,
            )

        self.assertTrue(report["ok"], report)
        self.assertEqual(
            descriptor,
            verifier.call_args.kwargs["node_executable"],
        )

    def test_resolves_current_actions_job_context_without_artifact_claims(
        self,
    ) -> None:
        adapter = FakeGitHubAdapter()

        report = attest_github_actions_emission_context(
            adapter,
            repository=REPOSITORY,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            expected_head_sha=HEAD_SHA,
            workflow_ref=WORKFLOW_REF,
            workflow_sha=WORKFLOW_SHA,
        )

        self.assertTrue(report["ok"], report)
        self.assertEqual(JOB_ID, report["job_id"])
        self.assertEqual(WORKFLOW_ID, report["workflow_id"])

        adapter.jobs.append(dict(adapter.jobs[0]))
        ambiguous = attest_github_actions_emission_context(
            adapter,
            repository=REPOSITORY,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            expected_head_sha=HEAD_SHA,
            workflow_ref=WORKFLOW_REF,
            workflow_sha=WORKFLOW_SHA,
        )
        self.assertFalse(ambiguous["ok"], ambiguous)

    def test_accepts_current_run_during_workflow_list_eventual_consistency(
        self,
    ) -> None:
        adapter = FakeGitHubAdapter()
        adapter.workflow_run["status"] = "in_progress"
        adapter.workflow_run["conclusion"] = None
        adapter.jobs[0]["status"] = "in_progress"
        adapter.jobs[0]["conclusion"] = None
        adapter.job["status"] = "in_progress"
        adapter.job["conclusion"] = None
        adapter.workflow_runs.clear()

        report = attest_github_actions_emission_context(
            adapter,
            repository=REPOSITORY,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            expected_head_sha=HEAD_SHA,
            workflow_ref=WORKFLOW_REF,
            workflow_sha=WORKFLOW_SHA,
        )

        self.assertTrue(report["ok"], report)
        self.assertEqual(RUN_ID, report["run_id"])
        self.assertEqual("in_progress", report["job_status"])

    def test_accepts_completed_source_when_workflow_list_lags(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.workflow_runs.clear()

        report = self.attest(adapter)

        self.assertTrue(report["ok"], report)
        self.assertEqual(RUN_ID, report["source_ids"]["workflow_run_id"])

    def test_rejects_tampered_runner_workflow_identity(self) -> None:
        mutations = {
            "repository": (
                f"attacker/example/{WORKFLOW_PATH}@refs/pull/137/merge",
                WORKFLOW_SHA,
            ),
            "path": (
                f"{REPOSITORY}/.github/workflows/other.yml@refs/pull/137/merge",
                WORKFLOW_SHA,
            ),
            "ref": (
                f"{REPOSITORY}/{WORKFLOW_PATH}@refs/tags/forged",
                WORKFLOW_SHA,
            ),
            "sha": (WORKFLOW_REF, "D" * 40),
        }
        for name, (workflow_ref, workflow_sha) in mutations.items():
            with self.subTest(name=name):
                report = attest_github_actions_emission_context(
                    FakeGitHubAdapter(),
                    repository=REPOSITORY,
                    run_id=RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    expected_head_sha=HEAD_SHA,
                    workflow_ref=workflow_ref,
                    workflow_sha=workflow_sha,
                )

                self.assertFalse(report["ok"], report)
                self.assertRegex(
                    " ".join(report["blockers"]),
                    r"workflow(?: execution|_sha)",
                )

    def test_rejects_downloaded_bundle_with_unbound_workflow_identity(
        self,
    ) -> None:
        mutations = {
            "unrelated branch": (
                f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/unrelated",
                WORKFLOW_SHA,
            ),
            "other pull request": (
                f"{REPOSITORY}/{WORKFLOW_PATH}@refs/pull/999/merge",
                WORKFLOW_SHA,
            ),
            "different valid sha": (WORKFLOW_REF, "d" * 40),
        }
        for name, (workflow_ref, workflow_sha) in mutations.items():
            with self.subTest(name=name):
                adapter = FakeGitHubAdapter()
                adapter.artifact_bytes = make_source_artifact_bundle(
                    HEAD_SHA,
                    workflow_ref=workflow_ref,
                    workflow_sha=workflow_sha,
                )
                adapter.artifact["digest"] = (
                    "sha256:"
                    + hashlib.sha256(adapter.artifact_bytes).hexdigest()
                )

                report = self.attest(adapter)

                self.assertFalse(report["ok"], report)
                self.assertRegex(
                    " ".join(report["blockers"]),
                    r"workflow (?:execution|SHA)",
                )

    def test_rejects_unrelated_issue_without_pr_closure_binding(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.pull_request["body"] = "This text does not close #138."
        adapter.closing_issues.clear()

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn(
            "closingIssuesReferences",
            " ".join(report["blockers"]),
        )

    def test_accepts_exactly_one_dynamic_closing_issue(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.issue.update({"id": 143, "number": 143})
        adapter.closing_issues[0].update(
            {"databaseId": 143, "number": 143}
        )
        adapter.artifact_bytes = make_source_artifact_bundle(
            HEAD_SHA,
            issue_id=143,
            issue_number=143,
        )
        adapter.artifact["digest"] = (
            "sha256:" + hashlib.sha256(adapter.artifact_bytes).hexdigest()
        )

        report = attest_github_source(
            adapter,
            repository=REPOSITORY,
            expected_repository_id=AUTHORITATIVE_REPOSITORY_ID,
            issue_number=143,
            pull_number=137,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            job_id=JOB_ID,
            artifact_id=ARTIFACT_ID,
            expected_head_sha=HEAD_SHA,
        )

        self.assertTrue(report["ok"], report)
        self.assertEqual(143, report["source_ids"]["issue_number"])

    def test_rejects_artifact_rebound_to_a_different_closing_issue(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.issue.update({"id": 143, "number": 143})
        adapter.closing_issues[0].update(
            {"databaseId": 143, "number": 143}
        )

        report = attest_github_source(
            adapter,
            repository=REPOSITORY,
            expected_repository_id=AUTHORITATIVE_REPOSITORY_ID,
            issue_number=143,
            pull_number=137,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            job_id=JOB_ID,
            artifact_id=ARTIFACT_ID,
            expected_head_sha=HEAD_SHA,
        )

        self.assertFalse(report["ok"], report)
        self.assertIn(
            "authority.closing_issue",
            " ".join(report["blockers"]),
        )

    def test_rejects_multiple_foreign_or_mismatched_closing_issues(self) -> None:
        mutations = {
            "multiple": lambda adapter: adapter.closing_issues.append(
                dict(adapter.closing_issues[0])
            ),
            "foreign repository id": lambda adapter: adapter.closing_issues[0][
                "repository"
            ].update({"databaseId": 999}),
            "foreign repository name": lambda adapter: adapter.closing_issues[0][
                "repository"
            ].update({"nameWithOwner": "attacker/other"}),
            "mismatched issue": lambda adapter: adapter.closing_issues[0].update(
                {"databaseId": 77, "number": 77}
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                adapter = FakeGitHubAdapter()
                mutate(adapter)

                report = self.attest(adapter)

                self.assertFalse(report["ok"], report)
                self.assertIn(
                    "closing",
                    " ".join(report["blockers"]).casefold(),
                )

    def test_accepts_server_closing_relationship_without_body_keyword(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.pull_request["body"] = "No user-authored closing keyword."

        report = self.attest(adapter)

        self.assertTrue(report["ok"], report)

    def test_rejects_non_main_base_and_non_descendant_head(self) -> None:
        mutations = {
            "base branch": lambda adapter: adapter.pull_request["base"].update(
                {"ref": "release"}
            ),
            "base repository": lambda adapter: adapter.pull_request["base"][
                "repo"
            ].update({"id": 999}),
            "base ancestry": lambda adapter: adapter.comparison.update(
                {"status": "diverged"}
            ),
            "behind main": lambda adapter: adapter.comparison.update(
                {"behind_by": 1}
            ),
            "merge base": lambda adapter: adapter.comparison[
                "merge_base_commit"
            ].update({"sha": "d" * 40}),
            "head termination": lambda adapter: adapter.comparison[
                "commits"
            ][-1].update({"sha": "d" * 40}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                adapter = FakeGitHubAdapter()
                mutate(adapter)
                report = self.attest(adapter)
                self.assertFalse(report["ok"], report)

    def test_rejects_stale_success_when_newer_applicable_run_exists(self) -> None:
        adapter = FakeGitHubAdapter()
        newer = dict(adapter.workflow_run)
        newer.update(
            {
                "id": RUN_ID + 1,
                "run_number": adapter.workflow_run["run_number"] + 1,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "failure",
            }
        )
        adapter.workflow_runs.append(newer)
        adapter.workflow_run_details[RUN_ID + 1] = newer

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn("stale", " ".join(report["blockers"]))
        self.assertEqual(0, adapter.download_calls)

    def test_hydrates_newer_run_when_list_record_omits_pull_requests(self) -> None:
        adapter = FakeGitHubAdapter()
        newer = dict(adapter.workflow_run)
        newer.update(
            {
                "id": RUN_ID + 1,
                "run_number": adapter.workflow_run["run_number"] + 1,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "failure",
            }
        )
        summary = dict(newer)
        summary["pull_requests"] = []
        adapter.workflow_runs.append(summary)
        adapter.workflow_run_details[RUN_ID + 1] = newer

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn("stale", " ".join(report["blockers"]))
        self.assertEqual(0, adapter.download_calls)

    def test_rejects_listed_run_that_fails_direct_pr_binding(self) -> None:
        adapter = FakeGitHubAdapter()
        newer = dict(adapter.workflow_run)
        newer.update(
            {
                "id": RUN_ID + 1,
                "run_number": adapter.workflow_run["run_number"] + 1,
                "run_attempt": 1,
            }
        )
        summary = dict(newer)
        summary["pull_requests"] = []
        hydrated = dict(newer)
        hydrated["pull_requests"] = []
        adapter.workflow_runs.append(summary)
        adapter.workflow_run_details[RUN_ID + 1] = hydrated

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn(
            "failed direct candidate binding",
            " ".join(report["blockers"]),
        )
        self.assertEqual(0, adapter.download_calls)

    def test_rejects_hydrated_run_identity_mismatch(self) -> None:
        adapter = FakeGitHubAdapter()
        newer = dict(adapter.workflow_run)
        newer.update(
            {
                "id": RUN_ID + 1,
                "run_number": adapter.workflow_run["run_number"] + 1,
                "run_attempt": 1,
            }
        )
        summary = dict(newer)
        summary["pull_requests"] = []
        hydrated = dict(newer)
        hydrated["run_number"] = newer["run_number"] + 1
        adapter.workflow_runs.append(summary)
        adapter.workflow_run_details[RUN_ID + 1] = hydrated

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn(
            "hydration identity mismatch",
            " ".join(report["blockers"]),
        )
        self.assertEqual(0, adapter.download_calls)

    def test_rejects_artifact_without_exclusive_selected_job_window(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.jobs.append(
            {
                "id": JOB_ID + 1,
                "run_id": RUN_ID,
                "run_attempt": RUN_ATTEMPT,
                "name": "overlapping-job",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-07-30T00:04:00Z",
                "completed_at": "2026-07-30T00:06:00Z",
            }
        )

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn("overlaps another workflow job", " ".join(report["blockers"]))
        self.assertEqual(0, adapter.download_calls)

    def test_rejects_stale_attempt_job_and_artifact_membership(self) -> None:
        mutations = {
            "run attempt": lambda adapter: adapter.attempt.update(
                {"run_attempt": RUN_ATTEMPT - 1}
            ),
            "job attempt": lambda adapter: adapter.job.update(
                {"run_attempt": RUN_ATTEMPT - 1}
            ),
            "job listing": lambda adapter: adapter.jobs.clear(),
            "artifact listing": lambda adapter: adapter.artifacts.clear(),
            "artifact run": lambda adapter: adapter.artifact["workflow_run"].update(
                {"id": RUN_ID - 1}
            ),
            "artifact from prior attempt": lambda adapter: adapter.artifact.update(
                {
                    "created_at": "2026-07-29T23:50:00Z",
                    "updated_at": "2026-07-29T23:50:00Z",
                }
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                adapter = FakeGitHubAdapter()
                mutate(adapter)
                report = self.attest(adapter)
                self.assertFalse(report["ok"], report)

    def test_rejects_wrong_head_sha_at_each_server_binding(self) -> None:
        mutations = {
            "pull": lambda adapter: adapter.pull_request["head"].update(
                {"sha": "b" * 40}
            ),
            "run": lambda adapter: adapter.workflow_run.update({"head_sha": "b" * 40}),
            "attempt": lambda adapter: adapter.attempt.update({"head_sha": "b" * 40}),
            "artifact": lambda adapter: adapter.artifact["workflow_run"].update(
                {"head_sha": "b" * 40}
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                adapter = FakeGitHubAdapter()
                mutate(adapter)
                report = self.attest(adapter)
                self.assertFalse(report["ok"], report)

    def test_rejects_download_digest_mismatch_not_caller_checksum(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.artifact_bytes = b"tampered download"

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn("artifact digest mismatch", " ".join(report["blockers"]))

    def test_requires_canonical_server_artifact_digest(self) -> None:
        for digest in (None, "", "sha256:" + "A" * 64, "0" * 64):
            with self.subTest(digest=digest):
                adapter = FakeGitHubAdapter()
                if digest is None:
                    adapter.artifact.pop("digest")
                else:
                    adapter.artifact["digest"] = digest

                report = self.attest(adapter)

                self.assertFalse(report["ok"], report)
                self.assertIn(
                    "digest is missing or not canonical",
                    " ".join(report["blockers"]),
                )
                self.assertEqual(0, adapter.download_calls)

    def test_server_failure_and_server_conclusion_fail_closed(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.fail_at = "job"
        report = self.attest(adapter)
        self.assertFalse(report["ok"], report)
        self.assertIn("fixture API failure", " ".join(report["blockers"]))

        adapter = FakeGitHubAdapter()
        adapter.workflow_run["conclusion"] = "failure"
        report = self.attest(adapter)
        self.assertFalse(report["ok"], report)
        self.assertIn("did not complete successfully", " ".join(report["blockers"]))
        self.assertEqual(0, adapter.download_calls)

    def test_rejects_repository_workflow_job_and_lifecycle_mismatch(self) -> None:
        mutations = {
            "repository id": lambda adapter: adapter.repository.update({"id": 9}),
            "issue state": lambda adapter: adapter.issue.update({"state": "closed"}),
            "pull state": lambda adapter: adapter.pull_request.update(
                {"state": "closed"}
            ),
            "pull repository id": lambda adapter: adapter.pull_request["head"][
                "repo"
            ].update({"id": 9}),
            "run repository id": lambda adapter: adapter.workflow_run[
                "head_repository"
            ].update({"id": 9}),
            "workflow registry": lambda adapter: adapter.workflow.update(
                {"path": ".github/workflows/other.yml"}
            ),
            "inactive workflow": lambda adapter: adapter.workflow.update(
                {"state": "disabled_manually"}
            ),
            "job head": lambda adapter: adapter.job.update({"head_sha": "b" * 40}),
            "job identity": lambda adapter: adapter.job.update({"name": ""}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                adapter = FakeGitHubAdapter()
                mutate(adapter)
                report = self.attest(adapter)
                self.assertFalse(report["ok"], report)

    def test_rejects_alternate_active_workflow_even_when_records_agree(self) -> None:
        adapter = FakeGitHubAdapter()
        alternate = ".github/workflows/attacker-selected.yml"
        adapter.workflow_run["path"] = alternate
        adapter.attempt["path"] = alternate
        adapter.workflow["path"] = alternate

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn(
            AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH,
            " ".join(report["blockers"]),
        )

    def test_rejects_unrelated_pull_request_and_ambiguous_named_evidence(
        self,
    ) -> None:
        mutations = {
            "workflow event": lambda adapter: adapter.workflow_run.update(
                {"event": "push"}
            ),
            "run pull request": lambda adapter: adapter.workflow_run.update(
                {"pull_requests": []}
            ),
            "attempt pull request": lambda adapter: adapter.attempt["pull_requests"][
                0
            ].update({"number": 999}),
            "head ref": lambda adapter: adapter.pull_request["head"].update(
                {"ref": "unrelated-branch"}
            ),
            "multiple jobs": lambda adapter: adapter.jobs.append(
                {
                    "id": JOB_ID + 1,
                    "run_id": RUN_ID,
                    "run_attempt": RUN_ATTEMPT,
                    "name": AUTHORITATIVE_PROVENANCE_JOB_NAME,
                }
            ),
            "multiple artifacts": lambda adapter: adapter.artifacts.append(
                {"id": ARTIFACT_ID + 1, "name": "pre-live"}
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                adapter = FakeGitHubAdapter()
                mutate(adapter)
                report = self.attest(adapter)
                self.assertFalse(report["ok"], report)

    def test_rejects_internally_valid_bundle_with_wrong_pr_authority(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.artifact_bytes = make_source_artifact_bundle(
            HEAD_SHA,
            pull_number=999,
        )
        adapter.artifact["digest"] = (
            "sha256:" + hashlib.sha256(adapter.artifact_bytes).hexdigest()
        )

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn(
            "authority.pull_request.number mismatch",
            " ".join(report["blockers"]),
        )

    def test_allows_unrelated_parallel_jobs_and_artifacts(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.jobs.extend(
            [
                {
                    "id": JOB_ID + 1,
                    "run_id": RUN_ID,
                    "run_attempt": RUN_ATTEMPT,
                    "name": "unit-contracts",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-07-29T23:50:00Z",
                    "completed_at": "2026-07-30T00:00:30Z",
                },
                {
                    "id": JOB_ID + 2,
                    "run_id": RUN_ID,
                    "run_attempt": RUN_ATTEMPT,
                    "name": "micromachine-macos-contracts",
                    "status": "completed",
                    "conclusion": "skipped",
                    "started_at": None,
                    "completed_at": None,
                },
            ]
        )
        adapter.artifacts.append(
            {
                "id": ARTIFACT_ID + 1,
                "name": "micromachine-build-identity",
            }
        )

        report = self.attest(adapter)

        self.assertTrue(report["ok"], report)

    def test_rejects_producer_provenance_outside_selected_job(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.artifact_bytes = make_source_artifact_bundle(
            HEAD_SHA,
            producer_started_at="2026-07-30T00:00:00Z",
            producer_ended_at="2026-07-30T00:00:01Z",
        )
        adapter.artifact["digest"] = (
            "sha256:" + hashlib.sha256(adapter.artifact_bytes).hexdigest()
        )

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn("outside", " ".join(report["blockers"]))


class StdlibGitHubRESTAdapterTest(unittest.TestCase):
    def test_reads_all_required_rest_resources_and_artifact_bytes(self) -> None:
        requested: list[tuple[str, str | None, str, bytes | None]] = []
        replay_ref = AUTHORITATIVE_REPLAY_REF_PREFIX + ("1" * 64)
        replay_record = {
            "ref": replay_ref,
            "object": {"type": "commit", "sha": HEAD_SHA},
        }
        workflow_reference_record = {
            "ref": "refs/pull/137/merge",
            "object": {"type": "commit", "sha": WORKFLOW_SHA},
        }
        rulesets, ruleset_details = make_replay_ruleset_fixtures()
        payloads: dict[str, object] = {
            f"/repos/{REPOSITORY}": {"id": 1},
            f"/repos/{REPOSITORY}/issues/138": {"id": 2},
            f"/repos/{REPOSITORY}/pulls/137": {"id": 3},
            f"/repos/{REPOSITORY}/compare/{BASE_SHA}...{HEAD_SHA}": {
                "status": "ahead"
            },
            f"/repos/{REPOSITORY}/actions/runs/{RUN_ID}": {"id": RUN_ID},
            f"/repos/{REPOSITORY}/actions/workflows/{WORKFLOW_ID}": {"id": WORKFLOW_ID},
            f"/repos/{REPOSITORY}/actions/workflows/{WORKFLOW_ID}/runs": {
                "total_count": 1,
                "workflow_runs": [{"id": RUN_ID}],
            },
            (f"/repos/{REPOSITORY}/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}"): {
                "id": RUN_ID,
                "run_attempt": RUN_ATTEMPT,
            },
            (
                f"/repos/{REPOSITORY}/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}/jobs"
            ): {"total_count": 1, "jobs": [{"id": JOB_ID}]},
            f"/repos/{REPOSITORY}/actions/jobs/{JOB_ID}": {"id": JOB_ID},
            f"/repos/{REPOSITORY}/actions/runs/{RUN_ID}/artifacts": {
                "total_count": 1,
                "artifacts": [{"id": ARTIFACT_ID}],
            },
            f"/repos/{REPOSITORY}/actions/artifacts/{ARTIFACT_ID}": {"id": ARTIFACT_ID},
            (
                f"/repos/{REPOSITORY}/git/ref/{replay_ref.removeprefix('refs/')}"
            ): replay_record,
            (
                f"/repos/{REPOSITORY}/git/ref/pull/137/merge"
            ): workflow_reference_record,
            f"/repos/{REPOSITORY}/rulesets": rulesets,
            f"/repos/{REPOSITORY}/rulesets/501": ruleset_details[501],
            f"/repos/{REPOSITORY}/rulesets/502": ruleset_details[502],
        }

        def urlopen(request: object, *, timeout: float) -> FakeHTTPResponse:
            url = request.full_url
            parsed = urllib.parse.urlsplit(url)
            requested.append(
                (
                    url,
                    request.get_header("Authorization"),
                    request.get_method(),
                    request.data,
                )
            )
            if parsed.path.endswith(f"/artifacts/{ARTIFACT_ID}/zip"):
                return FakeHTTPResponse(b"artifact zip")
            if request.get_method() == "POST":
                if parsed.path == "/graphql":
                    graphql_payload = {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "closingIssuesReferences": {
                                        "nodes": [
                                            {
                                                "databaseId": 2,
                                                "number": 138,
                                                "repository": {
                                                    "databaseId": (
                                                        AUTHORITATIVE_REPOSITORY_ID
                                                    ),
                                                    "nameWithOwner": REPOSITORY,
                                                },
                                            }
                                        ],
                                        "pageInfo": {"hasNextPage": False},
                                    }
                                }
                            }
                        }
                    }
                    return FakeHTTPResponse(
                        json.dumps(graphql_payload).encode()
                    )
                self.assertEqual(
                    {"ref": replay_ref, "sha": HEAD_SHA},
                    json.loads(request.data),
                )
                return FakeHTTPResponse(json.dumps(replay_record).encode())
            return FakeHTTPResponse(json.dumps(payloads[parsed.path]).encode())

        adapter = StdlibGitHubRESTAdapter(
            token="fixture-token",
            urlopen=urlopen,
        )

        self.assertEqual(1, adapter.get_repository(REPOSITORY)["id"])
        self.assertEqual(2, adapter.get_issue(REPOSITORY, 138)["id"])
        self.assertEqual(3, adapter.get_pull_request(REPOSITORY, 137)["id"])
        self.assertEqual(
            138,
            adapter.list_pull_request_closing_issues(REPOSITORY, 137)[0][
                "number"
            ],
        )
        self.assertEqual(
            "ahead",
            adapter.compare_commits(
                REPOSITORY,
                base=BASE_SHA,
                head=HEAD_SHA,
            )["status"],
        )
        self.assertEqual(
            RUN_ID,
            adapter.get_workflow_run(REPOSITORY, RUN_ID)["id"],
        )
        self.assertEqual(
            WORKFLOW_ID,
            adapter.get_workflow(REPOSITORY, WORKFLOW_ID)["id"],
        )
        self.assertEqual(
            RUN_ID,
            adapter.list_workflow_runs(
                REPOSITORY,
                WORKFLOW_ID,
                branch="issue-138-authenticated-prelive-provenance",
                event="pull_request",
            )[0]["id"],
        )
        self.assertEqual(
            RUN_ATTEMPT,
            adapter.get_workflow_run_attempt(
                REPOSITORY,
                RUN_ID,
                RUN_ATTEMPT,
            )["run_attempt"],
        )
        self.assertEqual(
            JOB_ID,
            adapter.list_workflow_run_attempt_jobs(
                REPOSITORY,
                RUN_ID,
                RUN_ATTEMPT,
            )[0]["id"],
        )
        self.assertEqual(JOB_ID, adapter.get_job(REPOSITORY, JOB_ID)["id"])
        self.assertEqual(
            ARTIFACT_ID,
            adapter.list_workflow_run_artifacts(
                REPOSITORY,
                RUN_ID,
            )[0]["id"],
        )
        self.assertEqual(
            ARTIFACT_ID,
            adapter.get_artifact(REPOSITORY, ARTIFACT_ID)["id"],
        )
        self.assertEqual(
            b"artifact zip",
            adapter.download_artifact(REPOSITORY, ARTIFACT_ID),
        )
        self.assertEqual(
            replay_record,
            adapter.create_git_reference(
                REPOSITORY,
                ref=replay_ref,
                sha=HEAD_SHA,
            ),
        )
        self.assertEqual(
            replay_record,
            adapter.get_git_reference(REPOSITORY, ref=replay_ref),
        )
        self.assertEqual(
            workflow_reference_record,
            adapter.get_git_reference(
                REPOSITORY,
                ref="refs/pull/137/merge",
            ),
        )
        self.assertEqual(
            rulesets,
            adapter.list_repository_rulesets(REPOSITORY),
        )
        self.assertEqual(
            ruleset_details[501],
            adapter.get_repository_ruleset(REPOSITORY, 501),
        )
        self.assertEqual(
            ruleset_details[502],
            adapter.get_repository_ruleset(REPOSITORY, 502),
        )
        self.assertEqual(20, len(requested))
        self.assertTrue(
            all(header == "Bearer fixture-token" for _, header, _, _ in requested)
        )


class BuildBindingTest(unittest.TestCase):
    def test_cross_runner_artifact_handoff_requires_identical_candidate_layout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            dir=BUILD_IDENTITY_REPO_ROOT,
        ) as directory:
            root = Path(directory)
            fixture_root = root / "runner-workspace"
            fixture = make_build_fixture(fixture_root)
            repository = fixture["repository"]
            commit = fixture["repository_commit"]
            config = fixture["config"]
            archive_path = root / "pre-live-build-runtime.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(
                    config.micromachine_dir,
                    arcname="MicroMachine",
                )
                archive.add(
                    config.s2client_dir,
                    arcname="s2client-api",
                )
            shutil.rmtree(config.micromachine_dir)
            shutil.rmtree(config.s2client_dir)
            with tarfile.open(archive_path, "r:gz") as archive:
                archive.extractall(fixture_root)

            accepted = attest_build_binding(
                fixture["report_path"],
                repository_dir=repository,
                expected_repository_commit=commit,
                expected_build_dir=config.micromachine_build_dir,
                command_runner=passing_ctest,
            )

            relocated_repository = root / "relocated" / "candidate"
            shutil.copytree(repository, relocated_repository)
            rejected = attest_build_binding(
                fixture["report_path"],
                repository_dir=relocated_repository,
                expected_repository_commit=commit,
                expected_build_dir=config.micromachine_build_dir,
                command_runner=passing_ctest,
            )

        self.assertTrue(accepted["ok"], accepted)
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn(
            "repository build inputs",
            " ".join(rejected["blockers"]),
        )

    def test_rejects_coherent_build_from_non_authoritative_upstream_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            micromachine_dir = fixture["config"].micromachine_dir
            (micromachine_dir / "new-upstream-state.txt").write_text("new\n")
            git(micromachine_dir, "add", "new-upstream-state.txt")
            git(
                micromachine_dir,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "untrusted upstream",
            )
            untrusted_commit = git(
                micromachine_dir,
                "rev-parse",
                "HEAD",
            ).stdout.strip()
            refresh_build_fixture(
                fixture,
                replace(
                    fixture["config"],
                    micromachine_commit=untrusted_commit,
                ),
            )

            report = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=passing_ctest,
            )

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "repository-authoritative",
                " ".join(report["blockers"]),
            )

    def test_rebuilds_supported_identity_and_exact_required_ctests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))

            report = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=passing_ctest,
            )

            self.assertTrue(report["ok"], report)
            self.assertEqual(fixture["report"]["identity"], report["current_identity"])
            self.assertEqual(REQUIRED_CTEST_COUNT, report["ctest"]["passed"])
            self.assertEqual(REQUIRED_CTEST_COUNT, report["ctest"]["total"])
            self.assertEqual(
                fixture["report"]["checksums"]["native_test_registry_sha256"],
                report["ctest"]["registry_sha256"],
            )

    def test_rejects_missing_atomic_telemetry_artifact_before_ctest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            atomic_test = (
                fixture["config"].micromachine_build_dir
                / "bin"
                / MICROMACHINE_REQUIRED_NATIVE_TESTS["voi_atomic_telemetry"]
            )
            atomic_test.unlink()
            ctest_calls: list[object] = []

            def forbidden_ctest(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                ctest_calls.append(args)
                return passing_ctest(*args, **kwargs)

            report = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=forbidden_ctest,
            )

            self.assertFalse(report["ok"], report)
            self.assertEqual([], ctest_calls)
            self.assertIn(
                "missing_or_invalid_native_test",
                " ".join(report["blockers"]),
            )
            self.assertIn("voi_atomic_telemetry", " ".join(report["blockers"]))

    def test_rejects_independently_renamed_atomic_telemetry_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            atomic_test = (
                fixture["config"].micromachine_build_dir
                / "bin"
                / MICROMACHINE_REQUIRED_NATIVE_TESTS["voi_atomic_telemetry"]
            )
            atomic_test.rename(
                atomic_test.with_name("voi_atomic_telemetry_test.renamed")
            )
            ctest_calls: list[object] = []

            def forbidden_ctest(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                ctest_calls.append(args)
                return passing_ctest(*args, **kwargs)

            report = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=forbidden_ctest,
            )

            self.assertFalse(report["ok"], report)
            self.assertEqual([], ctest_calls)
            self.assertIn(
                "missing_or_invalid_native_test",
                " ".join(report["blockers"]),
            )
            self.assertIn("voi_atomic_telemetry", " ".join(report["blockers"]))

    def test_rejects_missing_corrupt_and_wrong_schema_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            repository_commit = init_git_repo(repository)
            missing = attest_build_binding(
                root / "voi_build_identity.json",
                repository_dir=repository,
                expected_repository_commit=repository_commit,
                command_runner=passing_ctest,
            )
            self.assertFalse(missing["ok"], missing)

            corrupt_path = root / "voi_build_identity.json"
            corrupt_path.write_text("{")
            corrupt = attest_build_binding(
                corrupt_path,
                repository_dir=repository,
                expected_repository_commit=repository_commit,
                command_runner=passing_ctest,
            )
            self.assertFalse(corrupt["ok"], corrupt)

        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            report_payload = dict(fixture["report"])
            report_payload["schema_version"] = (
                MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION - 1
            )
            fixture["report_path"].write_text(json.dumps(report_payload))
            ctest_calls: list[object] = []

            def forbidden_ctest(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                ctest_calls.append(args)
                return passing_ctest(*args, **kwargs)

            wrong_schema = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=forbidden_ctest,
            )
            self.assertFalse(wrong_schema["ok"], wrong_schema)
            self.assertEqual([], ctest_calls)

        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            report_payload = dict(fixture["report"])
            report_payload["schema_version"] = float(
                MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION
            )
            fixture["report_path"].write_text(json.dumps(report_payload))
            ctest_calls = []

            float_schema = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=forbidden_ctest,
            )
            self.assertFalse(float_schema["ok"], float_schema)
            self.assertEqual([], ctest_calls)
            self.assertIn(
                "unsupported build report schema",
                " ".join(float_schema["blockers"]),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            linked_report = root / "voi_build_identity.json"
            linked_report.symlink_to(fixture["report_path"])
            ctest_calls = []
            symlinked = attest_build_binding(
                linked_report,
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=forbidden_ctest,
            )
            self.assertFalse(symlinked["ok"], symlinked)
            self.assertIn("symlink", " ".join(symlinked["blockers"]))
            self.assertEqual([], ctest_calls)

    def test_arbitrary_nonempty_or_forged_identity_does_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            report_payload = dict(fixture["report"])
            report_payload["identity"] = "sha256:" + "f" * 64
            report_payload["ok"] = True
            fixture["report_path"].write_text(json.dumps(report_payload))

            result = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=passing_ctest,
            )

            self.assertFalse(result["ok"], result)
            self.assertIn("stale build identity", " ".join(result["blockers"]))

    def test_rejects_failed_report_and_report_replacement_during_ctest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            failed = dict(fixture["report"])
            failed["ok"] = False
            failed["failures"] = ["fixture failure"]
            fixture["report_path"].write_text(json.dumps(failed))
            calls: list[object] = []

            def forbidden(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                calls.append(args)
                return passing_ctest(*args, **kwargs)

            result = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=forbidden,
            )

            self.assertFalse(result["ok"], result)
            self.assertEqual([], calls)

            self.assertIn("not accepted", " ".join(result["blockers"]))

        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))

            def replace_report(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                if "--show-only=json-v1" in args[0]:
                    return passing_ctest(*args, **kwargs)
                replacement = dict(fixture["report"])
                replacement["ok"] = False
                replacement["failures"] = ["replaced during CTest"]
                fixture["report_path"].write_text(json.dumps(replacement))
                return passing_ctest(*args, **kwargs)

            result = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=replace_report,
            )

            self.assertFalse(result["ok"], result)
            self.assertIn(
                "build report changed during",
                " ".join(result["blockers"]),
            )

    def test_rejects_binary_executable_embedded_and_source_attestation_changes(
        self,
    ) -> None:
        def mutate_binary(fixture: dict[str, Any]) -> None:
            fixture["config"].binary_path.write_text("#!/bin/sh\nexit 7\n")
            fixture["config"].binary_path.chmod(0o755)

        def mutate_mode(fixture: dict[str, Any]) -> None:
            fixture["config"].binary_path.chmod(0o644)

        def mutate_embedded(fixture: dict[str, Any]) -> None:
            fixture["config"].embedded_build_identity_header_path.write_text(
                '#define VOI_BUILD_INPUT_IDENTITY "sha256:forged"\n'
            )

        def mutate_attestation(fixture: dict[str, Any]) -> None:
            attestation_path = fixture["config"].source_attestation_path
            payload = json.loads(attestation_path.read_text())
            payload["micromachine_commit"] = "f" * 40
            attestation_path.write_text(json.dumps(payload))

        mutations = {
            "binary": mutate_binary,
            "executable bit": mutate_mode,
            "embedded identity": mutate_embedded,
            "source attestation": mutate_attestation,
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = make_build_fixture(Path(directory))
                    mutate(fixture)
                    report = attest_build_binding(
                        fixture["report_path"],
                        repository_dir=fixture["repository"],
                        expected_repository_commit=fixture["repository_commit"],
                        command_runner=passing_ctest,
                    )
                    self.assertFalse(report["ok"], report)

    def test_rejects_ctest_failure_missing_and_inexact_summaries(self) -> None:
        cases = (
            subprocess.CompletedProcess(
                [],
                1,
                stdout=(
                    f"88% tests passed, 1 tests failed out of "
                    f"{REQUIRED_CTEST_COUNT}\n"
                ),
                stderr="failed\n",
            ),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess(
                [],
                0,
                stdout="100% tests passed, 0 tests failed out of 4\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    "100% tests passed, 0 tests failed out of "
                    f"{REQUIRED_CTEST_COUNT}\n"
                    "88% tests passed, 1 tests failed out of "
                    f"{REQUIRED_CTEST_COUNT}\n"
                ),
                stderr="",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            for completed in cases:
                with self.subTest(completed=completed):

                    def runner(
                        *args: object,
                        _completed: subprocess.CompletedProcess = completed,
                        **kwargs: object,
                    ) -> subprocess.CompletedProcess:
                        if "--show-only=json-v1" in args[0]:
                            return passing_ctest(*args, **kwargs)
                        return _completed

                    report = attest_build_binding(
                        fixture["report_path"],
                        repository_dir=fixture["repository"],
                        expected_repository_commit=fixture["repository_commit"],
                        command_runner=runner,
                    )
                    self.assertFalse(report["ok"], report)
                    self.assertIn(
                        "ctest",
                        " ".join(report["blockers"]).casefold(),
                    )

    def test_rejects_build_inputs_not_owned_by_exact_repository_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root)
            foreign_patch = root / "foreign.patch"
            foreign_patch.write_text("forged build input\n")
            forged_config = replace(
                fixture["config"],
                micromachine_patch=foreign_patch,
            )
            refresh_build_fixture(fixture, forged_config)

            report = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=passing_ctest,
            )

            self.assertFalse(report["ok"], report)
            self.assertIn("build input path mismatch", " ".join(report["blockers"]))

            wrong_commit = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit="b" * 40,
                command_runner=passing_ctest,
            )
            self.assertFalse(wrong_commit["ok"], wrong_commit)
            self.assertIn("HEAD mismatch", " ".join(wrong_commit["blockers"]))

    def test_rejects_wrong_ctest_names_and_build_mutation_during_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))

            def wrong_names(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                if "--show-only=json-v1" in args[0]:
                    build_dir = fixture["config"].micromachine_build_dir
                    tests = [
                        {
                            "name": f"untrusted_{index}",
                            "command": [str(build_dir / "bin" / executable)],
                        }
                        for index, executable in enumerate(
                            sorted(MICROMACHINE_REQUIRED_NATIVE_TESTS.values())
                        )
                    ]
                    return subprocess.CompletedProcess(
                        args[0],
                        0,
                        stdout=json.dumps({"tests": tests}),
                        stderr="",
                    )
                return passing_ctest(*args, **kwargs)

            wrong_identity = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=wrong_names,
            )
            self.assertFalse(wrong_identity["ok"], wrong_identity)
            self.assertIn(
                "CTest registry identity mismatch",
                " ".join(wrong_identity["blockers"]),
            )

        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))

            def mutating_ctest(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                if "--show-only=json-v1" in args[0]:
                    return passing_ctest(*args, **kwargs)
                fixture["config"].binary_path.write_text("#!/bin/sh\nexit 9\n")
                fixture["config"].binary_path.chmod(0o755)
                return passing_ctest(*args, **kwargs)

            mutated = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=mutating_ctest,
            )
            self.assertFalse(mutated["ok"], mutated)
            self.assertIn(
                "build changed during CTest execution",
                " ".join(mutated["blockers"]),
            )

    def test_rejects_ctest_discovery_command_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))

            def injected_argument(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                discovered = passing_ctest(*args, **kwargs)
                if "--show-only=json-v1" not in args[0]:
                    return discovered
                payload = json.loads(discovered.stdout)
                payload["tests"][0]["command"].append("--skip-real-test")
                return subprocess.CompletedProcess(
                    args[0],
                    0,
                    stdout=json.dumps(payload),
                    stderr="",
                )

            result = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=injected_argument,
            )

            self.assertFalse(result["ok"], result)
            self.assertIn(
                "CTest registry discovery returned an invalid test",
                " ".join(result["blockers"]),
            )

    def test_initial_ctest_registry_uses_authenticated_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            build_dir = fixture["config"].micromachine_build_dir.resolve()
            original_ctest = provenance_module._resolve_cmake_ctest_path(
                build_dir
            ).resolve()
            registry_executables: list[Path] = []

            def recording_runner(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                argv = list(args[0])
                if (
                    "--show-only=json-v1" in argv
                    and Path(argv[argv.index("--test-dir") + 1]).resolve()
                    == build_dir
                ):
                    registry_executables.append(Path(argv[0]).resolve())
                return passing_ctest(*args, **kwargs)

            result = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=recording_runner,
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(1, len(registry_executables))
            self.assertNotEqual(original_ctest, registry_executables[0])
            self.assertEqual("ctest", registry_executables[0].name)

    def test_rejects_noncanonical_ctest_discovery_json(self) -> None:
        cases = {
            "duplicate registry key": (
                True,
                '{"tests":[],"tests":[]}',
                "CTest registry discovery returned malformed JSON",
            ),
            "non-finite discovery value": (
                False,
                '{"tests":[],"attacker":NaN}',
                "CTest discovery returned malformed JSON",
            ),
        }
        for name, (attack_registry, document, blocker) in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = make_build_fixture(Path(directory))
                    build_dir = (
                        fixture["config"].micromachine_build_dir.resolve()
                    )

                    def malformed_json(
                        *args: object,
                        **kwargs: object,
                    ) -> subprocess.CompletedProcess:
                        argv = list(args[0])
                        if "--show-only=json-v1" not in argv:
                            return passing_ctest(*args, **kwargs)
                        test_dir = Path(
                            argv[argv.index("--test-dir") + 1]
                        ).resolve()
                        is_registry = test_dir == build_dir
                        if is_registry == attack_registry:
                            return subprocess.CompletedProcess(
                                argv,
                                0,
                                stdout=document,
                                stderr="",
                            )
                        return passing_ctest(*args, **kwargs)

                    result = attest_build_binding(
                        fixture["report_path"],
                        repository_dir=fixture["repository"],
                        expected_repository_commit=(
                            fixture["repository_commit"]
                        ),
                        command_runner=malformed_json,
                    )

                    self.assertFalse(result["ok"], result)
                    self.assertIn(blocker, " ".join(result["blockers"]))

    def test_ctest_blockers_prevent_all_direct_native_execution(self) -> None:
        cases = {
            "registry exit": "registry_exit",
            "registry stderr": "registry_stderr",
            "discovery JSON": "discovery_json",
            "CTest exit": "ctest_exit",
            "missing test": "missing_test",
        }
        real_run = subprocess.run
        for name, mode in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = make_build_fixture(Path(directory))
                    build_dir = (
                        fixture["config"].micromachine_build_dir.resolve()
                    )

                    def blocked_ctest(
                        *args: object,
                        **kwargs: object,
                    ) -> subprocess.CompletedProcess:
                        argv = list(args[0])
                        show_only = "--show-only=json-v1" in argv
                        test_dir = Path(
                            argv[argv.index("--test-dir") + 1]
                        ).resolve()
                        is_registry = test_dir == build_dir
                        if mode == "registry_exit" and show_only and is_registry:
                            return subprocess.CompletedProcess(
                                argv,
                                1,
                                stdout='{"tests":[]}',
                                stderr="",
                            )
                        if mode == "registry_stderr" and show_only and is_registry:
                            passed = passing_ctest(*args, **kwargs)
                            return subprocess.CompletedProcess(
                                argv,
                                0,
                                stdout=passed.stdout,
                                stderr="unexpected registry stderr",
                            )
                        if (
                            mode == "discovery_json"
                            and show_only
                            and not is_registry
                        ):
                            return subprocess.CompletedProcess(
                                argv,
                                0,
                                stdout="{",
                                stderr="",
                            )
                        if mode == "ctest_exit" and not show_only:
                            return subprocess.CompletedProcess(
                                argv,
                                1,
                                stdout="",
                                stderr="CTest failed",
                            )
                        if mode == "missing_test" and show_only and is_registry:
                            passed = passing_ctest(*args, **kwargs)
                            payload = json.loads(passed.stdout)
                            payload["tests"].pop()
                            return subprocess.CompletedProcess(
                                argv,
                                0,
                                stdout=json.dumps(payload),
                                stderr="",
                            )
                        return passing_ctest(*args, **kwargs)

                    def forbid_direct_native(
                        *args: object,
                        **kwargs: object,
                    ) -> subprocess.CompletedProcess:
                        argv = list(args[0])
                        executable = Path(argv[0])
                        if (
                            executable.parent.name == "bin"
                            and ".voi-ctest-" in str(executable)
                        ):
                            raise AssertionError(
                                "direct native test ran after a CTest blocker"
                            )
                        return real_run(*args, **kwargs)

                    with mock.patch.object(
                        provenance_module.subprocess,
                        "run",
                        side_effect=forbid_direct_native,
                    ):
                        result = attest_build_binding(
                            fixture["report_path"],
                            repository_dir=fixture["repository"],
                            expected_repository_commit=(
                                fixture["repository_commit"]
                            ),
                            command_runner=blocked_ctest,
                        )

                    self.assertFalse(result["ok"], result)
                    self.assertEqual(0, result["ctest"]["passed"])

    def test_rejects_noncanonical_ctest_registry_command_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            build_dir = fixture["config"].micromachine_build_dir.resolve()
            aliases = {
                "missing parent alias": (
                    build_dir
                    / "bin"
                    / "missing"
                    / ".."
                    / "voi_atomic_telemetry_test"
                ),
                "symlink directory alias": (
                    build_dir / "linked-bin" / "voi_atomic_telemetry_test"
                ),
            }
            (build_dir / "linked-bin").symlink_to(
                build_dir / "bin",
                target_is_directory=True,
            )

            for name, alias in aliases.items():
                with self.subTest(name=name):

                    def aliased_registry(
                        *args: object,
                        _alias: Path = alias,
                        **kwargs: object,
                    ) -> subprocess.CompletedProcess:
                        discovered = passing_ctest(*args, **kwargs)
                        if "--show-only=json-v1" not in args[0]:
                            return discovered
                        payload = json.loads(discovered.stdout)
                        for test in payload["tests"]:
                            if test["name"] == "voi_atomic_telemetry":
                                test["command"] = [str(_alias)]
                        return subprocess.CompletedProcess(
                            args[0],
                            0,
                            stdout=json.dumps(payload),
                            stderr="",
                        )

                    result = attest_build_binding(
                        fixture["report_path"],
                        repository_dir=fixture["repository"],
                        expected_repository_commit=fixture["repository_commit"],
                        command_runner=aliased_registry,
                    )

                    self.assertFalse(result["ok"], result)
                    self.assertIn(
                        "CTest registry identity mismatch",
                        " ".join(result["blockers"]),
                    )

    def test_ctest_executes_pinned_tests_during_original_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            original = (
                fixture["config"].micromachine_build_dir
                / "bin"
                / "voi_runtime_convergence_test"
            )
            original_payload = original.read_bytes()
            sentinel = root / "attacker-test-executed"

            def swapping_ctest(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                argv = list(args[0])
                if "--show-only=json-v1" in argv:
                    return subprocess.run(*args, **kwargs)
                original.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 0\n")
                original.chmod(0o755)
                try:
                    return subprocess.run(*args, **kwargs)
                finally:
                    original.write_bytes(original_payload)
                    original.chmod(0o755)

            result = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=swapping_ctest,
            )

            self.assertFalse(result["ok"], result)
            self.assertIn(
                "CTest command changed during execution: voi_runtime_convergence",
                result["blockers"],
            )
            self.assertFalse(sentinel.exists())

    def test_native_test_mutation_is_rejected_before_ctest_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            failing_test = (
                fixture["config"].micromachine_build_dir
                / "bin"
                / "voi_runtime_convergence_test"
            )
            failing_test.write_text("#!/bin/sh\nexit 7\n")
            failing_test.chmod(0o755)
            shadow = root / "shadow"
            shadow.mkdir()
            fake_ctest = shadow / "ctest"
            build_dir = fixture["config"].micromachine_build_dir.resolve()
            discovered = {
                "tests": [
                    {
                        "name": name,
                        "command": [str(build_dir / "bin" / executable)],
                    }
                    for name, executable in sorted(
                        MICROMACHINE_REQUIRED_NATIVE_TESTS.items()
                    )
                ]
            }
            fake_ctest.write_text(
                "#!/bin/sh\n"
                'if [ "$3" = "--show-only=json-v1" ]; then\n'
                f"  printf '%s\\n' '{json.dumps(discovered)}'\n"
                "else\n"
                "  printf '%s\\n' "
                f"'100% tests passed, 0 tests failed out of {REQUIRED_CTEST_COUNT}'\n"
                "fi\n"
                "exit 0\n"
            )
            fake_ctest.chmod(0o755)

            with mock.patch.dict(
                os.environ,
                {"PATH": f"{shadow}:{os.environ.get('PATH', '')}"},
                clear=False,
            ):
                result = attest_build_binding(
                    fixture["report_path"],
                    repository_dir=fixture["repository"],
                    expected_repository_commit=fixture["repository_commit"],
                    command_runner=subprocess.run,
                )

            self.assertFalse(result["ok"], result)
            self.assertIn(
                "native_test_attestation_mismatch",
                " ".join(result["blockers"]),
            )
            self.assertIsNone(result["ctest"]["returncode"])

    def test_rejects_ctest_cache_and_complete_native_test_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            shadow = root / "shadow"
            shadow.mkdir()
            fake_ctest = shadow / "ctest"
            build_dir = fixture["config"].micromachine_build_dir.resolve()
            discovered = {
                "tests": [
                    {
                        "name": name,
                        "command": [str(build_dir / "bin" / executable)],
                    }
                    for name, executable in sorted(
                        MICROMACHINE_REQUIRED_NATIVE_TESTS.items()
                    )
                ]
            }
            fake_ctest.write_text(
                "#!/bin/sh\n"
                'if [ "$3" = "--show-only=json-v1" ]; then\n'
                f"  printf '%s\\n' '{json.dumps(discovered)}'\n"
                "else\n"
                "  printf '%s\\n' "
                f"'100% tests passed, 0 tests failed out of {REQUIRED_CTEST_COUNT}'\n"
                "fi\n"
                "exit 0\n"
            )
            fake_ctest.chmod(0o755)
            (build_dir / "CMakeCache.txt").write_text(
                f"CMAKE_CTEST_COMMAND:INTERNAL={fake_ctest.resolve()}\n"
            )
            for executable_name in MICROMACHINE_REQUIRED_NATIVE_TESTS.values():
                executable = build_dir / "bin" / executable_name
                executable.write_bytes(Path("/usr/bin/true").read_bytes())
                executable.chmod(0o755)

            result = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=passing_ctest,
            )

            self.assertFalse(result["ok"], result)
            self.assertIn(
                "native_test_attestation_mismatch",
                " ".join(result["blockers"]),
            )

    def test_rejects_linked_build_root_before_ctest_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            build_dir = fixture["config"].micromachine_build_dir
            external_build = root / "external-build"
            build_dir.rename(external_build)
            build_dir.symlink_to(external_build, target_is_directory=True)
            ctest_calls: list[object] = []

            def forbidden_ctest(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                ctest_calls.append(args)
                return passing_ctest(*args, **kwargs)

            result = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                command_runner=forbidden_ctest,
            )

            self.assertFalse(result["ok"], result)
            self.assertEqual([], ctest_calls)
            self.assertIn(
                "contains a symlink",
                " ".join(result["blockers"]),
            )

    def test_build_reconstruction_ignores_hostile_git_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            (fixture["config"].micromachine_dir / "tracked.txt").write_text(
                "tampered source\n"
            )
            shadow = root / "shadow"
            shadow.mkdir()
            fake_git = shadow / "git"
            fake_git.write_text("#!/bin/sh\nexit 0\n")
            fake_git.chmod(0o755)

            with mock.patch.dict(
                os.environ,
                {
                    "GIT_DIR": str(root / "foreign.git"),
                    "PATH": str(shadow),
                },
                clear=False,
            ):
                result = attest_build_binding(
                    fixture["report_path"],
                    repository_dir=fixture["repository"],
                    expected_repository_commit=fixture["repository_commit"],
                    command_runner=passing_ctest,
                )

            self.assertFalse(result["ok"], result)
            self.assertIn("source_state_mismatch", " ".join(result["blockers"]))


class GitHubActionsBundleEmissionTest(unittest.TestCase):
    def test_deterministic_output_is_bound_to_the_admitted_build(self) -> None:
        raw_output = b"raw deterministic journey output"
        bound_output = b"bound deterministic journey output"
        build_report = b"canonical build report"
        binary = b"exact MicroMachine binary"
        node_executable = Path(sys.executable).resolve()
        node_sha256 = hashlib.sha256(node_executable.read_bytes()).hexdigest()

        with mock.patch.object(
            provenance_module,
            "bind_deterministic_journey_bundle_to_build",
            return_value=bound_output,
        ) as binder:
            result = (
                provenance_module._bind_producer_output_to_admitted_build(
                    producer_id=(
                        PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
                    ),
                    producer_output=raw_output,
                    build_report=build_report,
                    binary=binary,
                    node_executable=node_executable,
                    node_sha256=node_sha256,
                )
            )

        self.assertEqual(bound_output, result)
        binder.assert_called_once()
        self.assertEqual(raw_output, binder.call_args.args[0])
        self.assertEqual(build_report, binder.call_args.kwargs["build_report_bytes"])
        self.assertEqual(binary, binder.call_args.kwargs["binary_bytes"])
        self.assertRegex(
            str(binder.call_args.kwargs["node_executable"]),
            r"^/dev/fd/\d+$",
        )

        with mock.patch.object(
            provenance_module,
            "bind_deterministic_journey_bundle_to_build",
        ) as unused_binder:
            passthrough = (
                provenance_module._bind_producer_output_to_admitted_build(
                    producer_id="fixture_producer",
                    producer_output=raw_output,
                    build_report=build_report,
                    binary=binary,
                )
            )

        self.assertEqual(raw_output, passthrough)
        unused_binder.assert_not_called()

    def test_emitter_accepts_dynamic_single_closing_issue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            adapter = FakeGitHubAdapter(
                head_sha=fixture["repository_commit"],
            )
            adapter.issue.update({"id": 143, "number": 143})
            adapter.closing_issues[0].update(
                {"databaseId": 143, "number": 143}
            )

            report = emit_github_actions_pre_live_bundle(
                adapter=adapter,
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                workflow_ref=WORKFLOW_REF,
                workflow_sha=WORKFLOW_SHA,
                build_report_path=fixture["report_path"],
                expected_build_dir=fixture["config"].micromachine_build_dir,
                output_path=root / GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
                producer_id="fixture_producer",
                ctest_runner=passing_ctest,
            )

            self.assertTrue(report["ok"], report)
            self.assertEqual(143, report["github_context"]["closing_issue_id"])
            self.assertEqual(
                143,
                report["github_context"]["closing_issue_number"],
            )
            self.assertEqual(
                "open",
                report["github_context"]["closing_issue_state"],
            )

    def test_emitter_rejects_ambiguous_or_foreign_closing_issue(self) -> None:
        mutations = {
            "missing": lambda adapter: adapter.closing_issues.clear(),
            "multiple": lambda adapter: adapter.closing_issues.append(
                dict(adapter.closing_issues[0])
            ),
            "foreign repository id": lambda adapter: adapter.closing_issues[0][
                "repository"
            ].update({"databaseId": 999}),
            "foreign repository name": lambda adapter: adapter.closing_issues[0][
                "repository"
            ].update({"nameWithOwner": "attacker/other"}),
            "closed": lambda adapter: adapter.issue.update({"state": "closed"}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    fixture = make_build_fixture(root / "fixture")
                    adapter = FakeGitHubAdapter(
                        head_sha=fixture["repository_commit"],
                    )
                    mutate(adapter)

                    report = emit_github_actions_pre_live_bundle(
                        adapter=adapter,
                        repository_dir=fixture["repository"],
                        expected_commit=fixture["repository_commit"],
                        run_id=RUN_ID,
                        run_attempt=RUN_ATTEMPT,
                        workflow_ref=WORKFLOW_REF,
                        workflow_sha=WORKFLOW_SHA,
                        build_report_path=fixture["report_path"],
                        expected_build_dir=(
                            fixture["config"].micromachine_build_dir
                        ),
                        output_path=root / GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
                        producer_id="fixture_producer",
                        ctest_runner=passing_ctest,
                    )

                    self.assertFalse(report["ok"], report)
                    self.assertIn(
                        "closing",
                        " ".join(report["blockers"]).casefold(),
                    )

    def test_emitter_output_is_the_bundle_consumed_by_production_verifier(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            adapter = FakeGitHubAdapter(
                head_sha=fixture["repository_commit"],
            )
            output = root / GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME

            report = emit_github_actions_pre_live_bundle(
                adapter=adapter,
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                workflow_ref=WORKFLOW_REF,
                workflow_sha=WORKFLOW_SHA,
                build_report_path=fixture["report_path"],
                expected_build_dir=fixture["config"].micromachine_build_dir,
                output_path=output,
                producer_id="fixture_producer",
                ctest_runner=passing_ctest,
            )

            self.assertTrue(report["ok"], report)
            self.assertTrue(output.is_file())
            verification = verify_pre_live_artifact_bundle(output.read_bytes())
            self.assertTrue(verification["ok"], verification)
            self.assertEqual(
                fixture["repository_commit"],
                verification["manifest"]["repository"]["commit_sha"],
            )
            self.assertEqual(
                JOB_ID,
                verification["manifest"]["job"]["id"],
            )
            self.assertEqual(
                candidate_authority(fixture["repository_commit"]),
                verification["manifest"]["authority"],
            )
            self.assertFalse(report["authority"]["release_authoritative"])

    def test_emitter_rejects_non_pr_or_ambiguous_candidate_context(self) -> None:
        mutations = {
            "push event": lambda adapter: adapter.workflow_run.update(
                {"event": "push"}
            ),
            "missing pull": lambda adapter: adapter.workflow_run.update(
                {"pull_requests": []}
            ),
            "multiple pulls": lambda adapter: adapter.workflow_run[
                "pull_requests"
            ].append(dict(adapter.workflow_run["pull_requests"][0])),
            "pull id": lambda adapter: adapter.workflow_run["pull_requests"][0].update(
                {"id": 999}
            ),
            "pull number": lambda adapter: adapter.workflow_run["pull_requests"][
                0
            ].update({"number": 999}),
            "pull head": lambda adapter: adapter.pull_request["head"].update(
                {"sha": "b" * 40}
            ),
            "pull ref": lambda adapter: adapter.pull_request["head"].update(
                {"ref": "other-branch"}
            ),
            "pull repository": lambda adapter: adapter.pull_request["head"][
                "repo"
            ].update({"id": 999}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    fixture = make_build_fixture(root / "fixture")
                    adapter = FakeGitHubAdapter(
                        head_sha=fixture["repository_commit"],
                    )
                    mutate(adapter)
                    report = emit_github_actions_pre_live_bundle(
                        adapter=adapter,
                        repository_dir=fixture["repository"],
                        expected_commit=fixture["repository_commit"],
                        run_id=RUN_ID,
                        run_attempt=RUN_ATTEMPT,
                        workflow_ref=WORKFLOW_REF,
                        workflow_sha=WORKFLOW_SHA,
                        build_report_path=fixture["report_path"],
                        expected_build_dir=(fixture["config"].micromachine_build_dir),
                        output_path=root / GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
                        producer_id="fixture_producer",
                        ctest_runner=passing_ctest,
                    )
                    self.assertFalse(report["ok"], report)

    def test_emitter_fails_closed_before_producer_on_wrong_job_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            adapter = FakeGitHubAdapter(
                head_sha=fixture["repository_commit"],
            )
            adapter.job["name"] = "attacker-selected-job"
            output = root / GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME
            producer_calls: list[object] = []

            def forbidden_producer(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                producer_calls.append((args, kwargs))
                raise AssertionError("producer must not run")

            report = emit_github_actions_pre_live_bundle(
                adapter=adapter,
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                workflow_ref=WORKFLOW_REF,
                workflow_sha=WORKFLOW_SHA,
                build_report_path=fixture["report_path"],
                expected_build_dir=fixture["config"].micromachine_build_dir,
                output_path=output,
                producer_id="fixture_producer",
                ctest_runner=passing_ctest,
                producer_runner=forbidden_producer,
            )

            self.assertFalse(report["ok"], report)
            self.assertFalse(output.exists())
            self.assertEqual([], producer_calls)

    def test_emitter_rejects_build_replacement_during_producer_execution(
        self,
    ) -> None:
        for label in ("report", "binary"):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    fixture = make_build_fixture(root / "fixture")
                    build_binding = attest_build_binding(
                        fixture["report_path"],
                        repository_dir=fixture["repository"],
                        expected_repository_commit=fixture["repository_commit"],
                        expected_build_dir=(
                            fixture["config"].micromachine_build_dir
                        ),
                        command_runner=passing_ctest,
                    )
                    admitted_build = (
                        provenance_module._capture_admitted_build_snapshots(
                            build_binding
                        )
                    )
                    target = (
                        fixture["report_path"]
                        if label == "report"
                        else fixture["config"].binary_path
                    )
                    replacement = target.with_name(target.name + ".replacement")
                    replacement.write_bytes(b"replacement")
                    if label == "binary":
                        replacement.chmod(0o755)
                    os.replace(replacement, target)
                    report = (
                        provenance_module._verify_admitted_build_snapshots_unchanged(
                            build_binding,
                            admitted_build,
                        )
                    )

                    self.assertFalse(report["ok"], report)
                    self.assertIn(
                        f"admitted {label} changed after build attestation",
                        " ".join(report["blockers"]),
                    )

    def test_emitter_rejects_producer_output_path_replacement_before_assembly(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = (root / "producer-output.json").resolve()
            payload = b'{"source":"captured"}\n'
            output.write_bytes(payload)
            _, snapshot = provenance_module._read_regular_file_snapshot(
                output,
                maximum=provenance_module.MAX_GITHUB_ARTIFACT_BYTES,
            )
            local_execution = {
                "output_artifact": {
                    "path": str(output),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                    "published_stat_identity": list(snapshot),
                }
            }
            replacement = output.with_name(output.name + ".replacement")
            replacement.write_bytes(b'{"source":"attacker"}\n')
            os.replace(replacement, output)

            with self.assertRaisesRegex(
                ValueError,
                "captured producer output identity changed before consumption",
            ):
                provenance_module._read_published_producer_output(
                    local_execution
                )

    def test_real_binder_emission_and_local_attestation_share_bound_digest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            adapter = FakeGitHubAdapter(
                head_sha=fixture["repository_commit"],
            )
            source_context = attest_github_actions_emission_context(
                adapter,
                repository=REPOSITORY,
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                expected_head_sha=fixture["repository_commit"],
                workflow_ref=WORKFLOW_REF,
                workflow_sha=WORKFLOW_SHA,
            )
            build_binding = attest_build_binding(
                fixture["report_path"],
                repository_dir=fixture["repository"],
                expected_repository_commit=fixture["repository_commit"],
                expected_build_dir=fixture["config"].micromachine_build_dir,
                command_runner=passing_ctest,
            )
            admitted_build = (
                provenance_module._capture_admitted_build_snapshots(
                    build_binding
                )
            )
            producer_policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                producer_id="fixture_producer",
            )
            producer_policy["producer_id"] = (
                PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
            )
            node_executable = Path(sys.executable).resolve()
            producer_policy["node_executable_path"] = str(node_executable)
            producer_policy["node_executable_sha256"] = hashlib.sha256(
                node_executable.read_bytes()
            ).hexdigest()
            raw_output = make_stub_deterministic_journey_bundle()
            producer_output_path = Path(
                str(producer_policy["output_artifact"])
            )
            producer_output_path.write_bytes(raw_output)
            _, output_snapshot = (
                provenance_module._read_regular_file_snapshot(
                    producer_output_path,
                    maximum=provenance_module.MAX_GITHUB_ARTIFACT_BYTES,
                )
            )
            executable = Path(str(producer_policy["argv"][0])).read_bytes()
            local_execution = {
                "executable_sha256": hashlib.sha256(executable).hexdigest(),
                "exit_code": 0,
                "started_at": "2026-08-02T00:00:00Z",
                "ended_at": "2026-08-02T00:00:01Z",
                "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "output_artifact": {
                    "path": str(producer_output_path),
                    "sha256": hashlib.sha256(raw_output).hexdigest(),
                    "size_bytes": len(raw_output),
                    "published_stat_identity": list(output_snapshot),
                },
            }
            nested_identity = {
                "ok": True,
                "blockers": [],
                "binary_sha256": build_binding["binary_sha256"],
                "embedded_build_input_identity": (
                    build_binding["embedded_build_input_identity"]
                ),
            }
            verifier = (
                "starcraft_commander.micromachine_pre_live_journeys."
                "_verify_pre_live_journey_payload_cache"
            )

            with mock.patch(verifier, return_value=nested_identity):
                bundle = (
                    provenance_module._assemble_github_actions_pre_live_bundle(
                        repository_root=fixture["repository"].resolve(),
                        expected_commit=fixture["repository_commit"],
                        source_context=source_context,
                        build_binding=build_binding,
                        admitted_build=admitted_build,
                        producer_policy=producer_policy,
                        local_execution=local_execution,
                    )
                )
                verification = verify_pre_live_artifact_bundle(
                    bundle,
                    admission_snapshot=(
                        provenance_module._admission_snapshot_from_mapping(
                            admitted_build
                        )
                    ),
                    node_executable=node_executable,
                )
                self.assertTrue(verification["ok"], verification)
                github_source = {
                    "repository": source_context["repository"],
                    "source_ids": {
                        "repository_id": source_context["repository_id"],
                        "issue_id": source_context["closing_issue_id"],
                        "issue_number": (
                            source_context["closing_issue_number"]
                        ),
                    },
                    "artifact_bundle": verification,
                }
                binding = provenance_module.attest_artifact_local_bindings(
                    github_source=github_source,
                    build_binding=build_binding,
                    producer_policy=producer_policy,
                    local_execution=local_execution,
                )

            manifest_digest = verification["manifest"]["producer"][
                "output_sha256"
            ]
            raw_digest = hashlib.sha256(raw_output).hexdigest()
            self.assertNotEqual(raw_digest, manifest_digest)
            self.assertTrue(binding["ok"], binding)
            self.assertEqual(raw_digest, binding["raw_output_sha256"])
            self.assertEqual(manifest_digest, binding["bound_output_sha256"])


class LocalProducerTest(unittest.TestCase):
    def dedicated_producer_uid(self) -> tuple[int, int]:
        if os.geteuid() != 0:
            self.skipTest("dedicated producer UID tests require a root verifier")
        uid = int(os.environ.get("VOI_PRODUCER_UID", "65001"))
        gid = int(os.environ.get("VOI_PRODUCER_GID", "65001"))
        provenance_module._assert_dedicated_producer_identity_available(uid, gid)
        return uid, gid

    def test_checked_in_policy_has_an_executable_production_producer(self) -> None:
        repository = BUILD_IDENTITY_REPO_ROOT.resolve()
        policy_path = repository / PRODUCER_POLICY_RELATIVE_PATH
        payload = json.loads(policy_path.read_bytes())
        producer = payload["producers"]["provenance_qualification"]
        self.assertEqual(
            {"argv", "cwd", "output"},
            set(producer),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "foundation.json"
            replacements = {
                "{python}": str(Path(sys.executable).resolve()),
                "{repository}": str(repository),
                "{output}": str(output),
            }
            argv = [replacements.get(value, value) for value in producer["argv"]]
            completed = subprocess.run(
                argv,
                cwd=repository,
                check=False,
                capture_output=True,
                text=False,
                shell=False,
                env=dict(SANITIZED_PRODUCER_ENV),
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(
                {
                    "evidence_kind": ("authenticated_pre_live_provenance_foundation"),
                    "producer_id": "provenance_qualification",
                    "schema_version": 1,
                },
                json.loads(output.read_bytes()),
            )

    def test_resolves_only_the_policy_committed_at_the_exact_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))

            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                producer_id="fixture_producer",
            )

            self.assertTrue(policy["ok"], policy)
            self.assertEqual(str(Path(sys.executable).resolve()), policy["argv"][0])
            self.assertEqual(
                ["-I", "-B", "-S", "-c", ISOLATED_PYTHON_BOOTSTRAP],
                policy["argv"][1:6],
            )
            self.assertEqual(
                str(fixture["repository"].resolve()),
                policy["argv"][6],
            )
            self.assertEqual("fixture_producer.py", policy["argv"][7])
            self.assertEqual(
                "fixture/evidence.json",
                str(
                    Path(str(policy["output_artifact"])).relative_to(
                        canonical_pre_live_state_dir(fixture["repository"])
                    )
                ),
            )

            policy_path = fixture["repository"] / PRODUCER_POLICY_RELATIVE_PATH
            policy_path.write_text('{"schema_version":1,"producers":{}}\n')
            tampered = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                producer_id="fixture_producer",
            )
            self.assertFalse(tampered["ok"], tampered)
            self.assertIn(
                "differs from the exact commit",
                " ".join(tampered["blockers"]),
            )

    def test_rejects_noncanonical_committed_producer_policy_json(self) -> None:
        cases = {
            "float schema": (
                '{"schema_version":1.0,"producers":{}}\n',
                "producer policy schema mismatch",
            ),
            "non-finite schema": (
                '{"schema_version":NaN,"producers":{}}\n',
                "non-finite JSON number",
            ),
            "duplicate schema": (
                (
                    '{"schema_version":1,"schema_version":1,'
                    '"producers":{}}\n'
                ),
                "duplicate JSON object key",
            ),
        }
        for name, (document, blocker) in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = make_build_fixture(Path(directory))
                    repository = fixture["repository"]
                    policy_path = repository / PRODUCER_POLICY_RELATIVE_PATH
                    policy_path.write_text(document)
                    git(
                        repository,
                        "add",
                        PRODUCER_POLICY_RELATIVE_PATH.as_posix(),
                    )
                    git(
                        repository,
                        "-c",
                        "user.name=Test",
                        "-c",
                        "user.email=test@example.com",
                        "commit",
                        "-m",
                        "malformed producer policy",
                    )
                    commit = git(
                        repository,
                        "rev-parse",
                        "HEAD",
                    ).stdout.strip()

                    policy = resolve_local_producer_policy(
                        repository_dir=repository,
                        expected_commit=commit,
                        producer_id="fixture_producer",
                    )

                    self.assertFalse(policy["ok"], policy)
                    self.assertIn(blocker, " ".join(policy["blockers"]))

    def test_executes_from_committed_snapshot_not_ignored_python_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            repository = fixture["repository"]
            producer_script = repository / "fixture_producer.py"
            producer_script.write_text(
                "from pathlib import Path\n"
                "import fcntl\n"
                "import sys\n"
                "Path(sys.argv[sys.argv.index('--output') + 1]).write_text("
                "'{\"fixture\":true}\\n')\n"
            )
            (repository / ".gitignore").write_text("fcntl.py\n")
            git(repository, "add", "fixture_producer.py", ".gitignore")
            git(
                repository,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "import stdlib fcntl",
            )
            commit = git(repository, "rev-parse", "HEAD").stdout.strip()
            (repository / "fcntl.py").write_text(
                "raise RuntimeError('ignored module executed')\n"
            )
            policy = resolve_local_producer_policy(
                repository_dir=repository,
                expected_commit=commit,
                producer_id="fixture_producer",
            )
            self.assertTrue(policy["ok"], policy)
            source_files = policy["runtime_sources"]["files"]
            authenticated_files = [item["path"] for item in source_files]
            authenticated_digests = {
                item["path"]: item["sha256"] for item in source_files
            }

            report = run_local_producer(
                repository_dir=repository,
                cwd=policy["cwd"],
                argv=policy["argv"],
                allowed_argv=(policy["argv"],),
                output_artifact=policy["output_artifact"],
                authenticated_files=authenticated_files,
                authenticated_file_digests=authenticated_digests,
            )

            self.assertTrue(report["ok"], report)
            self.assertEqual(
                {"fixture": True},
                json.loads(Path(str(policy["output_artifact"])).read_bytes()),
            )

    def test_rejects_module_from_attacker_controlled_inherited_sys_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            attacker_dir = root / "attacker"
            attacker_dir.mkdir()
            sentinel = root / "inherited-path-module-executed"
            (attacker_dir / "inherited_path_attack.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(sentinel)!r}).write_text('executed')\n"
            )
            commit = replace_fixture_producer_source(
                fixture,
                "import inherited_path_attack\n"
                "from pathlib import Path\n"
                "import sys\n"
                "Path(sys.argv[sys.argv.index('--output') + 1]).write_text("
                "'{\"fixture\":true}\\n')\n",
            )
            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=commit,
                producer_id="fixture_producer",
            )
            self.assertTrue(policy["ok"], policy)
            source_files = policy["runtime_sources"]["files"]

            with mock.patch.object(
                sys,
                "path",
                [str(attacker_dir), *sys.path],
            ):
                report = run_local_producer(
                    repository_dir=fixture["repository"],
                    cwd=policy["cwd"],
                    argv=policy["argv"],
                    allowed_argv=(policy["argv"],),
                    output_artifact=policy["output_artifact"],
                    authenticated_files=[item["path"] for item in source_files],
                    authenticated_file_digests={
                        item["path"]: item["sha256"] for item in source_files
                    },
                )

            self.assertFalse(report["ok"], report)
            self.assertFalse(sentinel.exists())
            self.assertFalse(Path(str(policy["output_artifact"])).exists())

    def test_rejects_unauthenticated_preloaded_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            commit = replace_fixture_producer_source(
                fixture,
                "import preloaded_attack\n"
                "from pathlib import Path\n"
                "import sys\n"
                "Path(sys.argv[sys.argv.index('--output') + 1]).write_text("
                "preloaded_attack.PAYLOAD)\n",
            )
            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=commit,
                producer_id="fixture_producer",
            )
            self.assertTrue(policy["ok"], policy)
            source_files = policy["runtime_sources"]["files"]
            attacker_module = types.ModuleType("preloaded_attack")
            attacker_module.PAYLOAD = '{"attacker":true}\n'
            attacker_module.__file__ = str(root / "preloaded_attack.py")

            with mock.patch.dict(
                sys.modules,
                {"preloaded_attack": attacker_module},
            ):
                report = run_local_producer(
                    repository_dir=fixture["repository"],
                    cwd=policy["cwd"],
                    argv=policy["argv"],
                    allowed_argv=(policy["argv"],),
                    output_artifact=policy["output_artifact"],
                    authenticated_files=[item["path"] for item in source_files],
                    authenticated_file_digests={
                        item["path"]: item["sha256"] for item in source_files
                    },
                )

            self.assertFalse(report["ok"], report)
            self.assertFalse(Path(str(policy["output_artifact"])).exists())

    def test_authenticated_python_exec_cannot_recover_parent_github_token(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = make_build_fixture(root / "fixture")
            sentinel_token = "github-token-visible-only-in-parent-heap"
            adapter = StdlibGitHubRESTAdapter(token=sentinel_token)
            commit = replace_fixture_producer_source(
                fixture,
                "import gc\n"
                "import json\n"
                "from pathlib import Path\n"
                "import sys\n"
                "recovered=[]\n"
                "for candidate in gc.get_objects():\n"
                "    try:\n"
                "        if candidate.__class__.__name__ == "
                "'StdlibGitHubRESTAdapter':\n"
                "            recovered.append(candidate._token)\n"
                "    except BaseException:\n"
                "        pass\n"
                "Path(sys.argv[sys.argv.index('--output') + 1]).write_text(\n"
                "    json.dumps({'recovered': recovered}) + '\\n'\n"
                ")\n",
            )
            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=commit,
                producer_id="fixture_producer",
            )
            self.assertTrue(policy["ok"], policy)
            source_files = policy["runtime_sources"]["files"]

            report = run_local_producer(
                repository_dir=fixture["repository"],
                cwd=policy["cwd"],
                argv=policy["argv"],
                allowed_argv=(policy["argv"],),
                output_artifact=policy["output_artifact"],
                authenticated_files=[item["path"] for item in source_files],
                authenticated_file_digests={
                    item["path"]: item["sha256"] for item in source_files
                },
            )

            self.assertIsNotNone(adapter)
            self.assertTrue(report["ok"], report)
            output = json.loads(
                Path(str(policy["output_artifact"])).read_bytes()
            )
            self.assertNotIn(sentinel_token, output["recovered"])
            self.assertEqual([], output["recovered"])

    def test_dedicated_producer_uid_cannot_read_parent_environment_token(
        self,
    ) -> None:
        producer_uid, producer_gid = self.dedicated_producer_uid()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o711)
            fixture = make_build_fixture(root / "fixture")
            sentinel_token = "github-token-visible-only-in-root-parent-environment"
            commit = replace_fixture_producer_source(
                fixture,
                "import json\n"
                "import os\n"
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                f"sentinel={sentinel_token!r}\n"
                "parent_pid=os.getppid()\n"
                "observed=[]\n"
                "ps_path=next(\n"
                "    (value for value in ('/bin/ps','/usr/bin/ps')\n"
                "     if Path(value).is_file()),\n"
                "    None,\n"
                ")\n"
                "if ps_path is not None:\n"
                "    visible=subprocess.run(\n"
                "        [ps_path,'-axo','pid=,ppid=,command='],\n"
                "        check=False,capture_output=True,text=False,\n"
                "    )\n"
                "    observed.extend((visible.stdout,visible.stderr))\n"
                "ancestor_pid=parent_pid\n"
                "visited=set()\n"
                "while ps_path is not None and ancestor_pid not in visited:\n"
                "    visited.add(ancestor_pid)\n"
                "    result=subprocess.run(\n"
                "        [ps_path,'eww','-p',str(ancestor_pid)],\n"
                "        check=False,capture_output=True,text=False,\n"
                "    )\n"
                "    observed.extend((result.stdout,result.stderr))\n"
                "    proc_environ=(\n"
                "        Path('/proc')/str(ancestor_pid)/'environ'\n"
                "    )\n"
                "    try:\n"
                "        observed.append(proc_environ.read_bytes())\n"
                "    except OSError as exc:\n"
                "        observed.append(str(exc).encode())\n"
                "    parent=subprocess.run(\n"
                "        [ps_path,'-o','ppid=,command=',\n"
                "         '-p',str(ancestor_pid)],\n"
                "        check=False,capture_output=True,text=False,\n"
                "    )\n"
                "    observed.extend((parent.stdout,parent.stderr))\n"
                "    fields=parent.stdout.strip().split(None,1)\n"
                "    if not fields:\n"
                "        break\n"
                "    try:\n"
                "        next_pid=int(fields[0])\n"
                "    except ValueError:\n"
                "        break\n"
                "    if next_pid <= 0 or next_pid == ancestor_pid:\n"
                "        break\n"
                "    ancestor_pid=next_pid\n"
                "combined=b'\\n'.join(observed)\n"
                "Path(sys.argv[sys.argv.index('--output') + 1]).write_text(\n"
                "    json.dumps({\n"
                "        'euid':os.geteuid(),\n"
                "        'parent_pid':parent_pid,\n"
                "        'recovered':sentinel.encode() in combined,\n"
                "    })+'\\n'\n"
                ")\n",
            )
            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=commit,
                producer_id="fixture_producer",
            )
            self.assertTrue(policy["ok"], policy)
            source_files = policy["runtime_sources"]["files"]

            with mock.patch.dict(
                os.environ,
                {"GITHUB_TOKEN": sentinel_token},
            ):
                report = run_local_producer(
                    repository_dir=fixture["repository"],
                    cwd=policy["cwd"],
                    argv=policy["argv"],
                    allowed_argv=(policy["argv"],),
                    output_artifact=policy["output_artifact"],
                    authenticated_files=[item["path"] for item in source_files],
                    authenticated_file_digests={
                        item["path"]: item["sha256"] for item in source_files
                    },
                    producer_uid=producer_uid,
                    producer_gid=producer_gid,
                )

            self.assertTrue(report["ok"], report)
            output = json.loads(
                Path(str(policy["output_artifact"])).read_bytes()
            )
            self.assertEqual(producer_uid, output["euid"])
            self.assertFalse(output["recovered"], output)

    def test_github_token_stdin_is_bounded_and_exact(self) -> None:
        self.assertEqual(
            "ghs_exact-token",
            provenance_module._read_github_token_from_stdin(
                io.BytesIO(b"ghs_exact-token")
            ),
        )
        for name, payload in {
            "empty": b"",
            "newline": b"ghs_token\n",
            "non-ASCII": b"ghs_\xff",
            "oversized": b"x" * 4097,
        }.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    provenance_module._read_github_token_from_stdin(
                        io.BytesIO(payload)
                    )

    def test_github_actions_main_prevalidates_before_reading_token(self) -> None:
        events: list[str] = []
        trusted = {"ok": True, "status": "accepted", "blockers": []}
        build = {
            "ok": True,
            "status": "accepted",
            "blockers": [],
            "report_path": "/runtime/voi_build_identity.json",
            "repository_commit": HEAD_SHA,
            "binary_path": "/runtime/bin/MicroMachine",
            "ctest": {"ok": True},
        }
        environment = {
            "GITHUB_API_URL": "https://api.github.test",
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_RUN_ATTEMPT": str(RUN_ATTEMPT),
            "GITHUB_RUN_ID": str(RUN_ID),
            "GITHUB_WORKFLOW_REF": (
                f"{REPOSITORY}/.github/workflows/ci.yml@refs/pull/137/merge"
            ),
            "GITHUB_WORKFLOW_SHA": HEAD_SHA,
            "VOI_CANDIDATE_WORKSPACE": "/candidate",
            "VOI_NODE_EXECUTABLE": "/usr/local/bin/node",
            "VOI_PRODUCER_GID": "65001",
            "VOI_PRODUCER_UID": "65001",
            "VOI_RELEASE_COMMIT": HEAD_SHA,
            "VOI_TRUSTED_VERIFIER_COMMIT": BASE_SHA,
            "VOI_TRUSTED_VERIFIER_WORKSPACE": "/trusted",
        }

        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.object(
                provenance_module,
                "_attest_trusted_verifier_runtime",
                side_effect=lambda *args, **kwargs: events.append("trusted")
                or trusted,
            ) as trusted_attestation,
            mock.patch.object(
                provenance_module,
                "attest_build_binding",
                side_effect=lambda *args, **kwargs: events.append("build")
                or build,
            ) as build_attestation,
            mock.patch.object(
                provenance_module,
                "_read_github_token_from_stdin",
                side_effect=lambda stream: events.append("token") or "ghs_token",
            ),
            mock.patch.object(
                provenance_module,
                "emit_github_actions_pre_live_bundle",
                side_effect=lambda **kwargs: events.append("emit")
                or {"ok": True},
            ) as emitter,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            returncode = provenance_module._main(
                (
                    "--emit-github-actions-bundle",
                    "/output/pre-live.zip",
                    "/runtime/voi_build_identity.json",
                    "/runtime",
                )
            )

        self.assertEqual(0, returncode)
        self.assertEqual(["trusted", "build", "token", "emit"], events)
        trusted_attestation.assert_called_once_with(
            "/trusted",
            expected_commit=BASE_SHA,
        )
        self.assertEqual(65001, build_attestation.call_args.kwargs["execution_uid"])
        self.assertEqual(65001, build_attestation.call_args.kwargs["execution_gid"])
        self.assertIs(
            build,
            emitter.call_args.kwargs["_prevalidated_build_binding"],
        )
        self.assertEqual(
            "/candidate",
            emitter.call_args.kwargs["repository_dir"],
        )

    def test_dedicated_ctest_command_uses_bounded_uid_runner(self) -> None:
        completed = subprocess.CompletedProcess(
            ["/trusted/ctest"],
            0,
            b"stdout",
            b"",
        )
        with mock.patch.object(
            provenance_module,
            "_run_dedicated_uid_native_command",
            return_value=completed,
        ) as runner:
            observed = provenance_module._run_ctest_command(
                subprocess.run,
                ["/trusted/ctest", "--show-only=json-v1"],
                cwd="/runtime",
                text=True,
                env={"PATH": "/usr/bin:/bin"},
                timeout=120.0,
                execution_identity=(65001, 65001),
            )

        self.assertIs(completed, observed)
        runner.assert_called_once_with(
            ["/trusted/ctest", "--show-only=json-v1"],
            cwd="/runtime",
            env={"PATH": "/usr/bin:/bin"},
            timeout=120.0,
            uid=65001,
            gid=65001,
        )

    def test_build_identity_probe_uses_bounded_uid_runner(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=BUILD_IDENTITY_REPO_ROOT,
        ) as directory:
            snapshot_path = Path(directory) / "MicroMachine.snapshot"
            snapshot_path.write_bytes(b"fixture")
            ctest_environment = {
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            }

            def run_native(
                argv: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess[bytes]:
                del kwargs
                command = list(argv)
                stdout = (
                    b"sha256:" + (b"a" * 64) + b"\n"
                    if "--voi-build-input-identity" in command
                    else b'{"tests":[]}\n'
                )
                return subprocess.CompletedProcess(command, 0, stdout, b"")

            def reconstruct(
                config: object,
                *,
                binary_identity_runner: object,
                ctest_registry_runner: object,
            ) -> dict[str, object]:
                self.assertIs(mock.sentinel.config, config)
                self.assertTrue(callable(binary_identity_runner))
                observed = binary_identity_runner(
                    (
                        "/candidate/MicroMachine",
                        "--voi-build-input-identity",
                    ),
                    snapshot_path,
                )
                self.assertEqual(
                    b"sha256:" + (b"a" * 64) + b"\n",
                    observed.stdout,
                )
                self.assertTrue(callable(ctest_registry_runner))
                registry = ctest_registry_runner(
                    (
                        "/candidate/ctest",
                        "--test-dir",
                        str(snapshot_path.parent),
                        "--show-only=json-v1",
                    ),
                    snapshot_path.parent,
                    ctest_environment,
                    120.0,
                )
                self.assertEqual(b'{"tests":[]}\n', registry.stdout)
                return {"ok": True}

            with (
                mock.patch.object(
                    provenance_module,
                    "_normalize_producer_identity",
                    return_value=(65001, 65001),
                ),
                mock.patch.object(
                    provenance_module,
                    "build_micromachine_build_identity",
                    side_effect=reconstruct,
                ),
                mock.patch.object(
                    provenance_module,
                    "_run_dedicated_uid_native_command",
                    side_effect=run_native,
                ) as native_runner,
            ):
                report = provenance_module._build_micromachine_identity_with_boundary(
                    mock.sentinel.config,
                    execution_uid=65001,
                    execution_gid=65001,
                )

        self.assertEqual({"ok": True}, report)
        self.assertEqual(
            [
                mock.call(
                    (
                        str(snapshot_path),
                        "--voi-build-input-identity",
                    ),
                    cwd=str(snapshot_path.parent),
                    env=provenance_module.SANITIZED_TEST_ENV,
                    timeout=provenance_module.BUILD_IDENTITY_PROBE_TIMEOUT_SECONDS,
                    uid=65001,
                    gid=65001,
                ),
                mock.call(
                    (
                        "/candidate/ctest",
                        "--test-dir",
                        str(snapshot_path.parent),
                        "--show-only=json-v1",
                    ),
                    cwd=str(snapshot_path.parent),
                    env=ctest_environment,
                    timeout=120.0,
                    uid=65001,
                    gid=65001,
                ),
            ],
            native_runner.call_args_list,
        )

    def test_dedicated_producer_uid_ctest_registry_closes_stdin_and_cleans_descendant(
        self,
    ) -> None:
        producer_uid, producer_gid = self.dedicated_producer_uid()
        with tempfile.TemporaryDirectory(
            dir=BUILD_IDENTITY_REPO_ROOT,
        ) as directory:
            root = Path(directory)
            root.chmod(0o711)
            producer_io = root / "producer-io"
            producer_io.mkdir(mode=0o700)
            os.chown(producer_io, producer_uid, producer_gid)
            observation_path = producer_io / "ctest-observation.json"
            child_pid_path = producer_io / "ctest-child.pid"
            sentinel_token = "token-visible-only-in-root-parent"
            script = (
                "import json,os,subprocess,sys,time\n"
                "from pathlib import Path\n"
                "with open('/dev/null','wb') as sink:\n"
                "    subprocess.Popen(\n"
                "        ['/bin/sh','-c','echo $$ > \"$1\"; exec sleep 30',\n"
                "         'ctest-child',sys.argv[2]],\n"
                "        stdin=sink,stdout=sink,stderr=sink,\n"
                "        start_new_session=True,\n"
                "    )\n"
                "child_path=Path(sys.argv[2])\n"
                "for _ in range(100):\n"
                "    if child_path.is_file():\n"
                "        break\n"
                "    time.sleep(0.01)\n"
                "Path(sys.argv[1]).write_text(json.dumps({\n"
                "    'stdin':sys.stdin.buffer.read().decode(),\n"
                "    'token_env':os.environ.get('GITHUB_TOKEN'),\n"
                "}))\n"
            )

            def reconstruct(
                config: object,
                *,
                binary_identity_runner: object,
                ctest_registry_runner: object,
            ) -> dict[str, object]:
                del config, binary_identity_runner
                self.assertTrue(callable(ctest_registry_runner))
                ctest_registry_runner(
                    (
                        str(Path(sys.executable).resolve()),
                        "-I",
                        "-B",
                        "-S",
                        "-c",
                        script,
                        str(observation_path),
                        str(child_pid_path),
                    ),
                    producer_io,
                    provenance_module.SANITIZED_TEST_ENV,
                    5.0,
                )
                return {"ok": True}

            with (
                mock.patch.dict(
                    os.environ,
                    {"GITHUB_TOKEN": sentinel_token},
                ),
                mock.patch.object(
                    provenance_module,
                    "build_micromachine_build_identity",
                    side_effect=reconstruct,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "native verifier command left detached descendants",
                ),
            ):
                provenance_module._build_micromachine_identity_with_boundary(
                    mock.sentinel.config,
                    execution_uid=producer_uid,
                    execution_gid=producer_gid,
                )

            self.assertEqual(
                {"stdin": "", "token_env": None},
                json.loads(observation_path.read_text()),
            )
            child_pid = int(child_pid_path.read_text().strip())
            self.assertNotIn(
                child_pid,
                provenance_module._process_ids_for_uid(producer_uid),
            )
            self.assertEqual(
                (),
                provenance_module._process_ids_for_uid(producer_uid),
            )

    def test_dedicated_producer_uid_native_output_limit_cleans_descendant(
        self,
    ) -> None:
        producer_uid, producer_gid = self.dedicated_producer_uid()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o711)
            producer_io = root / "producer-io"
            producer_io.mkdir(mode=0o700)
            os.chown(producer_io, producer_uid, producer_gid)
            child_pid_path = producer_io / "native-output-child.pid"
            script = (
                "from pathlib import Path\n"
                "import os,subprocess,sys,time\n"
                "with open('/dev/null','wb') as sink:\n"
                "    subprocess.Popen(\n"
                "        ['/bin/sh','-c','echo $$ > \"$1\"; exec sleep 30',\n"
                "         'native-output-child',sys.argv[1]],\n"
                "        stdin=sink,stdout=sink,stderr=sink,\n"
                "        start_new_session=True,\n"
                "    )\n"
                "pid_path=Path(sys.argv[1])\n"
                "for _ in range(100):\n"
                "    if pid_path.is_file():\n"
                "        break\n"
                "    time.sleep(0.01)\n"
                "os.write(1,b'x'*4096)\n"
            )

            with (
                mock.patch.object(
                    provenance_module,
                    "MAX_PROCESS_STDOUT_BYTES",
                    128,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "stdout exceeded the bounded capture limit",
                ),
            ):
                provenance_module._run_dedicated_uid_native_command(
                    (
                        str(Path(sys.executable).resolve()),
                        "-I",
                        "-B",
                        "-S",
                        "-c",
                        script,
                        str(child_pid_path),
                    ),
                    cwd=str(producer_io),
                    env=SANITIZED_PRODUCER_ENV,
                    timeout=5.0,
                    uid=producer_uid,
                    gid=producer_gid,
                )

            child_pid = int(child_pid_path.read_text().strip())
            self.assertNotIn(
                child_pid,
                provenance_module._process_ids_for_uid(producer_uid),
            )
            self.assertEqual(
                (),
                provenance_module._process_ids_for_uid(producer_uid),
            )

    def test_github_actions_main_does_not_read_token_after_failed_preflight(
        self,
    ) -> None:
        environment = {
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_RUN_ATTEMPT": str(RUN_ATTEMPT),
            "GITHUB_RUN_ID": str(RUN_ID),
            "GITHUB_WORKFLOW_REF": (
                f"{REPOSITORY}/.github/workflows/ci.yml@refs/pull/137/merge"
            ),
            "GITHUB_WORKFLOW_SHA": HEAD_SHA,
            "VOI_CANDIDATE_WORKSPACE": "/candidate",
            "VOI_NODE_EXECUTABLE": "/usr/local/bin/node",
            "VOI_PRODUCER_GID": "65001",
            "VOI_PRODUCER_UID": "65001",
            "VOI_RELEASE_COMMIT": HEAD_SHA,
            "VOI_TRUSTED_VERIFIER_COMMIT": BASE_SHA,
            "VOI_TRUSTED_VERIFIER_WORKSPACE": "/trusted",
        }
        rejected_build = {
            "ok": False,
            "status": "blocked",
            "blockers": ["ctest failed"],
        }

        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch.object(
                provenance_module,
                "_attest_trusted_verifier_runtime",
                return_value={"ok": True},
            ),
            mock.patch.object(
                provenance_module,
                "attest_build_binding",
                return_value=rejected_build,
            ),
            mock.patch.object(
                provenance_module,
                "_read_github_token_from_stdin",
            ) as token_reader,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            returncode = provenance_module._main(
                (
                    "--emit-github-actions-bundle",
                    "/output/pre-live.zip",
                    "/runtime/voi_build_identity.json",
                    "/runtime",
                )
            )

        self.assertEqual(1, returncode)
        token_reader.assert_not_called()

    def test_dedicated_producer_uid_kills_setsided_descendant_on_timeout(
        self,
    ) -> None:
        producer_uid, producer_gid = self.dedicated_producer_uid()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o711)
            state_dir = root / "state"
            state_dir.mkdir(mode=0o700)
            producer_io = root / "producer-io"
            producer_io.mkdir(mode=0o700)
            os.chown(producer_io, producer_uid, producer_gid)
            child_pid_path = producer_io / "detached-child.pid"
            script = (
                "import pathlib,subprocess,sys\n"
                "child=subprocess.Popen(\n"
                "    ['/bin/sh','-c','echo $$ > \"$1\"; exec sleep 30',\n"
                "     'detached-child',sys.argv[1]],\n"
                "    start_new_session=True,\n"
                ")\n"
                "child.wait()\n"
            ).encode()
            argv = (
                str(Path(sys.executable).resolve()),
                "-I",
                "-B",
                "-S",
                "-c",
                ISOLATED_PYTHON_BOOTSTRAP,
                str(root),
                "timeout_producer.py",
                str(child_pid_path),
            )
            executable_payload = Path(argv[0]).read_bytes()

            with self.assertRaises(subprocess.TimeoutExpired):
                provenance_module._run_pinned_command(
                    subprocess.run,
                    argv,
                    executable_payload=executable_payload,
                    executable_snapshot=(
                        0,
                        0,
                        len(executable_payload),
                        0,
                        hashlib.sha256(executable_payload).hexdigest(),
                    ),
                    authenticated_python_sources={
                        "timeout_producer.py": script,
                    },
                    state_dir=state_dir,
                    cwd=str(producer_io),
                    timeout=1.0,
                    producer_uid=producer_uid,
                    producer_gid=producer_gid,
                )

            self.assertTrue(child_pid_path.is_file())
            child_pid = int(child_pid_path.read_text().strip())
            self.assertNotIn(
                child_pid,
                provenance_module._process_ids_for_uid(producer_uid),
            )
            self.assertEqual(
                (),
                provenance_module._process_ids_for_uid(producer_uid),
            )

    def test_dedicated_producer_uid_output_limit_cleans_descendant(
        self,
    ) -> None:
        producer_uid, producer_gid = self.dedicated_producer_uid()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o711)
            state_dir = root / "state"
            state_dir.mkdir(mode=0o700)
            producer_io = root / "producer-io"
            producer_io.mkdir(mode=0o700)
            os.chown(producer_io, producer_uid, producer_gid)
            child_pid_path = producer_io / "producer-output-child.pid"
            script = (
                "from pathlib import Path\n"
                "import os,subprocess,sys,time\n"
                "with open('/dev/null','wb') as sink:\n"
                "    subprocess.Popen(\n"
                "        ['/bin/sh','-c','echo $$ > \"$1\"; exec sleep 30',\n"
                "         'producer-output-child',sys.argv[1]],\n"
                "        stdin=sink,stdout=sink,stderr=sink,\n"
                "        start_new_session=True,\n"
                "    )\n"
                "pid_path=Path(sys.argv[1])\n"
                "for _ in range(100):\n"
                "    if pid_path.is_file():\n"
                "        break\n"
                "    time.sleep(0.01)\n"
                "os.write(1,b'x'*4096)\n"
            ).encode()
            argv = (
                str(Path(sys.executable).resolve()),
                "-I",
                "-B",
                "-S",
                "-c",
                ISOLATED_PYTHON_BOOTSTRAP,
                str(root),
                "output_limit_producer.py",
                str(child_pid_path),
            )
            executable_payload = Path(argv[0]).read_bytes()

            with (
                mock.patch.object(
                    provenance_module,
                    "MAX_PROCESS_STDOUT_BYTES",
                    128,
                ),
                self.assertRaisesRegex(
                    OSError,
                    "stdout exceeded the bounded capture limit",
                ),
            ):
                provenance_module._run_pinned_command(
                    subprocess.run,
                    argv,
                    executable_payload=executable_payload,
                    executable_snapshot=(
                        0,
                        0,
                        len(executable_payload),
                        0,
                        hashlib.sha256(executable_payload).hexdigest(),
                    ),
                    authenticated_python_sources={
                        "output_limit_producer.py": script,
                    },
                    state_dir=state_dir,
                    cwd=str(producer_io),
                    timeout=5.0,
                    producer_uid=producer_uid,
                    producer_gid=producer_gid,
                )

            child_pid = int(child_pid_path.read_text().strip())
            self.assertNotIn(
                child_pid,
                provenance_module._process_ids_for_uid(producer_uid),
            )
            self.assertEqual(
                (),
                provenance_module._process_ids_for_uid(producer_uid),
            )

    def test_timeout_reaps_main_before_dedicated_uid_cleanup(self) -> None:
        events: list[str] = []

        class TimedOutProcess:
            pid = 4321
            returncode: int | None = None

            def kill(self) -> None:
                events.append("kill")

            def wait(self, *, timeout: float) -> int:
                self.assert_timeout(timeout)
                events.append("wait")
                self.returncode = -9
                return self.returncode

            def communicate(self) -> tuple[bytes, bytes]:
                events.append("communicate")
                return b"stdout", b"stderr"

            @staticmethod
            def assert_timeout(timeout: float) -> None:
                if timeout != 1.0:
                    raise AssertionError(f"unexpected timeout: {timeout}")

        process = TimedOutProcess()
        with (
            mock.patch.object(
                provenance_module.os,
                "killpg",
                side_effect=lambda pid, sig: events.append("killpg"),
            ),
            mock.patch.object(
                provenance_module,
                "_terminate_producer_uid_processes",
                side_effect=lambda uid: events.append("uid_cleanup") or (),
            ),
        ):
            stdout, stderr = provenance_module._finish_timed_out_process(
                process,
                producer_uid=65001,
            )

        self.assertEqual(b"stdout", stdout)
        self.assertEqual(b"stderr", stderr)
        self.assertEqual(
            ["killpg", "wait", "uid_cleanup", "communicate"],
            events,
        )

    def test_dedicated_producer_uid_native_command_rejects_descendant(
        self,
    ) -> None:
        producer_uid, producer_gid = self.dedicated_producer_uid()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o711)
            producer_io = root / "producer-io"
            producer_io.mkdir(mode=0o700)
            os.chown(producer_io, producer_uid, producer_gid)
            child_pid_path = producer_io / "detached-native-child.pid"
            script = (
                "from pathlib import Path\n"
                "import subprocess,sys,time\n"
                "with open('/dev/null','wb') as sink:\n"
                "    subprocess.Popen(\n"
                "        ['/bin/sh','-c','echo $$ > \"$1\"; exec sleep 30',\n"
                "         'detached-native-child',sys.argv[1]],\n"
                "        stdin=sink,stdout=sink,stderr=sink,\n"
                "        start_new_session=True,\n"
                "    )\n"
                "pid_path=Path(sys.argv[1])\n"
                "for _ in range(100):\n"
                "    if pid_path.is_file():\n"
                "        break\n"
                "    time.sleep(0.01)\n"
            )

            with self.assertRaisesRegex(
                OSError,
                "native verifier command left detached descendants",
            ):
                provenance_module._run_dedicated_uid_native_command(
                    (
                        str(Path(sys.executable).resolve()),
                        "-I",
                        "-B",
                        "-S",
                        "-c",
                        script,
                        str(child_pid_path),
                    ),
                    cwd=str(producer_io),
                    env=SANITIZED_PRODUCER_ENV,
                    timeout=5.0,
                    uid=producer_uid,
                    gid=producer_gid,
                )

            self.assertTrue(child_pid_path.is_file())
            child_pid = int(child_pid_path.read_text().strip())
            self.assertNotIn(
                child_pid,
                provenance_module._process_ids_for_uid(producer_uid),
            )
            self.assertEqual(
                (),
                provenance_module._process_ids_for_uid(producer_uid),
            )

    def test_dedicated_producer_uid_rejects_and_kills_normal_exit_descendant(
        self,
    ) -> None:
        producer_uid, producer_gid = self.dedicated_producer_uid()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o711)
            state_dir = root / "state"
            state_dir.mkdir(mode=0o700)
            producer_io = root / "producer-io"
            producer_io.mkdir(mode=0o700)
            os.chown(producer_io, producer_uid, producer_gid)
            child_pid_path = producer_io / "detached-child.pid"
            script = (
                "from pathlib import Path\n"
                "import subprocess,sys,time\n"
                "with open('/dev/null','wb') as sink:\n"
                "    subprocess.Popen(\n"
                "        ['/bin/sh','-c','echo $$ > \"$1\"; exec sleep 30',\n"
                "         'detached-child',sys.argv[1]],\n"
                "        stdin=sink,stdout=sink,stderr=sink,\n"
                "        start_new_session=True,\n"
                "    )\n"
                "pid_path=Path(sys.argv[1])\n"
                "for _ in range(100):\n"
                "    if pid_path.is_file():\n"
                "        break\n"
                "    time.sleep(0.01)\n"
            ).encode()
            argv = (
                str(Path(sys.executable).resolve()),
                "-I",
                "-B",
                "-S",
                "-c",
                ISOLATED_PYTHON_BOOTSTRAP,
                str(root),
                "detached_producer.py",
                str(child_pid_path),
            )
            executable_payload = Path(argv[0]).read_bytes()

            with self.assertRaisesRegex(
                OSError,
                "detached descendants",
            ):
                provenance_module._run_pinned_command(
                    subprocess.run,
                    argv,
                    executable_payload=executable_payload,
                    executable_snapshot=(
                        0,
                        0,
                        len(executable_payload),
                        0,
                        hashlib.sha256(executable_payload).hexdigest(),
                    ),
                    authenticated_python_sources={
                        "detached_producer.py": script,
                    },
                    state_dir=state_dir,
                    cwd=str(producer_io),
                    timeout=5.0,
                    producer_uid=producer_uid,
                    producer_gid=producer_gid,
                )

            self.assertTrue(child_pid_path.is_file())
            self.assertEqual(
                (),
                provenance_module._process_ids_for_uid(producer_uid),
            )

    def test_rejects_symlinked_output_leaf_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                producer_id="fixture_producer",
            )
            output = Path(str(policy["output_artifact"]))
            target = Path(directory) / "target.json"
            target.write_text("untouched\n")
            output.symlink_to(target)
            source_files = policy["runtime_sources"]["files"]
            called = False

            def forbidden(
                *args: object, **kwargs: object
            ) -> subprocess.CompletedProcess:
                nonlocal called
                called = True
                return subprocess.CompletedProcess([], 0, b"", b"")

            report = run_local_producer(
                repository_dir=fixture["repository"],
                cwd=policy["cwd"],
                argv=policy["argv"],
                allowed_argv=(policy["argv"],),
                output_artifact=output,
                command_runner=forbidden,
                authenticated_files=[item["path"] for item in source_files],
                authenticated_file_digests={
                    item["path"]: item["sha256"] for item in source_files
                },
            )

            self.assertFalse(report["ok"], report)
            self.assertFalse(called)
            self.assertEqual("untouched\n", target.read_text())

    def test_output_parent_symlink_race_fails_without_target_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                producer_id="fixture_producer",
            )
            output = Path(str(policy["output_artifact"]))
            original_parent = output.parent
            moved_parent = original_parent.with_name("fixture-original")
            attacker_target = Path(directory) / "attacker-target"
            attacker_target.mkdir()
            source_files = policy["runtime_sources"]["files"]

            def racing_runner(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                original_parent.rename(moved_parent)
                original_parent.symlink_to(attacker_target, target_is_directory=True)
                staged_output = Path(list(args[0])[-1])
                staged_output.write_text('{"fixture":true}\n')
                return subprocess.CompletedProcess(args[0], 0, b"", b"")

            report = run_local_producer(
                repository_dir=fixture["repository"],
                cwd=policy["cwd"],
                argv=policy["argv"],
                allowed_argv=(policy["argv"],),
                output_artifact=output,
                command_runner=racing_runner,
                authenticated_files=[item["path"] for item in source_files],
                authenticated_file_digests={
                    item["path"]: item["sha256"] for item in source_files
                },
            )

            self.assertFalse(report["ok"], report)
            self.assertFalse((attacker_target / output.name).exists())

    def test_executes_pinned_bytes_during_executable_path_swap_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            init_git_repo(root)
            cwd = root / "producer"
            cwd.mkdir()
            output = cwd / "evidence.json"
            executable = root / "producer.sh"
            executable.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' \'{"source":"pinned"}\' > "$1"\n'
            )
            executable.chmod(0o755)
            backup = root / "producer.original"

            def swapping_runner(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                executable.rename(backup)
                executable.write_text(
                    '#!/bin/sh\nprintf \'%s\\n\' \'{"source":"attacker"}\' > "$1"\n'
                )
                executable.chmod(0o755)
                try:
                    return subprocess.run(*args, **kwargs)
                finally:
                    executable.unlink()
                    backup.rename(executable)

            report = run_local_producer(
                repository_dir=root,
                cwd=cwd,
                argv=(str(executable), str(output)),
                allowed_argv=((str(executable), str(output)),),
                output_artifact=output,
                command_runner=swapping_runner,
            )

            self.assertTrue(report["ok"], report)
            self.assertEqual(
                {"source": "pinned"},
                json.loads(output.read_bytes()),
            )

    def test_pins_argv_binary_and_rejects_path_replacement_during_execution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory).resolve()
            root = temporary_root / "repo"
            root.mkdir()
            init_git_repo(root)
            cwd = root / "producer"
            cwd.mkdir()
            executable = root / "producer.sh"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            output = cwd / "evidence.json"
            admitted_binary = temporary_root / "MicroMachine"
            admitted_payload = b"trusted admitted MicroMachine bytes"
            admitted_binary.write_bytes(admitted_payload)
            admitted_binary.chmod(0o755)
            admitted_digest = hashlib.sha256(admitted_payload).hexdigest()
            producer_argv = (
                str(executable),
                "--micromachine-binary",
                str(admitted_binary),
                "--output",
                str(output),
            )
            observed_execution_binary: Path | None = None

            def replacing_runner(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                nonlocal observed_execution_binary
                execution_argv = list(args[0])
                observed_execution_binary = Path(execution_argv[2])
                self.assertNotEqual(admitted_binary, observed_execution_binary)
                self.assertEqual(
                    admitted_digest,
                    hashlib.sha256(
                        observed_execution_binary.read_bytes()
                    ).hexdigest(),
                )
                replacement = admitted_binary.with_name(
                    admitted_binary.name + ".replacement"
                )
                replacement.write_bytes(b"attacker binary bytes")
                replacement.chmod(0o755)
                os.replace(replacement, admitted_binary)
                Path(execution_argv[-1]).write_text(
                    json.dumps({"binary_sha256": admitted_digest}) + "\n"
                )
                return subprocess.CompletedProcess(
                    execution_argv,
                    0,
                    b"",
                    b"",
                )

            report = run_local_producer(
                repository_dir=root,
                cwd=cwd,
                argv=producer_argv,
                allowed_argv=(producer_argv,),
                output_artifact=output,
                command_runner=replacing_runner,
                pinned_argv_file_digests={
                    str(admitted_binary): admitted_digest
                },
            )

            self.assertFalse(report["ok"], report)
            self.assertIsNotNone(observed_execution_binary)
            self.assertEqual(
                {"binary_sha256": admitted_digest},
                json.loads(output.read_bytes()),
            )
            self.assertIn(
                "pinned argv file pathname changed",
                " ".join(report["blockers"]),
            )

    def test_pins_node_and_rejects_path_replacement_during_execution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory).resolve()
            root = temporary_root / "repo"
            root.mkdir()
            init_git_repo(root)
            cwd = root / "producer"
            cwd.mkdir()
            executable = root / "producer.sh"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            output = cwd / "evidence.json"
            admitted_node = temporary_root / "node"
            admitted_payload = b"trusted admitted Node.js bytes"
            admitted_node.write_bytes(admitted_payload)
            admitted_node.chmod(0o755)
            admitted_digest = hashlib.sha256(admitted_payload).hexdigest()
            producer_argv = (
                str(executable),
                "--node-executable",
                str(admitted_node),
                "--output",
                str(output),
            )

            def replacing_runner(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                execution_argv = list(args[0])
                execution_node = Path(execution_argv[2])
                self.assertEqual(Path("/dev/fd"), execution_node.parent)
                self.assertEqual(
                    admitted_digest,
                    hashlib.sha256(execution_node.read_bytes()).hexdigest(),
                )
                replacement = admitted_node.with_name("node.replacement")
                replacement.write_bytes(b"attacker Node.js bytes")
                replacement.chmod(0o755)
                os.replace(replacement, admitted_node)
                Path(execution_argv[-1]).write_text(
                    json.dumps({"node_sha256": admitted_digest}) + "\n"
                )
                return subprocess.CompletedProcess(
                    execution_argv,
                    0,
                    b"",
                    b"",
                )

            report = run_local_producer(
                repository_dir=root,
                cwd=cwd,
                argv=producer_argv,
                allowed_argv=(producer_argv,),
                output_artifact=output,
                command_runner=replacing_runner,
                pinned_argv_file_digests={
                    str(admitted_node): admitted_digest
                },
            )

            self.assertFalse(report["ok"], report)
            self.assertEqual(
                {"node_sha256": admitted_digest},
                json.loads(output.read_bytes()),
            )
            self.assertIn(
                "pinned argv file pathname changed",
                " ".join(report["blockers"]),
            )

    def test_private_argv_binary_snapshot_has_no_replaceable_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory).resolve()
            root = temporary_root / "repo"
            root.mkdir()
            init_git_repo(root)
            cwd = root / "producer"
            cwd.mkdir()
            executable = root / "producer.sh"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
            output = cwd / "evidence.json"
            admitted_binary = temporary_root / "MicroMachine"
            admitted_payload = b"trusted admitted MicroMachine bytes"
            admitted_binary.write_bytes(admitted_payload)
            admitted_binary.chmod(0o755)
            admitted_digest = hashlib.sha256(admitted_payload).hexdigest()
            producer_argv = (
                str(executable),
                "--micromachine-binary",
                str(admitted_binary),
                "--output",
                str(output),
            )

            def replacing_runner(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                execution_argv = list(args[0])
                execution_binary = Path(execution_argv[2])
                self.assertEqual(Path("/dev/fd"), execution_binary.parent)
                self.assertEqual(
                    admitted_digest,
                    hashlib.sha256(execution_binary.read_bytes()).hexdigest(),
                )
                replacement = temporary_root / "attacker-binary"
                replacement.write_bytes(b"attacker binary bytes")
                replacement.chmod(0o500)
                with self.assertRaises(OSError):
                    os.replace(replacement, execution_binary)
                Path(execution_argv[-1]).write_text(
                    json.dumps({"binary_sha256": admitted_digest}) + "\n"
                )
                return subprocess.CompletedProcess(
                    execution_argv,
                    0,
                    b"",
                    b"",
                )

            report = run_local_producer(
                repository_dir=root,
                cwd=cwd,
                argv=producer_argv,
                allowed_argv=(producer_argv,),
                output_artifact=output,
                command_runner=replacing_runner,
                pinned_argv_file_digests={
                    str(admitted_binary): admitted_digest
                },
            )

            self.assertTrue(report["ok"], report)
            self.assertEqual(
                {"binary_sha256": admitted_digest},
                json.loads(output.read_bytes()),
            )
            self.assertEqual(admitted_payload, admitted_binary.read_bytes())

    def test_authenticated_timeout_kills_native_process_group_and_cleans_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            state_dir.mkdir(mode=0o700)
            child_pid_path = root / "native-child.pid"
            script = (
                "import os,pathlib,subprocess,sys\n"
                "root=pathlib.Path(os.environ['VOI_PINNED_NATIVE_EXEC_ROOT'])\n"
                "residual=root/'.voi-native-exec-timeout'\n"
                "residual.mkdir(mode=0o700)\n"
                "(residual/'MicroMachine').write_bytes(b'residual')\n"
                "child=subprocess.Popen(['/bin/sh','-c',"
                "'echo $$ > \"$1\"; exec sleep 30','timeout-child',"
                "sys.argv[1]])\n"
                "child.wait()\n"
            ).encode()
            argv = (
                str(Path(sys.executable).resolve()),
                "-I",
                "-B",
                "-S",
                "-c",
                ISOLATED_PYTHON_BOOTSTRAP,
                str(root),
                "timeout_producer.py",
                str(child_pid_path),
            )
            executable_payload = Path(argv[0]).read_bytes()

            with self.assertRaises(subprocess.TimeoutExpired):
                provenance_module._run_pinned_command(
                    subprocess.run,
                    argv,
                    executable_payload=executable_payload,
                    executable_snapshot=(
                        0,
                        0,
                        len(executable_payload),
                        0,
                        hashlib.sha256(executable_payload).hexdigest(),
                    ),
                    authenticated_python_sources={
                        "timeout_producer.py": script,
                    },
                    state_dir=state_dir,
                    cwd=str(root),
                    timeout=1.0,
                )

            self.assertTrue(child_pid_path.is_file())
            child_pid = int(child_pid_path.read_text().strip())
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                self.fail("native timeout child survived process-group cleanup")
            self.assertEqual(
                [],
                list(state_dir.glob(".native-execution-*")),
            )

    def test_executes_authenticated_python_bytes_not_mutable_snapshot_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                producer_id="fixture_producer",
            )
            source_files = policy["runtime_sources"]["files"]
            output = Path(str(policy["output_artifact"]))
            sentinel = Path(directory) / "snapshot-attacker-executed"
            real_write = provenance_module._write_private_snapshot_file

            def mutate_snapshot(path: Path, payload: bytes) -> None:
                real_write(path, payload)
                if path.name != "fixture_producer.py":
                    return
                path.chmod(0o600)
                path.write_text(
                    "from pathlib import Path\n"
                    f"Path({str(sentinel)!r}).write_text('executed')\n"
                    "raise SystemExit(0)\n"
                )
                path.chmod(0o400)

            with mock.patch.object(
                provenance_module,
                "_write_private_snapshot_file",
                side_effect=mutate_snapshot,
            ):
                report = run_local_producer(
                    repository_dir=fixture["repository"],
                    cwd=policy["cwd"],
                    argv=policy["argv"],
                    allowed_argv=(policy["argv"],),
                    output_artifact=output,
                    authenticated_files=[item["path"] for item in source_files],
                    authenticated_file_digests={
                        item["path"]: item["sha256"] for item in source_files
                    },
                )

            self.assertTrue(report["ok"], report)
            self.assertFalse(sentinel.exists())
            self.assertEqual({"fixture": True}, json.loads(output.read_bytes()))

    def test_output_replacement_after_atomic_publication_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=fixture["repository_commit"],
                producer_id="fixture_producer",
            )
            source_files = policy["runtime_sources"]["files"]
            output = Path(str(policy["output_artifact"]))
            trusted_output = b'{"source":"captured"}\n'

            def producer(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                Path(list(args[0])[-1]).write_bytes(trusted_output)
                return subprocess.CompletedProcess(args[0], 0, b"", b"")

            real_publish = provenance_module._write_output_atomically

            def replace_after_publish(
                path: Path,
                payload: bytes,
                *,
                expected_parent_identity: tuple[int, int, int, int] | None,
            ) -> tuple[int, int, int, int, str]:
                identity = real_publish(
                    path,
                    payload,
                    expected_parent_identity=expected_parent_identity,
                )
                path.write_bytes(b'{"source":"attacker"}\n')
                return identity

            with mock.patch.object(
                provenance_module,
                "_write_output_atomically",
                side_effect=replace_after_publish,
            ):
                report = run_local_producer(
                    repository_dir=fixture["repository"],
                    cwd=policy["cwd"],
                    argv=policy["argv"],
                    allowed_argv=(policy["argv"],),
                    output_artifact=output,
                    command_runner=producer,
                    authenticated_files=[item["path"] for item in source_files],
                    authenticated_file_digests={
                        item["path"]: item["sha256"] for item in source_files
                    },
                )

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "changed after publication",
                " ".join(report["blockers"]),
            )
            self.assertEqual(
                hashlib.sha256(trusted_output).hexdigest(),
                report["output_artifact"]["sha256"],
            )

    def test_records_derived_execution_and_artifact_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            commit = init_git_repo(root)
            cwd = root / "producer"
            cwd.mkdir()
            executable = cwd / "fixture-producer"
            executable.write_text("#!/bin/sh\n")
            executable.chmod(0o700)
            output = cwd / "evidence.json"
            producer_argv = (str(executable), "fixture-producer")

            def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
                self.assertEqual(SANITIZED_PRODUCER_ENV, kwargs["env"])
                self.assertFalse(kwargs["shell"])
                output.write_bytes(b'{"evidence":true}\n')
                return subprocess.CompletedProcess(
                    args[0],
                    0,
                    stdout=b"producer stdout",
                    stderr=b"",
                )

            report = run_local_producer(
                repository_dir=root,
                cwd=cwd,
                argv=producer_argv,
                allowed_argv=(producer_argv,),
                output_artifact=output,
                command_runner=runner,
            )

            self.assertTrue(report["ok"], report)
            self.assertEqual(commit, report["repository_commit"])
            self.assertEqual(0, report["exit_code"])
            self.assertEqual(
                hashlib.sha256(b'{"evidence":true}\n').hexdigest(),
                report["output_artifact"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256(b"producer stdout").hexdigest(),
                report["stdout_sha256"],
            )

    def test_producer_timestamps_use_monotonic_elapsed_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            init_git_repo(root)
            cwd = root / "producer"
            cwd.mkdir()
            executable = cwd / "fixture-producer"
            executable.write_text("#!/bin/sh\n")
            executable.chmod(0o700)
            output = cwd / "evidence.json"
            producer_argv = (str(executable), "fixture-producer")

            def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
                output.write_bytes(b'{"evidence":true}\n')
                return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

            with (
                mock.patch.object(
                    provenance_module,
                    "_utc_now",
                    return_value="2026-07-31T00:00:04.000000Z",
                ) as utc_now,
                mock.patch.object(
                    provenance_module.time,
                    "monotonic",
                    side_effect=(100.0, 104.0),
                ),
            ):
                report = run_local_producer(
                    repository_dir=root,
                    cwd=cwd,
                    argv=producer_argv,
                    allowed_argv=(producer_argv,),
                    output_artifact=output,
                    command_runner=runner,
                )

            self.assertTrue(report["ok"], report)
            self.assertEqual(1, utc_now.call_count)
            self.assertEqual(
                "2026-07-31T00:00:04.000000Z",
                report["started_at"],
            )
            self.assertEqual(
                "2026-07-31T00:00:08Z",
                report["ended_at"],
            )

    def test_rejects_ambient_python_module_launcher_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = make_build_fixture(Path(directory))
            policy_path = fixture["repository"] / PRODUCER_POLICY_RELATIVE_PATH
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "producers": {
                            "fixture_producer": {
                                "argv": [
                                    "{python}",
                                    "-m",
                                    "fixture_producer",
                                    "--output",
                                    "{output}",
                                ],
                                "cwd": ".",
                                "output": "fixture/evidence.json",
                            }
                        },
                    }
                )
            )
            git(fixture["repository"], "add", ".")
            git(
                fixture["repository"],
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "insecure policy fixture",
            )
            commit = git(
                fixture["repository"],
                "rev-parse",
                "HEAD",
            ).stdout.strip()

            policy = resolve_local_producer_policy(
                repository_dir=fixture["repository"],
                expected_commit=commit,
                producer_id="fixture_producer",
            )

            self.assertFalse(policy["ok"], policy)
            self.assertIn("isolated Python launcher", " ".join(policy["blockers"]))

    def test_rejects_nonallowlisted_exit_failure_and_stale_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            init_git_repo(root)
            cwd = root / "producer"
            cwd.mkdir()
            executable = cwd / "fixture-producer"
            executable.write_text("#!/bin/sh\n")
            executable.chmod(0o700)
            output = cwd / "evidence.json"
            producer_argv = (str(executable), "fixture-producer")
            calls: list[object] = []

            def should_not_run(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                calls.append(args)
                return subprocess.CompletedProcess([], 0, b"", b"")

            report = run_local_producer(
                repository_dir=root,
                cwd=cwd,
                argv=(str(executable), "-c", "touch evidence.json"),
                allowed_argv=(producer_argv,),
                output_artifact=output,
                command_runner=should_not_run,
            )
            self.assertFalse(report["ok"], report)
            self.assertEqual([], calls)

            def failing(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                output.write_text("failed evidence\n")
                return subprocess.CompletedProcess([], 7, b"", b"failure")

            report = run_local_producer(
                repository_dir=root,
                cwd=cwd,
                argv=producer_argv,
                allowed_argv=(producer_argv,),
                output_artifact=output,
                command_runner=failing,
            )
            self.assertFalse(report["ok"], report)
            self.assertEqual(7, report["exit_code"])

            def stale(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                return subprocess.CompletedProcess([], 0, b"", b"")

            report = run_local_producer(
                repository_dir=root,
                cwd=cwd,
                argv=producer_argv,
                allowed_argv=(producer_argv,),
                output_artifact=output,
                command_runner=stale,
            )
            self.assertFalse(report["ok"], report)
            self.assertIn("not refreshed", " ".join(report["blockers"]))


class ReplayLedgerTest(unittest.TestCase):
    def test_declared_ruleset_payloads_match_runtime_contract(self) -> None:
        ruleset_root = BUILD_IDENTITY_REPO_ROOT / ".github" / "rulesets"
        create = json.loads(
            (ruleset_root / "voi-pre-live-replay-create-only.json").read_text()
        )
        immutable = json.loads(
            (ruleset_root / "voi-pre-live-replay-immutable.json").read_text()
        )

        self.assertEqual(
            {
                "name": AUTHORITATIVE_REPLAY_CREATE_RULESET_NAME,
                "target": "tag",
                "enforcement": "active",
                "bypass_actors": [
                    {
                        "actor_id": AUTHORITATIVE_REPLAY_CLAIMER_USER_ID,
                        "actor_type": "User",
                        "bypass_mode": "always",
                    }
                ],
                "conditions": {
                    "ref_name": {
                        "include": [AUTHORITATIVE_REPLAY_REF_PATTERN],
                        "exclude": [],
                    }
                },
                "rules": [{"type": "creation"}],
            },
            create,
        )
        self.assertEqual(
            {
                "name": AUTHORITATIVE_REPLAY_IMMUTABLE_RULESET_NAME,
                "target": "tag",
                "enforcement": "active",
                "bypass_actors": [],
                "conditions": {
                    "ref_name": {
                        "include": [AUTHORITATIVE_REPLAY_REF_PATTERN],
                        "exclude": [],
                    }
                },
                "rules": [{"type": "update"}, {"type": "deletion"}],
            },
            immutable,
        )

    def test_attests_exact_create_only_and_immutable_rulesets(self) -> None:
        adapter = FakeGitHubAdapter()

        report = attest_github_replay_rulesets(
            adapter,
            repository=REPOSITORY,
        )

        self.assertTrue(report["ok"], report)
        self.assertEqual(
            {
                AUTHORITATIVE_REPLAY_CREATE_RULESET_NAME,
                AUTHORITATIVE_REPLAY_IMMUTABLE_RULESET_NAME,
            },
            set(report["rulesets"]),
        )

    def test_replay_store_rejects_invalid_rulesets_before_claim(self) -> None:
        mutations = {
            "missing create ruleset": lambda adapter: adapter.rulesets.pop(0),
            "duplicate immutable ruleset": lambda adapter: adapter.rulesets.append(
                dict(adapter.rulesets[1], id=503)
            ),
            "inactive": lambda adapter: adapter.ruleset_details[501].update(
                {"enforcement": "disabled"}
            ),
            "wrong target": lambda adapter: adapter.ruleset_details[501].update(
                {"target": "branch"}
            ),
            "wrong source type": lambda adapter: adapter.ruleset_details[501].update(
                {"source_type": "Organization"}
            ),
            "broad include": lambda adapter: adapter.ruleset_details[501]["conditions"][
                "ref_name"
            ].update({"include": ["refs/tags/**"]}),
            "excluded replay ref": lambda adapter: adapter.ruleset_details[501][
                "conditions"
            ]["ref_name"].update({"exclude": ["refs/tags/voi-pre-live-replay/x"]}),
            "extra condition": lambda adapter: adapter.ruleset_details[501][
                "conditions"
            ].update({"repository_name": {"include": ["~ALL"], "exclude": []}}),
            "wrong create rule": lambda adapter: adapter.ruleset_details[501].update(
                {"rules": [{"type": "update"}]}
            ),
            "duplicate immutable rule": lambda adapter: adapter.ruleset_details[
                502
            ].update(
                {
                    "rules": [
                        {"type": "update"},
                        {"type": "update"},
                        {"type": "deletion"},
                    ]
                }
            ),
            "create rule parameters": lambda adapter: adapter.ruleset_details[501][
                "rules"
            ][0].update({"parameters": {"unexpected": True}}),
            "immutable rule extra key": lambda adapter: adapter.ruleset_details[502][
                "rules"
            ][0].update({"unexpected": True}),
            "overbroad create bypass": lambda adapter: adapter.ruleset_details[501][
                "bypass_actors"
            ].append(
                {
                    "actor_id": 1,
                    "actor_type": "OrganizationAdmin",
                    "bypass_mode": "always",
                }
            ),
            "wrong claimer user": lambda adapter: adapter.ruleset_details[501][
                "bypass_actors"
            ][0].update({"actor_id": AUTHORITATIVE_REPLAY_CLAIMER_USER_ID + 1}),
            "immutable bypass": lambda adapter: adapter.ruleset_details[502].update(
                {
                    "bypass_actors": [
                        {
                            "actor_id": AUTHORITATIVE_REPLAY_CLAIMER_USER_ID,
                            "actor_type": "User",
                            "bypass_mode": "always",
                        }
                    ]
                }
            ),
            "detail id mismatch": lambda adapter: adapter.ruleset_details[501].update(
                {"id": 999}
            ),
        }
        digest = "sha256:" + "b" * 64
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                adapter = FakeGitHubAdapter()
                mutate(adapter)

                report = GitHubRefReplayStore(adapter).consume(
                    repository=REPOSITORY,
                    replay_digest=digest,
                    expected_head_sha=HEAD_SHA,
                )

                self.assertFalse(report["ok"], report)
                self.assertFalse(report["consumed"])
                self.assertEqual({}, adapter.references)
                self.assertIn("rulesets:", " ".join(report["blockers"]))

    def test_replay_store_fails_closed_when_ruleset_api_is_unavailable(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.fail_at = "rulesets"

        report = GitHubRefReplayStore(adapter).consume(
            repository=REPOSITORY,
            replay_digest="sha256:" + "b" * 64,
            expected_head_sha=HEAD_SHA,
        )

        self.assertFalse(report["ok"], report)
        self.assertFalse(report["consumed"])
        self.assertEqual({}, adapter.references)
        self.assertIn("listing failed", " ".join(report["blockers"]))

    def test_canonical_digest_binds_run_artifact_and_build_identity(self) -> None:
        github_source = {
            "ok": True,
            "repository": REPOSITORY,
            "head_sha": HEAD_SHA,
            "workflow_path": WORKFLOW_PATH,
            "workflow_ref": WORKFLOW_REF,
            "workflow_sha": WORKFLOW_SHA,
            "artifact_sha256": "1" * 64,
            "source_ids": {
                "repository_id": AUTHORITATIVE_REPOSITORY_ID,
                "issue_id": 2,
                "issue_number": 138,
                "workflow_run_id": RUN_ID,
                "workflow_id": WORKFLOW_ID,
                "run_attempt": RUN_ATTEMPT,
                "job_id": JOB_ID,
                "artifact_database_id": ARTIFACT_ID,
            },
        }
        build_binding = {
            "ok": True,
            "current_identity": "sha256:" + "2" * 64,
            "binary_sha256": "3" * 64,
            "repository_inputs": {"digest": "sha256:" + "4" * 64},
            "ctest": {
                "test_manifest_sha256": "sha256:" + "5" * 64,
                "registry_sha256": "sha256:" + "c" * 64,
            },
        }
        producer_policy = {
            "ok": True,
            "producer_id": "fixture_producer",
            "policy_sha256": "6" * 64,
            "argv_sha256": "7" * 64,
            "module": {"sha256": "a" * 64},
            "runtime_sources": {"digest": "sha256:" + "b" * 64},
        }
        local_execution = {
            "ok": True,
            "executable_sha256": "8" * 64,
            "output_artifact": {"sha256": "9" * 64},
        }

        first = canonical_replay_digest(
            github_source,
            build_binding,
            producer_policy,
            local_execution,
        )
        changed_build = dict(build_binding)
        changed_build["binary_sha256"] = "a" * 64
        second = canonical_replay_digest(
            github_source,
            changed_build,
            producer_policy,
            local_execution,
        )

        self.assertNotEqual(first, second)
        for field, replacement in (
            (
                "workflow_ref",
                f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/main",
            ),
            ("workflow_sha", "d" * 40),
        ):
            with self.subTest(field=field):
                changed_source = dict(github_source)
                changed_source[field] = replacement
                self.assertNotEqual(
                    first,
                    canonical_replay_digest(
                        changed_source,
                        build_binding,
                        producer_policy,
                        local_execution,
                    ),
                )
        changed_issue_source = dict(github_source)
        changed_issue_source["source_ids"] = {
            **github_source["source_ids"],
            "issue_id": 143,
            "issue_number": 143,
        }
        self.assertNotEqual(
            first,
            canonical_replay_digest(
                changed_issue_source,
                build_binding,
                producer_policy,
                local_execution,
            ),
        )
        missing_workflow_sha = dict(github_source)
        missing_workflow_sha.pop("workflow_sha")
        with self.assertRaises(ValueError):
            canonical_replay_digest(
                missing_workflow_sha,
                build_binding,
                producer_policy,
                local_execution,
            )
        rejected_source = dict(github_source)
        rejected_source["ok"] = False
        with self.assertRaises(ValueError):
            canonical_replay_digest(
                rejected_source,
                build_binding,
                producer_policy,
                local_execution,
            )

    def test_missing_ledger_initializes_once_and_duplicate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            digest = "sha256:" + "1" * 64

            first = consume_replay_ledger(
                ledger,
                digest,
                source_ids={"run_id": RUN_ID},
            )
            second = consume_replay_ledger(
                ledger,
                digest,
                source_ids={"run_id": RUN_ID},
            )

            self.assertTrue(first["ok"], first)
            self.assertTrue(first["consumed"])
            self.assertFalse(second["ok"], second)
            self.assertFalse(second["consumed"])
            payload = json.loads(ledger.read_text())
            self.assertIn(digest, payload["entries"])

    def test_github_replay_reference_is_an_atomic_cross_runner_authority(self) -> None:
        adapter = FakeGitHubAdapter()
        digest = "sha256:" + "c" * 64

        first = consume_github_replay_reference(
            adapter,
            repository=REPOSITORY,
            replay_digest=digest,
            expected_head_sha=HEAD_SHA,
        )
        second = consume_github_replay_reference(
            adapter,
            repository=REPOSITORY,
            replay_digest=digest,
            expected_head_sha=HEAD_SHA,
        )

        self.assertTrue(first["ok"], first)
        self.assertTrue(first["consumed"])
        self.assertFalse(second["ok"], second)
        self.assertFalse(second["consumed"])
        self.assertIn("already consumed", " ".join(second["blockers"]))

    def test_parallel_github_replay_stores_have_exactly_one_winner(self) -> None:
        class AtomicReferenceAdapter:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.references: dict[str, dict[str, object]] = {}
                self.rulesets, self.ruleset_details = make_replay_ruleset_fixtures()

            def create_git_reference(
                self,
                repository: str,
                *,
                ref: str,
                sha: str,
            ) -> dict[str, object]:
                del repository
                with self.lock:
                    if ref in self.references:
                        raise GitHubHTTPError(
                            path="/git/refs",
                            status=422,
                            body=b'{"message":"Reference already exists"}',
                        )
                    result = {
                        "ref": ref,
                        "object": {"type": "commit", "sha": sha},
                    }
                    self.references[ref] = result
                    return result

            def get_git_reference(
                self,
                repository: str,
                *,
                ref: str,
            ) -> dict[str, object]:
                del repository
                with self.lock:
                    return dict(self.references[ref])

            def list_repository_rulesets(
                self,
                repository: str,
            ) -> list[dict[str, object]]:
                del repository
                return self.rulesets

            def get_repository_ruleset(
                self,
                repository: str,
                ruleset_id: int,
            ) -> dict[str, object]:
                del repository
                return self.ruleset_details[ruleset_id]

        backend = AtomicReferenceAdapter()
        stores = [GitHubRefReplayStore(backend) for _ in range(16)]
        digest = "sha256:" + "e" * 64

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(
                executor.map(
                    lambda store: store.consume(
                        repository=REPOSITORY,
                        replay_digest=digest,
                        expected_head_sha=HEAD_SHA,
                    ),
                    stores,
                )
            )

        self.assertEqual(1, sum(result["ok"] is True for result in results))
        self.assertEqual(1, sum(result["consumed"] is True for result in results))

    def test_github_replay_failure_boundaries_fail_closed(self) -> None:
        digest = "sha256:" + "f" * 64
        replay_ref = AUTHORITATIVE_REPLAY_REF_PREFIX + ("f" * 64)
        cases: dict[str, tuple[object, object, str]] = {
            "conflicting target": (
                GitHubHTTPError(
                    path="/git/refs",
                    status=422,
                    body=b"already exists",
                ),
                {
                    "ref": replay_ref,
                    "object": {"type": "commit", "sha": "b" * 40},
                },
                "conflicting target",
            ),
            "missing exact ref": (
                GitHubHTTPError(
                    path="/git/refs",
                    status=409,
                    body=b"conflict",
                ),
                GitHubHTTPError(
                    path="/git/ref",
                    status=404,
                    body=b"not found",
                ),
                "without an exact existing reference",
            ),
            "permission failure": (
                GitHubHTTPError(
                    path="/git/refs",
                    status=403,
                    body=b"forbidden",
                ),
                GitHubHTTPError(
                    path="/git/ref",
                    status=404,
                    body=b"not found",
                ),
                "HTTP 403",
            ),
            "timeout": (
                TimeoutError("timed out"),
                GitHubHTTPError(
                    path="/git/ref",
                    status=404,
                    body=b"not found",
                ),
                "timed out",
            ),
        }
        for name, (create_result, get_result, expected) in cases.items():
            with self.subTest(name=name):
                adapter = FakeGitHubAdapter()
                adapter.create_git_reference = mock.Mock(side_effect=create_result)
                if isinstance(get_result, BaseException):
                    adapter.get_git_reference = mock.Mock(side_effect=get_result)
                else:
                    adapter.get_git_reference = mock.Mock(return_value=get_result)

                report = consume_github_replay_reference(
                    adapter,
                    repository=REPOSITORY,
                    replay_digest=digest,
                    expected_head_sha=HEAD_SHA,
                )

                self.assertFalse(report["ok"], report)
                self.assertFalse(report["consumed"])
                self.assertIn(expected, " ".join(report["blockers"]))

    def test_global_ledger_rejects_the_same_digest_across_two_clones(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            global_root = root / "global-replay"
            clone_a = root / "clone-a"
            clone_b = root / "clone-b"
            clone_a.mkdir()
            clone_b.mkdir()
            init_git_repo(clone_a)
            init_git_repo(clone_b)
            digest = "sha256:" + "d" * 64

            with mock.patch(
                "starcraft_commander.micromachine_pre_live_provenance."
                "GLOBAL_REPLAY_STATE_ROOT",
                global_root,
            ):
                ledger_a = (
                    canonical_global_replay_state_dir(AUTHORITATIVE_REPOSITORY_ID)
                    / "replay-ledger.json"
                )
                first = consume_replay_ledger(
                    ledger_a,
                    digest,
                    source_ids={"clone": str(clone_a)},
                )
                ledger_b = (
                    canonical_global_replay_state_dir(AUTHORITATIVE_REPOSITORY_ID)
                    / "replay-ledger.json"
                )
                second = consume_replay_ledger(
                    ledger_b,
                    digest,
                    source_ids={"clone": str(clone_b)},
                )

            self.assertEqual(ledger_a, ledger_b)
            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"], second)
            self.assertIn("already consumed", " ".join(second["blockers"]))

    def test_malformed_ledger_fails_closed_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            ledger.write_text("{malformed")
            original = ledger.read_bytes()

            report = consume_replay_ledger(
                ledger,
                "sha256:" + "2" * 64,
                source_ids={"run_id": RUN_ID},
            )

            self.assertFalse(report["ok"], report)
            self.assertEqual(original, ledger.read_bytes())

    def test_symlink_ledger_fails_closed_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text('{"do_not_replace":true}\n')
            ledger = root / "ledger.json"
            ledger.symlink_to(target)

            report = consume_replay_ledger(
                ledger,
                "sha256:" + "4" * 64,
                source_ids={"run_id": RUN_ID},
            )

            self.assertFalse(report["ok"], report)
            self.assertEqual('{"do_not_replace":true}\n', target.read_text())

    def test_broken_symlink_and_crash_points_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broken = root / "broken-ledger.json"
            broken.symlink_to(root / "missing-target.json")
            self.assertTrue(os.path.lexists(broken))
            broken_result = consume_replay_ledger(
                broken,
                "sha256:" + "5" * 64,
                source_ids={"run_id": RUN_ID},
            )
            self.assertFalse(broken_result["ok"], broken_result)
            self.assertIn("symlink", " ".join(broken_result["blockers"]))

            ledger = root / "ledger.json"
            digest = "sha256:" + "6" * 64
            with mock.patch(
                "starcraft_commander.micromachine_pre_live_provenance.os.replace",
                side_effect=OSError("replace crash"),
            ):
                before_replace = consume_replay_ledger(
                    ledger,
                    digest,
                    source_ids={"run_id": RUN_ID},
                )
            self.assertFalse(before_replace["ok"], before_replace)
            self.assertFalse(ledger.exists())
            retry = consume_replay_ledger(
                ledger,
                digest,
                source_ids={"run_id": RUN_ID},
            )
            self.assertTrue(retry["ok"], retry)

        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            digest = "sha256:" + "7" * 64
            real_fsync = os.fsync
            fsync_calls = 0

            def fail_directory_fsync(file_descriptor: int) -> None:
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 2:
                    raise OSError("directory fsync crash")
                real_fsync(file_descriptor)

            with mock.patch(
                "starcraft_commander.micromachine_pre_live_provenance.os.fsync",
                side_effect=fail_directory_fsync,
            ):
                after_replace = consume_replay_ledger(
                    ledger,
                    digest,
                    source_ids={"run_id": RUN_ID},
                )
            self.assertFalse(after_replace["ok"], after_replace)
            self.assertTrue(ledger.exists())
            duplicate = consume_replay_ledger(
                ledger,
                digest,
                source_ids={"run_id": RUN_ID},
            )
            self.assertFalse(duplicate["ok"], duplicate)
            self.assertIn("already consumed", " ".join(duplicate["blockers"]))

    def test_concurrent_consumption_has_exactly_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            digest = "sha256:" + "3" * 64
            script = (
                "import json,sys;"
                "from starcraft_commander.micromachine_pre_live_provenance "
                "import consume_replay_ledger;"
                "print(json.dumps(consume_replay_ledger("
                "sys.argv[1],sys.argv[2],source_ids={'run_id':101})))"
            )
            processes = [
                subprocess.Popen(
                    [sys.executable, "-I", "-c", script, str(ledger), digest],
                    cwd=BUILD_IDENTITY_REPO_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env={"PATH": os.environ.get("PATH", "")},
                )
                for _ in range(16)
            ]
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(0, process.returncode, stderr)
                results.append(json.loads(stdout))

            self.assertEqual(1, sum(result["ok"] is True for result in results))
            self.assertEqual(
                1,
                sum(result["consumed"] is True for result in results),
            )


class AggregateProvenanceTest(unittest.TestCase):
    def attest(
        self,
        global_replay_root: Path,
        **kwargs: object,
    ) -> dict[str, object]:
        del global_replay_root
        adapter = kwargs["github_adapter"]
        kwargs["replay_store"] = GitHubRefReplayStore(adapter)
        return attest_pre_live_provenance(**kwargs)

    def test_non_deterministic_producers_cannot_qualify_or_consume_replay(
        self,
    ) -> None:
        for producer_id in ("provenance_qualification", "fixture_producer"):
            with self.subTest(producer_id=producer_id):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    build = make_build_fixture(root / "build-fixture")
                    commit = build["repository_commit"]
                    adapter = FakeGitHubAdapter(head_sha=commit)
                    adapter.workflow_run["head_sha"] = commit
                    adapter.attempt["head_sha"] = commit
                    adapter.pull_request["head"]["sha"] = commit
                    adapter.artifact["workflow_run"]["head_sha"] = commit
                    adapter.workflow_run["pull_requests"][0]["head"][
                        "sha"
                    ] = commit
                    adapter.attempt["pull_requests"][0]["head"]["sha"] = commit
                    bind_adapter_to_build_fixture(
                        adapter,
                        build,
                        output=b'{"trusted":"execution"}\n',
                    )
                    producer_calls: list[object] = []

                    def producer(
                        *args: object,
                        **kwargs: object,
                    ) -> subprocess.CompletedProcess:
                        producer_calls.append((args, kwargs))
                        output = Path(list(args[0])[-1])
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_text('{"trusted":"execution"}\n')
                        return subprocess.CompletedProcess(
                            args[0],
                            0,
                            b"out",
                            b"",
                        )

                    report = self.attest(
                        root / "global-replay",
                        repository_dir=build["repository"],
                        expected_commit=commit,
                        github_adapter=adapter,
                        issue_number=138,
                        pull_number=137,
                        run_id=RUN_ID,
                        run_attempt=RUN_ATTEMPT,
                        job_id=JOB_ID,
                        artifact_id=ARTIFACT_ID,
                        expected_head_sha=commit,
                        build_report_path=build["report_path"],
                        expected_build_dir=(
                            build["config"].micromachine_build_dir
                        ),
                        producer_id=producer_id,
                        ctest_runner=passing_ctest,
                        producer_runner=producer,
                    )

                    self.assertFalse(report["ok"], report)
                    self.assertEqual("blocked", report["status"])
                    self.assertEqual(0, len(producer_calls))
                    self.assertEqual({}, adapter.references)
                    self.assertIn(
                        "production candidate evidence requires producer_id="
                        f"{PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID!r}",
                        " ".join(report["blockers"]),
                    )

    def test_deterministic_github_replay_uses_pinned_node_descriptor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = make_build_fixture(root / "build-fixture")
            commit = build["repository_commit"]
            adapter = FakeGitHubAdapter(head_sha=commit)
            adapter.workflow_run["head_sha"] = commit
            adapter.attempt["head_sha"] = commit
            adapter.pull_request["head"]["sha"] = commit
            adapter.artifact["workflow_run"]["head_sha"] = commit
            adapter.workflow_run["pull_requests"][0]["head"]["sha"] = commit
            adapter.attempt["pull_requests"][0]["head"]["sha"] = commit
            node_executable = Path(sys.executable).resolve()
            admitted_policy = {
                "ok": True,
                "status": "accepted",
                "blockers": [],
                "producer_id": PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID,
                "node_executable_path": str(node_executable),
                "node_executable_sha256": hashlib.sha256(
                    node_executable.read_bytes()
                ).hexdigest(),
            }

            with (
                mock.patch.object(
                    provenance_module,
                    "resolve_local_producer_policy",
                    return_value=admitted_policy,
                ),
                mock.patch.object(
                    provenance_module,
                    "attest_github_source",
                    return_value={
                        "ok": False,
                        "status": "blocked",
                        "blockers": ["stop after descriptor observation"],
                    },
                ) as github_attestation,
            ):
                report = self.attest(
                    root / "global-replay",
                    repository_dir=build["repository"],
                    expected_commit=commit,
                    github_adapter=adapter,
                    issue_number=138,
                    pull_number=137,
                    run_id=RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    job_id=JOB_ID,
                    artifact_id=ARTIFACT_ID,
                    expected_head_sha=commit,
                    build_report_path=build["report_path"],
                    expected_build_dir=(
                        build["config"].micromachine_build_dir
                    ),
                    producer_id=(
                        PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
                    ),
                    node_executable=node_executable,
                    ctest_runner=passing_ctest,
                )

            self.assertFalse(report["ok"], report)
            descriptor = github_attestation.call_args.kwargs[
                "node_executable"
            ]
            self.assertRegex(str(descriptor), r"^/dev/fd/\d+$")
            self.assertNotEqual(str(node_executable), str(descriptor))

    def test_production_gate_ignores_caller_success_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = make_build_fixture(root / "build-fixture")
            repository = build["repository"]
            commit = build["repository_commit"]
            adapter = FakeGitHubAdapter(head_sha=commit)
            adapter.workflow_run["head_sha"] = commit
            adapter.attempt["head_sha"] = commit
            adapter.pull_request["head"]["sha"] = commit
            adapter.artifact["workflow_run"]["head_sha"] = commit
            adapter.workflow_run["pull_requests"][0]["head"]["sha"] = commit
            adapter.attempt["pull_requests"][0]["head"]["sha"] = commit
            bind_adapter_to_build_fixture(
                adapter,
                build,
                output=b'{"trusted":"execution"}\n',
            )

            def producer(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                output = Path(list(args[0])[-1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text('{"trusted":"execution"}\n')
                return subprocess.CompletedProcess(args[0], 0, b"out", b"")

            with mock.patch.object(
                provenance_module,
                "canonical_global_replay_state_dir",
                side_effect=AssertionError("local replay state must not be used"),
            ):
                report = self.attest(
                    root / "global-replay",
                    repository_dir=repository,
                    expected_commit=commit,
                    github_adapter=adapter,
                    issue_number=138,
                    pull_number=137,
                    run_id=RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    job_id=JOB_ID,
                    artifact_id=ARTIFACT_ID,
                    expected_head_sha=commit,
                    build_report_path=build["report_path"],
                    expected_build_dir=build["config"].micromachine_build_dir,
                    producer_id="fixture_producer",
                    ctest_runner=passing_ctest,
                    producer_runner=producer,
                    untrusted_payload={
                        "authority_scope": "release_post_merge",
                        "producer": "forged-producer",
                        "ok": False,
                        "release_authoritative": True,
                        "conclusion": "failure",
                        "state": "forged",
                        "sha256": "0" * 64,
                    },
                )

            self.assertFalse(report["ok"], report)
            self.assertEqual("blocked", report["status"])
            self.assertEqual({}, report["authority"])
            self.assertFalse(report["release_authoritative"])
            self.assertEqual(
                [
                    "authority_scope",
                    "conclusion",
                    "ok",
                    "producer",
                    "release_authoritative",
                    "sha256",
                    "state",
                ],
                report["ignored_untrusted_fields"],
            )
            self.assertEqual({}, report["accepted_source_ids"])
            self.assertEqual({}, report["accepted_digests"])
            self.assertIn(
                "production candidate evidence requires producer_id=",
                " ".join(report["blockers"]),
            )
            release = require_release_authority(report)
            self.assertFalse(release["ok"], release)
            self.assertIn(
                "authenticated post-merge release authority is not implemented",
                " ".join(release["blockers"]),
            )

    def test_forged_standalone_release_mapping_is_never_authoritative(self) -> None:
        release = require_release_authority(
            {
                "ok": True,
                "status": "ready_for_live_qa",
                "authority": {
                    "scope": "release_post_merge",
                    "release_authoritative": True,
                },
            }
        )

        self.assertFalse(release["ok"], release)
        self.assertFalse(release["release_authoritative"])
        self.assertEqual({}, release["authority"])
        self.assertIn(
            "authenticated post-merge release authority is not implemented",
            " ".join(release["blockers"]),
        )

    def test_forged_caller_success_cannot_override_server_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = make_build_fixture(root / "build-fixture")
            repository = build["repository"]
            commit = build["repository_commit"]
            adapter = FakeGitHubAdapter(head_sha=commit)
            adapter.workflow_run.update({"head_sha": commit, "conclusion": "failure"})
            adapter.attempt["head_sha"] = commit
            adapter.pull_request["head"]["sha"] = commit
            adapter.artifact["workflow_run"]["head_sha"] = commit
            adapter.workflow_run["pull_requests"][0]["head"]["sha"] = commit
            adapter.attempt["pull_requests"][0]["head"]["sha"] = commit
            producer_called = False

            def producer(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                nonlocal producer_called
                producer_called = True
                return subprocess.CompletedProcess([], 0, b"", b"")

            report = self.attest(
                root / "global-replay",
                repository_dir=repository,
                expected_commit=commit,
                github_adapter=adapter,
                issue_number=138,
                pull_number=137,
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                job_id=JOB_ID,
                artifact_id=ARTIFACT_ID,
                expected_head_sha=commit,
                build_report_path=build["report_path"],
                expected_build_dir=build["config"].micromachine_build_dir,
                producer_id="fixture_producer",
                ctest_runner=passing_ctest,
                producer_runner=producer,
                untrusted_payload={
                    "producer": "trusted",
                    "ok": True,
                    "conclusion": "success",
                    "sha256": hashlib.sha256(adapter.artifact_bytes).hexdigest(),
                },
            )

            self.assertFalse(report["ok"], report)
            self.assertEqual("blocked", report["status"])
            self.assertFalse(producer_called)
            self.assertEqual({}, adapter.references)
            self.assertFalse((root / "global-replay").exists())

    def test_valid_github_bundle_cannot_hide_different_local_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = make_build_fixture(root)
            repository = build["repository"]
            commit = build["repository_commit"]
            adapter = FakeGitHubAdapter(head_sha=commit)
            adapter.workflow_run["head_sha"] = commit
            adapter.attempt["head_sha"] = commit
            adapter.pull_request["head"]["sha"] = commit
            adapter.artifact["workflow_run"]["head_sha"] = commit
            adapter.workflow_run["pull_requests"][0]["head"]["sha"] = commit
            adapter.attempt["pull_requests"][0]["head"]["sha"] = commit
            bind_adapter_to_build_fixture(
                adapter,
                build,
                output=b'{"artifact":"different"}\n',
            )

            def producer(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                output = Path(list(args[0])[-1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text('{"trusted":"execution"}\n')
                return subprocess.CompletedProcess(args[0], 0, b"out", b"")

            with mock.patch.object(
                provenance_module,
                "_production_candidate_producer_blockers",
                return_value=[],
            ):
                report = self.attest(
                    root / "global-replay",
                    repository_dir=repository,
                    expected_commit=commit,
                    github_adapter=adapter,
                    issue_number=138,
                    pull_number=137,
                    run_id=RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    job_id=JOB_ID,
                    artifact_id=ARTIFACT_ID,
                    expected_head_sha=commit,
                    build_report_path=build["report_path"],
                    expected_build_dir=build["config"].micromachine_build_dir,
                    producer_id="fixture_producer",
                    ctest_runner=passing_ctest,
                    producer_runner=producer,
                )

            self.assertFalse(report["ok"], report)
            self.assertIn(
                "artifact/local binding mismatch",
                " ".join(report["blockers"]),
            )

    def test_bundled_stdio_claims_must_match_local_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = make_build_fixture(root)
            repository = build["repository"]
            commit = build["repository_commit"]
            adapter = FakeGitHubAdapter(head_sha=commit)
            adapter.workflow_run["head_sha"] = commit
            adapter.attempt["head_sha"] = commit
            adapter.pull_request["head"]["sha"] = commit
            adapter.artifact["workflow_run"]["head_sha"] = commit
            adapter.workflow_run["pull_requests"][0]["head"]["sha"] = commit
            adapter.attempt["pull_requests"][0]["head"]["sha"] = commit
            bind_adapter_to_build_fixture(
                adapter,
                build,
                output=b'{"trusted":"execution"}\n',
                stdout=b"forged stdout",
            )

            def producer(
                *args: object,
                **kwargs: object,
            ) -> subprocess.CompletedProcess:
                output = Path(list(args[0])[-1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text('{"trusted":"execution"}\n')
                return subprocess.CompletedProcess(args[0], 0, b"out", b"")

            with mock.patch.object(
                provenance_module,
                "_production_candidate_producer_blockers",
                return_value=[],
            ):
                report = self.attest(
                    root / "global-replay",
                    repository_dir=repository,
                    expected_commit=commit,
                    github_adapter=adapter,
                    issue_number=138,
                    pull_number=137,
                    run_id=RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    job_id=JOB_ID,
                    artifact_id=ARTIFACT_ID,
                    expected_head_sha=commit,
                    build_report_path=build["report_path"],
                    expected_build_dir=build["config"].micromachine_build_dir,
                    producer_id="fixture_producer",
                    ctest_runner=passing_ctest,
                    producer_runner=producer,
                )

            self.assertFalse(report["ok"], report)
            self.assertIn("stdout_sha256", " ".join(report["blockers"]))


def make_build_fixture(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    repository = root / "release-repository"
    repository.mkdir()
    path_values: dict[str, Path] = {}
    excluded = {
        "micromachine_dir",
        "s2client_dir",
        "micromachine_build_dir",
        "s2client_build_dir",
        "micromachine_commit",
        "s2client_commit",
        "source_attestation",
    }
    for field in fields(MicroMachineBuildIdentityConfig):
        if field.name in excluded:
            continue
        source = field.default
        if not isinstance(source, Path):
            raise AssertionError(f"unexpected non-path build input: {field.name}")
        relative = source.resolve().relative_to(BUILD_IDENTITY_REPO_ROOT.resolve())
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        path_values[field.name] = destination
    build_script_source = (
        BUILD_IDENTITY_REPO_ROOT
        / "integrations"
        / "micromachine"
        / "scripts"
        / "build_macos_local.sh"
    )
    build_script = (
        repository
        / "integrations"
        / "micromachine"
        / "scripts"
        / "build_macos_local.sh"
    )
    build_script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(build_script_source, build_script)
    policy_path = repository / PRODUCER_POLICY_RELATIVE_PATH
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    (repository / "fixture_producer.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[sys.argv.index('--output') + 1]).write_text("
        "'{\"fixture\":true}\\n')\n"
    )
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "producers": {
                    "fixture_producer": {
                        "argv": [
                            "{python}",
                            "-I",
                            "-B",
                            "-S",
                            "-c",
                            ISOLATED_PYTHON_BOOTSTRAP,
                            "{repository}",
                            "fixture_producer.py",
                            "--output",
                            "{output}",
                        ],
                        "cwd": ".",
                        "output": "fixture/evidence.json",
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    repository_commit = init_git_repo(
        repository,
        create_tracked_fixture=False,
    )

    micromachine_dir = root / "MicroMachine"
    s2client_dir = root / "s2client-api"
    micromachine_dir.mkdir()
    s2client_dir.mkdir()
    micromachine_commit = init_git_repo(micromachine_dir, add_origin=False)
    s2client_commit = init_git_repo(s2client_dir, add_origin=False)
    build_script_text = build_script.read_text()
    build_script_text = re.sub(
        r'(?m)^MICROMACHINE_COMMIT=.*$',
        f'MICROMACHINE_COMMIT="${{MICROMACHINE_COMMIT:-{micromachine_commit}}}"',
        build_script_text,
        count=1,
    )
    build_script_text = re.sub(
        r'(?m)^S2CLIENT_COMMIT=.*$',
        f'S2CLIENT_COMMIT="${{S2CLIENT_COMMIT:-{s2client_commit}}}"',
        build_script_text,
        count=1,
    )
    build_script.write_text(build_script_text)
    git(repository, "add", build_script.relative_to(repository).as_posix())
    git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--amend",
        "--no-edit",
    )
    repository_commit = git(repository, "rev-parse", "HEAD").stdout.strip()
    build_dir = micromachine_dir / "build"
    build_dir.mkdir(parents=True)
    ctest_path = shutil.which("ctest")
    if ctest_path is None:
        raise AssertionError("ctest is required by the provenance fixture")
    (build_dir / "CMakeCache.txt").write_text(
        f"CMAKE_CTEST_COMMAND:INTERNAL={Path(ctest_path).resolve()}\n"
    )
    s2client_build_dir = s2client_dir / "build-latest"
    s2client_build_dir.mkdir(parents=True)
    (s2client_build_dir / "libsc2api.a").write_text("fixture archive\n")
    config = MicroMachineBuildIdentityConfig(
        micromachine_dir=micromachine_dir,
        s2client_dir=s2client_dir,
        micromachine_build_dir=build_dir,
        s2client_build_dir=s2client_build_dir,
        micromachine_commit=micromachine_commit,
        s2client_commit=s2client_commit,
        source_attestation=build_dir / "voi_source_attestation.json",
        **path_values,
    )
    identity = write_micromachine_embedded_build_identity_header(config)
    config.binary_path.parent.mkdir(parents=True)
    config.binary_path.write_text(
        "#!/bin/sh\n"
        'if [ "${1:-}" = "--voi-build-input-identity" ]; then\n'
        f"  printf '%s\\n' '{identity}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    config.binary_path.chmod(0o755)
    for test_name, executable_name in sorted(
        MICROMACHINE_REQUIRED_NATIVE_TESTS.items()
    ):
        executable = config.binary_path.parent / executable_name
        executable.write_text(f"#!/bin/sh\n# native-test:{test_name}\nexit 0\n")
        executable.chmod(0o755)
    (build_dir / "CTestTestfile.cmake").write_text(
        "\n".join(
            (
                f"add_test([=[{test_name}]=] "
                f"[=[{(config.binary_path.parent / executable_name).resolve()}]=])"
            )
            for test_name, executable_name in sorted(
                MICROMACHINE_REQUIRED_NATIVE_TESTS.items()
            )
        )
        + "\n"
    )
    write_micromachine_source_attestation(config)
    write_micromachine_build_attestation(config)
    report = build_micromachine_build_identity(config)
    if report["ok"] is not True:
        raise AssertionError(report)
    report_path = build_dir / "voi_build_identity.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return {
        "config": config,
        "repository": repository,
        "repository_commit": repository_commit,
        "report": report,
        "report_path": report_path,
    }


def replace_fixture_producer_source(
    fixture: dict[str, Any],
    source: str,
) -> str:
    repository = fixture["repository"]
    producer_path = repository / "fixture_producer.py"
    producer_path.write_text(source)
    git(repository, "add", producer_path.relative_to(repository).as_posix())
    git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "replace fixture producer",
    )
    return git(repository, "rev-parse", "HEAD").stdout.strip()


def refresh_build_fixture(
    fixture: dict[str, Any],
    config: MicroMachineBuildIdentityConfig,
) -> None:
    identity = write_micromachine_embedded_build_identity_header(config)
    config.binary_path.write_text(
        "#!/bin/sh\n"
        'if [ "${1:-}" = "--voi-build-input-identity" ]; then\n'
        f"  printf '%s\\n' '{identity}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    config.binary_path.chmod(0o755)
    write_micromachine_source_attestation(config)
    write_micromachine_build_attestation(config)
    report = build_micromachine_build_identity(config)
    if report["ok"] is not True:
        raise AssertionError(report)
    fixture["config"] = config
    fixture["report"] = report
    fixture["report_path"].write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )


def passing_ctest(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
    argv = list(args[0])
    if Path(argv[0]).name != "ctest":
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
    if "--show-only=json-v1" in argv:
        build_dir = Path(argv[argv.index("--test-dir") + 1])
        tests = []
        for name, executable in sorted(MICROMACHINE_REQUIRED_NATIVE_TESTS.items()):
            tests.append(
                {
                    "name": name,
                    "command": [str(build_dir / "bin" / executable)],
                }
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({"tests": tests}),
            stderr="",
        )
    return subprocess.CompletedProcess(
        argv,
        0,
        stdout=(
            "100% tests passed, 0 tests failed out of "
            f"{REQUIRED_CTEST_COUNT}\n"
        ),
        stderr="",
    )


def init_git_repo(
    path: Path,
    *,
    add_origin: bool = True,
    create_tracked_fixture: bool = True,
) -> str:
    git(path, "init")
    if create_tracked_fixture:
        (path / "tracked.txt").write_text("fixture\n")
    git(path, "add", ".")
    git(
        path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "fixture",
    )
    if add_origin:
        git(
            path,
            "remote",
            "add",
            "origin",
            f"git@github.com:{REPOSITORY}.git",
        )
    return git(path, "rev-parse", "HEAD").stdout.strip()


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def make_source_artifact_bundle(
    head_sha: str,
    *,
    producer_started_at: str = "2026-07-30T00:02:00Z",
    producer_ended_at: str = "2026-07-30T00:03:00Z",
    pull_id: int = 3,
    pull_number: int = 137,
    issue_id: int = 2,
    issue_number: int = 138,
    workflow_ref: str = WORKFLOW_REF,
    workflow_sha: str = WORKFLOW_SHA,
) -> bytes:
    authority = candidate_authority(
        head_sha,
        pull_id=pull_id,
        pull_number=pull_number,
        issue_id=issue_id,
        issue_number=issue_number,
    )
    binary = b"fixture-micromachine-binary"
    repository_input_identity = "sha256:" + "e" * 64
    repository_paths = {
        "hook_manifest": {
            "path": "integrations/micromachine/HOOK_MANIFEST.json",
            "sha256": "f" * 64,
        }
    }
    upstream_commit_policy = {
        "path": "integrations/micromachine/scripts/build_macos_local.sh",
        "sha256": "d" * 64,
        "micromachine_commit": "1" * 40,
        "s2client_commit": "2" * 40,
    }
    repository_input_material = {
        "paths": repository_paths,
        "upstream_commit_policy": upstream_commit_policy,
    }
    repository_input = canonical_json_bytes(
        {
            "schema_version": 1,
            "repository_commit": head_sha,
            "build_input_identity": repository_input_identity,
            "repository_inputs_digest": "sha256:"
            + hashlib.sha256(
                canonical_json_bytes(repository_input_material)
            ).hexdigest(),
            "paths": repository_paths,
            "upstream_commit_policy": upstream_commit_policy,
        }
    )
    report_identity = "sha256:" + "a" * 64
    ctest_payload = make_ctest_evidence(Path("/fixture/build"))
    native_tests = make_build_report_native_tests(ctest_payload)
    report = canonical_json_bytes(
        {
            "schema_version": MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
            "identity": report_identity,
            "ok": True,
            "failures": [],
            "observed": {
                "binary_sha256": hashlib.sha256(binary).hexdigest(),
                "embedded_build_input_identity": repository_input_identity,
                "native_tests": native_tests,
            },
            "checksums": {
                "native_test_registry_sha256": ctest_payload["registry_sha256"],
                "native_test_manifest_sha256": native_tests["manifest_sha256"],
            },
        }
    )
    policy = canonical_json_bytes({"fixture": "policy"})
    executable = b"fixture-producer"
    argv = canonical_json_bytes(["/fixture/producer"])
    output = canonical_json_bytes({"fixture": "output"})
    provenance = canonical_json_bytes(
        {
            "schema_version": 1,
            "authority": authority,
            "producer_id": "fixture_producer",
            "policy_sha256": hashlib.sha256(policy).hexdigest(),
            "repository_commit": head_sha,
            "argv_sha256": hashlib.sha256(argv).hexdigest(),
            "executable_sha256": hashlib.sha256(executable).hexdigest(),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "exit_code": 0,
            "started_at": producer_started_at,
            "ended_at": producer_ended_at,
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        }
    )
    ctest = canonical_ctest_evidence_bytes(ctest_payload)
    metadata = PreLiveArtifactMetadata(
        authority_scope="candidate_pr",
        release_authoritative=False,
        authority_event="pull_request",
        pull_request_database_id=pull_id,
        pull_request_number=pull_number,
        pull_request_head_sha=head_sha,
        pull_request_head_ref="issue-138-authenticated-prelive-provenance",
        pull_request_head_repository_id=AUTHORITATIVE_REPOSITORY_ID,
        closing_issue_repository_full_name=REPOSITORY,
        closing_issue_repository_database_id=AUTHORITATIVE_REPOSITORY_ID,
        closing_issue_database_id=issue_id,
        closing_issue_number=issue_number,
        repository_full_name=REPOSITORY,
        repository_database_id=AUTHORITATIVE_REPOSITORY_ID,
        repository_commit=head_sha,
        workflow_id=WORKFLOW_ID,
        workflow_path=WORKFLOW_PATH,
        workflow_ref=workflow_ref,
        workflow_sha=workflow_sha,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        job_name="pre-live-provenance",
        artifact_logical_name="pre-live",
        artifact_member="payload/evidence.json",
        build_report_identity=report_identity,
        build_report_member="build/voi_build_identity.json",
        binary_member="build/MicroMachine",
        repository_input_member="build/repository-input.json",
        repository_input_identity=repository_input_identity,
        ctest_member="build/ctest-evidence.json",
        producer_policy_id="fixture_producer",
        producer_policy_member="producer/policy.json",
        producer_executable_member="producer/executable",
        producer_argv_member="producer/argv.json",
        producer_output_member="payload/evidence.json",
        producer_provenance_member="producer/provenance.json",
    )
    return build_pre_live_artifact_bundle(
        metadata,
        {
            "build/voi_build_identity.json": report,
            "build/MicroMachine": binary,
            "build/repository-input.json": repository_input,
            "build/ctest-evidence.json": ctest,
            "producer/policy.json": policy,
            "producer/executable": executable,
            "producer/argv.json": argv,
            "payload/evidence.json": output,
            "producer/provenance.json": provenance,
        },
    )


def make_stub_deterministic_journey_bundle() -> bytes:
    manifest = canonical_json_bytes(
        {
            "schema_version": 1,
            "evidence_kind": (
                "deterministic_micromachine_pre_live_journeys"
            ),
            "suite_id": "provenance-bound-digest-test",
            "journey_count": 0,
            "failed_count": 0,
            "report_sha256": hashlib.sha256(b"").hexdigest(),
            "members": [],
        }
    )
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=False,
    ) as archive:
        info = zipfile.ZipInfo(
            "manifest.json",
            date_time=(1980, 1, 1, 0, 0, 0),
        )
        info.compress_type = zipfile.ZIP_STORED
        info.create_system = 3
        info.create_version = 20
        info.extract_version = 20
        info.external_attr = 0o100644 << 16
        archive.writestr(info, manifest)
    return output.getvalue()


def bind_adapter_to_build_fixture(
    adapter: FakeGitHubAdapter,
    fixture: dict[str, Any],
    *,
    output: bytes,
    stdout: bytes = b"out",
) -> None:
    config = fixture["config"]
    repository = fixture["repository"]
    adapter.workflow_runs = [dict(adapter.workflow_run)]
    adapter.comparison["commits"][-1]["sha"] = fixture["repository_commit"]
    repository_paths: dict[str, object] = {}
    excluded = {
        "micromachine_dir",
        "s2client_dir",
        "micromachine_build_dir",
        "s2client_build_dir",
        "micromachine_commit",
        "s2client_commit",
        "source_attestation",
    }
    for field in fields(MicroMachineBuildIdentityConfig):
        if field.name in excluded:
            continue
        path = getattr(config, field.name)
        relative = path.resolve().relative_to(repository.resolve())
        payload = path.read_bytes()
        repository_paths[field.name] = {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    build_script_relative = "integrations/micromachine/scripts/build_macos_local.sh"
    build_script = repository / build_script_relative
    upstream_commit_policy = {
        "path": build_script_relative,
        "sha256": hashlib.sha256(build_script.read_bytes()).hexdigest(),
        "micromachine_commit": config.micromachine_commit,
        "s2client_commit": config.s2client_commit,
    }
    repository_input_material = {
        "paths": repository_paths,
        "upstream_commit_policy": upstream_commit_policy,
    }
    repository_inputs_digest = (
        "sha256:"
        + hashlib.sha256(canonical_json_bytes(repository_input_material)).hexdigest()
    )
    build_input_identity = fixture["report"]["observed"][
        "embedded_build_input_identity"
    ]
    repository_input = canonical_json_bytes(
        {
            "schema_version": 1,
            "repository_commit": fixture["repository_commit"],
            "build_input_identity": build_input_identity,
            "repository_inputs_digest": repository_inputs_digest,
            "paths": repository_paths,
            "upstream_commit_policy": upstream_commit_policy,
        }
    )
    policy = (repository / PRODUCER_POLICY_RELATIVE_PATH).read_bytes()
    resolved_policy = resolve_local_producer_policy(
        repository_dir=repository,
        expected_commit=fixture["repository_commit"],
        producer_id="fixture_producer",
    )
    if resolved_policy["ok"] is not True:
        raise AssertionError(resolved_policy)
    resolved_argv = resolved_policy["argv"]
    executable = Path(resolved_argv[0]).read_bytes()
    argv = canonical_json_bytes(resolved_argv)
    provenance = canonical_json_bytes(
        {
            "schema_version": 1,
            "authority": candidate_authority(fixture["repository_commit"]),
            "producer_id": "fixture_producer",
            "policy_sha256": hashlib.sha256(policy).hexdigest(),
            "repository_commit": fixture["repository_commit"],
            "argv_sha256": hashlib.sha256(argv).hexdigest(),
            "executable_sha256": hashlib.sha256(executable).hexdigest(),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "exit_code": 0,
            "started_at": "2026-07-30T00:02:00Z",
            "ended_at": "2026-07-30T00:03:00Z",
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        }
    )
    ctest = canonical_ctest_evidence_bytes(
        make_ctest_evidence(config.micromachine_build_dir)
    )
    metadata = PreLiveArtifactMetadata(
        authority_scope="candidate_pr",
        release_authoritative=False,
        authority_event="pull_request",
        pull_request_database_id=3,
        pull_request_number=137,
        pull_request_head_sha=fixture["repository_commit"],
        pull_request_head_ref="issue-138-authenticated-prelive-provenance",
        pull_request_head_repository_id=AUTHORITATIVE_REPOSITORY_ID,
        closing_issue_repository_full_name=REPOSITORY,
        closing_issue_repository_database_id=AUTHORITATIVE_REPOSITORY_ID,
        closing_issue_database_id=2,
        closing_issue_number=138,
        repository_full_name=REPOSITORY,
        repository_database_id=AUTHORITATIVE_REPOSITORY_ID,
        repository_commit=fixture["repository_commit"],
        workflow_id=WORKFLOW_ID,
        workflow_path=WORKFLOW_PATH,
        workflow_ref=WORKFLOW_REF,
        workflow_sha=WORKFLOW_SHA,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
        job_id=JOB_ID,
        job_name="pre-live-provenance",
        artifact_logical_name="pre-live",
        artifact_member="payload/evidence.json",
        build_report_identity=fixture["report"]["identity"],
        build_report_member="build/voi_build_identity.json",
        binary_member="build/MicroMachine",
        repository_input_member="build/repository-input.json",
        repository_input_identity=build_input_identity,
        ctest_member="build/ctest-evidence.json",
        producer_policy_id="fixture_producer",
        producer_policy_member="producer/policy.json",
        producer_executable_member="producer/executable",
        producer_argv_member="producer/argv.json",
        producer_output_member="payload/evidence.json",
        producer_provenance_member="producer/provenance.json",
    )
    adapter.artifact_bytes = build_pre_live_artifact_bundle(
        metadata,
        {
            "build/voi_build_identity.json": fixture["report_path"].read_bytes(),
            "build/MicroMachine": config.binary_path.read_bytes(),
            "build/repository-input.json": repository_input,
            "build/ctest-evidence.json": ctest,
            "producer/policy.json": policy,
            "producer/executable": executable,
            "producer/argv.json": argv,
            "payload/evidence.json": output,
            "producer/provenance.json": provenance,
        },
    )
    adapter.artifact["digest"] = (
        "sha256:" + hashlib.sha256(adapter.artifact_bytes).hexdigest()
    )


def make_ctest_evidence(build_dir: Path) -> dict[str, object]:
    ctest_candidate = shutil.which("ctest")
    if ctest_candidate is None:
        raise AssertionError("ctest is required by the provenance fixture")
    ctest_path = Path(ctest_candidate).resolve()
    executable_names = dict(MICROMACHINE_REQUIRED_NATIVE_TESTS)
    test_executables = {
        name: {
            "path": str((build_dir / "bin" / executable).resolve()),
            "sha256": (
                digest := hashlib.sha256(
                    (
                        (build_dir / "bin" / executable).read_bytes()
                        if (build_dir / "bin" / executable).is_file()
                        else f"synthetic:{name}".encode()
                    )
                ).hexdigest()
            ),
            "sha256_after": digest,
            "argv": [str((build_dir / "bin" / executable).resolve())],
            "returncode": 0,
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        }
        for name, executable in sorted(executable_names.items())
    }
    return {
        "schema_version": PRE_LIVE_CTEST_EVIDENCE_SCHEMA_VERSION,
        "argv": [
            str(ctest_path),
            "--test-dir",
            str(build_dir.resolve()),
            "--output-on-failure",
        ],
        "discovery_argv": [
            str(ctest_path),
            "--test-dir",
            str(build_dir.resolve()),
            "--show-only=json-v1",
        ],
        "ctest_executable": str(ctest_path),
        "ctest_executable_sha256": hashlib.sha256(ctest_path.read_bytes()).hexdigest(),
        "returncode": 0,
        "passed": len(executable_names),
        "total": len(executable_names),
        "failures": 0,
        "test_names": sorted(executable_names),
        "test_executables": test_executables,
        "test_manifest_sha256": (
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(test_executables)).hexdigest()
        ),
        "registry_sha256": canonical_micromachine_ctest_registry(
            {
                name: descriptor["path"]
                for name, descriptor in test_executables.items()
            }
        )["sha256"],
        "stdout_sha256": hashlib.sha256(
            (
                "100% tests passed, 0 tests failed out of "
                f"{REQUIRED_CTEST_COUNT}\n"
            ).encode()
        ).hexdigest(),
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
    }


def make_build_report_native_tests(
    ctest_payload: dict[str, object],
) -> dict[str, object]:
    test_executables = ctest_payload["test_executables"]
    assert isinstance(test_executables, dict)
    tests = {
        name: {
            "path": descriptor["path"],
            "sha256": descriptor["sha256"],
            "size_bytes": len(f"synthetic:{name}".encode()),
        }
        for name, descriptor in test_executables.items()
    }
    return {
        "ctest": {
            "path": ctest_payload["ctest_executable"],
            "sha256": ctest_payload["ctest_executable_sha256"],
            "size_bytes": Path(str(ctest_payload["ctest_executable"])).stat().st_size,
        },
        "registry": {
            "sha256": ctest_payload["registry_sha256"],
        },
        "tests": tests,
        "manifest_sha256": "sha256:" + ("d" * 64),
    }


if __name__ == "__main__":
    unittest.main()
