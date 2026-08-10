import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "final-pre-live.yml"
EXPECTED_JOBS = {
    "event_admission",
    "build_identity",
    "deterministic_journeys",
    "browser_accessibility",
    "distribution_compliance",
    "pre_live_provenance",
    "seal_child_artifacts",
    "final_release_gate",
}
PRODUCER_JOBS = {
    "build_identity",
    "deterministic_journeys",
    "browser_accessibility",
    "distribution_compliance",
    "pre_live_provenance",
}
HEAVY_JOBS = PRODUCER_JOBS | {"seal_child_artifacts"}
EXPECTED_CHILD_ARTIFACTS = {
    "micromachine-build-identity",
    "micromachine-deterministic-journeys",
    "micromachine-browser-accessibility",
    "micromachine-distribution-compliance",
    "micromachine-pre-live-provenance",
}


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_unique_mapping,
)


class FinalPreLiveWorkflowContractTests(unittest.TestCase):
    def test_exact_jobs_and_read_only_permissions(self) -> None:
        workflow = self.workflow()

        self.assertEqual(EXPECTED_JOBS, set(workflow["jobs"]))
        self.assertEqual(
            {
                "actions": "read",
                "contents": "read",
                "issues": "read",
                "pull-requests": "read",
            },
            workflow["permissions"],
        )
        for name, job in workflow["jobs"].items():
            with self.subTest(job=name):
                for permission in job.get("permissions", {}).values():
                    self.assertEqual("read", permission)

    def test_trusted_pr_target_events_and_exact_main_push(self) -> None:
        workflow = self.workflow()
        trigger = workflow.get("on", workflow.get(True))

        self.assertNotIn("pull_request", trigger)
        self.assertEqual(["main"], trigger["pull_request_target"]["branches"])
        self.assertEqual(
            {
                "converted_to_draft",
                "edited",
                "opened",
                "ready_for_review",
                "reopened",
                "synchronize",
            },
            set(trigger["pull_request_target"]["types"]),
        )
        self.assertEqual(["main"], trigger["push"]["branches"])

    def test_admission_separates_release_and_qualification_identity(
        self,
    ) -> None:
        workflow = self.workflow()
        admission = workflow["jobs"]["event_admission"]
        boundary = self.step(
            admission,
            "Admit exact same-repository release event before checkout",
        )["run"]

        self.assertNotIn("if", admission)
        self.assertEqual([], self.checkouts(admission))
        self.assertEqual(
            "${{ steps.classify.outputs.release_required }}",
            admission["outputs"]["release_required"],
        )
        self.assertEqual("168", workflow["env"]["RELEASE_PULL_NUMBER"])
        self.assertEqual(
            "issue-141-final-prelive-gates",
            workflow["env"]["RELEASE_HEAD_REF"],
        )
        self.assertEqual(
            "721726d8da88d1c3a7448e22265af1f817b3f2cd",
            workflow["env"]["RELEASE_MERGE_SHA"],
        )
        self.assertEqual("179", workflow["env"]["QUALIFICATION_ISSUE_NUMBER"])
        self.assertEqual(
            "issue-179-final-prelive-release-rebind",
            workflow["env"]["QUALIFICATION_HEAD_REF"],
        )
        self.assertEqual(
            "5fff4afd83084e46cf1231835da4c65ba6dc4f6d",
            workflow["env"]["QUALIFICATION_BASE_SHA"],
        )
        self.assertIn("pull_request_target)", boundary)
        pull_request_boundary = boundary.split(
            "pull_request_target)",
            maxsplit=1,
        )[1].split(";;", maxsplit=1)[0]
        self.assertNotIn("release_required=true", pull_request_boundary)
        self.assertIn("release_required=false", pull_request_boundary)
        self.assertIn(
            'test "${GITHUB_WORKFLOW_SHA}" = "${EXPECTED_WORKFLOW_COMMIT}"',
            boundary,
        )
        self.assertIn("trusted_base_candidate_preflight", boundary)
        self.assertIn("authoritative_exact_main", boundary)
        self.assertIn(
            '"repos/${GITHUB_REPOSITORY}/pulls/${RELEASE_PULL_NUMBER}"',
            boundary,
        )
        self.assertIn(".merged", boundary)
        self.assertIn(".merge_commit_sha", boundary)
        self.assertIn(
            'test "${release_pull_merge_sha}" = "${RELEASE_MERGE_SHA}"',
            boundary,
        )
        self.assertIn(
            'test "${release_pull_head_ref}" = "${RELEASE_HEAD_REF}"',
            boundary,
        )
        self.assertIn(
            '"repos/${GITHUB_REPOSITORY}/pulls?state=closed&base=main&head='
            '${GITHUB_REPOSITORY_OWNER}:${QUALIFICATION_HEAD_REF}&per_page=100"',
            boundary,
        )
        self.assertIn(
            "for qualification_lookup_attempt in {1..12}; do",
            boundary,
        )
        self.assertIn(
            'test "${EXPECTED_RELEASE_BASE_COMMIT}" =',
            boundary,
        )
        self.assertIn('"${QUALIFICATION_BASE_SHA}"; then', boundary)
        self.assertIn(
            'test "${qualification_observed_merge_sha}" =',
            boundary,
        )
        self.assertIn(
            '"qualification pull lookup returned no branch match"',
            boundary,
        )
        self.assertIn(
            '"qualification merge identity did not converge"',
            boundary,
        )
        self.assertIn(
            'test "${qualification_merge_sha}" =',
            boundary,
        )
        self.assertIn('if test "${qualification_head_ref}" =', boundary)
        self.assertIn('"${QUALIFICATION_HEAD_REF}" \\', boundary)
        self.assertIn(
            '&& test "${qualification_merge_sha}" =',
            boundary,
        )
        self.assertIn('"${GITHUB_SHA}"; then', boundary)
        self.assertIn("closingIssuesReferences(first: 10)", boundary)
        self.assertIn("closedByPullRequestsReferences(first: 10)", boundary)
        self.assertIn('test "${declared_issue_number}" =', boundary)
        self.assertIn('test "${closing_pull_number}" =', boundary)
        self.assertIn('"${qualification_pull_number}"', boundary)
        self.assertIn('"${QUALIFICATION_ISSUE_NUMBER}"', boundary)
        self.assertIn(
            '"repos/${GITHUB_REPOSITORY}/compare/'
            '${release_pull_merge_sha}...${GITHUB_SHA}"',
            boundary,
        )
        self.assertIn('test "${comparison_base_sha}" =', boundary)
        self.assertIn('test "${merge_base_sha}" =', boundary)
        self.assertIn('"${RELEASE_MERGE_SHA}"', boundary)
        self.assertIn("release_required=true", boundary)
        self.assertIn(
            "printf 'release_required=%s\\n' \"${release_required}\"",
            boundary,
        )
        classify = self.step(
            admission,
            "Admit exact same-repository release event before checkout",
        )
        self.assertEqual("classify", classify["id"])
        self.assertEqual("${{ github.token }}", classify["env"]["GH_TOKEN"])
        self.assertEqual("read", admission["permissions"]["pull-requests"])
        self.assertEqual("read", admission["permissions"]["issues"])
        build_verify = self.step(
            workflow["jobs"]["build_identity"],
            "Verify exact clean release source",
        )["run"]
        self.assertIn(
            'git merge-base --is-ancestor "${RELEASE_MERGE_SHA}" HEAD',
            build_verify,
        )

    def test_heavy_jobs_run_only_for_admitted_release_events(self) -> None:
        workflow = self.workflow()

        for name in HEAVY_JOBS:
            with self.subTest(job=name):
                job = workflow["jobs"][name]
                condition = job["if"]
                self.assertIn("event_admission", job["needs"])
                self.assertIn("always()", condition)
                self.assertIn(
                    "needs.event_admission.result == 'success'",
                    condition,
                )
                self.assertIn(
                    "needs.event_admission.outputs.release_required == 'true'",
                    condition,
                )

        for name in HEAVY_JOBS - {"build_identity"}:
            with self.subTest(dependent_job=name):
                self.assertIn(
                    "needs.build_identity.result == 'success'",
                    workflow["jobs"][name]["if"],
                )

        seal_condition = workflow["jobs"]["seal_child_artifacts"]["if"]
        for producer in PRODUCER_JOBS - {"build_identity"}:
            with self.subTest(sealed_producer=producer):
                self.assertIn(
                    f"needs.{producer}.result == 'success'",
                    seal_condition,
                )

    def test_admission_fails_closed_when_qualification_lookup_fails(
        self,
    ) -> None:
        boundary = self.step(
            self.workflow()["jobs"]["event_admission"],
            "Admit exact same-repository release event before checkout",
        )["run"]
        release_sha = "7" * 40
        base_sha = "2" * 40

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            call_count = root / "gh-call-count"
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\n"
                f"count_file={call_count}\n"
                "count=0\n"
                "if test -f \"$count_file\"; then\n"
                "  count=\"$(cat \"$count_file\")\"\n"
                "fi\n"
                "count=$((count + 1))\n"
                "printf '%s\\n' \"$count\" > \"$count_file\"\n"
                "if test \"$count\" -eq 1; then\n"
                f"  printf 'true\\t{release_sha}\\t"
                "issue-141-final-prelive-gates\\t1\\tmain\\t1\\n'\n"
                "  exit 0\n"
                "fi\n"
                "echo 'qualification lookup failed' >&2\n"
                "exit 42\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            output = root / "github-output"
            environment = {
                "EVENT_BASE_REPOSITORY": "",
                "EVENT_BASE_REPOSITORY_ID": "",
                "EVENT_HEAD_REPOSITORY": "",
                "EVENT_HEAD_REPOSITORY_ID": "",
                "EVENT_REPOSITORY_ID": "1",
                "EXPECTED_RELEASE_BASE_COMMIT": base_sha,
                "EXPECTED_RELEASE_COMMIT": release_sha,
                "EXPECTED_WORKFLOW_COMMIT": release_sha,
                "GH_TOKEN": "fixture-token",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_REPOSITORY": "owner/repository",
                "GITHUB_REPOSITORY_OWNER": "owner",
                "GITHUB_SHA": release_sha,
                "GITHUB_WORKFLOW_SHA": release_sha,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "QUALIFICATION_BASE_SHA": base_sha,
                "QUALIFICATION_HEAD_REF": (
                    "issue-175-final-prelive-browser-envelope"
                ),
                "QUALIFICATION_ISSUE_NUMBER": "175",
                "RELEASE_AUTHORITY": "authoritative_exact_main",
                "RELEASE_HEAD_REF": "issue-141-final-prelive-gates",
                "RELEASE_MERGE_SHA": release_sha,
                "RELEASE_MODE": "ready_for_live_qa",
                "RELEASE_PULL_NUMBER": "168",
            }

            result = subprocess.run(
                ["/bin/bash"],
                input=boundary,
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(42, result.returncode, result.stderr)
            self.assertIn("qualification lookup failed", result.stderr)
            self.assertFalse(output.exists())

    def test_admission_fails_closed_when_qualification_lookup_stays_empty(
        self,
    ) -> None:
        boundary = self.step(
            self.workflow()["jobs"]["event_admission"],
            "Admit exact same-repository release event before checkout",
        )["run"]
        release_sha = "7" * 40
        base_sha = "2" * 40

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            call_count = root / "gh-call-count"
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\n"
                f"count_file={call_count}\n"
                "count=0\n"
                "if test -f \"$count_file\"; then\n"
                "  count=\"$(cat \"$count_file\")\"\n"
                "fi\n"
                "count=$((count + 1))\n"
                "printf '%s\\n' \"$count\" > \"$count_file\"\n"
                "if test \"$count\" -eq 1; then\n"
                f"  printf 'true\\t{release_sha}\\t"
                "issue-141-final-prelive-gates\\t1\\tmain\\t1\\n'\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            fake_sleep = fake_bin / "sleep"
            fake_sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_sleep.chmod(0o755)
            output = root / "github-output"
            environment = {
                "EVENT_BASE_REPOSITORY": "",
                "EVENT_BASE_REPOSITORY_ID": "",
                "EVENT_HEAD_REPOSITORY": "",
                "EVENT_HEAD_REPOSITORY_ID": "",
                "EVENT_REPOSITORY_ID": "1",
                "EXPECTED_RELEASE_BASE_COMMIT": base_sha,
                "EXPECTED_RELEASE_COMMIT": release_sha,
                "EXPECTED_WORKFLOW_COMMIT": release_sha,
                "GH_TOKEN": "fixture-token",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_REPOSITORY": "owner/repository",
                "GITHUB_REPOSITORY_OWNER": "owner",
                "GITHUB_SHA": release_sha,
                "GITHUB_WORKFLOW_SHA": release_sha,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "QUALIFICATION_BASE_SHA": base_sha,
                "QUALIFICATION_HEAD_REF": (
                    "issue-175-final-prelive-browser-envelope"
                ),
                "QUALIFICATION_ISSUE_NUMBER": "175",
                "RELEASE_AUTHORITY": "authoritative_exact_main",
                "RELEASE_HEAD_REF": "issue-141-final-prelive-gates",
                "RELEASE_MERGE_SHA": release_sha,
                "RELEASE_MODE": "ready_for_live_qa",
                "RELEASE_PULL_NUMBER": "168",
            }

            result = subprocess.run(
                ["/bin/bash"],
                input=boundary,
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(1, result.returncode, result.stderr)
            self.assertIn(
                "qualification pull lookup returned no branch match",
                result.stderr,
            )
            self.assertEqual("13", call_count.read_text().strip())
            self.assertFalse(output.exists())

    def test_admission_retries_empty_qualification_lookup_then_admits(
        self,
    ) -> None:
        boundary = self.step(
            self.workflow()["jobs"]["event_admission"],
            "Admit exact same-repository release event before checkout",
        )["run"]
        release_sha = "7" * 40
        base_sha = "2" * 40

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            call_count = root / "gh-call-count"
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\n"
                f"count_file={call_count}\n"
                "count=0\n"
                "if test -f \"$count_file\"; then\n"
                "  count=\"$(cat \"$count_file\")\"\n"
                "fi\n"
                "count=$((count + 1))\n"
                "printf '%s\\n' \"$count\" > \"$count_file\"\n"
                "case \"$count\" in\n"
                "  1)\n"
                f"    printf 'true\\t{release_sha}\\t"
                "issue-141-final-prelive-gates\\t1\\tmain\\t1\\n'\n"
                "    ;;\n"
                "  2)\n"
                "    ;;\n"
                "  3)\n"
                "    printf '176\\tclosed\\t2026-08-10T00:00:00Z\\t"
                f"{'6' * 40}\\tissue-175-final-prelive-browser-envelope"
                "\\towner/repository\\t1\\tmain\\towner/repository\\t1\\n'\n"
                "    ;;\n"
                "  4)\n"
                f"    printf '176\\tclosed\\t2026-08-10T00:00:00Z\\t"
                f"{release_sha}\\tissue-175-final-prelive-browser-envelope"
                "\\towner/repository\\t1\\tmain\\towner/repository\\t1\\n'\n"
                "    ;;\n"
                "  5)\n"
                "    printf '1\\t175\\tfalse\\towner/repository\\t175\\t"
                "CLOSED\\tCOMPLETED\\t1\\t176\\tfalse\\t"
                "owner/repository\\n'\n"
                "    ;;\n"
                "  6)\n"
                f"    printf 'identical\\t{release_sha}\\t"
                f"{release_sha}\\n'\n"
                "    ;;\n"
                "  *)\n"
                "    exit 42\n"
                "    ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            fake_sleep = fake_bin / "sleep"
            fake_sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_sleep.chmod(0o755)
            output = root / "github-output"
            environment = {
                "EVENT_BASE_REPOSITORY": "",
                "EVENT_BASE_REPOSITORY_ID": "",
                "EVENT_HEAD_REPOSITORY": "",
                "EVENT_HEAD_REPOSITORY_ID": "",
                "EVENT_REPOSITORY_ID": "1",
                "EXPECTED_RELEASE_BASE_COMMIT": base_sha,
                "EXPECTED_RELEASE_COMMIT": release_sha,
                "EXPECTED_WORKFLOW_COMMIT": release_sha,
                "GH_TOKEN": "fixture-token",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_REPOSITORY": "owner/repository",
                "GITHUB_REPOSITORY_OWNER": "owner",
                "GITHUB_SHA": release_sha,
                "GITHUB_WORKFLOW_SHA": release_sha,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "QUALIFICATION_BASE_SHA": base_sha,
                "QUALIFICATION_HEAD_REF": (
                    "issue-175-final-prelive-browser-envelope"
                ),
                "QUALIFICATION_ISSUE_NUMBER": "175",
                "RELEASE_AUTHORITY": "authoritative_exact_main",
                "RELEASE_HEAD_REF": "issue-141-final-prelive-gates",
                "RELEASE_MERGE_SHA": release_sha,
                "RELEASE_MODE": "ready_for_live_qa",
                "RELEASE_PULL_NUMBER": "168",
            }

            result = subprocess.run(
                ["/bin/bash"],
                input=boundary,
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("6", call_count.read_text().strip())
            self.assertEqual(
                "release_required=true\n",
                output.read_text(encoding="utf-8"),
            )

    def test_admission_fails_closed_when_qualification_merge_stays_stale(
        self,
    ) -> None:
        boundary = self.step(
            self.workflow()["jobs"]["event_admission"],
            "Admit exact same-repository release event before checkout",
        )["run"]
        release_sha = "7" * 40
        stale_sha = "6" * 40
        base_sha = "2" * 40

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            call_count = root / "gh-call-count"
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\n"
                f"count_file={call_count}\n"
                "count=0\n"
                "if test -f \"$count_file\"; then\n"
                "  count=\"$(cat \"$count_file\")\"\n"
                "fi\n"
                "count=$((count + 1))\n"
                "printf '%s\\n' \"$count\" > \"$count_file\"\n"
                "if test \"$count\" -eq 1; then\n"
                f"  printf 'true\\t{release_sha}\\t"
                "issue-141-final-prelive-gates\\t1\\tmain\\t1\\n'\n"
                "  exit 0\n"
                "fi\n"
                "printf '176\\tclosed\\t2026-08-10T00:00:00Z\\t"
                f"{stale_sha}\\tissue-175-final-prelive-browser-envelope"
                "\\towner/repository\\t1\\tmain\\towner/repository\\t1\\n'\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            fake_sleep = fake_bin / "sleep"
            fake_sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_sleep.chmod(0o755)
            output = root / "github-output"
            environment = {
                "EVENT_BASE_REPOSITORY": "",
                "EVENT_BASE_REPOSITORY_ID": "",
                "EVENT_HEAD_REPOSITORY": "",
                "EVENT_HEAD_REPOSITORY_ID": "",
                "EVENT_REPOSITORY_ID": "1",
                "EXPECTED_RELEASE_BASE_COMMIT": base_sha,
                "EXPECTED_RELEASE_COMMIT": release_sha,
                "EXPECTED_WORKFLOW_COMMIT": release_sha,
                "GH_TOKEN": "fixture-token",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_REPOSITORY": "owner/repository",
                "GITHUB_REPOSITORY_OWNER": "owner",
                "GITHUB_SHA": release_sha,
                "GITHUB_WORKFLOW_SHA": release_sha,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "QUALIFICATION_BASE_SHA": base_sha,
                "QUALIFICATION_HEAD_REF": (
                    "issue-175-final-prelive-browser-envelope"
                ),
                "QUALIFICATION_ISSUE_NUMBER": "175",
                "RELEASE_AUTHORITY": "authoritative_exact_main",
                "RELEASE_HEAD_REF": "issue-141-final-prelive-gates",
                "RELEASE_MERGE_SHA": release_sha,
                "RELEASE_MODE": "ready_for_live_qa",
                "RELEASE_PULL_NUMBER": "168",
            }

            result = subprocess.run(
                ["/bin/bash"],
                input=boundary,
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(1, result.returncode, result.stderr)
            self.assertIn(
                "qualification merge identity did not converge",
                result.stderr,
            )
            self.assertEqual("13", call_count.read_text().strip())
            self.assertFalse(output.exists())

    def test_admission_skips_qualification_lookup_after_one_shot_base(
        self,
    ) -> None:
        boundary = self.step(
            self.workflow()["jobs"]["event_admission"],
            "Admit exact same-repository release event before checkout",
        )["run"]
        release_sha = "7" * 40
        base_sha = "3" * 40
        qualification_base_sha = "2" * 40

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            call_count = root / "gh-call-count"
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                "#!/bin/sh\n"
                f"count_file={call_count}\n"
                "count=0\n"
                "if test -f \"$count_file\"; then\n"
                "  count=\"$(cat \"$count_file\")\"\n"
                "fi\n"
                "count=$((count + 1))\n"
                "printf '%s\\n' \"$count\" > \"$count_file\"\n"
                "if test \"$count\" -eq 1; then\n"
                f"  printf 'true\\t{release_sha}\\t"
                "issue-141-final-prelive-gates\\t1\\tmain\\t1\\n'\n"
                "  exit 0\n"
                "fi\n"
                "exit 42\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            output = root / "github-output"
            environment = {
                "EVENT_BASE_REPOSITORY": "",
                "EVENT_BASE_REPOSITORY_ID": "",
                "EVENT_HEAD_REPOSITORY": "",
                "EVENT_HEAD_REPOSITORY_ID": "",
                "EVENT_REPOSITORY_ID": "1",
                "EXPECTED_RELEASE_BASE_COMMIT": base_sha,
                "EXPECTED_RELEASE_COMMIT": release_sha,
                "EXPECTED_WORKFLOW_COMMIT": release_sha,
                "GH_TOKEN": "fixture-token",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_REPOSITORY": "owner/repository",
                "GITHUB_REPOSITORY_OWNER": "owner",
                "GITHUB_SHA": release_sha,
                "GITHUB_WORKFLOW_SHA": release_sha,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "QUALIFICATION_BASE_SHA": qualification_base_sha,
                "QUALIFICATION_HEAD_REF": (
                    "issue-175-final-prelive-browser-envelope"
                ),
                "QUALIFICATION_ISSUE_NUMBER": "175",
                "RELEASE_AUTHORITY": "authoritative_exact_main",
                "RELEASE_HEAD_REF": "issue-141-final-prelive-gates",
                "RELEASE_MERGE_SHA": release_sha,
                "RELEASE_MODE": "ready_for_live_qa",
                "RELEASE_PULL_NUMBER": "168",
            }

            result = subprocess.run(
                ["/bin/bash"],
                input=boundary,
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("1", call_count.read_text().strip())
            self.assertEqual(
                "release_required=false\n",
                output.read_text(encoding="utf-8"),
            )

    def test_candidate_and_trusted_checkouts_are_separated(self) -> None:
        workflow = self.workflow()

        for name in {
            "build_identity",
            "seal_child_artifacts",
        }:
            with self.subTest(job=name):
                checkouts = self.checkouts(workflow["jobs"][name])
                self.assertEqual(1, len(checkouts))
                self.assertEqual("candidate", checkouts[0]["with"]["path"])
                self.assertEqual(
                    "${{ env.EXPECTED_RELEASE_COMMIT }}",
                    checkouts[0]["with"]["ref"],
                )

        for name in {
            "deterministic_journeys",
            "browser_accessibility",
            "distribution_compliance",
            "pre_live_provenance",
            "final_release_gate",
        }:
            with self.subTest(job=name):
                checkouts = self.checkouts(workflow["jobs"][name])
                self.assertEqual(2, len(checkouts))
                refs = {
                    checkout["with"]["path"]: checkout["with"]["ref"]
                    for checkout in checkouts
                }
                self.assertEqual(
                    {
                        "candidate": "${{ env.EXPECTED_RELEASE_COMMIT }}",
                        "trusted-verifier": (
                            "${{ env.EXPECTED_WORKFLOW_COMMIT }}"
                        ),
                    },
                    refs,
                )

        for job in workflow["jobs"].values():
            for checkout in self.checkouts(job):
                self.assertRegex(
                    checkout["uses"],
                    r"^actions/checkout@[0-9a-f]{40}$",
                )
                self.assertEqual(0, checkout["with"]["fetch-depth"])
                self.assertFalse(checkout["with"]["persist-credentials"])

    def test_candidate_execution_never_receives_github_token(self) -> None:
        workflow = self.workflow()
        candidate_jobs = {
            name: workflow["jobs"][name]
            for name in PRODUCER_JOBS
        }

        for name, job in candidate_jobs.items():
            source = yaml.safe_dump(job, sort_keys=True)
            with self.subTest(job=name):
                self.assertNotRegex(
                    source,
                    re.compile(
                        r"(?m)^\s*(?:GITHUB_TOKEN|GH_TOKEN)\s*:",
                    ),
                )
                self.assertNotRegex(
                    source,
                    re.compile(
                        r"(?m)(?:^|[\s;])(?:export\s+)?"
                        r"(?:GITHUB_TOKEN|GH_TOKEN)=",
                    ),
                )
                self.assertNotIn("${{ github.token }}", source)

        final = workflow["jobs"]["final_release_gate"]
        generate = self.step(
            final,
            "Generate authenticated final release readiness",
        )
        self.assertEqual("${{ github.token }}", generate["env"]["GITHUB_TOKEN"])
        self.assertIn(
            "${TRUSTED_VERIFIER_ROOT}/starcraft_commander/"
            "micromachine_final_release.py",
            generate["run"],
        )

    def test_final_aggregator_converts_all_non_success_results_to_failure(
        self,
    ) -> None:
        final = self.workflow()["jobs"]["final_release_gate"]
        guard = self.step(final, "Enforce all prerequisite results")

        self.assertEqual("always()", final["if"])
        self.assertEqual(
            {
                "build_identity",
                "deterministic_journeys",
                "browser_accessibility",
                "distribution_compliance",
                "event_admission",
                "pre_live_provenance",
                "seal_child_artifacts",
            },
            set(final["needs"]),
        )
        self.assertEqual("${{ toJSON(needs) }}", final["env"]["PREREQUISITE_RESULTS"])
        self.assertEqual(
            "${{ needs.event_admission.outputs.release_required }}",
            final["env"]["RELEASE_REQUIRED"],
        )
        self.assertIn('release_required == "true"', guard["run"])
        self.assertIn('value.get("result") != "success"', guard["run"])
        self.assertIn('release_required == "false"', guard["run"])
        self.assertIn('value.get("result") != "skipped"', guard["run"])
        self.assertIn(
            'needs["event_admission"].get("result") != "success"',
            guard["run"],
        )
        self.assertIn("raise SystemExit", guard["run"])

    def test_final_gate_has_successful_not_applicable_path(self) -> None:
        final = self.workflow()["jobs"]["final_release_gate"]
        report = self.step(final, "Report not-applicable release event")

        self.assertEqual(
            "needs.event_admission.outputs.release_required == 'false'",
            report["if"],
        )
        self.assertIn("not applicable", report["run"])
        for step in final["steps"]:
            if step.get("name") in {
                "Enforce all prerequisite results",
                "Report not-applicable release event",
            }:
                continue
            with self.subTest(step=step.get("name", step.get("uses"))):
                self.assertIn(
                    "needs.event_admission.outputs.release_required == 'true'",
                    step["if"],
                )

    def test_all_actions_are_immutable_sha_pinned(self) -> None:
        workflow = self.workflow()
        action_uses = [
            step["uses"]
            for job in workflow["jobs"].values()
            for step in job["steps"]
            if "uses" in step
        ]

        self.assertEqual(30, len(action_uses))
        for action in action_uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@\s]+@[0-9a-f]{40}$")

    def test_browser_gate_is_real_and_non_skippable(self) -> None:
        browser = self.workflow()["jobs"]["browser_accessibility"]
        install = self.step(browser, "Install pinned browser gate environment")
        harden = self.step(
            browser,
            "Harden trusted verifier and candidate source boundaries",
        )
        gate = self.step(
            browser,
            "Run non-skippable browser and accessibility gate",
        )
        upload = self.step(
            browser,
            "Upload browser and accessibility child artifact",
        )

        self.assertNotIn("continue-on-error", install)
        self.assertNotIn("continue-on-error", gate)
        self.assertIn(
            "install --with-deps chromium",
            install["run"],
        )
        self.assertIn("--extra browser", install["run"])
        self.assertNotIn("requirements-browser.txt", install["run"])
        self.assertIn(
            "${TRUSTED_VERIFIER_ROOT}/starcraft_commander/"
            "battlefield_browser_gate.py",
            gate["run"],
        )
        self.assertIn("--candidate-root", gate["run"])
        self.assertIn("--candidate-uid", gate["run"])
        self.assertEqual(
            "/usr/bin/python3",
            browser["env"]["VOI_BROWSER_SYSTEM_PYTHON"],
        )
        self.assertIn(
            'readlink -f "${VOI_BROWSER_SYSTEM_PYTHON}"',
            harden["run"],
        )
        self.assertIn('test ! -L "${candidate_python}"', harden["run"])
        self.assertIn('/usr/bin/python3.*)', harden["run"])
        self.assertIn(
            '--user="#${VOI_BROWSER_CANDIDATE_UID}"',
            harden["run"],
        )
        self.assertIn(
            '"${candidate_python}" -I -B -c',
            harden["run"],
        )
        self.assertIn(
            'VOI_BROWSER_CANDIDATE_PYTHON=%s',
            harden["run"],
        )
        self.assertIn(
            '"${VOI_BROWSER_CANDIDATE_PYTHON}"',
            gate["run"],
        )
        self.assertNotIn(
            '--candidate-python \\\n'
            '              "${TRUSTED_VERIFIER_ROOT}/.venv/bin/python"',
            gate["run"],
        )
        self.assertEqual("error", upload["with"]["if-no-files-found"])

    def test_sealed_browser_artifact_enforces_one_percent_visual_limit(
        self,
    ) -> None:
        seal = self.workflow()["jobs"]["seal_child_artifacts"]
        source = self.step(
            seal,
            "Create canonical detached-digest envelopes",
        )["run"]

        self.assertIn("visual_diff_threshold", source)
        self.assertIn("!= 0.01", source)
        self.assertIn("not 0 <= ratio <= 0.01", source)
        self.assertNotIn("0.18", source)
        for evidence in (
            '"actions"',
            '"all_visible"',
            '"cards"',
            '"lanes"',
            '"stages"',
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, source)
        self.assertIn('"desktop": {', source)
        self.assertIn('"mobile": {', source)
        self.assertIn('"actions": 20', source)
        self.assertIn('"actions": 50', source)
        self.assertIn('"cards": 10', source)
        self.assertIn('"stages": 40', source)
        self.assertIn(
            'viewport["actions"] != expected["actions"]',
            source,
        )
        self.assertIn("viewport[\"all_visible\"] is not True", source)
        self.assertIn('viewport["lanes"] != expected["lanes"]', source)
        self.assertIn('viewport["stages"] != expected["stages"]', source)

    def test_sealer_accepts_exact_desktop_and_mobile_profiles(self) -> None:
        result = self.run_sealer(self.browser_viewports())

        self.assertEqual(0, result.returncode, result.stderr)

    def test_sealer_rejects_browser_viewport_profile_drift(self) -> None:
        invalid_mobile_evidence = (
            (
                "desktop counts",
                {
                    "actions": 20,
                    "cards": 4,
                    "stages": 16,
                },
            ),
            ("integral float", {"actions": 50.0}),
            ("boolean count", {"serious_critical_count": False}),
        )

        for label, evidence in invalid_mobile_evidence:
            with self.subTest(label=label):
                viewports = self.browser_viewports()
                viewports[1].update(evidence)
                result = self.run_sealer(viewports)

                self.assertNotEqual(0, result.returncode)
                self.assertIn(
                    "browser viewport evidence is malformed",
                    result.stderr,
                )

    def test_exact_child_ids_and_digests_are_sealed(self) -> None:
        workflow = self.workflow()
        observed_names = {
            step["with"]["name"]
            for job in workflow["jobs"].values()
            for step in job["steps"]
            if str(step.get("uses", "")).startswith(
                "actions/upload-artifact@"
            )
            and step.get("with", {}).get("name") in EXPECTED_CHILD_ARTIFACTS
        }
        seal = workflow["jobs"]["seal_child_artifacts"]
        seal_source = yaml.safe_dump(seal, sort_keys=True)
        downloads = [
            step
            for step in seal["steps"]
            if str(step.get("uses", "")).startswith(
                "actions/download-artifact@"
            )
        ]

        self.assertEqual(EXPECTED_CHILD_ARTIFACTS, observed_names)
        self.assertEqual(5, len(downloads))
        for name in PRODUCER_JOBS:
            outputs = workflow["jobs"][name]["outputs"]
            self.assertIn("artifact_id", outputs)
            self.assertIn("artifact_digest", outputs)
        for download in downloads:
            self.assertIn("artifact-ids", download["with"])
            self.assertNotIn("name", download["with"])
        self.assertIn("archive_sha256", seal_source)
        self.assertIn("ARTIFACT_DIGEST", seal_source)
        self.assertIn("validate_child", seal_source)
        self.assertIn("candidate_web_gui_sha256", seal_source)
        self.assertIn("verifier_sha", seal_source)

    def test_final_gate_uses_sealed_artifact_and_enforces_verdict(self) -> None:
        final = self.workflow()["jobs"]["final_release_gate"]
        download = self.step(final, "Download sealed child artifact tree")
        generate = self.step(
            final,
            "Generate authenticated final release readiness",
        )
        enforce = self.step(final, "Enforce final release verdict")

        self.assertIn("artifact-ids", download["with"])
        self.assertTrue(generate["continue-on-error"])
        self.assertIn("--workflow-sha", generate["run"])
        self.assertIn("--workflow-run-id", generate["run"])
        self.assertIn("--run-attempt", generate["run"])
        self.assertIn(
            "needs.event_admission.outputs.release_required == 'true'",
            enforce["if"],
        )
        self.assertIn("steps.release-gate.outcome != 'success'", enforce["if"])
        self.assertIn("exit 1", enforce["run"])

    def test_workflow_does_not_embed_private_provider_configuration(self) -> None:
        source = WORKFLOW_PATH.read_text(encoding="utf-8")

        for forbidden in (
            "MYPROXY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "api.openai.com",
            "api.anthropic.com",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertNotRegex(
            source,
            re.compile(r"permissions:\s*\n(?:.*\n)*?\s+write"),
        )

    @staticmethod
    def workflow() -> dict[str, object]:
        return yaml.load(
            WORKFLOW_PATH.read_text(encoding="utf-8"),
            Loader=UniqueKeyLoader,
        )

    @staticmethod
    def step(job: dict[str, object], name: str) -> dict[str, object]:
        return next(step for step in job["steps"] if step.get("name") == name)

    @staticmethod
    def checkouts(job: dict[str, object]) -> list[dict[str, object]]:
        return [
            step
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        ]

    @staticmethod
    def browser_viewports() -> list[dict[str, object]]:
        common = {
            "all_visible": True,
            "actual_sha256": "a" * 64,
            "baseline_sha256": "b" * 64,
            "lanes": 4,
            "serious_critical_count": 0,
            "visual_diff_ratio": 0.001,
        }
        return [
            {
                **common,
                "actions": 20,
                "cards": 4,
                "name": "desktop",
                "stages": 16,
            },
            {
                **common,
                "actions": 50,
                "cards": 10,
                "name": "mobile",
                "stages": 40,
            },
        ]

    @classmethod
    def run_sealer(
        cls,
        viewports: list[dict[str, object]],
    ) -> subprocess.CompletedProcess[str]:
        workflow = cls.workflow()
        source = cls.step(
            workflow["jobs"]["seal_child_artifacts"],
            "Create canonical detached-digest envelopes",
        )["run"]
        repository_sha = "7" * 40
        workflow_sha = "8" * 40
        base_sha = "6" * 40
        build_identity = f"sha256:{'9' * 64}"
        binary_sha = "c" * 64
        common = {
            "build_identity": build_identity,
            "manual_live_qa_remaining": True,
            "ok": True,
            "repository_sha": repository_sha,
            "schema_version": 1,
            "status": "passed",
            "verifier_sha": workflow_sha,
        }
        children = {
            "browser_accessibility": {
                **common,
                "candidate_web_gui_sha256": "d" * 64,
                "producer": "browser_accessibility",
                "viewports": viewports,
                "visual_diff_threshold": 0.01,
            },
            "build_identity": {
                **common,
                "binary_sha256": binary_sha,
                "build_report_schema_version": 80,
                "embedded_build_input_identity": build_identity,
                "producer": "build_identity",
            },
            "deterministic_journeys": {
                **common,
                "binary_sha256": binary_sha,
                "bundle_sha256": "e" * 64,
                "embedded_build_input_identity": build_identity,
                "journey_count": 14,
                "producer": "deterministic_journeys",
                "suite_id": "micromachine_deterministic_pre_live_v1",
            },
            "distribution_compliance": {
                **common,
                "base_commit": base_sha,
                "canonical_report_sha256": "f" * 64,
                "distribution_schema_version": 7,
                "producer": "distribution_compliance",
                "secret_finding_count": 0,
            },
            "pre_live_provenance": {
                **common,
                "binary_sha256": binary_sha,
                "build_report_sha256": "1" * 64,
                "embedded_build_input_identity": build_identity,
                "privilege_separation": "dedicated_producer_uid_passed",
                "producer": "pre_live_provenance",
                "provenance_contracts": "passed",
            },
        }
        environment_names = {
            "browser_accessibility": "BROWSER",
            "build_identity": "BUILD_IDENTITY",
            "deterministic_journeys": "JOURNEYS",
            "distribution_compliance": "DISTRIBUTION",
            "pre_live_provenance": "PROVENANCE",
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / "child-downloads"
            environment = os.environ.copy()
            environment.update(
                {
                    "BUILD_IDENTITY": build_identity,
                    "EXPECTED_RELEASE_BASE_COMMIT": base_sha,
                    "EXPECTED_RELEASE_COMMIT": repository_sha,
                    "EXPECTED_WORKFLOW_COMMIT": workflow_sha,
                    "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_RUN_ID": "123",
                    "RUNNER_TEMP": str(root),
                }
            )
            for index, (producer, child) in enumerate(
                children.items(),
                start=1,
            ):
                report = downloads / producer / "report.json"
                report.parent.mkdir(parents=True)
                report.write_text(
                    json.dumps(
                        child,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                prefix = environment_names[producer]
                environment[f"{prefix}_ARTIFACT_ID"] = str(index)
                environment[f"{prefix}_ARTIFACT_DIGEST"] = (
                    f"{index:x}" * 64
                )[:64]

            return subprocess.run(
                ["/bin/bash"],
                input=source,
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
