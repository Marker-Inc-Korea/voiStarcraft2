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
import tempfile
import threading
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
    PreLiveArtifactMetadata,
    build_pre_live_artifact_bundle,
    canonical_ctest_evidence_bytes,
    canonical_json_bytes,
    verify_pre_live_artifact_bundle,
)


REPOSITORY = "Marker-Inc-Korea/voiStarcraft2"
HEAD_SHA = "a" * 40
RUN_ID = 101
RUN_ATTEMPT = 2
JOB_ID = 201
ARTIFACT_ID = 301
WORKFLOW_ID = 401
WORKFLOW_PATH = AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH


def candidate_authority(
    head_sha: str,
    *,
    pull_id: int = 3,
    pull_number: int = 137,
    head_ref: str = "issue-138-authenticated-prelive-provenance",
    head_repository_id: int = AUTHORITATIVE_REPOSITORY_ID,
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
            "merged_at": None,
        }
        self.workflow_run = {
            "id": RUN_ID,
            "workflow_id": WORKFLOW_ID,
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

    def get_workflow_run(
        self,
        repository: str,
        run_id: int,
    ) -> dict[str, object]:
        return self._result("workflow_run", self.workflow_run)

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
        self.assertIn(f"  {AUTHORITATIVE_PROVENANCE_JOB_NAME}:\n", workflow)
        provenance_job = workflow.split(
            f"  {AUTHORITATIVE_PROVENANCE_JOB_NAME}:\n",
            1,
        )[1].split("\n  micromachine-macos-contracts:\n", 1)[0]
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
            2,
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
        self.assertGreaterEqual(len(pull_request_job_blocks), 2)
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
        build_step = provenance_job.split(
            "      - name: Build exact MicroMachine integration\n",
            1,
        )[1].split(
            "      - name: Emit canonical authenticated provenance bundle\n",
            1,
        )[0]
        self.assertNotIn("GITHUB_TOKEN", build_step)
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
            workflow_ref=("refs/heads/issue-138-authenticated-prelive-provenance"),
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
            workflow_ref=("refs/heads/issue-138-authenticated-prelive-provenance"),
        )
        self.assertFalse(ambiguous["ok"], ambiguous)

    def test_rejects_unrelated_issue_without_pr_closure_binding(self) -> None:
        adapter = FakeGitHubAdapter()
        adapter.pull_request["body"] = "No issue relationship."

        report = self.attest(adapter)

        self.assertFalse(report["ok"], report)
        self.assertIn("Closes #138", " ".join(report["blockers"]))

        unrelated = attest_github_source(
            adapter,
            repository=REPOSITORY,
            expected_repository_id=AUTHORITATIVE_REPOSITORY_ID,
            issue_number=77,
            pull_number=137,
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
            job_id=JOB_ID,
            artifact_id=ARTIFACT_ID,
            expected_head_sha=HEAD_SHA,
        )
        self.assertFalse(unrelated["ok"], unrelated)
        self.assertIn("expected=138", " ".join(unrelated["blockers"]))

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
                },
                {
                    "id": JOB_ID + 2,
                    "run_id": RUN_ID,
                    "run_attempt": RUN_ATTEMPT,
                    "name": "micromachine-macos-contracts",
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
        rulesets, ruleset_details = make_replay_ruleset_fixtures()
        payloads: dict[str, object] = {
            f"/repos/{REPOSITORY}": {"id": 1},
            f"/repos/{REPOSITORY}/issues/138": {"id": 2},
            f"/repos/{REPOSITORY}/pulls/137": {"id": 3},
            f"/repos/{REPOSITORY}/actions/runs/{RUN_ID}": {"id": RUN_ID},
            f"/repos/{REPOSITORY}/actions/workflows/{WORKFLOW_ID}": {"id": WORKFLOW_ID},
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
            RUN_ID,
            adapter.get_workflow_run(REPOSITORY, RUN_ID)["id"],
        )
        self.assertEqual(
            WORKFLOW_ID,
            adapter.get_workflow(REPOSITORY, WORKFLOW_ID)["id"],
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
        self.assertEqual(16, len(requested))
        self.assertTrue(
            all(header == "Bearer fixture-token" for _, header, _, _ in requested)
        )


class BuildBindingTest(unittest.TestCase):
    def test_rebuilds_schema_71_identity_and_exact_five_ctests(self) -> None:
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
            self.assertEqual(5, report["ctest"]["passed"])
            self.assertEqual(5, report["ctest"]["total"])

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

    def test_rejects_ctest_failure_missing_summary_and_four_of_five(self) -> None:
        cases = (
            subprocess.CompletedProcess(
                [],
                1,
                stdout="80% tests passed, 1 tests failed out of 5\n",
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
                    "100% tests passed, 0 tests failed out of 5\n"
                    "80% tests passed, 1 tests failed out of 5\n"
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
                            (
                                "voi_operation_transfer_admission_test",
                                "voi_runtime_convergence_test",
                                "voi_family_effect_lifecycle_test",
                                "voi_battlefield_projection_test",
                                "voi_battlefield_projection_ndebug_test",
                            )
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
                "CTest test identity mismatch",
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
            self.assertIn("unexpected arguments", " ".join(result["blockers"]))

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
                    for name, executable in (
                        (
                            "voi_operation_transfer_admission",
                            "voi_operation_transfer_admission_test",
                        ),
                        (
                            "voi_runtime_convergence",
                            "voi_runtime_convergence_test",
                        ),
                        (
                            "voi_family_effect_lifecycle",
                            "voi_family_effect_lifecycle_test",
                        ),
                        (
                            "voi_battlefield_projection",
                            "voi_battlefield_projection_test",
                        ),
                        (
                            "voi_battlefield_projection_ndebug",
                            "voi_battlefield_projection_ndebug_test",
                        ),
                    )
                ]
            }
            fake_ctest.write_text(
                "#!/bin/sh\n"
                'if [ "$3" = "--show-only=json-v1" ]; then\n'
                f"  printf '%s\\n' '{json.dumps(discovered)}'\n"
                "else\n"
                "  printf '%s\\n' "
                "'100% tests passed, 0 tests failed out of 5'\n"
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
            fake_ctest.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' '100% tests passed, 0 tests failed out of 5'\n"
                "exit 0\n"
            )
            fake_ctest.chmod(0o755)
            build_dir = fixture["config"].micromachine_build_dir
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
                workflow_ref=("refs/heads/issue-138-authenticated-prelive-provenance"),
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
                        workflow_ref=(
                            "refs/heads/issue-138-authenticated-prelive-provenance"
                        ),
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
                workflow_ref=("refs/heads/issue-138-authenticated-prelive-provenance"),
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


class LocalProducerTest(unittest.TestCase):
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
            "workflow_ref": "refs/heads/issue-138-authenticated-prelive-provenance",
            "artifact_sha256": "1" * 64,
            "source_ids": {
                "repository_id": AUTHORITATIVE_REPOSITORY_ID,
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
            "ctest": {"test_manifest_sha256": "sha256:" + "5" * 64},
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

    def test_derives_top_level_status_and_ignores_caller_claims(self) -> None:
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

            self.assertTrue(report["ok"], report)
            self.assertEqual("candidate_qualified", report["status"])
            self.assertEqual(candidate_authority(commit), report["authority"])
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
            self.assertEqual(JOB_ID, report["accepted_source_ids"]["job_id"])
            self.assertEqual(
                hashlib.sha256(adapter.artifact_bytes).hexdigest(),
                report["accepted_digests"]["github_artifact_sha256"],
            )
            release = require_release_authority(report)
            self.assertFalse(release["ok"], release)
            self.assertIn("qualification-only", " ".join(release["blockers"]))

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
        for name, executable in (
            (
                "voi_operation_transfer_admission",
                "voi_operation_transfer_admission_test",
            ),
            ("voi_runtime_convergence", "voi_runtime_convergence_test"),
            ("voi_family_effect_lifecycle", "voi_family_effect_lifecycle_test"),
            ("voi_battlefield_projection", "voi_battlefield_projection_test"),
            (
                "voi_battlefield_projection_ndebug",
                "voi_battlefield_projection_ndebug_test",
            ),
        ):
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
        stdout="100% tests passed, 0 tests failed out of 5\n",
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
) -> bytes:
    authority = candidate_authority(
        head_sha,
        pull_id=pull_id,
        pull_number=pull_number,
    )
    binary = b"fixture-micromachine-binary"
    repository_input_identity = "sha256:" + "e" * 64
    repository_paths = {
        "hook_manifest": {
            "path": "integrations/micromachine/HOOK_MANIFEST.json",
            "sha256": "f" * 64,
        }
    }
    repository_input = canonical_json_bytes(
        {
            "schema_version": 1,
            "repository_commit": head_sha,
            "build_input_identity": repository_input_identity,
            "repository_inputs_digest": "sha256:"
            + hashlib.sha256(canonical_json_bytes(repository_paths)).hexdigest(),
            "paths": repository_paths,
        }
    )
    report_identity = "sha256:" + "a" * 64
    report = canonical_json_bytes(
        {
            "schema_version": MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
            "identity": report_identity,
            "ok": True,
            "failures": [],
            "observed": {
                "binary_sha256": hashlib.sha256(binary).hexdigest(),
                "embedded_build_input_identity": repository_input_identity,
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
    ctest = canonical_ctest_evidence_bytes(make_ctest_evidence(Path("/fixture/build")))
    metadata = PreLiveArtifactMetadata(
        authority_scope="candidate_pr",
        release_authoritative=False,
        authority_event="pull_request",
        pull_request_database_id=pull_id,
        pull_request_number=pull_number,
        pull_request_head_sha=head_sha,
        pull_request_head_ref="issue-138-authenticated-prelive-provenance",
        pull_request_head_repository_id=AUTHORITATIVE_REPOSITORY_ID,
        repository_full_name=REPOSITORY,
        repository_database_id=AUTHORITATIVE_REPOSITORY_ID,
        repository_commit=head_sha,
        workflow_id=WORKFLOW_ID,
        workflow_path=WORKFLOW_PATH,
        workflow_ref="refs/heads/issue-138-authenticated-prelive-provenance",
        workflow_sha=head_sha,
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


def bind_adapter_to_build_fixture(
    adapter: FakeGitHubAdapter,
    fixture: dict[str, Any],
    *,
    output: bytes,
    stdout: bytes = b"out",
) -> None:
    config = fixture["config"]
    repository = fixture["repository"]
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
    repository_inputs_digest = (
        "sha256:" + hashlib.sha256(canonical_json_bytes(repository_paths)).hexdigest()
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
        repository_full_name=REPOSITORY,
        repository_database_id=AUTHORITATIVE_REPOSITORY_ID,
        repository_commit=fixture["repository_commit"],
        workflow_id=WORKFLOW_ID,
        workflow_path=WORKFLOW_PATH,
        workflow_ref="refs/heads/issue-138-authenticated-prelive-provenance",
        workflow_sha=fixture["repository_commit"],
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
    executable_names = {
        "voi_operation_transfer_admission": "voi_operation_transfer_admission_test",
        "voi_runtime_convergence": "voi_runtime_convergence_test",
        "voi_family_effect_lifecycle": "voi_family_effect_lifecycle_test",
        "voi_battlefield_projection": "voi_battlefield_projection_test",
        "voi_battlefield_projection_ndebug": "voi_battlefield_projection_ndebug_test",
    }
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
        "schema_version": 1,
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
        "passed": 5,
        "total": 5,
        "failures": 0,
        "test_names": sorted(executable_names),
        "test_executables": test_executables,
        "test_manifest_sha256": (
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(test_executables)).hexdigest()
        ),
        "stdout_sha256": hashlib.sha256(
            b"100% tests passed, 0 tests failed out of 5\n"
        ).hexdigest(),
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
    }


if __name__ == "__main__":
    unittest.main()
