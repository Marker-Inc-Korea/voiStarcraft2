"""Authenticated provenance primitives for MicroMachine pre-live evidence.

The module deliberately derives status from GitHub, git, build, process, and
filesystem observations. Caller-provided producer names, checksums, and status
claims are accepted only as explicitly ignored compatibility data.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Protocol, cast

from starcraft_commander.micromachine_build_identity import (
    MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION,
    MICROMACHINE_REQUIRED_NATIVE_TESTS,
    REPO_ROOT as BUILD_IDENTITY_REPO_ROOT,
    MicroMachineBuildIdentityConfig,
    build_micromachine_build_identity,
    canonical_micromachine_ctest_registry,
    inspect_git_worktree_state,
    micromachine_build_identity_admission_error,
)
from starcraft_commander.micromachine_pre_live_artifact import (
    GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
    PRE_LIVE_CANDIDATE_AUTHORITY_SCOPE,
    PRE_LIVE_CTEST_EVIDENCE_SCHEMA_VERSION,
    PRE_LIVE_DETERMINISTIC_JOURNEY_MEMBER_NAME,
    PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID,
    PreLiveArtifactMetadata,
    PreLiveBuildAdmissionSnapshot,
    bind_deterministic_journey_bundle_to_build,
    build_pre_live_artifact_bundle,
    canonical_ctest_evidence_bytes,
    canonical_json_bytes,
    verify_downloaded_pre_live_artifact,
    verify_pre_live_artifact_bundle,
)


PRE_LIVE_PROVENANCE_SCHEMA_VERSION: Final[int] = 1
REPLAY_LEDGER_SCHEMA_VERSION: Final[int] = 1
PRODUCER_POLICY_SCHEMA_VERSION: Final[int] = 1
AUTHORITATIVE_REPOSITORY: Final[str] = "Marker-Inc-Korea/voiStarcraft2"
AUTHORITATIVE_REPOSITORY_ID: Final[int] = 1_266_216_251
AUTHORITATIVE_BASE_BRANCH: Final[str] = "main"
AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH: Final[str] = ".github/workflows/ci.yml"
AUTHORITATIVE_PROVENANCE_JOB_NAME: Final[str] = "pre-live-provenance"
AUTHORITATIVE_REPLAY_REF_PREFIX: Final[str] = "refs/tags/voi-pre-live-replay/"
AUTHORITATIVE_REPLAY_REF_PATTERN: Final[str] = "refs/tags/voi-pre-live-replay/**"
AUTHORITATIVE_REPLAY_CREATE_RULESET_NAME: Final[str] = "voi-pre-live-replay-create-only"
AUTHORITATIVE_REPLAY_IMMUTABLE_RULESET_NAME: Final[str] = (
    "voi-pre-live-replay-immutable"
)
AUTHORITATIVE_REPLAY_CLAIMER_USER_ID: Final[int] = 60_510_718
PRODUCER_POLICY_RELATIVE_PATH: Final[Path] = Path(
    "integrations/micromachine/PRE_LIVE_PRODUCERS.json"
)
DETERMINISTIC_JOURNEY_MANIFEST_RELATIVE_PATH: Final[Path] = Path(
    "integrations/micromachine/PRE_LIVE_JOURNEYS.json"
)
DETERMINISTIC_JOURNEY_MODULE_RELATIVE_PATH: Final[Path] = Path(
    "starcraft_commander/micromachine_pre_live_journeys.py"
)
GLOBAL_REPLAY_STATE_ROOT: Final[Path] = (
    Path(pwd.getpwuid(os.getuid()).pw_dir)
    / ".local"
    / "state"
    / "voiStarcraft2"
    / "pre-live-replay"
)
GITHUB_API_VERSION: Final[str] = "2022-11-28"
MAX_GITHUB_JSON_BYTES: Final[int] = 16 * 1024 * 1024
MAX_GITHUB_ARTIFACT_BYTES: Final[int] = 512 * 1024 * 1024
MAX_BUILD_REPORT_BYTES: Final[int] = 16 * 1024 * 1024
MAX_CMAKE_CACHE_BYTES: Final[int] = 16 * 1024 * 1024
MAX_PRODUCER_SOURCE_BYTES: Final[int] = 16 * 1024 * 1024
MAX_PRODUCER_EXECUTABLE_BYTES: Final[int] = 512 * 1024 * 1024
MAX_REPLAY_LEDGER_BYTES: Final[int] = 16 * 1024 * 1024
MAX_REPLAY_ENTRIES: Final[int] = 100_000
UNTRUSTED_STATUS_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "artifact_sha256",
        "authority_scope",
        "checksum",
        "conclusion",
        "digest",
        "ok",
        "producer",
        "release_authoritative",
        "sha",
        "sha256",
        "state",
        "status",
    }
)

_SHA40_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_SHA256_IDENTITY_RE: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
PINNED_NATIVE_EXEC_ROOT_ENV: Final[str] = "VOI_PINNED_NATIVE_EXEC_ROOT"
_REPOSITORY_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
)
_CTEST_SUMMARY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^\s*(\d+)%\s+tests passed,\s+(\d+)\s+tests failed out of\s+(\d+)\s*$"
)
_CTEST_FRACTION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^\s*(\d+)/(\d+)\s+tests passed\s*$"
)
_CMAKE_CTEST_COMMAND_RE: Final[re.Pattern[str]] = re.compile(
    r"(?m)^CMAKE_CTEST_COMMAND:INTERNAL=(.+)$"
)
_BUILD_SCRIPT_UPSTREAM_COMMIT_RE: Final[re.Pattern[str]] = re.compile(
    r'(?m)^(MICROMACHINE_COMMIT|S2CLIENT_COMMIT)="\$\{'
    r'\1:-([0-9a-f]{40})\}"$'
)
_REQUIRED_CTEST_COMMANDS: Final[dict[str, str]] = dict(
    MICROMACHINE_REQUIRED_NATIVE_TESTS
)
_REQUIRED_CTEST_COUNT: Final[int] = len(_REQUIRED_CTEST_COMMANDS)
_EXTERNAL_BUILD_PATH_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "micromachine_dir",
        "s2client_dir",
        "micromachine_build_dir",
        "s2client_build_dir",
        "source_attestation",
    }
)
ISOLATED_PYTHON_BOOTSTRAP: Final[str] = (
    "import runpy,sys;"
    "root,relative,*args=sys.argv[1:];"
    "script=root+'/'+relative;"
    "sys.path.insert(0,root);"
    "sys.argv=[script,*args];"
    "runpy.run_path(script,run_name='__main__')"
)
AUTHENTICATED_PYTHON_EXEC_BOOTSTRAP: Final[str] = r"""
import base64
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback
import zipimport

source_fd_text, snapshot_root, relative_script, *candidate_args = sys.argv[1:]
source_fd = int(source_fd_text)
with os.fdopen(source_fd, "rb", closefd=False) as source_file:
    source_bundle = json.load(source_file)
source_records = {}
for record in source_bundle["sources"]:
    relative_path = Path(record["path"])
    payload = base64.b64decode(record["payload"], validate=True)
    if relative_path.suffix != ".py":
        continue
    if relative_path.name == "__init__.py":
        module_name = ".".join(relative_path.parts[:-1])
        is_package = True
    else:
        module_name = ".".join(relative_path.with_suffix("").parts)
        is_package = False
    if module_name:
        source_records[module_name] = (
            payload,
            str(Path(snapshot_root) / relative_path),
            is_package,
        )
main_source = base64.b64decode(
    source_bundle["main_source"],
    validate=True,
)


class AuthenticatedSourceLoader(
    importlib.abc.MetaPathFinder,
    importlib.abc.Loader,
):
    def find_spec(self, fullname, path=None, target=None):
        record = source_records.get(fullname)
        if record is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            self,
            is_package=record[2],
        )

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = str(module.__name__)
        payload, filename, is_package = source_records[name]
        module.__file__ = filename
        module.__package__ = name if is_package else name.rpartition(".")[0]
        if is_package:
            module.__path__ = [str(Path(filename).parent)]
        exec(compile(payload, filename, "exec"), module.__dict__)


stdlib_root = Path(os.__file__).resolve().parent
trusted_stdlib_paths = [
    str(path)
    for path in (
        stdlib_root.parent
        / f"python{sys.version_info.major}{sys.version_info.minor}.zip",
        stdlib_root,
        stdlib_root / "lib-dynload",
    )
    if path.exists()
]
stdlib_file_finder = importlib.machinery.FileFinder.path_hook(
    (
        importlib.machinery.SourceFileLoader,
        importlib.machinery.SOURCE_SUFFIXES,
    ),
    (
        importlib.machinery.SourcelessFileLoader,
        importlib.machinery.BYTECODE_SUFFIXES,
    ),
    (
        importlib.machinery.ExtensionFileLoader,
        importlib.machinery.EXTENSION_SUFFIXES,
    ),
)
sys.path[:] = trusted_stdlib_paths
sys.path_hooks[:] = [zipimport.zipimporter, stdlib_file_finder]
sys.path_importer_cache.clear()
sys.meta_path[:] = [
    AuthenticatedSourceLoader(),
    importlib.machinery.BuiltinImporter,
    importlib.machinery.FrozenImporter,
    importlib.machinery.PathFinder,
]
script = str(Path(snapshot_root) / relative_script)
sys.argv = [script, *candidate_args]
try:
    globals_dict = {
        "__builtins__": __builtins__,
        "__cached__": None,
        "__file__": script,
        "__loader__": None,
        "__name__": "__main__",
        "__package__": None,
        "__spec__": None,
    }
    exec(compile(main_source, script, "exec"), globals_dict)
except SystemExit as exc:
    if exc.code is None:
        raise SystemExit(0)
    if isinstance(exc.code, int):
        raise
    print(exc.code, file=sys.stderr)
    raise SystemExit(1)
except BaseException:
    traceback.print_exc()
    raise SystemExit(1)
"""
DETERMINISTIC_JOURNEY_PRODUCER_RAW_ARGV: Final[tuple[str, ...]] = (
    "{python}",
    "-I",
    "-B",
    "-S",
    "-c",
    ISOLATED_PYTHON_BOOTSTRAP,
    "{repository}",
    DETERMINISTIC_JOURNEY_MODULE_RELATIVE_PATH.as_posix(),
    "--emit-bundle",
    "{output}",
    "--micromachine-binary",
    "{micromachine_binary}",
    "--node-executable",
    "{node}",
)
DETERMINISTIC_JOURNEY_PRODUCER_CWD: Final[str] = "."
DETERMINISTIC_JOURNEY_PRODUCER_OUTPUT: Final[str] = (
    "producer/deterministic-journeys.zip"
)
SANITIZED_PRODUCER_ENV: Final[dict[str, str]] = {
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
TRUSTED_GIT_EXECUTABLE: Final[str] = "/usr/bin/git"
SANITIZED_GIT_ENV: Final[dict[str, str]] = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
SANITIZED_TEST_ENV: Final[dict[str, str]] = {
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
CommandRunner = Callable[..., subprocess.CompletedProcess[Any]]


class GitHubSourceAdapter(Protocol):
    """Read-only GitHub source boundary used by the verifier."""

    def get_repository(self, repository: str) -> Mapping[str, object]: ...

    def get_issue(
        self,
        repository: str,
        issue_number: int,
    ) -> Mapping[str, object]: ...

    def get_pull_request(
        self,
        repository: str,
        pull_number: int,
    ) -> Mapping[str, object]: ...

    def list_pull_request_closing_issues(
        self,
        repository: str,
        pull_number: int,
    ) -> Sequence[Mapping[str, object]]: ...

    def compare_commits(
        self,
        repository: str,
        *,
        base: str,
        head: str,
    ) -> Mapping[str, object]: ...

    def get_workflow_run(
        self,
        repository: str,
        run_id: int,
    ) -> Mapping[str, object]: ...

    def list_workflow_runs(
        self,
        repository: str,
        workflow_id: int,
        *,
        branch: str,
        event: str,
    ) -> Sequence[Mapping[str, object]]: ...

    def get_workflow(
        self,
        repository: str,
        workflow_id: int,
    ) -> Mapping[str, object]: ...

    def get_workflow_run_attempt(
        self,
        repository: str,
        run_id: int,
        run_attempt: int,
    ) -> Mapping[str, object]: ...

    def list_workflow_run_attempt_jobs(
        self,
        repository: str,
        run_id: int,
        run_attempt: int,
    ) -> Sequence[Mapping[str, object]]: ...

    def get_job(
        self,
        repository: str,
        job_id: int,
    ) -> Mapping[str, object]: ...

    def list_workflow_run_artifacts(
        self,
        repository: str,
        run_id: int,
    ) -> Sequence[Mapping[str, object]]: ...

    def get_artifact(
        self,
        repository: str,
        artifact_id: int,
    ) -> Mapping[str, object]: ...

    def download_artifact(self, repository: str, artifact_id: int) -> bytes: ...

    def get_git_reference(
        self,
        repository: str,
        *,
        ref: str,
    ) -> Mapping[str, object]: ...


class GitHubReferenceAdapter(Protocol):
    """Write-scoped Git reference boundary used only by the replay claimer."""

    def create_git_reference(
        self,
        repository: str,
        *,
        ref: str,
        sha: str,
    ) -> Mapping[str, object]: ...

    def get_git_reference(
        self,
        repository: str,
        *,
        ref: str,
    ) -> Mapping[str, object]: ...

    def list_repository_rulesets(
        self,
        repository: str,
    ) -> Sequence[Mapping[str, object]]: ...

    def get_repository_ruleset(
        self,
        repository: str,
        ruleset_id: int,
    ) -> Mapping[str, object]: ...


class ReplayStore(Protocol):
    """Atomic durable replay authority."""

    def consume(
        self,
        *,
        repository: str,
        replay_digest: str,
        expected_head_sha: str,
    ) -> Mapping[str, object]: ...


class GitHubSourceError(RuntimeError):
    """Raised when GitHub cannot provide bounded, valid API evidence."""


class GitHubHTTPError(GitHubSourceError):
    """Preserve an authenticated GitHub HTTP failure for fail-closed handling."""

    def __init__(self, *, path: str, status: int, body: bytes) -> None:
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"GitHub request failed for {path}: HTTP {status}")


class GitHubRefReplayStore:
    """Consume replay keys through create-only GitHub references."""

    def __init__(self, adapter: GitHubReferenceAdapter) -> None:
        self._adapter = adapter

    def consume(
        self,
        *,
        repository: str,
        replay_digest: str,
        expected_head_sha: str,
    ) -> Mapping[str, object]:
        rulesets = attest_github_replay_rulesets(
            self._adapter,
            repository=repository,
        )
        if rulesets.get("ok") is not True:
            return _component_result(
                _prefixed_blockers("rulesets", rulesets),
                authority="github_ref",
                repository=repository,
                replay_ref=(
                    AUTHORITATIVE_REPLAY_REF_PREFIX
                    + replay_digest.removeprefix("sha256:")
                    if _SHA256_IDENTITY_RE.fullmatch(replay_digest)
                    else None
                ),
                replay_digest=replay_digest,
                expected_head_sha=expected_head_sha,
                consumed=False,
                rulesets=rulesets,
            )
        claim = consume_github_replay_reference(
            self._adapter,
            repository=repository,
            replay_digest=replay_digest,
            expected_head_sha=expected_head_sha,
        )
        return {**claim, "rulesets": rulesets}


class _CrossHostAuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward a GitHub bearer token to artifact storage hosts."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Mapping[str, str],
        new_url: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )
        if redirected is None:
            return None
        old_host = urllib.parse.urlsplit(request.full_url).netloc.casefold()
        new_host = urllib.parse.urlsplit(new_url).netloc.casefold()
        if old_host != new_host:
            redirected.remove_header("Authorization")
        return redirected


class StdlibGitHubRESTAdapter:
    """Minimal stdlib GitHub REST client with bounded response reads."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
        max_artifact_bytes: int = MAX_GITHUB_ARTIFACT_BYTES,
        urlopen: Callable[..., Any] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_artifact_bytes <= 0:
            raise ValueError("max_artifact_bytes must be positive")
        self._token = token
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_artifact_bytes = max_artifact_bytes
        self._urlopen = (
            urlopen
            or urllib.request.build_opener(
                _CrossHostAuthStrippingRedirectHandler()
            ).open
        )

    def get_repository(self, repository: str) -> Mapping[str, object]:
        return self._get_json(self._repo_path(repository))

    def get_issue(
        self,
        repository: str,
        issue_number: int,
    ) -> Mapping[str, object]:
        return self._get_json(
            f"{self._repo_path(repository)}/issues/{_positive_id(issue_number, 'issue_number')}"
        )

    def get_pull_request(
        self,
        repository: str,
        pull_number: int,
    ) -> Mapping[str, object]:
        return self._get_json(
            f"{self._repo_path(repository)}/pulls/{_positive_id(pull_number, 'pull_number')}"
        )

    def list_pull_request_closing_issues(
        self,
        repository: str,
        pull_number: int,
    ) -> Sequence[Mapping[str, object]]:
        normalized = normalize_github_repository(repository, allow_slug=True)
        owner, name = normalized.split("/", 1)
        payload = self._request_json(
            "/graphql",
            method="POST",
            payload={
                "query": (
                    "query($owner:String!,$name:String!,$number:Int!){"
                    "repository(owner:$owner,name:$name){"
                    "pullRequest(number:$number){"
                    "closingIssuesReferences(first:100){"
                    "nodes{databaseId number repository{databaseId nameWithOwner}}"
                    "pageInfo{hasNextPage}"
                    "}}}}"
                ),
                "variables": {
                    "owner": owner,
                    "name": name,
                    "number": _positive_id(pull_number, "pull_number"),
                },
            },
        )
        errors = payload.get("errors")
        if errors not in (None, []):
            raise GitHubSourceError(
                f"GitHub GraphQL closing-issue lookup failed: {errors!r}"
            )
        data = _mapping(payload.get("data"))
        repository_record = _mapping(data.get("repository"))
        pull_request = _mapping(repository_record.get("pullRequest"))
        closing = _mapping(pull_request.get("closingIssuesReferences"))
        page_info = _mapping(closing.get("pageInfo"))
        if page_info.get("hasNextPage") is not False:
            raise GitHubSourceError(
                "pull request has more than 100 closing issue references"
            )
        nodes = closing.get("nodes")
        if not isinstance(nodes, list):
            raise GitHubSourceError(
                "GitHub closingIssuesReferences.nodes must be a list"
            )
        result: list[Mapping[str, object]] = []
        for node in nodes:
            if not isinstance(node, Mapping):
                raise GitHubSourceError(
                    "GitHub closing issue reference must be an object"
                )
            result.append(cast(Mapping[str, object], node))
        return result

    def compare_commits(
        self,
        repository: str,
        *,
        base: str,
        head: str,
    ) -> Mapping[str, object]:
        if not _SHA40_RE.fullmatch(base) or not _SHA40_RE.fullmatch(head):
            raise ValueError("commit comparison requires exact lowercase SHAs")
        return self._get_json(
            f"{self._repo_path(repository)}/compare/{base}...{head}"
        )

    def get_workflow_run(
        self,
        repository: str,
        run_id: int,
    ) -> Mapping[str, object]:
        return self._get_json(
            f"{self._repo_path(repository)}/actions/runs/{_positive_id(run_id, 'run_id')}"
        )

    def list_workflow_runs(
        self,
        repository: str,
        workflow_id: int,
        *,
        branch: str,
        event: str,
    ) -> Sequence[Mapping[str, object]]:
        if not branch or len(branch) > 255:
            raise ValueError("workflow branch is required")
        if event != "pull_request":
            raise ValueError("only pull_request workflow runs are authoritative")
        query = urllib.parse.urlencode(
            {
                "branch": branch,
                "event": event,
                "exclude_pull_requests": "false",
            }
        )
        return self._get_paginated(
            f"{self._repo_path(repository)}/actions/workflows/"
            f"{_positive_id(workflow_id, 'workflow_id')}/runs?{query}",
            "workflow_runs",
        )

    def get_workflow(
        self,
        repository: str,
        workflow_id: int,
    ) -> Mapping[str, object]:
        return self._get_json(
            f"{self._repo_path(repository)}/actions/workflows/"
            f"{_positive_id(workflow_id, 'workflow_id')}"
        )

    def get_workflow_run_attempt(
        self,
        repository: str,
        run_id: int,
        run_attempt: int,
    ) -> Mapping[str, object]:
        return self._get_json(
            f"{self._repo_path(repository)}/actions/runs/"
            f"{_positive_id(run_id, 'run_id')}/attempts/"
            f"{_positive_id(run_attempt, 'run_attempt')}"
        )

    def list_workflow_run_attempt_jobs(
        self,
        repository: str,
        run_id: int,
        run_attempt: int,
    ) -> Sequence[Mapping[str, object]]:
        return self._get_paginated(
            f"{self._repo_path(repository)}/actions/runs/"
            f"{_positive_id(run_id, 'run_id')}/attempts/"
            f"{_positive_id(run_attempt, 'run_attempt')}/jobs",
            "jobs",
        )

    def get_job(
        self,
        repository: str,
        job_id: int,
    ) -> Mapping[str, object]:
        return self._get_json(
            f"{self._repo_path(repository)}/actions/jobs/{_positive_id(job_id, 'job_id')}"
        )

    def list_workflow_run_artifacts(
        self,
        repository: str,
        run_id: int,
    ) -> Sequence[Mapping[str, object]]:
        return self._get_paginated(
            f"{self._repo_path(repository)}/actions/runs/"
            f"{_positive_id(run_id, 'run_id')}/artifacts",
            "artifacts",
        )

    def get_artifact(
        self,
        repository: str,
        artifact_id: int,
    ) -> Mapping[str, object]:
        return self._get_json(
            f"{self._repo_path(repository)}/actions/artifacts/"
            f"{_positive_id(artifact_id, 'artifact_id')}"
        )

    def download_artifact(self, repository: str, artifact_id: int) -> bytes:
        path = (
            f"{self._repo_path(repository)}/actions/artifacts/"
            f"{_positive_id(artifact_id, 'artifact_id')}/zip"
        )
        return self._request_bytes(path, self._max_artifact_bytes)

    def create_git_reference(
        self,
        repository: str,
        *,
        ref: str,
        sha: str,
    ) -> Mapping[str, object]:
        normalized_ref = _normalize_github_reference(ref)
        if not _SHA40_RE.fullmatch(sha):
            raise ValueError("GitHub reference target must be an exact lowercase SHA")
        return self._request_json(
            f"{self._repo_path(repository)}/git/refs",
            method="POST",
            payload={"ref": normalized_ref, "sha": sha},
        )

    def get_git_reference(
        self,
        repository: str,
        *,
        ref: str,
    ) -> Mapping[str, object]:
        normalized_ref = _normalize_github_reference(ref)
        relative_ref = normalized_ref.removeprefix("refs/")
        encoded_ref = urllib.parse.quote(relative_ref, safe="/")
        return self._get_json(f"{self._repo_path(repository)}/git/ref/{encoded_ref}")

    def list_repository_rulesets(
        self,
        repository: str,
    ) -> Sequence[Mapping[str, object]]:
        return self._get_paginated_array(
            f"{self._repo_path(repository)}/rulesets?includes_parents=false"
        )

    def get_repository_ruleset(
        self,
        repository: str,
        ruleset_id: int,
    ) -> Mapping[str, object]:
        return self._get_json(
            f"{self._repo_path(repository)}/rulesets/"
            f"{_positive_id(ruleset_id, 'ruleset_id')}"
        )

    def _repo_path(self, repository: str) -> str:
        normalized = normalize_github_repository(repository, allow_slug=True)
        owner, name = normalized.split("/", 1)
        return (
            "/repos/"
            + urllib.parse.quote(owner, safe="")
            + "/"
            + urllib.parse.quote(name, safe="")
        )

    def _get_json(self, path: str) -> Mapping[str, object]:
        raw = self._request_bytes(path, MAX_GITHUB_JSON_BYTES)
        return self._decode_json_object(raw, path)

    def _request_json(
        self,
        path: str,
        *,
        method: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        raw = self._request_bytes(
            path,
            MAX_GITHUB_JSON_BYTES,
            method=method,
            body=canonical_json_bytes(payload),
        )
        return self._decode_json_object(raw, path)

    @staticmethod
    def _decode_json_object(raw: bytes, path: str) -> Mapping[str, object]:
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubSourceError(f"invalid GitHub JSON for {path}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise GitHubSourceError(f"GitHub object response required for {path}")
        return cast(Mapping[str, object], payload)

    def _get_paginated(
        self,
        path: str,
        key: str,
    ) -> Sequence[Mapping[str, object]]:
        records: list[Mapping[str, object]] = []
        for page in range(1, 101):
            separator = "&" if "?" in path else "?"
            payload = self._get_json(f"{path}{separator}per_page=100&page={page}")
            page_records = payload.get(key)
            if not isinstance(page_records, list):
                raise GitHubSourceError(
                    f"GitHub paginated response missing {key!r} list"
                )
            for record in page_records:
                if not isinstance(record, Mapping):
                    raise GitHubSourceError(
                        f"GitHub {key!r} list contains a non-object"
                    )
                records.append(cast(Mapping[str, object], record))
            if len(page_records) < 100:
                return records
        raise GitHubSourceError(f"GitHub pagination limit exceeded for {path}")

    def _get_paginated_array(
        self,
        path: str,
    ) -> Sequence[Mapping[str, object]]:
        records: list[Mapping[str, object]] = []
        for page in range(1, 101):
            separator = "&" if "?" in path else "?"
            page_path = f"{path}{separator}per_page=100&page={page}"
            raw = self._request_bytes(page_path, MAX_GITHUB_JSON_BYTES)
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GitHubSourceError(
                    f"invalid GitHub JSON for {page_path}: {exc}"
                ) from exc
            if not isinstance(payload, list):
                raise GitHubSourceError(
                    f"GitHub array response required for {page_path}"
                )
            for record in payload:
                if not isinstance(record, Mapping):
                    raise GitHubSourceError(
                        f"GitHub array response contains a non-object for {page_path}"
                    )
                records.append(cast(Mapping[str, object], record))
            if len(payload) < 100:
                return records
        raise GitHubSourceError(f"GitHub pagination limit exceeded for {path}")

    def _request_bytes(
        self,
        path: str,
        maximum: int,
        *,
        method: str = "GET",
        body: bytes | None = None,
    ) -> bytes:
        url = (
            path
            if path.startswith(("http://", "https://"))
            else (self._api_base_url + path)
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "voiStarcraft2-pre-live-provenance",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                payload = response.read(maximum + 1)
        except urllib.error.HTTPError as exc:
            try:
                error_body = exc.read(MAX_GITHUB_JSON_BYTES + 1)
            except OSError:
                error_body = b""
            raise GitHubHTTPError(
                path=path,
                status=int(exc.code),
                body=error_body[:MAX_GITHUB_JSON_BYTES],
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GitHubSourceError(f"GitHub request failed for {path}: {exc}") from exc
        if len(payload) > maximum:
            raise GitHubSourceError(
                f"GitHub response exceeded {maximum} bytes for {path}"
            )
        return payload


def normalize_github_repository(
    remote: str,
    *,
    allow_slug: bool = False,
) -> str:
    """Normalize supported GitHub HTTPS/SSH remotes to ``owner/repository``."""

    if not isinstance(remote, str) or not remote.strip():
        raise ValueError("GitHub repository remote is required")
    value = remote.strip()
    scp_match = re.fullmatch(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?", value)
    if scp_match:
        normalized = f"{scp_match.group(1)}/{scp_match.group(2)}"
    elif "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"https", "ssh"}:
            raise ValueError(f"unsupported GitHub remote scheme: {parsed.scheme!r}")
        if parsed.hostname is None or parsed.hostname.casefold() != "github.com":
            raise ValueError(f"unsupported GitHub remote host: {parsed.hostname!r}")
        if parsed.port is not None:
            raise ValueError("GitHub remote must not use a non-default port")
        if parsed.scheme == "https" and (
            parsed.username is not None or parsed.password is not None
        ):
            raise ValueError("GitHub HTTPS remote must not embed credentials")
        if parsed.scheme == "ssh" and (
            parsed.username != "git" or parsed.password is not None
        ):
            raise ValueError("GitHub SSH remote must use the git account")
        if parsed.query or parsed.fragment:
            raise ValueError("GitHub remote must not contain query or fragment data")
        normalized = parsed.path.strip("/").removesuffix(".git")
    elif allow_slug:
        normalized = value.strip("/").removesuffix(".git")
    else:
        raise ValueError("GitHub remote must use HTTPS or SSH transport")
    if not _REPOSITORY_RE.fullmatch(normalized):
        raise ValueError(f"invalid GitHub repository: {remote!r}")
    return normalized


def _normalize_github_reference(ref: str) -> str:
    if not isinstance(ref, str) or not ref.startswith("refs/"):
        raise ValueError("GitHub reference must be fully qualified")
    if (
        len(ref) > 240
        or ref.endswith("/")
        or ".." in ref
        or "@{" in ref
        or re.search(r"[\x00-\x20~^:?*\\[]", ref)
    ):
        raise ValueError("GitHub reference is invalid")
    parts = ref.split("/")
    if len(parts) < 3 or any(not part or part.endswith(".") for part in parts):
        raise ValueError("GitHub reference is invalid")
    return ref


def attest_repository(
    repository_dir: Path | str,
    *,
    expected_repository: str,
    expected_commit: str,
    command_runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Attest exact HEAD, origin, and a clean tracked/untracked worktree."""

    blockers: list[str] = []
    root = Path(repository_dir).resolve()
    if not _SHA40_RE.fullmatch(expected_commit):
        blockers.append("expected_commit must be an exact lowercase 40-character SHA")
    try:
        normalized_expected = normalize_github_repository(
            expected_repository,
            allow_slug=True,
        )
    except ValueError as exc:
        normalized_expected = ""
        blockers.append(str(exc))
    if not root.is_dir():
        blockers.append(f"repository directory is missing: {root}")
        return _component_result(
            blockers,
            path=str(root),
            expected_repository=normalized_expected,
            expected_commit=expected_commit,
            observed_repository=None,
            observed_commit=None,
            dirty_entries=[],
        )

    observed_commit: str | None = None
    observed_repository: str | None = None
    dirty_entries: list[str] = []
    index_override_entries: list[str] = []
    try:
        head = _run_text(
            command_runner,
            (TRUSTED_GIT_EXECUTABLE, "rev-parse", "HEAD"),
            cwd=root,
            env=SANITIZED_GIT_ENV,
        )
        status_result = _run_text(
            command_runner,
            (
                TRUSTED_GIT_EXECUTABLE,
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--ignored=matching",
            ),
            cwd=root,
            preserve_whitespace=True,
            env=SANITIZED_GIT_ENV,
        )
        origin = _run_text(
            command_runner,
            (TRUSTED_GIT_EXECUTABLE, "remote", "get-url", "origin"),
            cwd=root,
            env=SANITIZED_GIT_ENV,
        )
        if head["returncode"] != 0:
            blockers.append("git rev-parse HEAD failed")
        else:
            observed_commit = str(head["stdout"]).strip()
            if not _SHA40_RE.fullmatch(observed_commit):
                blockers.append(
                    f"git HEAD is not an exact lowercase SHA: {observed_commit!r}"
                )
            elif observed_commit != expected_commit:
                blockers.append(
                    f"repository commit mismatch: expected={expected_commit} "
                    f"actual={observed_commit}"
                )
        if status_result["returncode"] != 0:
            blockers.append("git status failed")
        else:
            status_output = str(status_result["stdout"]).rstrip("\n")
            dirty_entries = status_output.splitlines() if status_output else []
            if dirty_entries:
                blockers.append("repository has tracked or untracked changes")
        worktree_state = inspect_git_worktree_state(root)
        if worktree_state is None:
            blockers.append("direct HEAD/worktree comparison failed")
        else:
            direct_dirty = worktree_state.get("dirty_entries")
            if isinstance(direct_dirty, list):
                dirty_entries = sorted(
                    {
                        *dirty_entries,
                        *(str(entry) for entry in direct_dirty),
                    }
                )
            direct_overrides = worktree_state.get("index_override_entries")
            if isinstance(direct_overrides, list):
                index_override_entries = sorted(
                    str(entry) for entry in direct_overrides
                )
            if direct_dirty:
                blockers.append(
                    "repository differs from HEAD under direct blob comparison"
                )
            if index_override_entries:
                blockers.append(
                    "repository index contains assume-unchanged or skip-worktree flags"
                )
        if origin["returncode"] != 0:
            blockers.append("git remote get-url origin failed")
        else:
            try:
                observed_repository = normalize_github_repository(
                    str(origin["stdout"]).strip()
                )
            except ValueError as exc:
                blockers.append(str(exc))
            else:
                if (
                    normalized_expected
                    and observed_repository.casefold() != normalized_expected.casefold()
                ):
                    blockers.append(
                        "repository origin mismatch: "
                        f"expected={normalized_expected} actual={observed_repository}"
                    )
    except Exception as exc:
        blockers.append(f"git repository attestation failed: {exc}")

    return _component_result(
        blockers,
        path=str(root),
        expected_repository=normalized_expected,
        expected_commit=expected_commit,
        observed_repository=observed_repository,
        observed_commit=observed_commit,
        dirty_entries=dirty_entries,
        index_override_entries=index_override_entries,
    )


def attest_github_source(
    adapter: GitHubSourceAdapter,
    *,
    repository: str,
    expected_repository_id: int,
    issue_number: int,
    pull_number: int,
    run_id: int,
    run_attempt: int,
    job_id: int,
    artifact_id: int,
    expected_head_sha: str,
    expected_issue_state: str = "open",
    expected_pull_state: str = "open",
    node_executable: Path | str | None = None,
) -> dict[str, object]:
    """Fetch and cross-bind immutable GitHub source records."""

    blockers: list[str] = []
    try:
        normalized_repository = normalize_github_repository(
            repository,
            allow_slug=True,
        )
        issue_number = _positive_id(issue_number, "issue_number")
        pull_number = _positive_id(pull_number, "pull_number")
        run_id = _positive_id(run_id, "run_id")
        run_attempt = _positive_id(run_attempt, "run_attempt")
        job_id = _positive_id(job_id, "job_id")
        artifact_id = _positive_id(artifact_id, "artifact_id")
        if not _SHA40_RE.fullmatch(expected_head_sha):
            raise ValueError(
                "expected_head_sha must be an exact lowercase 40-character SHA"
            )

        repository_record = adapter.get_repository(normalized_repository)
        issue = adapter.get_issue(normalized_repository, issue_number)
        pull_request = adapter.get_pull_request(
            normalized_repository,
            pull_number,
        )
        lookup_head = _mapping(pull_request.get("head"))
        lookup_head_ref = lookup_head.get("ref")
        lookup_base = _mapping(pull_request.get("base"))
        lookup_base_sha = lookup_base.get("sha")
        if not isinstance(lookup_head_ref, str) or not lookup_head_ref:
            raise ValueError("pull_request.head.ref is required")
        if not isinstance(lookup_base_sha, str) or not _SHA40_RE.fullmatch(
            lookup_base_sha
        ):
            raise ValueError("pull_request.base.sha must be an exact lowercase SHA")
        closing_issues = adapter.list_pull_request_closing_issues(
            normalized_repository,
            pull_number,
        )
        comparison = adapter.compare_commits(
            normalized_repository,
            base=lookup_base_sha,
            head=expected_head_sha,
        )
        workflow_run = adapter.get_workflow_run(normalized_repository, run_id)
        workflow_id = _positive_id(
            workflow_run.get("workflow_id"),
            "workflow_run.workflow_id",
        )
        workflow = adapter.get_workflow(normalized_repository, workflow_id)
        workflow_runs = adapter.list_workflow_runs(
            normalized_repository,
            workflow_id,
            branch=lookup_head_ref,
            event="pull_request",
        )
        attempt = adapter.get_workflow_run_attempt(
            normalized_repository,
            run_id,
            run_attempt,
        )
        attempt_jobs = adapter.list_workflow_run_attempt_jobs(
            normalized_repository,
            run_id,
            run_attempt,
        )
        job = adapter.get_job(normalized_repository, job_id)
        run_artifacts = adapter.list_workflow_run_artifacts(
            normalized_repository,
            run_id,
        )
        artifact = adapter.get_artifact(normalized_repository, artifact_id)
    except Exception as exc:
        return _component_result(
            [f"GitHub source attestation failed: {exc}"],
            repository=repository,
            expected_head_sha=expected_head_sha,
            source_ids={},
            artifact_sha256=None,
            artifact_size_bytes=None,
        )

    repository_id = _server_positive_id(
        repository_record.get("id"),
        "repository.id",
        blockers,
    )
    if repository_id != expected_repository_id:
        blockers.append(
            "GitHub repository database ID mismatch: "
            f"expected={expected_repository_id} actual={repository_id}"
        )
    server_full_name = repository_record.get("full_name")
    if not isinstance(server_full_name, str):
        blockers.append("repository.full_name is missing")
    else:
        try:
            normalized_server_repository = normalize_github_repository(
                server_full_name,
                allow_slug=True,
            )
        except ValueError as exc:
            blockers.append(str(exc))
        else:
            if (
                normalized_server_repository.casefold()
                != normalized_repository.casefold()
            ):
                blockers.append(
                    "GitHub repository mismatch: "
                    f"expected={normalized_repository} actual={normalized_server_repository}"
                )
    if repository_record.get("archived") is True:
        blockers.append("GitHub repository is archived")
    if repository_record.get("disabled") is True:
        blockers.append("GitHub repository is disabled")

    issue_id = _server_positive_id(issue.get("id"), "issue.id", blockers)
    _expect_server_value(issue, "number", issue_number, "issue", blockers)
    issue_state = _server_string(issue, "state", "issue", blockers)
    if issue_state != expected_issue_state:
        blockers.append(
            "issue state mismatch: "
            f"expected={expected_issue_state!r} actual={issue_state!r}"
        )

    pull_id = _server_positive_id(
        pull_request.get("id"),
        "pull_request.id",
        blockers,
    )
    _expect_server_value(
        pull_request,
        "number",
        pull_number,
        "pull_request",
        blockers,
    )
    pull_state = _server_string(
        pull_request,
        "state",
        "pull_request",
        blockers,
    )
    if pull_state != expected_pull_state:
        blockers.append(
            "pull request state mismatch: "
            f"expected={expected_pull_state!r} actual={pull_state!r}"
        )
    if expected_pull_state == "open" and pull_request.get("merged_at") is not None:
        blockers.append("open pull request unexpectedly has merged_at")
    closing_issue_id, closing_issue_number = _single_repository_closing_issue(
        closing_issues,
        repository=normalized_repository,
        repository_id=expected_repository_id,
        blockers=blockers,
    )
    if closing_issue_id != issue_id or closing_issue_number != issue_number:
        blockers.append(
            "GitHub closingIssuesReferences does not bind the selected "
            f"authenticated issue #{issue_number}"
        )
    pull_head = _mapping(pull_request.get("head"))
    pull_head_sha = pull_head.get("sha")
    if pull_head_sha != expected_head_sha:
        blockers.append(
            f"pull request head SHA mismatch: expected={expected_head_sha} "
            f"actual={pull_head_sha!r}"
        )
    pull_head_repo = _mapping(pull_head.get("repo"))
    pull_head_full_name = pull_head_repo.get("full_name")
    if (
        isinstance(pull_head_full_name, str)
        and pull_head_full_name.casefold() != normalized_repository.casefold()
    ):
        blockers.append(
            "pull request head repository mismatch: "
            f"expected={normalized_repository} actual={pull_head_full_name}"
        )
    pull_head_repository_id = _server_positive_id(
        pull_head_repo.get("id"),
        "pull_request.head.repo.id",
        blockers,
    )
    if pull_head_repository_id != expected_repository_id:
        blockers.append(
            "pull request head repository ID mismatch: "
            f"expected={expected_repository_id} actual={pull_head_repository_id}"
        )
    pull_head_ref = _server_string(
        pull_head,
        "ref",
        "pull_request.head",
        blockers,
    )
    pull_base = _mapping(pull_request.get("base"))
    pull_base_ref = _server_string(
        pull_base,
        "ref",
        "pull_request.base",
        blockers,
    )
    pull_base_sha = _server_string(
        pull_base,
        "sha",
        "pull_request.base",
        blockers,
    )
    if pull_base_ref != AUTHORITATIVE_BASE_BRANCH:
        blockers.append(
            "pull request base branch mismatch: "
            f"expected={AUTHORITATIVE_BASE_BRANCH!r} actual={pull_base_ref!r}"
        )
    pull_base_repo = _mapping(pull_base.get("repo"))
    pull_base_repository_id = _server_positive_id(
        pull_base_repo.get("id"),
        "pull_request.base.repo.id",
        blockers,
    )
    if pull_base_repository_id != expected_repository_id:
        blockers.append(
            "pull request base repository ID mismatch: "
            f"expected={expected_repository_id} actual={pull_base_repository_id}"
        )
    pull_base_full_name = pull_base_repo.get("full_name")
    if (
        not isinstance(pull_base_full_name, str)
        or pull_base_full_name.casefold() != normalized_repository.casefold()
    ):
        blockers.append(
            "pull request base repository mismatch: "
            f"expected={normalized_repository} actual={pull_base_full_name!r}"
        )
    _validate_comparison_ancestry(
        comparison,
        base_sha=pull_base_sha,
        head_sha=expected_head_sha,
        blockers=blockers,
    )
    authority = {
        "scope": PRE_LIVE_CANDIDATE_AUTHORITY_SCOPE,
        "release_authoritative": False,
        "event": "pull_request",
        "pull_request": {
            "database_id": pull_id,
            "number": pull_number,
            "head_sha": expected_head_sha,
            "head_ref": pull_head_ref,
            "head_repository_id": pull_head_repository_id,
        },
        "closing_issue": {
            "repository_full_name": normalized_repository,
            "repository_database_id": repository_id,
            "database_id": issue_id,
            "number": issue_number,
        },
    }

    _expect_server_value(workflow_run, "id", run_id, "workflow_run", blockers)
    _expect_server_value(
        workflow_run,
        "run_attempt",
        run_attempt,
        "workflow_run",
        blockers,
    )
    run_head_sha = workflow_run.get("head_sha")
    if run_head_sha != expected_head_sha:
        blockers.append(
            f"workflow run head SHA mismatch: expected={expected_head_sha} "
            f"actual={run_head_sha!r}"
        )
    run_head_repository = _mapping(workflow_run.get("head_repository"))
    run_head_repository_id = _server_positive_id(
        run_head_repository.get("id"),
        "workflow_run.head_repository.id",
        blockers,
    )
    if run_head_repository_id != expected_repository_id:
        blockers.append(
            "workflow run head repository ID mismatch: "
            f"expected={expected_repository_id} actual={run_head_repository_id}"
        )
    run_head_repository_name = run_head_repository.get("full_name")
    if (
        not isinstance(run_head_repository_name, str)
        or run_head_repository_name.casefold() != normalized_repository.casefold()
    ):
        blockers.append(
            "workflow run head repository mismatch: "
            f"expected={normalized_repository} actual={run_head_repository_name!r}"
        )
    _expect_server_value(
        workflow_run,
        "workflow_id",
        workflow_id,
        "workflow_run",
        blockers,
    )
    _expect_server_value(workflow, "id", workflow_id, "workflow", blockers)
    workflow_path = _server_string(
        workflow_run,
        "path",
        "workflow_run",
        blockers,
    )
    registered_workflow_path = _server_string(
        workflow,
        "path",
        "workflow",
        blockers,
    )
    if workflow.get("state") != "active":
        blockers.append(f"workflow is not active: state={workflow.get('state')!r}")
    if registered_workflow_path != workflow_path:
        blockers.append(
            "workflow path differs between run and workflow registry: "
            f"run={workflow_path!r} registry={registered_workflow_path!r}"
        )
    if workflow_path != AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH:
        blockers.append(
            "workflow path mismatch: "
            f"expected={AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH} "
            f"actual={workflow_path!r}"
        )
    head_branch = _server_string(
        workflow_run,
        "head_branch",
        "workflow_run",
        blockers,
    )
    event = _server_string(workflow_run, "event", "workflow_run", blockers)
    if event != "pull_request":
        blockers.append(
            f"workflow run event mismatch: expected='pull_request' actual={event!r}"
        )
    if head_branch != pull_head_ref:
        blockers.append(
            "workflow run branch differs from pull request head ref: "
            f"run={head_branch!r} pull={pull_head_ref!r}"
        )
    workflow_ref: str | None = None
    workflow_sha: str | None = None
    _validate_workflow_pull_request_binding(
        workflow_run.get("pull_requests"),
        label="workflow_run.pull_requests",
        pull_id=pull_id,
        pull_number=pull_number,
        expected_head_sha=expected_head_sha,
        expected_repository_id=expected_repository_id,
        blockers=blockers,
    )
    eligible_runs = _eligible_workflow_runs(
        adapter,
        workflow_runs,
        repository=normalized_repository,
        current_run=workflow_run,
        workflow_id=workflow_id,
        workflow_path=AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH,
        pull_id=pull_id,
        pull_number=pull_number,
        head_sha=expected_head_sha,
        head_branch=pull_head_ref,
        repository_id=expected_repository_id,
        blockers=blockers,
    )
    if not eligible_runs:
        blockers.append("no applicable workflow run exists for the candidate head")
    else:
        newest_run = max(eligible_runs, key=_workflow_run_order_key)
        if newest_run.get("id") != run_id:
            blockers.append(
                "selected workflow run is stale: "
                f"selected={run_id} latest={newest_run.get('id')!r}"
            )
        elif newest_run.get("run_attempt") != run_attempt:
            blockers.append(
                "selected workflow run attempt is stale: "
                f"selected={run_attempt} "
                f"latest={newest_run.get('run_attempt')!r}"
            )
    run_status = _server_string(
        workflow_run,
        "status",
        "workflow_run",
        blockers,
    )
    run_conclusion = _server_string(
        workflow_run,
        "conclusion",
        "workflow_run",
        blockers,
    )
    if run_status != "completed" or run_conclusion != "success":
        blockers.append(
            "workflow run did not complete successfully: "
            f"status={run_status!r} conclusion={run_conclusion!r}"
        )

    _expect_server_value(attempt, "id", run_id, "run_attempt", blockers)
    _expect_server_value(
        attempt,
        "run_attempt",
        run_attempt,
        "run_attempt",
        blockers,
    )
    attempt_head_sha = attempt.get("head_sha")
    if attempt_head_sha != expected_head_sha:
        blockers.append(
            f"workflow run attempt head SHA mismatch: expected={expected_head_sha} "
            f"actual={attempt_head_sha!r}"
        )
    _expect_server_value(
        attempt,
        "head_branch",
        pull_head_ref,
        "run_attempt",
        blockers,
    )
    _expect_server_value(
        attempt,
        "event",
        "pull_request",
        "run_attempt",
        blockers,
    )
    _validate_workflow_pull_request_binding(
        attempt.get("pull_requests"),
        label="run_attempt.pull_requests",
        pull_id=pull_id,
        pull_number=pull_number,
        expected_head_sha=expected_head_sha,
        expected_repository_id=expected_repository_id,
        blockers=blockers,
    )
    attempt_status = _server_string(
        attempt,
        "status",
        "run_attempt",
        blockers,
    )
    attempt_conclusion = _server_string(
        attempt,
        "conclusion",
        "run_attempt",
        blockers,
    )
    if attempt_status != "completed" or attempt_conclusion != "success":
        blockers.append(
            "workflow run attempt did not complete successfully: "
            f"status={attempt_status!r} conclusion={attempt_conclusion!r}"
        )
    attempt_started_at = _server_utc(
        attempt,
        "run_started_at",
        "run_attempt",
        blockers,
    )
    attempt_updated_at = _server_utc(
        attempt,
        "updated_at",
        "run_attempt",
        blockers,
    )
    if (
        attempt_started_at is not None
        and attempt_updated_at is not None
        and attempt_started_at > attempt_updated_at
    ):
        blockers.append("workflow run attempt timestamps are inverted")

    listed_job_ids = {
        candidate.get("id")
        for candidate in attempt_jobs
        if isinstance(candidate, Mapping)
    }
    if job_id not in listed_job_ids:
        blockers.append(
            f"job {job_id} is not in workflow run {run_id} attempt {run_attempt}"
        )
    matching_job_summaries = [
        candidate
        for candidate in attempt_jobs
        if isinstance(candidate, Mapping)
        and candidate.get("name") == AUTHORITATIVE_PROVENANCE_JOB_NAME
    ]
    if len(matching_job_summaries) != 1:
        blockers.append(
            "pre-live workflow attempt must contain exactly one named "
            f"provenance job: actual={len(matching_job_summaries)}"
        )
    elif matching_job_summaries[0].get("id") != job_id:
        blockers.append(
            "selected provenance job ID differs from the uniquely named job: "
            f"selected={job_id} actual={matching_job_summaries[0].get('id')!r}"
        )
    _expect_server_value(job, "id", job_id, "job", blockers)
    _expect_server_value(job, "run_id", run_id, "job", blockers)
    _expect_server_value(job, "run_attempt", run_attempt, "job", blockers)
    if job.get("head_sha") != expected_head_sha:
        blockers.append(
            "workflow job head SHA mismatch: "
            f"expected={expected_head_sha} actual={job.get('head_sha')!r}"
        )
    job_name = _server_string(job, "name", "job", blockers)
    if job_name != AUTHORITATIVE_PROVENANCE_JOB_NAME:
        blockers.append(
            "workflow job name mismatch: "
            f"expected={AUTHORITATIVE_PROVENANCE_JOB_NAME!r} actual={job_name!r}"
        )
    job_status = _server_string(job, "status", "job", blockers)
    job_conclusion = _server_string(job, "conclusion", "job", blockers)
    if job_status != "completed" or job_conclusion != "success":
        blockers.append(
            "workflow job did not complete successfully: "
            f"status={job_status!r} conclusion={job_conclusion!r}"
        )
    job_started_at = _server_utc(job, "started_at", "job", blockers)
    job_completed_at = _server_utc(job, "completed_at", "job", blockers)
    if (
        job_started_at is not None
        and job_completed_at is not None
        and job_started_at > job_completed_at
    ):
        blockers.append("workflow job timestamps are inverted")
    if (
        attempt_started_at is not None
        and job_started_at is not None
        and job_started_at < attempt_started_at
    ):
        blockers.append("workflow job predates the selected run attempt")
    if (
        attempt_updated_at is not None
        and job_completed_at is not None
        and job_completed_at > attempt_updated_at
    ):
        blockers.append("workflow job completed after the selected run attempt")

    listed_artifact_ids = {
        candidate.get("id")
        for candidate in run_artifacts
        if isinstance(candidate, Mapping)
    }
    if artifact_id not in listed_artifact_ids:
        blockers.append(f"artifact {artifact_id} is not in workflow run {run_id}")
    artifact_name = _server_string(artifact, "name", "artifact", blockers)
    matching_artifact_summaries = [
        candidate
        for candidate in run_artifacts
        if isinstance(candidate, Mapping) and candidate.get("name") == artifact_name
    ]
    if len(matching_artifact_summaries) != 1:
        blockers.append(
            "pre-live workflow run must contain exactly one artifact with the "
            f"selected logical name: actual={len(matching_artifact_summaries)}"
        )
    elif matching_artifact_summaries[0].get("id") != artifact_id:
        blockers.append(
            "selected provenance artifact ID differs from the uniquely named "
            f"artifact: selected={artifact_id} "
            f"actual={matching_artifact_summaries[0].get('id')!r}"
        )
    _expect_server_value(artifact, "id", artifact_id, "artifact", blockers)
    if artifact.get("expired") is not False:
        blockers.append("GitHub artifact is expired or has no server expiry state")
    artifact_run = _mapping(artifact.get("workflow_run"))
    _expect_server_value(
        artifact_run,
        "id",
        run_id,
        "artifact.workflow_run",
        blockers,
    )
    artifact_head_sha = artifact_run.get("head_sha")
    if artifact_head_sha != expected_head_sha:
        blockers.append(
            f"artifact head SHA mismatch: expected={expected_head_sha} "
            f"actual={artifact_head_sha!r}"
        )
    artifact_created_at = _server_utc(
        artifact,
        "created_at",
        "artifact",
        blockers,
    )
    artifact_updated_at = _server_utc(
        artifact,
        "updated_at",
        "artifact",
        blockers,
    )
    if (
        artifact_created_at is not None
        and artifact_updated_at is not None
        and artifact_created_at > artifact_updated_at
    ):
        blockers.append("artifact timestamps are inverted")
    if (
        job_started_at is not None
        and artifact_created_at is not None
        and artifact_created_at < job_started_at
    ):
        blockers.append("artifact predates the selected workflow job")
    if (
        job_completed_at is not None
        and artifact_created_at is not None
        and artifact_created_at > job_completed_at
    ):
        blockers.append("artifact was created after the selected workflow job")
    if (
        job_completed_at is not None
        and artifact_updated_at is not None
        and artifact_updated_at > job_completed_at
    ):
        blockers.append("artifact was updated after the selected workflow job")
    if artifact_created_at is not None:
        for candidate in attempt_jobs:
            if not isinstance(candidate, Mapping) or candidate.get("id") == job_id:
                continue
            candidate_conclusion = candidate.get("conclusion")
            candidate_started = _parse_utc(candidate.get("started_at"))
            candidate_completed = _parse_utc(candidate.get("completed_at"))
            if (
                candidate_conclusion == "skipped"
                and candidate_started is None
                and candidate_completed is None
            ):
                continue
            if candidate_started is None or candidate_completed is None:
                blockers.append(
                    "cannot establish exclusive artifact upload window because "
                    f"job {candidate.get('id')!r} lacks exact timestamps"
                )
                continue
            if candidate_started <= artifact_created_at <= candidate_completed:
                blockers.append(
                    "artifact creation overlaps another workflow job: "
                    f"selected={job_id} overlapping={candidate.get('id')!r}"
                )
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None
    artifact_bundle: dict[str, object] = {
        "ok": False,
        "blockers": [{"code": "not_verified"}],
        "manifest": None,
    }
    server_artifact_digest = artifact.get("digest")
    if (
        not isinstance(server_artifact_digest, str)
        or _SHA256_IDENTITY_RE.fullmatch(server_artifact_digest) is None
    ):
        blockers.append("GitHub artifact digest is missing or not canonical sha256")
    if not blockers:
        try:
            artifact_bytes = adapter.download_artifact(
                normalized_repository,
                artifact_id,
            )
        except Exception as exc:
            blockers.append(f"GitHub artifact download failed: {exc}")
        else:
            artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
            artifact_size_bytes = len(artifact_bytes)
            expected_server_digest = f"sha256:{artifact_sha256}"
            if server_artifact_digest != expected_server_digest:
                blockers.append(
                    "GitHub artifact digest mismatch: "
                    f"server={server_artifact_digest!r} "
                    f"downloaded={expected_server_digest}"
                )
            artifact_bundle = verify_downloaded_pre_live_artifact(
                artifact_bytes,
                node_executable=node_executable,
            )
            if artifact_bundle.get("ok") is not True:
                bundle_blockers = artifact_bundle.get("blockers")
                blockers.append(
                    f"GitHub artifact bundle failed verification: {bundle_blockers!r}"
                )
            else:
                bundle_role_evidence = _mapping(artifact_bundle.get("role_evidence"))
                bundled_provenance = _mapping(
                    bundle_role_evidence.get("producer_provenance")
                )
                producer_started_at = _parse_utc(bundled_provenance.get("started_at"))
                producer_ended_at = _parse_utc(bundled_provenance.get("ended_at"))
                if (
                    producer_started_at is None
                    or producer_ended_at is None
                    or job_started_at is None
                    or job_completed_at is None
                    or producer_started_at < job_started_at
                    or producer_ended_at > job_completed_at
                ):
                    blockers.append(
                        "bundled producer execution is outside the selected "
                        "workflow job window"
                    )
                manifest = _mapping(artifact_bundle.get("manifest"))
                manifest_authority = _mapping(manifest.get("authority"))
                manifest_pull_request = _mapping(manifest_authority.get("pull_request"))
                manifest_closing_issue = _mapping(
                    manifest_authority.get("closing_issue")
                )
                manifest_repository = _mapping(manifest.get("repository"))
                manifest_workflow = _mapping(manifest.get("workflow"))
                manifest_run = _mapping(manifest.get("run"))
                manifest_job = _mapping(manifest.get("job"))
                manifest_artifact = _mapping(manifest.get("artifact"))
                workflow_ref = _server_string(
                    manifest_workflow,
                    "ref",
                    "artifact manifest workflow",
                    blockers,
                )
                workflow_sha = _server_string(
                    manifest_workflow,
                    "sha",
                    "artifact manifest workflow",
                    blockers,
                )
                workflow_git_ref = _validate_workflow_execution_identity(
                    repository=normalized_repository,
                    workflow_path=workflow_path,
                    pull_number=pull_number,
                    pull_head_ref=pull_head_ref,
                    workflow_ref=workflow_ref,
                    workflow_sha=workflow_sha,
                    blockers=blockers,
                )
                _validate_workflow_reference_target(
                    adapter,
                    repository=normalized_repository,
                    workflow_git_ref=workflow_git_ref,
                    workflow_sha=workflow_sha,
                    blockers=blockers,
                )
                bundle_bindings = {
                    "authority.scope": (
                        manifest_authority.get("scope"),
                        PRE_LIVE_CANDIDATE_AUTHORITY_SCOPE,
                    ),
                    "authority.release_authoritative": (
                        manifest_authority.get("release_authoritative"),
                        False,
                    ),
                    "authority.event": (
                        manifest_authority.get("event"),
                        "pull_request",
                    ),
                    "authority.pull_request.database_id": (
                        manifest_pull_request.get("database_id"),
                        pull_id,
                    ),
                    "authority.pull_request.number": (
                        manifest_pull_request.get("number"),
                        pull_number,
                    ),
                    "authority.pull_request.head_sha": (
                        manifest_pull_request.get("head_sha"),
                        expected_head_sha,
                    ),
                    "authority.pull_request.head_ref": (
                        manifest_pull_request.get("head_ref"),
                        pull_head_ref,
                    ),
                    "authority.pull_request.head_repository_id": (
                        manifest_pull_request.get("head_repository_id"),
                        pull_head_repository_id,
                    ),
                    "authority.closing_issue.repository_full_name": (
                        manifest_closing_issue.get("repository_full_name"),
                        normalized_repository,
                    ),
                    "authority.closing_issue.repository_database_id": (
                        manifest_closing_issue.get("repository_database_id"),
                        repository_id,
                    ),
                    "authority.closing_issue.database_id": (
                        manifest_closing_issue.get("database_id"),
                        issue_id,
                    ),
                    "authority.closing_issue.number": (
                        manifest_closing_issue.get("number"),
                        issue_number,
                    ),
                    "repository.full_name": (
                        manifest_repository.get("full_name"),
                        normalized_repository,
                    ),
                    "repository.database_id": (
                        manifest_repository.get("database_id"),
                        expected_repository_id,
                    ),
                    "repository.commit_sha": (
                        manifest_repository.get("commit_sha"),
                        expected_head_sha,
                    ),
                    "workflow.id": (
                        manifest_workflow.get("id"),
                        workflow_id,
                    ),
                    "workflow.path": (
                        manifest_workflow.get("path"),
                        workflow_path,
                    ),
                    "run.id": (manifest_run.get("id"), run_id),
                    "run.attempt": (
                        manifest_run.get("attempt"),
                        run_attempt,
                    ),
                    "job.id": (manifest_job.get("id"), job_id),
                    "job.name": (manifest_job.get("name"), job_name),
                    "artifact.logical_name": (
                        manifest_artifact.get("logical_name"),
                        artifact.get("name"),
                    ),
                }
                for label, (actual, expected) in bundle_bindings.items():
                    if actual != expected:
                        blockers.append(
                            f"GitHub artifact manifest {label} mismatch: "
                            f"expected={expected!r} actual={actual!r}"
                        )

    source_ids = {
        "repository_id": repository_id,
        "issue_id": issue_id,
        "issue_number": issue_number,
        "pull_request_id": pull_id,
        "pull_number": pull_number,
        "workflow_run_id": run_id,
        "workflow_id": workflow_id,
        "run_attempt": run_attempt,
        "job_id": job_id,
        "artifact_database_id": artifact_id,
    }
    return _component_result(
        blockers,
        repository=normalized_repository,
        expected_head_sha=expected_head_sha,
        head_sha=run_head_sha,
        workflow_path=workflow_path,
        workflow_ref=workflow_ref,
        workflow_sha=workflow_sha,
        workflow_id=workflow_id,
        head_branch=head_branch,
        event=event,
        issue_state=issue_state,
        pull_request_state=pull_state,
        run_status=run_status,
        run_conclusion=run_conclusion,
        run_attempt_status=attempt_status,
        run_attempt_conclusion=attempt_conclusion,
        run_attempt_started_at=(
            _format_utc(attempt_started_at) if attempt_started_at is not None else None
        ),
        run_attempt_updated_at=(
            _format_utc(attempt_updated_at) if attempt_updated_at is not None else None
        ),
        job_status=job_status,
        job_conclusion=job_conclusion,
        job_name=job_name,
        job_started_at=(
            _format_utc(job_started_at) if job_started_at is not None else None
        ),
        job_completed_at=(
            _format_utc(job_completed_at) if job_completed_at is not None else None
        ),
        artifact_name=artifact_name,
        artifact_created_at=(
            _format_utc(artifact_created_at)
            if artifact_created_at is not None
            else None
        ),
        artifact_updated_at=(
            _format_utc(artifact_updated_at)
            if artifact_updated_at is not None
            else None
        ),
        artifact_server_digest=server_artifact_digest,
        artifact_sha256=artifact_sha256,
        artifact_size_bytes=artifact_size_bytes,
        artifact_bundle=artifact_bundle,
        authority=authority,
        source_ids=source_ids,
    )


def attest_build_binding(
    report_path: Path | str,
    *,
    repository_dir: Path | str,
    expected_repository_commit: str,
    expected_build_dir: Path | str | None = None,
    command_runner: CommandRunner = subprocess.run,
    git_runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Bind supported build identity inputs to one commit and run required CTests."""

    blockers: list[str] = []
    path = Path(report_path).absolute()
    repository_root = Path(repository_dir).resolve()
    recorded: Mapping[str, object] | None = None
    current: Mapping[str, object] | None = None
    config: MicroMachineBuildIdentityConfig | None = None
    report_sha256: str | None = None
    report_snapshot: tuple[int, int, int, int, str] | None = None
    report_eligible = True
    upstream_commit_policy = _repository_authoritative_upstream_commits(
        repository_root,
        expected_commit=expected_repository_commit,
        git_runner=git_runner,
    )
    blockers.extend(
        _prefixed_blockers("repository upstream commit policy", upstream_commit_policy)
    )
    repository_head = _git_head(repository_root, git_runner)
    if repository_head != expected_repository_commit:
        blockers.append(
            "repository build-input HEAD mismatch: "
            f"expected={expected_repository_commit} actual={repository_head}"
        )
    if path.name != "voi_build_identity.json":
        blockers.append("build report must be named voi_build_identity.json")
        report_eligible = False
    if path.is_symlink():
        blockers.append("build report must not be a symlink")
        report_eligible = False
    if report_eligible:
        try:
            raw, report_snapshot = _read_regular_file_snapshot(
                path,
                maximum=MAX_BUILD_REPORT_BYTES,
            )
            report_sha256 = hashlib.sha256(raw).hexdigest()
        except OSError as exc:
            blockers.append(f"build report is missing or unreadable: {exc}")
        else:
            try:
                payload = json.loads(
                    raw,
                    object_pairs_hook=_reject_duplicate_json_object_keys,
                    parse_constant=_reject_nonfinite_json,
                )
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                blockers.append(f"build report is malformed: {exc}")
            else:
                if not isinstance(payload, Mapping):
                    blockers.append("build report must contain a JSON object")
                else:
                    recorded = cast(Mapping[str, object], payload)
                    schema_version = recorded.get("schema_version")
                    if (
                        type(schema_version) is not int
                        or schema_version
                        != MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION
                    ):
                        blockers.append(
                            "unsupported build report schema: "
                            f"expected={MICROMACHINE_BUILD_IDENTITY_SCHEMA_VERSION} "
                            f"actual={schema_version!r}"
                        )
                    elif recorded.get("ok") is not True:
                        blockers.append(
                            "supported build identity report is not accepted"
                        )
                    elif recorded.get("failures") != []:
                        blockers.append(
                            "supported build identity report contains recorded failures"
                        )
                    elif upstream_commit_policy.get("ok") is True:
                        try:
                            config = _build_config_from_report(
                                recorded,
                                expected_micromachine_commit=str(
                                    upstream_commit_policy["micromachine_commit"]
                                ),
                                expected_s2client_commit=str(
                                    upstream_commit_policy["s2client_commit"]
                                ),
                            )
                        except (TypeError, ValueError) as exc:
                            blockers.append(
                                f"invalid build report configuration: {exc}"
                            )

    ctest_result: dict[str, object] = {
        "schema_version": PRE_LIVE_CTEST_EVIDENCE_SCHEMA_VERSION,
        "argv": None,
        "discovery_argv": None,
        "ctest_executable": None,
        "ctest_executable_sha256": None,
        "returncode": None,
        "passed": 0,
        "total": 0,
        "failures": 0,
        "test_names": [],
        "test_executables": {},
        "test_manifest_sha256": None,
        "registry_sha256": None,
        "stdout_sha256": None,
        "stderr_sha256": None,
    }
    repository_inputs: dict[str, object] = {
        "ok": False,
        "blockers": ["build configuration was not available"],
        "repository_commit": expected_repository_commit,
        "digest": None,
        "paths": {},
    }
    if config is not None:
        repository_inputs = _attest_repository_build_inputs(
            config,
            repository_root=repository_root,
            expected_commit=expected_repository_commit,
            git_runner=git_runner,
        )
        blockers.extend(
            _prefixed_blockers("repository build inputs", repository_inputs)
        )
        if repository_inputs.get("ok") is True:
            repository_input_material = {
                "paths": dict(_mapping(repository_inputs.get("paths"))),
                "upstream_commit_policy": {
                    "path": upstream_commit_policy.get("path"),
                    "sha256": upstream_commit_policy.get("sha256"),
                    "micromachine_commit": upstream_commit_policy.get(
                        "micromachine_commit"
                    ),
                    "s2client_commit": upstream_commit_policy.get("s2client_commit"),
                },
            }
            repository_inputs["upstream_commit_policy"] = repository_input_material[
                "upstream_commit_policy"
            ]
            repository_inputs["digest"] = (
                "sha256:"
                + hashlib.sha256(
                    _canonical_json(repository_input_material)
                ).hexdigest()
            )
        build_dir_bound = True
        lexical_build_dir = Path(os.path.abspath(config.micromachine_build_dir))
        lexical_source_dir = Path(os.path.abspath(config.micromachine_dir))
        if _path_has_symlink_component(
            lexical_build_dir,
            stop=lexical_source_dir,
        ):
            blockers.append("recorded micromachine_build_dir contains a symlink")
            build_dir_bound = False
        if path.parent.resolve() != config.micromachine_build_dir.resolve():
            blockers.append(
                "build report must reside in its recorded micromachine_build_dir"
            )
            build_dir_bound = False
        if expected_build_dir is not None and (
            config.micromachine_build_dir.resolve()
            != Path(expected_build_dir).resolve()
        ):
            blockers.append(
                "build directory mismatch: "
                f"expected={Path(expected_build_dir).resolve()} "
                f"actual={config.micromachine_build_dir.resolve()}"
            )
            build_dir_bound = False
        if build_dir_bound:
            try:
                current = build_micromachine_build_identity(config)
            except Exception as exc:
                blockers.append(f"current build identity reconstruction failed: {exc}")
            if recorded is not None and current is not None:
                admission_error = micromachine_build_identity_admission_error(
                    recorded,
                    current,
                )
                if admission_error:
                    blockers.append(admission_error)
            if current is not None and current.get("ok") is True and not blockers:
                ctest_result = _run_ctest(
                    config.micromachine_build_dir,
                    command_runner,
                )
                if ctest_result["ok"] is not True:
                    blockers.extend(cast(list[str], ctest_result["blockers"]))
                current_observed = current.get("observed")
                current_native_tests = (
                    current_observed.get("native_tests")
                    if isinstance(current_observed, Mapping)
                    else None
                )
                current_registry = (
                    current_native_tests.get("registry")
                    if isinstance(current_native_tests, Mapping)
                    else None
                )
                expected_registry_sha256 = (
                    current_registry.get("sha256")
                    if isinstance(current_registry, Mapping)
                    else None
                )
                if (
                    ctest_result.get("registry_sha256")
                    != expected_registry_sha256
                ):
                    blockers.append(
                        "CTest registry digest differs from the supported "
                        "build identity: "
                        f"expected={expected_registry_sha256!r} "
                        f"actual={ctest_result.get('registry_sha256')!r}"
                    )
                try:
                    current_after_ctest = build_micromachine_build_identity(config)
                except Exception as exc:
                    blockers.append(
                        f"post-CTest build identity reconstruction failed: {exc}"
                    )
                else:
                    post_test_error = micromachine_build_identity_admission_error(
                        current,
                        current_after_ctest,
                    )
                    if post_test_error:
                        blockers.append(
                            f"build changed during CTest execution: {post_test_error}"
                        )
                    current = current_after_ctest
                if recorded is not None:
                    post_recorded_error = micromachine_build_identity_admission_error(
                        recorded,
                        current_after_ctest,
                    )
                    if post_recorded_error:
                        blockers.append(post_recorded_error)

    if report_snapshot is not None:
        try:
            _, report_snapshot_after = _read_regular_file_snapshot(
                path,
                maximum=MAX_BUILD_REPORT_BYTES,
            )
        except OSError as exc:
            blockers.append(f"build report changed or disappeared: {exc}")
        else:
            if report_snapshot_after != report_snapshot:
                blockers.append("build report changed during build/CTest attestation")

    observed = current.get("observed") if isinstance(current, Mapping) else {}
    return _component_result(
        blockers,
        report_path=str(path),
        report_sha256=report_sha256,
        recorded_identity=(
            recorded.get("identity") if isinstance(recorded, Mapping) else None
        ),
        current_identity=(
            current.get("identity") if isinstance(current, Mapping) else None
        ),
        binary_path=(str(config.binary_path.resolve()) if config is not None else None),
        binary_sha256=(
            observed.get("binary_sha256") if isinstance(observed, Mapping) else None
        ),
        embedded_build_input_identity=(
            observed.get("embedded_build_input_identity")
            if isinstance(observed, Mapping)
            else None
        ),
        micromachine_commit=(
            current.get("observed", {}).get("micromachine_commit")
            if isinstance(current, Mapping)
            and isinstance(current.get("observed"), Mapping)
            else None
        ),
        s2client_commit=(
            current.get("observed", {}).get("s2client_commit")
            if isinstance(current, Mapping)
            and isinstance(current.get("observed"), Mapping)
            else None
        ),
        repository_commit=expected_repository_commit,
        repository_inputs=repository_inputs,
        ctest=ctest_result,
    )


def canonical_pre_live_state_dir(
    repository_dir: Path | str,
    *,
    git_runner: CommandRunner = subprocess.run,
) -> Path:
    """Return the repository-owned state directory used for replay and outputs."""

    root = Path(repository_dir).resolve()
    result = _run_text(
        git_runner,
        (TRUSTED_GIT_EXECUTABLE, "rev-parse", "--git-common-dir"),
        cwd=root,
        env=SANITIZED_GIT_ENV,
    )
    if result["returncode"] != 0:
        raise ValueError("could not resolve the repository git common directory")
    git_common = Path(str(result["stdout"]))
    if not git_common.is_absolute():
        git_common = root / git_common
    git_common = git_common.resolve()
    if not git_common.is_dir() or _path_has_symlink_component(git_common):
        raise ValueError("git common directory is missing or linked")
    state_dir = git_common / "voi" / "pre-live"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _path_has_symlink_component(state_dir, stop=git_common):
        raise ValueError("pre-live state directory contains a symlink")
    state_stat = state_dir.stat()
    if state_stat.st_uid != os.getuid():
        raise ValueError("pre-live state directory is not owned by the current user")
    os.chmod(state_dir, 0o700)
    return state_dir


def canonical_global_replay_state_dir(repository_id: int) -> Path:
    """Return the host-global replay directory shared by every local clone."""

    repository_id = _positive_id(repository_id, "repository_id")
    root = GLOBAL_REPLAY_STATE_ROOT.absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.path.lexists(root) and root.is_symlink():
        raise ValueError("global replay state root contains a symlink")
    state_dir = root / f"repository-{repository_id}"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    if _path_has_symlink_component(state_dir, stop=root):
        raise ValueError("global replay state directory contains a symlink")
    for candidate in (root, state_dir):
        file_stat = candidate.stat()
        if not stat.S_ISDIR(file_stat.st_mode):
            raise ValueError("global replay state path is not a directory")
        if file_stat.st_uid != os.getuid():
            raise ValueError("global replay state directory is not user-owned")
        os.chmod(candidate, 0o700)
    return state_dir


def resolve_local_producer_policy(
    *,
    repository_dir: Path | str,
    expected_commit: str,
    producer_id: str,
    git_runner: CommandRunner = subprocess.run,
    python_executable: Path | str = sys.executable,
    micromachine_binary_path: Path | str | None = None,
    micromachine_binary_sha256: str | None = None,
    node_executable: Path | str | None = None,
) -> dict[str, object]:
    """Load one exact producer command from the policy committed at HEAD."""

    blockers: list[str] = []
    root = Path(repository_dir).resolve()
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", producer_id):
        blockers.append("producer_id is invalid")
    if not _SHA40_RE.fullmatch(expected_commit):
        blockers.append("producer policy commit must be an exact lowercase SHA")
    policy_path = root / PRODUCER_POLICY_RELATIVE_PATH
    if _path_has_symlink_component(policy_path, stop=root):
        blockers.append("producer policy path contains a symlink")
    committed_bytes = b""
    if not blockers:
        try:
            completed = git_runner(
                [
                    TRUSTED_GIT_EXECUTABLE,
                    "show",
                    f"{expected_commit}:{PRODUCER_POLICY_RELATIVE_PATH.as_posix()}",
                ],
                cwd=str(root),
                check=False,
                capture_output=True,
                text=False,
                shell=False,
                env=dict(SANITIZED_GIT_ENV),
            )
            if int(completed.returncode) != 0:
                raise ValueError("producer policy is not tracked at the exact commit")
            committed_bytes = _as_bytes(completed.stdout)
            if policy_path.read_bytes() != committed_bytes:
                raise ValueError("producer policy differs from the exact commit")
            payload = json.loads(
                committed_bytes,
                object_pairs_hook=_reject_duplicate_json_object_keys,
                parse_constant=_reject_nonfinite_json,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            blockers.append(f"producer policy could not be authenticated: {exc}")
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, Mapping):
        blockers.append("producer policy must contain a JSON object")
        payload = {}
    schema_version = payload.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != PRODUCER_POLICY_SCHEMA_VERSION
    ):
        blockers.append(
            "producer policy schema mismatch: "
            f"expected={PRODUCER_POLICY_SCHEMA_VERSION} "
            f"actual={schema_version!r}"
        )
    producers = payload.get("producers")
    if not isinstance(producers, Mapping):
        blockers.append("producer policy requires a producers object")
        producers = {}
    producer = producers.get(producer_id)
    if not isinstance(producer, Mapping):
        blockers.append(f"producer policy does not define {producer_id!r}")
        producer = {}
    if set(producer) != {"argv", "cwd", "output"}:
        blockers.append("producer policy entry must contain argv, cwd, and output only")
    raw_argv = producer.get("argv")
    cwd_value = producer.get("cwd")
    output_value = producer.get("output")
    if not isinstance(raw_argv, list):
        blockers.append("producer policy argv must be a list")
        raw_argv = []
    if not isinstance(cwd_value, str):
        blockers.append("producer policy cwd must be a string")
        cwd_value = ""
    if not isinstance(output_value, str):
        blockers.append("producer policy output must be a string")
        output_value = ""
    if producer_id == PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID:
        if raw_argv != list(DETERMINISTIC_JOURNEY_PRODUCER_RAW_ARGV):
            blockers.append(
                "deterministic journey producer argv does not match the "
                "required raw policy"
            )
        if cwd_value != DETERMINISTIC_JOURNEY_PRODUCER_CWD:
            blockers.append(
                "deterministic journey producer cwd does not match the "
                "required raw policy"
            )
        if output_value != DETERMINISTIC_JOURNEY_PRODUCER_OUTPUT:
            blockers.append(
                "deterministic journey producer output does not match the "
                "required raw policy"
            )

    state_dir: Path | None = None
    try:
        state_dir = canonical_pre_live_state_dir(root, git_runner=git_runner)
    except ValueError as exc:
        blockers.append(str(exc))
    cwd_relative = Path(cwd_value)
    output_relative = Path(output_value)
    if (
        cwd_relative.is_absolute()
        or ".." in cwd_relative.parts
        or output_relative.is_absolute()
        or ".." in output_relative.parts
    ):
        blockers.append("producer cwd and output must be safe relative paths")
    cwd = (root / cwd_relative).resolve()
    if not output_relative.name or output_relative.name in {".", ".."}:
        blockers.append("producer output must name a regular file")
    output_parent = (
        (state_dir / output_relative.parent).resolve()
        if state_dir is not None
        else root
    )
    output = (
        output_parent / output_relative.name
        if state_dir is not None
        else root / ".invalid-producer-output"
    )
    try:
        cwd.relative_to(root)
        if state_dir is not None:
            output.relative_to(state_dir)
    except ValueError:
        blockers.append("producer cwd or output escaped its trusted root")
    if not cwd.is_dir() or _path_has_symlink_component(cwd, stop=root):
        blockers.append("producer cwd is missing or linked")
    if state_dir is not None:
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if _path_has_symlink_component(output.parent, stop=state_dir):
            blockers.append("producer output parent contains a symlink")
        if os.path.lexists(output) and output.is_symlink():
            blockers.append("producer output artifact must not be a symlink")

    python_path = Path(python_executable).resolve()
    binary_path: Path | None = None
    node_path: Path | None = None
    node_sha256: str | None = None
    binary_placeholder_count = raw_argv.count("{micromachine_binary}")
    node_placeholder_count = raw_argv.count("{node}")
    if producer_id == PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID:
        if binary_placeholder_count != 1:
            blockers.append(
                "deterministic journey producer argv must contain exactly one "
                "{micromachine_binary} placeholder"
            )
        else:
            placeholder_index = raw_argv.index("{micromachine_binary}")
            if (
                placeholder_index == 0
                or raw_argv[placeholder_index - 1]
                != "--micromachine-binary"
            ):
                blockers.append(
                    "{micromachine_binary} must be the value of "
                    "--micromachine-binary"
                )
        if (
            not isinstance(micromachine_binary_sha256, str)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                micromachine_binary_sha256,
            )
        ):
            blockers.append(
                "deterministic journey producer requires the admitted "
                "MicroMachine binary SHA-256"
            )
        if micromachine_binary_path is None:
            blockers.append(
                "deterministic journey producer requires the admitted "
                "MicroMachine binary path"
            )
        else:
            raw_binary_path = Path(micromachine_binary_path)
            if not raw_binary_path.is_absolute():
                blockers.append(
                    "admitted MicroMachine binary path must be absolute"
                )
            elif _path_has_symlink_component(raw_binary_path):
                blockers.append(
                    "admitted MicroMachine binary path contains a symlink"
                )
            else:
                try:
                    binary_stat = raw_binary_path.stat()
                except OSError as exc:
                    blockers.append(
                        f"admitted MicroMachine binary is unreadable: {exc}"
                    )
                else:
                    if not stat.S_ISREG(binary_stat.st_mode):
                        blockers.append(
                            "admitted MicroMachine binary is not a regular file"
                        )
                    elif binary_stat.st_mode & 0o111 == 0:
                        blockers.append(
                            "admitted MicroMachine binary is not executable"
                        )
                    elif (
                        _sha256_file(raw_binary_path)
                        != micromachine_binary_sha256
                    ):
                        blockers.append(
                            "admitted MicroMachine binary digest mismatch"
                        )
                    else:
                        binary_path = raw_binary_path.resolve()
        if node_placeholder_count != 1:
            blockers.append(
                "deterministic journey producer argv must contain exactly one "
                "{node} placeholder"
            )
        else:
            placeholder_index = raw_argv.index("{node}")
            if (
                placeholder_index == 0
                or raw_argv[placeholder_index - 1]
                != "--node-executable"
            ):
                blockers.append(
                    "{node} must be the value of --node-executable"
                )
        if node_executable is None:
            blockers.append(
                "deterministic journey producer requires an explicitly "
                "admitted Node.js executable"
            )
        else:
            raw_node_path = Path(node_executable)
            if not raw_node_path.is_absolute():
                blockers.append("Node.js executable path must be absolute")
            elif _path_has_symlink_component(raw_node_path):
                blockers.append("Node.js executable path contains a symlink")
            else:
                try:
                    node_stat = raw_node_path.stat()
                except OSError as exc:
                    blockers.append(
                        f"Node.js executable is unreadable: {exc}"
                    )
                else:
                    if not stat.S_ISREG(node_stat.st_mode):
                        blockers.append(
                            "Node.js executable is not a regular file"
                        )
                    elif node_stat.st_mode & 0o111 == 0:
                        blockers.append("Node.js executable is not executable")
                    else:
                        node_path = raw_node_path.resolve()
                        node_sha256 = _sha256_file(node_path)
    elif binary_placeholder_count:
        blockers.append(
            "{micromachine_binary} is reserved for deterministic journeys"
        )
    elif node_placeholder_count:
        blockers.append("{node} is reserved for deterministic journeys")
    replacements = {
        "{python}": str(python_path),
        "{repository}": str(root),
        "{state_dir}": str(state_dir) if state_dir is not None else "",
        "{output}": str(output),
        "{micromachine_binary}": (
            str(binary_path) if binary_path is not None else ""
        ),
        "{node}": str(node_path) if node_path is not None else "",
    }
    argv: list[str] = []
    for value in raw_argv:
        if not isinstance(value, str) or not value or "\x00" in value:
            blockers.append("producer policy argv contains an invalid value")
            continue
        resolved = replacements.get(value, value)
        if "{" in resolved or "}" in resolved:
            blockers.append(f"producer policy contains an unknown placeholder: {value}")
            continue
        argv.append(resolved)
    try:
        normalized_argv = _normalize_argv(argv)
    except (TypeError, ValueError) as exc:
        blockers.append(str(exc))
        normalized_argv = ()

    module_evidence: dict[str, object] | None = None
    runtime_sources: dict[str, object] = _component_result(
        ["producer Python source manifest was not resolved"],
        files=[],
        digest=None,
    )
    isolated_prefix = (
        str(python_path),
        "-I",
        "-B",
        "-S",
        "-c",
        ISOLATED_PYTHON_BOOTSTRAP,
        str(root),
    )
    if (
        len(normalized_argv) < len(isolated_prefix) + 1
        or normalized_argv[: len(isolated_prefix)] != isolated_prefix
    ):
        blockers.append(
            "producer policy must use the authenticated isolated Python launcher"
        )
    else:
        module_value = normalized_argv[len(isolated_prefix)]
        module_relative = Path(module_value)
        if (
            module_relative.is_absolute()
            or ".." in module_relative.parts
            or module_relative.suffix != ".py"
            or not module_relative.parts
        ):
            blockers.append("producer Python script path is invalid")
        else:
            module_path = root / module_relative
            module_evidence = _attest_committed_file(
                module_path,
                repository_root=root,
                expected_commit=expected_commit,
                git_runner=git_runner,
            )
            blockers.extend(_prefixed_blockers("producer module", module_evidence))
            runtime_sources = _attest_committed_python_sources(
                module_relative,
                repository_root=root,
                expected_commit=expected_commit,
                git_runner=git_runner,
            )
            blockers.extend(
                _prefixed_blockers("producer runtime sources", runtime_sources)
            )

    policy_digest = hashlib.sha256(committed_bytes).hexdigest()
    argv_digest = hashlib.sha256(_canonical_json(list(normalized_argv))).hexdigest()
    return _component_result(
        blockers,
        producer_id=producer_id,
        policy_path=PRODUCER_POLICY_RELATIVE_PATH.as_posix(),
        policy_sha256=policy_digest,
        repository_commit=expected_commit,
        argv=list(normalized_argv),
        argv_sha256=argv_digest,
        cwd=str(cwd),
        output_artifact=str(output),
        micromachine_binary_path=(
            str(binary_path) if binary_path is not None else None
        ),
        micromachine_binary_sha256=(
            micromachine_binary_sha256 if binary_path is not None else None
        ),
        node_executable_path=(
            str(node_path) if node_path is not None else None
        ),
        node_executable_sha256=node_sha256,
        module=module_evidence,
        runtime_sources=runtime_sources,
    )


def run_local_producer(
    *,
    repository_dir: Path | str,
    cwd: Path | str,
    argv: Sequence[str],
    allowed_argv: Iterable[Sequence[str]],
    output_artifact: Path | str,
    command_runner: CommandRunner = subprocess.run,
    git_runner: CommandRunner = subprocess.run,
    timeout_seconds: float = 3600.0,
    producer_id: str | None = None,
    producer_policy_sha256: str | None = None,
    authenticated_files: Sequence[Path | str] = (),
    authenticated_file_digests: Mapping[str, str] | None = None,
    pinned_argv_file_digests: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Run one exact allowlisted producer and derive provenance from execution."""

    blockers: list[str] = []
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    root = Path(repository_dir).resolve()
    working_dir = Path(cwd).resolve()
    raw_output_path = Path(output_artifact)
    if not raw_output_path.is_absolute():
        raw_output_path = working_dir / raw_output_path
    output_parent = raw_output_path.parent.resolve()
    output_path = output_parent / raw_output_path.name
    try:
        normalized_argv = _normalize_argv(argv)
        allowed = {_normalize_argv(candidate) for candidate in allowed_argv}
    except (TypeError, ValueError) as exc:
        normalized_argv = ()
        allowed = set()
        blockers.append(f"invalid producer allowlist configuration: {exc}")
    if normalized_argv not in allowed:
        blockers.append("producer argv is not exactly allowlisted")
    executable: Path | None = None
    executable_fd: int | None = None
    executable_payload: bytes | None = None
    executable_snapshot_before: tuple[int, int, int, int, str] | None = None
    if not normalized_argv:
        blockers.append("producer argv is empty")
    else:
        executable = Path(normalized_argv[0])
        if not executable.is_absolute():
            blockers.append("producer executable must use an absolute path")
        else:
            try:
                (
                    executable_fd,
                    executable_payload,
                    executable_snapshot_before,
                ) = _open_pinned_executable(executable)
            except OSError as exc:
                blockers.append(
                    "producer executable is missing, linked, non-regular, "
                    f"non-executable, or unreadable: {exc}"
                )
    if not working_dir.is_dir():
        blockers.append(f"producer cwd is missing: {working_dir}")
    try:
        working_dir.relative_to(root)
    except ValueError:
        blockers.append("producer cwd must be inside the attested repository")
    if _path_has_symlink_component(working_dir, stop=root):
        blockers.append("producer cwd contains a symlink")
    try:
        output_path.relative_to(working_dir)
    except ValueError:
        blockers.append("producer output artifact must be inside cwd")
    if _path_has_symlink_component(output_path.parent, stop=working_dir):
        blockers.append("producer output parent contains a symlink")
    if os.path.lexists(output_path) and output_path.is_symlink():
        blockers.append("producer output artifact must not be a symlink")
    if timeout_seconds <= 0:
        blockers.append("producer timeout_seconds must be positive")

    expected_file_digests = dict(authenticated_file_digests or {})
    authenticated_snapshots: dict[
        str,
        tuple[tuple[int, int, int, int, str], bytes, str],
    ] = {}
    for candidate in authenticated_files:
        raw_source_path = Path(candidate).absolute()
        if _path_has_symlink_component(raw_source_path, stop=root):
            blockers.append(
                f"authenticated producer file contains a symlink: {raw_source_path}"
            )
            continue
        source_path = raw_source_path.resolve()
        try:
            relative = source_path.relative_to(root)
        except ValueError:
            blockers.append("authenticated producer file escapes the repository")
            continue
        try:
            payload, snapshot = _read_regular_file_snapshot(
                source_path,
                maximum=MAX_PRODUCER_SOURCE_BYTES,
            )
        except OSError as exc:
            blockers.append(
                f"authenticated producer file is unreadable: {relative}: {exc}"
            )
            continue
        expected_digest = expected_file_digests.get(str(source_path))
        if expected_digest is not None and snapshot[4] != expected_digest:
            blockers.append(
                "authenticated producer file differs from its committed digest: "
                f"{relative}"
            )
            continue
        authenticated_snapshots[str(source_path)] = (
            snapshot,
            payload,
            relative.as_posix(),
        )

    pinned_argv_snapshots: dict[
        str,
        tuple[int, bytes, tuple[int, int, int, int, str]],
    ] = {}
    for raw_candidate, expected_digest in sorted(
        (pinned_argv_file_digests or {}).items()
    ):
        candidate = Path(raw_candidate).absolute()
        candidate_text = str(candidate)
        if normalized_argv.count(candidate_text) != 1:
            blockers.append(
                "pinned argv file must occur exactly once in producer argv: "
                f"{candidate}"
            )
            continue
        if not _SHA256_RE.fullmatch(expected_digest):
            blockers.append(
                f"pinned argv file digest is invalid: {candidate}"
            )
            continue
        if _path_has_symlink_component(candidate):
            blockers.append(f"pinned argv file contains a symlink: {candidate}")
            continue
        try:
            descriptor, payload, snapshot = _open_pinned_executable(candidate)
        except OSError as exc:
            blockers.append(f"pinned argv file is unreadable: {candidate}: {exc}")
            continue
        if snapshot[4] != expected_digest:
            os.close(descriptor)
            blockers.append(f"pinned argv file digest mismatch: {candidate}")
            continue
        pinned_argv_snapshots[candidate_text] = (
            descriptor,
            payload,
            snapshot,
        )

    commit_before = _git_head(root, git_runner)
    if commit_before is None:
        blockers.append("could not record producer repository commit")
    before_state = _artifact_state(output_path)
    output_parent_stat_before = _stable_directory_identity(output_path.parent)
    returncode: int | None = None
    stdout = b""
    stderr = b""
    captured_output: bytes | None = None
    published_output_identity: tuple[int, int, int, int, str] | None = None
    if not blockers:
        if authenticated_snapshots or pinned_argv_snapshots:
            try:
                state_dir = canonical_pre_live_state_dir(
                    root,
                    git_runner=git_runner,
                )
                with (
                    tempfile.TemporaryDirectory(
                        prefix=".producer-snapshot-",
                        dir=state_dir,
                    ) as snapshot_directory,
                    tempfile.TemporaryDirectory(
                        prefix=".producer-output-",
                        dir=state_dir,
                    ) as staging_directory,
                ):
                    snapshot_root = Path(snapshot_directory)
                    staging_root = Path(staging_directory)
                    for _, payload, relative in authenticated_snapshots.values():
                        snapshot_file = snapshot_root / relative
                        snapshot_file.parent.mkdir(
                            mode=0o700,
                            parents=True,
                            exist_ok=True,
                        )
                        _write_private_snapshot_file(snapshot_file, payload)
                    pinned_argv_paths: dict[str, str] = {}
                    pinned_execution_snapshots: dict[
                        str,
                        tuple[
                            int,
                            tuple[int, int, int, int, str],
                        ],
                    ] = {}
                    try:
                        if pinned_argv_snapshots:
                            pinned_argv_root = snapshot_root / ".pinned-argv"
                            pinned_argv_root.mkdir(mode=0o700)
                            for index, (
                                source,
                                (_, payload, source_snapshot),
                            ) in enumerate(
                                sorted(pinned_argv_snapshots.items())
                            ):
                                snapshot_file = (
                                    pinned_argv_root
                                    / f"{index:04d}-{Path(source).name}"
                                )
                                _write_private_executable_file(
                                    snapshot_file,
                                    payload,
                                )
                                (
                                    snapshot_descriptor,
                                    snapshot_payload,
                                    execution_snapshot,
                                ) = _open_pinned_executable(snapshot_file)
                                if (
                                    snapshot_payload != payload
                                    or execution_snapshot[4]
                                    != source_snapshot[4]
                                ):
                                    os.close(snapshot_descriptor)
                                    raise OSError(
                                        "private pinned argv snapshot differs "
                                        f"from admitted bytes: {source}"
                                    )
                                snapshot_file.unlink()
                                execution_path = _descriptor_execution_path(
                                    snapshot_descriptor
                                )
                                pinned_execution_snapshots[source] = (
                                    snapshot_descriptor,
                                    execution_snapshot,
                                )
                                pinned_argv_paths[source] = execution_path
                        relative_cwd = working_dir.relative_to(root)
                        execution_cwd = snapshot_root / relative_cwd
                        execution_cwd.mkdir(
                            mode=0o700,
                            parents=True,
                            exist_ok=True,
                        )
                        staged_output = staging_root / output_path.name
                        execution_replacements = {
                            str(root): str(snapshot_root),
                            str(output_path): str(staged_output),
                            **pinned_argv_paths,
                        }
                        execution_argv = [
                            execution_replacements.get(value, value)
                            for value in normalized_argv
                        ]
                        completed = _run_pinned_command(
                            command_runner,
                            execution_argv,
                            executable_payload=executable_payload,
                            executable_snapshot=executable_snapshot_before,
                            authenticated_python_sources={
                                authenticated[2]: authenticated[1]
                                for authenticated in (
                                    authenticated_snapshots.values()
                                )
                            },
                            state_dir=state_dir,
                            cwd=str(execution_cwd),
                            timeout=timeout_seconds,
                            inherited_fds=tuple(
                                descriptor
                                for descriptor, _ in (
                                    pinned_execution_snapshots.values()
                                )
                            ),
                        )
                        for source, (
                            snapshot_descriptor,
                            snapshot_before,
                        ) in pinned_execution_snapshots.items():
                            _, descriptor_snapshot_after = (
                                _read_open_regular_file_snapshot(
                                    snapshot_descriptor,
                                    maximum=MAX_PRODUCER_EXECUTABLE_BYTES,
                                )
                            )
                            if descriptor_snapshot_after != snapshot_before:
                                raise OSError(
                                    "private pinned argv snapshot changed "
                                    f"during execution: {source}"
                                )
                        returncode = int(completed.returncode)
                        stdout = _as_bytes(completed.stdout)
                        stderr = _as_bytes(completed.stderr)
                        if returncode == 0:
                            captured_output, _ = _read_regular_file_snapshot(
                                staged_output,
                                maximum=MAX_GITHUB_ARTIFACT_BYTES,
                            )
                            published_output_identity = _write_output_atomically(
                                output_path,
                                captured_output,
                                expected_parent_identity=(
                                    output_parent_stat_before
                                ),
                            )
                    finally:
                        for (
                            snapshot_descriptor,
                            _,
                        ) in pinned_execution_snapshots.values():
                            os.close(snapshot_descriptor)
            except Exception as exc:
                blockers.append(f"producer execution failed: {exc}")
        else:
            try:
                state_dir = canonical_pre_live_state_dir(
                    root,
                    git_runner=git_runner,
                )
                completed = _run_pinned_command(
                    command_runner,
                    list(normalized_argv),
                    executable_payload=executable_payload,
                    executable_snapshot=executable_snapshot_before,
                    authenticated_python_sources={},
                    state_dir=state_dir,
                    cwd=str(working_dir),
                    timeout=timeout_seconds,
                )
                returncode = int(completed.returncode)
                stdout = _as_bytes(completed.stdout)
                stderr = _as_bytes(completed.stderr)
                if returncode == 0:
                    captured_output, published_output_identity = (
                        _read_regular_file_snapshot(
                            output_path,
                            maximum=MAX_GITHUB_ARTIFACT_BYTES,
                        )
                    )
            except Exception as exc:
                blockers.append(f"producer execution failed: {exc}")
    if returncode is not None and returncode != 0:
        blockers.append(f"producer exited with code {returncode}")

    output_parent_stat_after = _stable_directory_identity(output_path.parent)
    if (
        output_parent_stat_before is None
        or output_parent_stat_after != output_parent_stat_before
    ):
        blockers.append("producer output parent changed during execution")
    if os.path.lexists(output_path) and output_path.is_symlink():
        blockers.append("producer output artifact became a symlink")
    after_state = _artifact_state(output_path)
    if after_state is None:
        blockers.append("producer did not create a regular output artifact")
    elif (
        before_state is not None
        and after_state["stat_identity"] == before_state["stat_identity"]
    ):
        blockers.append("producer output artifact was not refreshed")
    if captured_output is not None:
        captured_output_sha256 = hashlib.sha256(captured_output).hexdigest()
        if (
            after_state is None
            or after_state["sha256"] != captured_output_sha256
            or after_state["size_bytes"] != len(captured_output)
        ):
            blockers.append("producer output artifact changed after publication")
        elif (
            published_output_identity is not None
            and after_state["stat_identity"] != published_output_identity
        ):
            blockers.append(
                "producer output artifact identity changed after publication"
            )
    else:
        captured_output_sha256 = None

    commit_after = _git_head(root, git_runner)
    if commit_after is None:
        blockers.append("could not re-read producer repository commit")
    elif commit_before is not None and commit_after != commit_before:
        blockers.append(
            "repository commit changed during producer execution: "
            f"before={commit_before} after={commit_after}"
        )
    executable_snapshot_after: tuple[int, int, int, int, str] | None = None
    if executable_fd is not None:
        try:
            _, executable_snapshot_after = _read_open_regular_file_snapshot(
                executable_fd,
                maximum=MAX_PRODUCER_EXECUTABLE_BYTES,
            )
        except OSError as exc:
            blockers.append(f"pinned producer executable changed: {exc}")
        finally:
            os.close(executable_fd)
            executable_fd = None
    if (
        executable_snapshot_before is not None
        and executable_snapshot_after != executable_snapshot_before
    ):
        blockers.append("pinned producer executable changed during execution")
    executable_path_sha256_after = (
        _sha256_file(executable) if executable is not None else None
    )
    if (
        executable_snapshot_before is not None
        and executable_path_sha256_after != executable_snapshot_before[4]
    ):
        blockers.append("producer executable pathname changed during execution")
    for source, authenticated in authenticated_snapshots.items():
        snapshot_before = authenticated[0]
        try:
            _, snapshot_after = _read_regular_file_snapshot(
                Path(source),
                maximum=MAX_PRODUCER_SOURCE_BYTES,
            )
        except OSError as exc:
            blockers.append(f"authenticated producer file changed: {source}: {exc}")
            continue
        if snapshot_after != snapshot_before:
            blockers.append(f"authenticated producer file changed: {source}")
    for source, pinned in pinned_argv_snapshots.items():
        descriptor, _, snapshot_before = pinned
        try:
            _, descriptor_snapshot_after = _read_open_regular_file_snapshot(
                descriptor,
                maximum=MAX_PRODUCER_EXECUTABLE_BYTES,
            )
        except OSError as exc:
            blockers.append(f"pinned argv file changed: {source}: {exc}")
        else:
            if descriptor_snapshot_after != snapshot_before:
                blockers.append(f"pinned argv file changed: {source}")
        finally:
            os.close(descriptor)
        try:
            _, pathname_snapshot_after = _read_regular_file_snapshot(
                Path(source),
                maximum=MAX_PRODUCER_EXECUTABLE_BYTES,
            )
        except OSError as exc:
            blockers.append(f"pinned argv file pathname changed: {source}: {exc}")
        else:
            if pathname_snapshot_after != snapshot_before:
                blockers.append(f"pinned argv file pathname changed: {source}")
    started_timestamp = _parse_utc(started_at)
    if started_timestamp is None:
        raise RuntimeError("internal producer start timestamp is invalid")
    elapsed_seconds = max(0.0, time.monotonic() - started_monotonic)
    ended_at = _format_utc(
        started_timestamp + timedelta(seconds=elapsed_seconds)
    )
    return _component_result(
        blockers,
        producer=(Path(normalized_argv[0]).name if normalized_argv else None),
        producer_id=producer_id,
        producer_policy_sha256=producer_policy_sha256,
        started_at=started_at,
        ended_at=ended_at,
        cwd=str(working_dir),
        repository_commit=commit_before,
        repository_commit_after=commit_after,
        argv=list(normalized_argv),
        argv_sha256=hashlib.sha256(_canonical_json(list(normalized_argv))).hexdigest(),
        executable_sha256=(
            executable_snapshot_before[4]
            if executable_snapshot_before is not None
            else None
        ),
        executable_sha256_after=(
            executable_snapshot_after[4]
            if executable_snapshot_after is not None
            else None
        ),
        executable_path_sha256_after=executable_path_sha256_after,
        authenticated_files={
            source: {
                "sha256": authenticated[0][4],
                "size_bytes": authenticated[0][2],
                "relative_path": authenticated[2],
            }
            for source, authenticated in sorted(authenticated_snapshots.items())
        },
        exit_code=returncode,
        stdout_sha256=hashlib.sha256(stdout).hexdigest(),
        stdout_size_bytes=len(stdout),
        stderr_sha256=hashlib.sha256(stderr).hexdigest(),
        stderr_size_bytes=len(stderr),
        output_artifact=(
            {
                "path": str(output_path),
                "sha256": captured_output_sha256,
                "size_bytes": len(captured_output),
                "published_stat_identity": list(published_output_identity),
            }
            if captured_output is not None and published_output_identity is not None
            else {
                "path": str(output_path),
                "sha256": None,
                "size_bytes": None,
                "published_stat_identity": None,
            }
        ),
    )


def attest_github_actions_emission_context(
    adapter: GitHubSourceAdapter,
    *,
    repository: str,
    run_id: int,
    run_attempt: int,
    expected_head_sha: str,
    workflow_ref: str,
    workflow_sha: str,
    job_name: str = AUTHORITATIVE_PROVENANCE_JOB_NAME,
) -> dict[str, object]:
    """Resolve immutable workflow and current-job IDs before artifact upload."""

    blockers: list[str] = []
    try:
        normalized_repository = normalize_github_repository(
            repository,
            allow_slug=True,
        )
        run_id = _positive_id(run_id, "run_id")
        run_attempt = _positive_id(run_attempt, "run_attempt")
        if not _SHA40_RE.fullmatch(expected_head_sha):
            raise ValueError(
                "expected_head_sha must be an exact lowercase 40-character SHA"
            )
        if not isinstance(workflow_ref, str) or len(workflow_ref) > 1024:
            raise ValueError("workflow_ref must be a bounded workflow reference")
        if not isinstance(workflow_sha, str) or not _SHA40_RE.fullmatch(workflow_sha):
            raise ValueError("workflow_sha must be an exact lowercase SHA")
        if job_name != AUTHORITATIVE_PROVENANCE_JOB_NAME:
            raise ValueError("job_name must identify the authoritative provenance job")
        repository_record = adapter.get_repository(normalized_repository)
        run = adapter.get_workflow_run(normalized_repository, run_id)
        workflow_id = _positive_id(
            run.get("workflow_id"),
            "workflow_run.workflow_id",
        )
        workflow = adapter.get_workflow(normalized_repository, workflow_id)
        run_pull_requests = run.get("pull_requests")
        if not isinstance(run_pull_requests, list) or len(run_pull_requests) != 1:
            raise ValueError(
                "workflow run must contain exactly one pull-request binding"
            )
        pull_summary = run_pull_requests[0]
        if not isinstance(pull_summary, Mapping):
            raise ValueError("workflow run pull-request binding must be an object")
        pull_number = _positive_id(
            pull_summary.get("number"),
            "workflow_run.pull_requests[0].number",
        )
        pull_request = adapter.get_pull_request(
            normalized_repository,
            pull_number,
        )
        pull_base = _mapping(pull_request.get("base"))
        pull_base_sha = pull_base.get("sha")
        if not isinstance(pull_base_sha, str) or not _SHA40_RE.fullmatch(
            pull_base_sha
        ):
            raise ValueError("pull_request.base.sha must be an exact lowercase SHA")
        closing_issues = adapter.list_pull_request_closing_issues(
            normalized_repository,
            pull_number,
        )
        comparison = adapter.compare_commits(
            normalized_repository,
            base=pull_base_sha,
            head=expected_head_sha,
        )
        workflow_runs = adapter.list_workflow_runs(
            normalized_repository,
            workflow_id,
            branch=str(run.get("head_branch")),
            event="pull_request",
        )
        jobs = adapter.list_workflow_run_attempt_jobs(
            normalized_repository,
            run_id,
            run_attempt,
        )
    except Exception as exc:
        return _component_result(
            [f"GitHub Actions emission context failed: {exc}"],
            repository=repository,
            repository_id=None,
            workflow_id=None,
            workflow_path=None,
            workflow_ref=workflow_ref,
            workflow_sha=workflow_sha,
            run_id=run_id,
            run_attempt=run_attempt,
            job_id=None,
            job_name=job_name,
            head_sha=expected_head_sha,
            authority=None,
        )

    repository_id = _server_positive_id(
        repository_record.get("id"),
        "repository.id",
        blockers,
    )
    if repository_id != AUTHORITATIVE_REPOSITORY_ID:
        blockers.append(
            "GitHub repository database ID mismatch: "
            f"expected={AUTHORITATIVE_REPOSITORY_ID} actual={repository_id}"
        )
    repository_name = repository_record.get("full_name")
    if (
        not isinstance(repository_name, str)
        or repository_name.casefold() != normalized_repository.casefold()
    ):
        blockers.append(
            "GitHub repository name mismatch: "
            f"expected={normalized_repository} actual={repository_name!r}"
        )
    _expect_server_value(run, "id", run_id, "workflow_run", blockers)
    _expect_server_value(
        run,
        "run_attempt",
        run_attempt,
        "workflow_run",
        blockers,
    )
    if run.get("head_sha") != expected_head_sha:
        blockers.append(
            "workflow run head SHA mismatch: "
            f"expected={expected_head_sha} actual={run.get('head_sha')!r}"
        )
    event = _server_string(run, "event", "workflow_run", blockers)
    if event != "pull_request":
        blockers.append(
            "candidate evidence requires a pull_request workflow event: "
            f"actual={event!r}"
        )
    run_repository = _mapping(run.get("head_repository"))
    _expect_server_value(
        run_repository,
        "id",
        AUTHORITATIVE_REPOSITORY_ID,
        "workflow_run.head_repository",
        blockers,
    )
    workflow_path = _server_string(
        run,
        "path",
        "workflow_run",
        blockers,
    )
    if workflow_path != AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH:
        blockers.append(
            "workflow path mismatch: "
            f"expected={AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH} "
            f"actual={workflow_path!r}"
        )
    _expect_server_value(workflow, "id", workflow_id, "workflow", blockers)
    _expect_server_value(
        workflow,
        "path",
        AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH,
        "workflow",
        blockers,
    )
    if workflow.get("state") != "active":
        blockers.append(f"workflow is not active: state={workflow.get('state')!r}")
    head_branch = _server_string(run, "head_branch", "workflow_run", blockers)
    pull_id = _server_positive_id(
        pull_request.get("id"),
        "pull_request.id",
        blockers,
    )
    _expect_server_value(
        pull_request,
        "number",
        pull_number,
        "pull_request",
        blockers,
    )
    _expect_server_value(
        pull_request,
        "state",
        "open",
        "pull_request",
        blockers,
    )
    if pull_request.get("merged_at") is not None:
        blockers.append("candidate pull request is already merged")
    closing_issue_id, closing_issue_number = _single_repository_closing_issue(
        closing_issues,
        repository=normalized_repository,
        repository_id=AUTHORITATIVE_REPOSITORY_ID,
        blockers=blockers,
    )
    closing_issue_state: str | None = None
    if closing_issue_number is not None:
        try:
            closing_issue = adapter.get_issue(
                normalized_repository,
                closing_issue_number,
            )
        except Exception as exc:
            blockers.append(f"GitHub closing issue lookup failed: {exc}")
        else:
            _expect_server_value(
                closing_issue,
                "id",
                closing_issue_id,
                "closing_issue",
                blockers,
            )
            _expect_server_value(
                closing_issue,
                "number",
                closing_issue_number,
                "closing_issue",
                blockers,
            )
            closing_issue_state = _server_string(
                closing_issue,
                "state",
                "closing_issue",
                blockers,
            )
            if closing_issue_state != "open":
                blockers.append(
                    "closing issue state mismatch: "
                    f"expected='open' actual={closing_issue_state!r}"
                )
    pull_head = _mapping(pull_request.get("head"))
    _expect_server_value(
        pull_head,
        "sha",
        expected_head_sha,
        "pull_request.head",
        blockers,
    )
    pull_head_ref = _server_string(
        pull_head,
        "ref",
        "pull_request.head",
        blockers,
    )
    pull_head_repository = _mapping(pull_head.get("repo"))
    pull_head_repository_id = _server_positive_id(
        pull_head_repository.get("id"),
        "pull_request.head.repo.id",
        blockers,
    )
    if pull_head_repository_id != AUTHORITATIVE_REPOSITORY_ID:
        blockers.append(
            "pull-request head repository ID mismatch: "
            f"expected={AUTHORITATIVE_REPOSITORY_ID} "
            f"actual={pull_head_repository_id}"
        )
    pull_base = _mapping(pull_request.get("base"))
    pull_base_ref = _server_string(
        pull_base,
        "ref",
        "pull_request.base",
        blockers,
    )
    pull_base_sha = _server_string(
        pull_base,
        "sha",
        "pull_request.base",
        blockers,
    )
    if pull_base_ref != AUTHORITATIVE_BASE_BRANCH:
        blockers.append(
            "pull request base branch mismatch: "
            f"expected={AUTHORITATIVE_BASE_BRANCH!r} actual={pull_base_ref!r}"
        )
    pull_base_repository = _mapping(pull_base.get("repo"))
    _expect_server_value(
        pull_base_repository,
        "id",
        AUTHORITATIVE_REPOSITORY_ID,
        "pull_request.base.repo",
        blockers,
    )
    _expect_server_value(
        pull_base_repository,
        "full_name",
        normalized_repository,
        "pull_request.base.repo",
        blockers,
    )
    _validate_comparison_ancestry(
        comparison,
        base_sha=pull_base_sha,
        head_sha=expected_head_sha,
        blockers=blockers,
    )
    _validate_workflow_pull_request_binding(
        run_pull_requests,
        label="workflow_run.pull_requests",
        pull_id=pull_id,
        pull_number=pull_number,
        expected_head_sha=expected_head_sha,
        expected_repository_id=AUTHORITATIVE_REPOSITORY_ID,
        blockers=blockers,
    )
    if head_branch != pull_head_ref:
        blockers.append(
            "workflow run branch differs from pull-request head ref: "
            f"run={head_branch!r} pull={pull_head_ref!r}"
        )
    workflow_git_ref = _validate_workflow_execution_identity(
        repository=normalized_repository,
        workflow_path=workflow_path,
        pull_number=pull_number,
        pull_head_ref=pull_head_ref,
        workflow_ref=workflow_ref,
        workflow_sha=workflow_sha,
        blockers=blockers,
    )
    _validate_workflow_reference_target(
        adapter,
        repository=normalized_repository,
        workflow_git_ref=workflow_git_ref,
        workflow_sha=workflow_sha,
        blockers=blockers,
    )
    eligible_runs = _eligible_workflow_runs(
        adapter,
        workflow_runs,
        repository=normalized_repository,
        current_run=run,
        workflow_id=workflow_id,
        workflow_path=AUTHORITATIVE_PROVENANCE_WORKFLOW_PATH,
        pull_id=pull_id,
        pull_number=pull_number,
        head_sha=expected_head_sha,
        head_branch=pull_head_ref,
        repository_id=AUTHORITATIVE_REPOSITORY_ID,
        blockers=blockers,
    )
    if not eligible_runs:
        blockers.append("no applicable workflow run exists for the candidate head")
    else:
        newest_run = max(eligible_runs, key=_workflow_run_order_key)
        if newest_run.get("id") != run_id:
            blockers.append(
                "current workflow run is not the latest applicable run: "
                f"current={run_id} latest={newest_run.get('id')!r}"
            )
        elif newest_run.get("run_attempt") != run_attempt:
            blockers.append(
                "current workflow attempt is not the latest applicable attempt"
            )

    matching_jobs = [
        candidate
        for candidate in jobs
        if isinstance(candidate, Mapping) and candidate.get("name") == job_name
    ]
    selected_job: Mapping[str, object] = {}
    job_id: int | None = None
    if len(matching_jobs) != 1:
        blockers.append(
            "workflow attempt must contain exactly one named provenance job: "
            f"actual={len(matching_jobs)}"
        )
    else:
        selected_job = matching_jobs[0]
        job_id = _server_positive_id(
            selected_job.get("id"),
            "job.id",
            blockers,
        )
        if job_id is not None:
            try:
                selected_job = adapter.get_job(
                    normalized_repository,
                    job_id,
                )
            except Exception as exc:
                blockers.append(f"GitHub workflow job lookup failed: {exc}")
                selected_job = {}
    if job_id is not None:
        _expect_server_value(selected_job, "id", job_id, "job", blockers)
    _expect_server_value(selected_job, "run_id", run_id, "job", blockers)
    _expect_server_value(
        selected_job,
        "run_attempt",
        run_attempt,
        "job",
        blockers,
    )
    _expect_server_value(selected_job, "name", job_name, "job", blockers)
    if selected_job.get("head_sha") != expected_head_sha:
        blockers.append(
            "workflow job head SHA mismatch: "
            f"expected={expected_head_sha} actual={selected_job.get('head_sha')!r}"
        )
    job_status = selected_job.get("status")
    job_conclusion = selected_job.get("conclusion")
    if job_status not in {"in_progress", "completed"}:
        blockers.append(f"workflow job is not running or completed: {job_status!r}")
    if job_status == "completed" and job_conclusion != "success":
        blockers.append(
            f"completed workflow job is not successful: conclusion={job_conclusion!r}"
        )
    if job_status == "in_progress" and job_conclusion is not None:
        blockers.append(
            "in-progress workflow job unexpectedly has a conclusion: "
            f"{job_conclusion!r}"
        )

    authority = {
        "scope": PRE_LIVE_CANDIDATE_AUTHORITY_SCOPE,
        "release_authoritative": False,
        "event": "pull_request",
        "pull_request": {
            "database_id": pull_id,
            "number": pull_number,
            "head_sha": expected_head_sha,
            "head_ref": pull_head_ref,
            "head_repository_id": pull_head_repository_id,
        },
        "closing_issue": {
            "repository_full_name": normalized_repository,
            "repository_database_id": repository_id,
            "database_id": closing_issue_id,
            "number": closing_issue_number,
        },
    }
    return _component_result(
        blockers,
        repository=normalized_repository,
        repository_id=repository_id,
        workflow_id=workflow_id,
        workflow_path=workflow_path,
        workflow_ref=workflow_ref,
        workflow_sha=workflow_sha,
        run_id=run_id,
        run_attempt=run_attempt,
        job_id=job_id,
        job_name=job_name,
        job_status=job_status,
        head_sha=expected_head_sha,
        closing_issue_id=closing_issue_id,
        closing_issue_number=closing_issue_number,
        closing_issue_state=closing_issue_state,
        authority=authority,
    )


def emit_github_actions_pre_live_bundle(
    *,
    adapter: GitHubSourceAdapter,
    repository_dir: Path | str,
    expected_commit: str,
    run_id: int,
    run_attempt: int,
    workflow_ref: str,
    workflow_sha: str,
    build_report_path: Path | str,
    expected_build_dir: Path | str,
    output_path: Path | str,
    producer_id: str = PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID,
    node_executable: Path | str | None = None,
    git_runner: CommandRunner = subprocess.run,
    ctest_runner: CommandRunner = subprocess.run,
    producer_runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Create the exact canonical bundle uploaded by the provenance CI job."""

    blockers: list[str] = []
    repository_root = Path(repository_dir).resolve()
    raw_output = Path(output_path).absolute()
    output = raw_output.parent.resolve() / raw_output.name
    try:
        output.relative_to(repository_root)
    except ValueError:
        pass
    else:
        blockers.append("pre-live bundle output must be outside the repository")
    if not raw_output.parent.is_dir():
        blockers.append("pre-live bundle output parent is missing")
    elif _path_has_symlink_component(output.parent):
        blockers.append("pre-live bundle output parent contains a symlink")
    if os.path.lexists(raw_output) and raw_output.is_symlink():
        blockers.append("pre-live bundle output must not be a symlink")

    repository_before = attest_repository(
        repository_root,
        expected_repository=AUTHORITATIVE_REPOSITORY,
        expected_commit=expected_commit,
        command_runner=git_runner,
    )
    source_context = attest_github_actions_emission_context(
        adapter,
        repository=AUTHORITATIVE_REPOSITORY,
        run_id=run_id,
        run_attempt=run_attempt,
        expected_head_sha=expected_commit,
        workflow_ref=workflow_ref,
        workflow_sha=workflow_sha,
    )
    build_binding = attest_build_binding(
        build_report_path,
        repository_dir=repository_root,
        expected_repository_commit=expected_commit,
        expected_build_dir=expected_build_dir,
        command_runner=ctest_runner,
        git_runner=git_runner,
    )
    blockers.extend(_prefixed_blockers("repository", repository_before))
    blockers.extend(_prefixed_blockers("github_context", source_context))
    blockers.extend(_prefixed_blockers("build", build_binding))
    admitted_build = _capture_admitted_build_snapshots(build_binding)
    blockers.extend(_prefixed_blockers("admitted_build", admitted_build))

    producer_policy: dict[str, object]
    local_execution: dict[str, object]
    repository_after: dict[str, object]
    if blockers:
        producer_policy = _component_result(
            ["producer policy was not loaded because prerequisites failed"],
            producer_id=producer_id,
        )
        local_execution = _component_result(
            ["producer was not executed because prerequisites failed"],
        )
        repository_after = repository_before
    else:
        producer_policy = resolve_local_producer_policy(
            repository_dir=repository_root,
            expected_commit=expected_commit,
            producer_id=producer_id,
            git_runner=git_runner,
            micromachine_binary_path=build_binding.get("binary_path"),
            micromachine_binary_sha256=build_binding.get("binary_sha256"),
            node_executable=node_executable,
        )
        blockers.extend(_prefixed_blockers("producer_policy", producer_policy))
        if blockers:
            local_execution = _component_result(
                ["producer was not executed because its policy was rejected"],
            )
            repository_after = repository_before
        else:
            authenticated_files, authenticated_digests = (
                _producer_authenticated_runtime_files(producer_policy)
            )
            local_execution = run_local_producer(
                repository_dir=repository_root,
                cwd=str(producer_policy["cwd"]),
                argv=cast(list[str], producer_policy["argv"]),
                allowed_argv=(cast(list[str], producer_policy["argv"]),),
                output_artifact=str(producer_policy["output_artifact"]),
                command_runner=producer_runner,
                git_runner=git_runner,
                producer_id=producer_id,
                producer_policy_sha256=str(producer_policy["policy_sha256"]),
                authenticated_files=authenticated_files,
                authenticated_file_digests=authenticated_digests,
                pinned_argv_file_digests=(
                    _producer_pinned_argv_file_digests(producer_policy)
                ),
            )
            blockers.extend(_prefixed_blockers("local_execution", local_execution))
            repository_after = attest_repository(
                repository_root,
                expected_repository=AUTHORITATIVE_REPOSITORY,
                expected_commit=expected_commit,
                command_runner=git_runner,
            )
            blockers.extend(_prefixed_blockers("repository_after", repository_after))
            unchanged_build = _verify_admitted_build_snapshots_unchanged(
                build_binding,
                admitted_build,
            )
            blockers.extend(
                _prefixed_blockers("admitted_build_after", unchanged_build)
            )

    bundle_sha256: str | None = None
    bundle_size_bytes: int | None = None
    verification: dict[str, object] = {
        "ok": False,
        "status": "blocked",
        "blockers": [{"code": "bundle_not_built"}],
    }
    if not blockers:
        try:
            bundle = _assemble_github_actions_pre_live_bundle(
                repository_root=repository_root,
                expected_commit=expected_commit,
                source_context=source_context,
                build_binding=build_binding,
                admitted_build=admitted_build,
                producer_policy=producer_policy,
                local_execution=local_execution,
            )
            admission_snapshot = _admission_snapshot_from_mapping(admitted_build)
            if (
                producer_policy.get("producer_id")
                == PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
            ):
                verification = cast(
                    dict[str, object],
                    _with_pinned_node_executable(
                        producer_policy.get("node_executable_path"),
                        producer_policy.get("node_executable_sha256"),
                        lambda node_descriptor: verify_pre_live_artifact_bundle(
                            bundle,
                            admission_snapshot=admission_snapshot,
                            node_executable=node_descriptor,
                        ),
                    ),
                )
            else:
                verification = verify_pre_live_artifact_bundle(
                    bundle,
                    admission_snapshot=admission_snapshot,
                )
            if verification.get("ok") is not True:
                raise ValueError(
                    "assembled bundle failed verification: "
                    f"{verification.get('blockers')!r}"
                )
            parent_identity = _stable_directory_identity(output.parent)
            published_identity = _write_output_atomically(
                output,
                bundle,
                expected_parent_identity=parent_identity,
            )
            published, published_snapshot = _read_regular_file_snapshot(
                output,
                maximum=MAX_GITHUB_ARTIFACT_BYTES,
            )
            if published != bundle or published_snapshot != published_identity:
                raise ValueError("published pre-live bundle changed after assembly")
            bundle_sha256 = hashlib.sha256(bundle).hexdigest()
            bundle_size_bytes = len(bundle)
        except (OSError, TypeError, ValueError) as exc:
            blockers.append(f"pre-live bundle assembly failed: {exc}")

    return _component_result(
        blockers,
        output_path=str(output),
        bundle_member_name=GITHUB_ARTIFACT_BUNDLE_MEMBER_NAME,
        bundle_sha256=bundle_sha256,
        bundle_size_bytes=bundle_size_bytes,
        repository_before=repository_before,
        repository_after=repository_after,
        github_context=source_context,
        build_binding=build_binding,
        producer_policy=producer_policy,
        local_execution=local_execution,
        verification=verification,
        authority=source_context.get("authority"),
    )


def _producer_authenticated_runtime_files(
    producer_policy: Mapping[str, object],
) -> tuple[list[str], dict[str, str]]:
    runtime_sources = _mapping(producer_policy.get("runtime_sources"))
    source_files = runtime_sources.get("files")
    authenticated_files: list[str] = []
    authenticated_digests: dict[str, str] = {}
    if isinstance(source_files, list):
        for item in source_files:
            if not isinstance(item, Mapping):
                continue
            source_path = item.get("path")
            source_digest = item.get("sha256")
            if isinstance(source_path, str) and isinstance(source_digest, str):
                authenticated_files.append(source_path)
                authenticated_digests[source_path] = source_digest
    return authenticated_files, authenticated_digests


def _producer_pinned_argv_file_digests(
    producer_policy: Mapping[str, object],
) -> dict[str, str]:
    if (
        producer_policy.get("producer_id")
        != PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
    ):
        return {}
    binary_path = producer_policy.get("micromachine_binary_path")
    binary_sha256 = producer_policy.get("micromachine_binary_sha256")
    node_path = producer_policy.get("node_executable_path")
    node_sha256 = producer_policy.get("node_executable_sha256")
    pinned: dict[str, str] = {}
    if isinstance(binary_path, str) and isinstance(binary_sha256, str):
        pinned[binary_path] = binary_sha256
    if isinstance(node_path, str) and isinstance(node_sha256, str):
        pinned[node_path] = node_sha256
    return pinned


def _capture_admitted_build_snapshots(
    build_binding: Mapping[str, object],
) -> dict[str, object]:
    blockers: list[str] = []
    report_payload: bytes | None = None
    binary_payload: bytes | None = None
    report_snapshot: tuple[int, int, int, int, str] | None = None
    binary_snapshot: tuple[int, int, int, int, str] | None = None
    binary_mode: int | None = None
    if build_binding.get("ok") is not True:
        blockers.append("build binding must be accepted before snapshot capture")
    else:
        try:
            report_path = Path(str(build_binding["report_path"]))
            binary_path = Path(str(build_binding["binary_path"]))
            report_payload, report_snapshot = _read_regular_file_snapshot(
                report_path,
                maximum=MAX_BUILD_REPORT_BYTES,
            )
            binary_payload, binary_snapshot = _read_regular_file_snapshot(
                binary_path,
                maximum=MAX_GITHUB_ARTIFACT_BYTES,
            )
            binary_stat = binary_path.stat()
            binary_mode = binary_stat.st_mode
        except (KeyError, OSError, ValueError) as exc:
            blockers.append(f"could not capture admitted build snapshots: {exc}")
        else:
            if report_snapshot[4] != build_binding.get("report_sha256"):
                blockers.append("admitted report digest differs from build binding")
            if binary_snapshot[4] != build_binding.get("binary_sha256"):
                blockers.append("admitted binary digest differs from build binding")
            if binary_mode is None or binary_mode & 0o111 == 0:
                blockers.append("admitted binary is not executable")
    return _component_result(
        blockers,
        report_payload=report_payload,
        binary_payload=binary_payload,
        report_snapshot=list(report_snapshot) if report_snapshot is not None else None,
        binary_snapshot=list(binary_snapshot) if binary_snapshot is not None else None,
        binary_mode=binary_mode,
    )


def _verify_admitted_build_snapshots_unchanged(
    build_binding: Mapping[str, object],
    admitted_build: Mapping[str, object],
) -> dict[str, object]:
    blockers: list[str] = []
    if admitted_build.get("ok") is not True:
        blockers.append("admitted build snapshots were not accepted")
        return _component_result(blockers)
    for label, path_key, maximum in (
        ("report", "report_path", MAX_BUILD_REPORT_BYTES),
        ("binary", "binary_path", MAX_GITHUB_ARTIFACT_BYTES),
    ):
        expected_snapshot = admitted_build.get(f"{label}_snapshot")
        try:
            _, current_snapshot = _read_regular_file_snapshot(
                Path(str(build_binding[path_key])),
                maximum=maximum,
            )
        except (KeyError, OSError) as exc:
            blockers.append(f"admitted {label} changed or disappeared: {exc}")
            continue
        if list(current_snapshot) != expected_snapshot:
            blockers.append(f"admitted {label} changed after build attestation")
    return _component_result(blockers)


def _assemble_github_actions_pre_live_bundle(
    *,
    repository_root: Path,
    expected_commit: str,
    source_context: Mapping[str, object],
    build_binding: Mapping[str, object],
    admitted_build: Mapping[str, object],
    producer_policy: Mapping[str, object],
    local_execution: Mapping[str, object],
) -> bytes:
    ctest = _mapping(build_binding.get("ctest"))
    report_payload = admitted_build.get("report_payload")
    binary_payload = admitted_build.get("binary_payload")
    if not isinstance(report_payload, bytes) or not isinstance(binary_payload, bytes):
        raise ValueError("admitted build payloads are missing")
    raw_producer_output = _read_published_producer_output(local_execution)
    producer_output_payload = _bind_producer_output_to_admitted_build(
        producer_id=str(producer_policy.get("producer_id", "")),
        producer_output=raw_producer_output,
        build_report=report_payload,
        binary=binary_payload,
        node_executable=producer_policy.get("node_executable_path"),
        node_sha256=producer_policy.get("node_executable_sha256"),
    )
    producer_output_sha256 = hashlib.sha256(
        producer_output_payload
    ).hexdigest()
    repository_input = canonical_json_bytes(
        _repository_input_evidence_payload(
            build_binding,
            repository_commit=expected_commit,
        )
    )
    ctest_bytes = canonical_ctest_evidence_bytes(ctest)
    argv_bytes = canonical_json_bytes(producer_policy.get("argv"))
    provenance_bytes = canonical_json_bytes(
        {
            "schema_version": 1,
            "authority": dict(_mapping(source_context.get("authority"))),
            "producer_id": producer_policy.get("producer_id"),
            "policy_sha256": producer_policy.get("policy_sha256"),
            "repository_commit": expected_commit,
            "argv_sha256": hashlib.sha256(argv_bytes).hexdigest(),
            "executable_sha256": local_execution.get("executable_sha256"),
            "output_sha256": producer_output_sha256,
            "exit_code": local_execution.get("exit_code"),
            "started_at": local_execution.get("started_at"),
            "ended_at": local_execution.get("ended_at"),
            "stdout_sha256": local_execution.get("stdout_sha256"),
            "stderr_sha256": local_execution.get("stderr_sha256"),
        }
    )
    policy_path = repository_root / PRODUCER_POLICY_RELATIVE_PATH
    executable_path = Path(cast(list[str], producer_policy["argv"])[0])
    producer_output_member = (
        PRE_LIVE_DETERMINISTIC_JOURNEY_MEMBER_NAME
        if producer_policy.get("producer_id")
        == PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
        else "payload/provenance-foundation.json"
    )
    members = {
        "build/voi_build_identity.json": report_payload,
        "build/MicroMachine": binary_payload,
        "build/repository-input.json": repository_input,
        "build/ctest-evidence.json": ctest_bytes,
        "producer/policy.json": policy_path.read_bytes(),
        "producer/executable": executable_path.read_bytes(),
        "producer/argv.json": argv_bytes,
        producer_output_member: producer_output_payload,
        "producer/provenance.json": provenance_bytes,
    }
    authority = _mapping(source_context.get("authority"))
    pull_request = _mapping(authority.get("pull_request"))
    closing_issue = _mapping(authority.get("closing_issue"))
    metadata = PreLiveArtifactMetadata(
        authority_scope=str(authority.get("scope")),
        release_authoritative=bool(authority.get("release_authoritative")),
        authority_event=str(authority.get("event")),
        pull_request_database_id=int(pull_request.get("database_id")),
        pull_request_number=int(pull_request.get("number")),
        pull_request_head_sha=str(pull_request.get("head_sha")),
        pull_request_head_ref=str(pull_request.get("head_ref")),
        pull_request_head_repository_id=int(pull_request.get("head_repository_id")),
        closing_issue_repository_full_name=str(
            closing_issue.get("repository_full_name")
        ),
        closing_issue_repository_database_id=int(
            closing_issue.get("repository_database_id")
        ),
        closing_issue_database_id=int(closing_issue.get("database_id")),
        closing_issue_number=int(closing_issue.get("number")),
        repository_full_name=str(source_context["repository"]),
        repository_database_id=int(source_context["repository_id"]),
        repository_commit=expected_commit,
        workflow_id=int(source_context["workflow_id"]),
        workflow_path=str(source_context["workflow_path"]),
        workflow_ref=str(source_context["workflow_ref"]),
        workflow_sha=str(source_context["workflow_sha"]),
        run_id=int(source_context["run_id"]),
        run_attempt=int(source_context["run_attempt"]),
        job_id=int(source_context["job_id"]),
        job_name=str(source_context["job_name"]),
        artifact_logical_name="pre-live",
        artifact_member=producer_output_member,
        build_report_identity=str(build_binding["current_identity"]),
        build_report_member="build/voi_build_identity.json",
        binary_member="build/MicroMachine",
        repository_input_member="build/repository-input.json",
        repository_input_identity=str(build_binding["embedded_build_input_identity"]),
        ctest_member="build/ctest-evidence.json",
        producer_policy_id=str(producer_policy["producer_id"]),
        producer_policy_member="producer/policy.json",
        producer_executable_member="producer/executable",
        producer_argv_member="producer/argv.json",
        producer_output_member=producer_output_member,
        producer_provenance_member="producer/provenance.json",
    )
    admission_snapshot = _admission_snapshot_from_mapping(admitted_build)
    if (
        producer_policy.get("producer_id")
        != PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
    ):
        return build_pre_live_artifact_bundle(
            metadata,
            members,
            admission_snapshot=admission_snapshot,
        )
    return cast(
        bytes,
        _with_pinned_node_executable(
            producer_policy.get("node_executable_path"),
            producer_policy.get("node_executable_sha256"),
            lambda node_descriptor: build_pre_live_artifact_bundle(
                metadata,
                members,
                admission_snapshot=admission_snapshot,
                node_executable=node_descriptor,
            ),
        ),
    )


def _bind_producer_output_to_admitted_build(
    *,
    producer_id: str,
    producer_output: bytes,
    build_report: bytes,
    binary: bytes,
    node_executable: object = None,
    node_sha256: object = None,
) -> bytes:
    if producer_id != PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID:
        return producer_output
    return cast(
        bytes,
        _with_pinned_node_executable(
            node_executable,
            node_sha256,
            lambda node_descriptor: bind_deterministic_journey_bundle_to_build(
                producer_output,
                build_report_bytes=build_report,
                binary_bytes=binary,
                node_executable=node_descriptor,
            ),
        ),
    )


def _with_pinned_node_executable(
    node_executable: object,
    node_sha256: object,
    operation: Callable[[str], object],
) -> object:
    if not isinstance(node_executable, (str, Path)):
        raise ValueError("admitted Node.js executable path is missing or invalid")
    if not Path(node_executable).is_absolute():
        raise ValueError("admitted Node.js executable path is missing or invalid")
    if not isinstance(node_sha256, str) or not _SHA256_RE.fullmatch(node_sha256):
        raise ValueError("admitted Node.js executable digest is missing or invalid")
    node_path = Path(node_executable)
    if _path_has_symlink_component(node_path):
        raise ValueError("admitted Node.js executable path contains a symlink")
    descriptor, _, snapshot_before = _open_pinned_executable(node_path)
    try:
        if snapshot_before[4] != node_sha256:
            raise ValueError("admitted Node.js executable digest mismatch")
        descriptor_path = _descriptor_execution_path(descriptor)
        result = operation(descriptor_path)
        _, descriptor_snapshot_after = _read_open_regular_file_snapshot(
            descriptor,
            maximum=MAX_PRODUCER_EXECUTABLE_BYTES,
        )
        _, pathname_snapshot_after = _read_regular_file_snapshot(
            node_path,
            maximum=MAX_PRODUCER_EXECUTABLE_BYTES,
        )
        if (
            descriptor_snapshot_after != snapshot_before
            or pathname_snapshot_after != snapshot_before
        ):
            raise ValueError(
                "admitted Node.js executable changed during output binding"
            )
        return result
    finally:
        os.close(descriptor)


def _read_published_producer_output(
    local_execution: Mapping[str, object],
) -> bytes:
    local_artifact = _mapping(local_execution.get("output_artifact"))
    raw_path = local_artifact.get("path")
    expected_digest = local_artifact.get("sha256")
    expected_size = local_artifact.get("size_bytes")
    expected_identity = local_artifact.get("published_stat_identity")
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        raise ValueError("captured producer output path is missing or non-absolute")
    if not isinstance(expected_digest, str) or not _SHA256_RE.fullmatch(
        expected_digest
    ):
        raise ValueError("captured producer output digest is missing or invalid")
    if type(expected_size) is not int or expected_size < 0:
        raise ValueError("captured producer output size is missing or invalid")
    if (
        not isinstance(expected_identity, list)
        or len(expected_identity) != 5
        or any(type(value) is not int for value in expected_identity[:4])
        or not isinstance(expected_identity[4], str)
        or not _SHA256_RE.fullmatch(expected_identity[4])
    ):
        raise ValueError("captured producer output identity is missing or invalid")
    if (
        expected_identity[2] != expected_size
        or expected_identity[4] != expected_digest
    ):
        raise ValueError("captured producer output metadata is inconsistent")
    output_path = Path(raw_path)
    if _path_has_symlink_component(output_path):
        raise ValueError("captured producer output path contains a symlink")
    payload, snapshot = _read_regular_file_snapshot(
        output_path,
        maximum=MAX_GITHUB_ARTIFACT_BYTES,
    )
    if list(snapshot) != expected_identity:
        raise ValueError("captured producer output identity changed before consumption")
    if len(payload) != expected_size:
        raise ValueError("captured producer output size changed before consumption")
    if not hmac.compare_digest(
        hashlib.sha256(payload).hexdigest(),
        expected_digest,
    ):
        raise ValueError("captured producer output digest changed before consumption")
    return payload


def _read_attested_build_payload(
    build_binding: Mapping[str, object],
    *,
    path_key: str,
    digest_key: str,
    maximum: int,
    label: str,
) -> bytes:
    raw_path = build_binding.get(path_key)
    expected_digest = build_binding.get(digest_key)
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        raise ValueError(f"attested {label} path is missing or non-absolute")
    if not isinstance(expected_digest, str) or not _SHA256_RE.fullmatch(
        expected_digest
    ):
        raise ValueError(f"attested {label} digest is missing or invalid")
    raw_build_path = Path(raw_path)
    if os.path.lexists(raw_build_path) and raw_build_path.is_symlink():
        raise ValueError(f"attested {label} path is a symlink")
    path = raw_build_path.resolve()
    payload, snapshot = _read_regular_file_snapshot(path, maximum=maximum)
    if not hmac.compare_digest(snapshot[4], expected_digest):
        raise ValueError(f"attested {label} digest changed before binding")
    return payload


def _bound_local_producer_output_sha256(
    *,
    build_binding: Mapping[str, object],
    producer_policy: Mapping[str, object],
    local_execution: Mapping[str, object],
) -> str:
    raw_output = _read_published_producer_output(local_execution)
    if (
        producer_policy.get("producer_id")
        != PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID
    ):
        return hashlib.sha256(raw_output).hexdigest()
    build_report = _read_attested_build_payload(
        build_binding,
        path_key="report_path",
        digest_key="report_sha256",
        maximum=MAX_BUILD_REPORT_BYTES,
        label="build report",
    )
    binary = _read_attested_build_payload(
        build_binding,
        path_key="binary_path",
        digest_key="binary_sha256",
        maximum=MAX_GITHUB_ARTIFACT_BYTES,
        label="MicroMachine binary",
    )
    bound_output = _bind_producer_output_to_admitted_build(
        producer_id=PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID,
        producer_output=raw_output,
        build_report=build_report,
        binary=binary,
        node_executable=producer_policy.get("node_executable_path"),
        node_sha256=producer_policy.get("node_executable_sha256"),
    )
    return hashlib.sha256(bound_output).hexdigest()


def _repository_input_evidence_payload(
    build_binding: Mapping[str, object],
    *,
    repository_commit: object,
) -> dict[str, object]:
    repository_inputs = _mapping(build_binding.get("repository_inputs"))
    return {
        "schema_version": 1,
        "repository_commit": repository_commit,
        "build_input_identity": build_binding.get("embedded_build_input_identity"),
        "repository_inputs_digest": repository_inputs.get("digest"),
        "paths": dict(_mapping(repository_inputs.get("paths"))),
        "upstream_commit_policy": dict(
            _mapping(repository_inputs.get("upstream_commit_policy"))
        ),
    }


def _admission_snapshot_from_mapping(
    admitted_build: Mapping[str, object],
) -> PreLiveBuildAdmissionSnapshot:
    report_payload = admitted_build.get("report_payload")
    binary_payload = admitted_build.get("binary_payload")
    binary_mode = admitted_build.get("binary_mode")
    if (
        not isinstance(report_payload, bytes)
        or not isinstance(binary_payload, bytes)
        or type(binary_mode) is not int
    ):
        raise ValueError("admitted build snapshot is incomplete")
    return PreLiveBuildAdmissionSnapshot(
        build_report_bytes=report_payload,
        binary_bytes=binary_payload,
        binary_mode=binary_mode,
    )


def canonical_replay_digest(
    github_source: Mapping[str, object],
    build_binding: Mapping[str, object],
    producer_policy: Mapping[str, object],
    local_execution: Mapping[str, object],
) -> str:
    """Hash the immutable source/build/producer/output replay tuple."""

    if github_source.get("ok") is not True:
        raise ValueError("GitHub source must be accepted before replay hashing")
    if build_binding.get("ok") is not True:
        raise ValueError("build binding must be accepted before replay hashing")
    if producer_policy.get("ok") is not True:
        raise ValueError("producer policy must be accepted before replay hashing")
    if local_execution.get("ok") is not True:
        raise ValueError("local execution must be accepted before replay hashing")
    source_ids = _mapping(github_source.get("source_ids"))
    repository_inputs = _mapping(build_binding.get("repository_inputs"))
    ctest = _mapping(build_binding.get("ctest"))
    producer_module = _mapping(producer_policy.get("module"))
    producer_runtime_sources = _mapping(producer_policy.get("runtime_sources"))
    local_artifact = _mapping(local_execution.get("output_artifact"))
    material = {
        "repository": github_source.get("repository"),
        "repository_id": source_ids.get("repository_id"),
        "closing_issue_repository": github_source.get("repository"),
        "closing_issue_repository_id": source_ids.get("repository_id"),
        "closing_issue_id": source_ids.get("issue_id"),
        "closing_issue_number": source_ids.get("issue_number"),
        "workflow_run_id": source_ids.get("workflow_run_id"),
        "workflow_id": source_ids.get("workflow_id"),
        "run_attempt": source_ids.get("run_attempt"),
        "job_id": source_ids.get("job_id"),
        "artifact_database_id": source_ids.get("artifact_database_id"),
        "workflow_path": github_source.get("workflow_path"),
        "workflow_ref": github_source.get("workflow_ref"),
        "workflow_sha": github_source.get("workflow_sha"),
        "head_sha": github_source.get("head_sha"),
        "artifact_sha256": github_source.get("artifact_sha256"),
        "build_identity": build_binding.get("current_identity"),
        "binary_sha256": build_binding.get("binary_sha256"),
        "repository_inputs_sha256": repository_inputs.get("digest"),
        "ctest_manifest_sha256": ctest.get("test_manifest_sha256"),
        "ctest_registry_sha256": ctest.get("registry_sha256"),
        "producer_id": producer_policy.get("producer_id"),
        "producer_policy_sha256": producer_policy.get("policy_sha256"),
        "producer_argv_sha256": producer_policy.get("argv_sha256"),
        "producer_module_sha256": producer_module.get("sha256"),
        "producer_runtime_sources_sha256": producer_runtime_sources.get("digest"),
        "producer_executable_sha256": local_execution.get("executable_sha256"),
        "local_artifact_sha256": local_artifact.get("sha256"),
    }
    required = {
        "repository",
        "repository_id",
        "closing_issue_repository",
        "closing_issue_repository_id",
        "closing_issue_id",
        "closing_issue_number",
        "workflow_run_id",
        "workflow_id",
        "run_attempt",
        "job_id",
        "artifact_database_id",
        "workflow_path",
        "workflow_ref",
        "workflow_sha",
        "head_sha",
        "artifact_sha256",
        "build_identity",
        "binary_sha256",
        "repository_inputs_sha256",
        "ctest_manifest_sha256",
        "ctest_registry_sha256",
        "producer_id",
        "producer_policy_sha256",
        "producer_argv_sha256",
        "producer_module_sha256",
        "producer_runtime_sources_sha256",
        "producer_executable_sha256",
        "local_artifact_sha256",
    }
    missing = sorted(key for key in required if material.get(key) in (None, ""))
    if missing:
        raise ValueError("cannot construct replay digest without " + ", ".join(missing))
    return "sha256:" + hashlib.sha256(_canonical_json(material)).hexdigest()


def attest_artifact_local_bindings(
    *,
    github_source: Mapping[str, object],
    build_binding: Mapping[str, object],
    producer_policy: Mapping[str, object],
    local_execution: Mapping[str, object],
) -> dict[str, object]:
    """Cross-bind downloaded bundle roles to locally re-derived evidence."""

    blockers: list[str] = []
    artifact_bundle = _mapping(github_source.get("artifact_bundle"))
    manifest = _mapping(artifact_bundle.get("manifest"))
    role_evidence = _mapping(artifact_bundle.get("role_evidence"))
    bundled_provenance = _mapping(role_evidence.get("producer_provenance"))
    bundled_provenance_authority = _mapping(
        bundled_provenance.get("authority")
    )
    bundled_provenance_closing_issue = _mapping(
        bundled_provenance_authority.get("closing_issue")
    )
    manifest_authority = _mapping(manifest.get("authority"))
    manifest_closing_issue = _mapping(
        manifest_authority.get("closing_issue")
    )
    source_ids = _mapping(github_source.get("source_ids"))
    build = _mapping(manifest.get("build"))
    producer = _mapping(manifest.get("producer"))
    artifact = _mapping(manifest.get("artifact"))
    ctest = _mapping(build_binding.get("ctest"))
    local_artifact = _mapping(local_execution.get("output_artifact"))
    repository_input_payload = _repository_input_evidence_payload(
        build_binding,
        repository_commit=build_binding.get("repository_commit"),
    )
    repository_input_bytes = canonical_json_bytes(repository_input_payload)
    argv_bytes = canonical_json_bytes(producer_policy.get("argv"))
    try:
        ctest_bytes = canonical_ctest_evidence_bytes(ctest)
    except (TypeError, ValueError) as exc:
        blockers.append(f"local CTest evidence is invalid: {exc}")
        ctest_bytes = b""
    try:
        bound_local_output_sha256 = _bound_local_producer_output_sha256(
            build_binding=build_binding,
            producer_policy=producer_policy,
            local_execution=local_execution,
        )
    except (OSError, TypeError, ValueError) as exc:
        blockers.append(f"local producer output binding failed: {exc}")
        bound_local_output_sha256 = None
    expected = {
        "build.report_identity": build_binding.get("current_identity"),
        "build.report_sha256": build_binding.get("report_sha256"),
        "build.binary_sha256": build_binding.get("binary_sha256"),
        "build.repository_input_identity": build_binding.get(
            "embedded_build_input_identity"
        ),
        "build.repository_input_sha256": hashlib.sha256(
            repository_input_bytes
        ).hexdigest(),
        "build.ctest_sha256": (
            hashlib.sha256(ctest_bytes).hexdigest() if ctest_bytes else None
        ),
        "producer.policy_id": producer_policy.get("producer_id"),
        "producer.policy_sha256": producer_policy.get("policy_sha256"),
        "producer.executable_sha256": local_execution.get("executable_sha256"),
        "producer.argv_sha256": hashlib.sha256(argv_bytes).hexdigest(),
        "producer.output_sha256": bound_local_output_sha256,
        "producer.provenance.exit_code": local_execution.get("exit_code"),
        "producer.provenance.stdout_sha256": local_execution.get("stdout_sha256"),
        "producer.provenance.stderr_sha256": local_execution.get("stderr_sha256"),
        "authority.closing_issue.repository_full_name": github_source.get(
            "repository"
        ),
        "authority.closing_issue.repository_database_id": source_ids.get(
            "repository_id"
        ),
        "authority.closing_issue.database_id": source_ids.get("issue_id"),
        "authority.closing_issue.number": source_ids.get("issue_number"),
        "producer.provenance.authority.closing_issue.repository_full_name": (
            github_source.get("repository")
        ),
        "producer.provenance.authority.closing_issue.repository_database_id": (
            source_ids.get("repository_id")
        ),
        "producer.provenance.authority.closing_issue.database_id": (
            source_ids.get("issue_id")
        ),
        "producer.provenance.authority.closing_issue.number": source_ids.get(
            "issue_number"
        ),
        "artifact.sha256": bound_local_output_sha256,
    }
    actual = {
        "build.report_identity": build.get("report_identity"),
        "build.report_sha256": build.get("report_sha256"),
        "build.binary_sha256": build.get("binary_sha256"),
        "build.repository_input_identity": build.get("repository_input_identity"),
        "build.repository_input_sha256": build.get("repository_input_sha256"),
        "build.ctest_sha256": build.get("ctest_sha256"),
        "producer.policy_id": producer.get("policy_id"),
        "producer.policy_sha256": producer.get("policy_sha256"),
        "producer.executable_sha256": producer.get("executable_sha256"),
        "producer.argv_sha256": producer.get("argv_sha256"),
        "producer.output_sha256": producer.get("output_sha256"),
        "producer.provenance.exit_code": bundled_provenance.get("exit_code"),
        "producer.provenance.stdout_sha256": bundled_provenance.get("stdout_sha256"),
        "producer.provenance.stderr_sha256": bundled_provenance.get("stderr_sha256"),
        "authority.closing_issue.repository_full_name": (
            manifest_closing_issue.get("repository_full_name")
        ),
        "authority.closing_issue.repository_database_id": (
            manifest_closing_issue.get("repository_database_id")
        ),
        "authority.closing_issue.database_id": manifest_closing_issue.get(
            "database_id"
        ),
        "authority.closing_issue.number": manifest_closing_issue.get("number"),
        "producer.provenance.authority.closing_issue.repository_full_name": (
            bundled_provenance_closing_issue.get("repository_full_name")
        ),
        "producer.provenance.authority.closing_issue.repository_database_id": (
            bundled_provenance_closing_issue.get("repository_database_id")
        ),
        "producer.provenance.authority.closing_issue.database_id": (
            bundled_provenance_closing_issue.get("database_id")
        ),
        "producer.provenance.authority.closing_issue.number": (
            bundled_provenance_closing_issue.get("number")
        ),
        "artifact.sha256": artifact.get("sha256"),
    }
    for label in sorted(expected):
        if expected[label] in (None, ""):
            blockers.append(f"local artifact binding value is missing: {label}")
        elif actual[label] != expected[label]:
            blockers.append(
                f"artifact/local binding mismatch for {label}: "
                f"expected={expected[label]!r} actual={actual[label]!r}"
            )
    binding_material = {
        "github_artifact_sha256": github_source.get("artifact_sha256"),
        "expected": expected,
    }
    return _component_result(
        blockers,
        binding_sha256=(
            "sha256:"
            + hashlib.sha256(canonical_json_bytes(binding_material)).hexdigest()
            if not blockers
            else None
        ),
        repository_input_payload=repository_input_payload,
        ctest_payload=(json.loads(ctest_bytes) if ctest_bytes else None),
        raw_output_sha256=local_artifact.get("sha256"),
        bound_output_sha256=bound_local_output_sha256,
        expected=expected,
        actual=actual,
    )


def attest_github_replay_rulesets(
    adapter: GitHubReferenceAdapter,
    *,
    repository: str,
) -> dict[str, object]:
    """Verify the create-only and immutable replay tag rulesets."""

    blockers: list[str] = []
    expected_names = (
        AUTHORITATIVE_REPLAY_CREATE_RULESET_NAME,
        AUTHORITATIVE_REPLAY_IMMUTABLE_RULESET_NAME,
    )
    try:
        normalized_repository = normalize_github_repository(
            repository,
            allow_slug=True,
        )
        if normalized_repository.casefold() != AUTHORITATIVE_REPOSITORY.casefold():
            raise ValueError("replay ruleset repository is not authoritative")
    except ValueError as exc:
        return _component_result(
            [str(exc)],
            repository=repository,
            rulesets={},
        )

    try:
        summaries = adapter.list_repository_rulesets(normalized_repository)
    except Exception as exc:
        return _component_result(
            [f"GitHub replay ruleset listing failed: {exc}"],
            repository=normalized_repository,
            rulesets={},
        )
    if isinstance(summaries, (str, bytes)) or not isinstance(summaries, Sequence):
        return _component_result(
            ["GitHub replay ruleset listing did not return a sequence"],
            repository=normalized_repository,
            rulesets={},
        )

    selected_ids: dict[str, list[int]] = {name: [] for name in expected_names}
    for index, summary in enumerate(summaries):
        if not isinstance(summary, Mapping):
            blockers.append(f"GitHub replay ruleset summary {index} is not an object")
            continue
        name = summary.get("name")
        if name not in selected_ids:
            continue
        ruleset_id = _server_positive_id(
            summary.get("id"),
            f"ruleset summary {name!r} id",
            blockers,
        )
        if ruleset_id is not None:
            selected_ids[cast(str, name)].append(ruleset_id)

    selected: dict[str, Mapping[str, object]] = {}
    for name in expected_names:
        ids = selected_ids[name]
        if not ids:
            blockers.append(f"required GitHub replay ruleset is missing: {name}")
            continue
        if len(ids) != 1:
            blockers.append(
                f"required GitHub replay ruleset is ambiguous: {name} ids={ids!r}"
            )
            continue
        try:
            selected[name] = adapter.get_repository_ruleset(
                normalized_repository,
                ids[0],
            )
        except Exception as exc:
            blockers.append(f"GitHub replay ruleset detail failed for {name}: {exc}")

    observed: dict[str, object] = {}
    expected_rules = {
        AUTHORITATIVE_REPLAY_CREATE_RULESET_NAME: frozenset({"creation"}),
        AUTHORITATIVE_REPLAY_IMMUTABLE_RULESET_NAME: frozenset({"update", "deletion"}),
    }
    for name in expected_names:
        record = selected.get(name)
        if record is None:
            continue
        label = f"ruleset {name!r}"
        expected_id = selected_ids[name][0]
        _expect_server_value(record, "id", expected_id, label, blockers)
        _expect_server_value(record, "name", name, label, blockers)
        _expect_server_value(record, "target", "tag", label, blockers)
        _expect_server_value(record, "source_type", "Repository", label, blockers)
        _expect_server_value(record, "enforcement", "active", label, blockers)

        conditions = record.get("conditions")
        if not isinstance(conditions, Mapping):
            blockers.append(f"{label}.conditions is missing")
            conditions = {}
        if set(conditions) != {"ref_name"}:
            blockers.append(
                f"{label}.conditions must contain only ref_name: "
                f"actual={sorted(str(key) for key in conditions)!r}"
            )
        ref_name = _mapping(conditions.get("ref_name"))
        include = ref_name.get("include")
        exclude = ref_name.get("exclude")
        if include != [AUTHORITATIVE_REPLAY_REF_PATTERN]:
            blockers.append(
                f"{label}.conditions.ref_name.include mismatch: "
                f"expected={[AUTHORITATIVE_REPLAY_REF_PATTERN]!r} "
                f"actual={include!r}"
            )
        if exclude != []:
            blockers.append(
                f"{label}.conditions.ref_name.exclude must be empty: actual={exclude!r}"
            )

        rules = record.get("rules")
        rule_types: list[str] = []
        if not isinstance(rules, list):
            blockers.append(f"{label}.rules is missing")
        else:
            for index, rule in enumerate(rules):
                if not isinstance(rule, Mapping):
                    blockers.append(f"{label}.rules[{index}] is not an object")
                    continue
                if set(rule) != {"type"}:
                    blockers.append(
                        f"{label}.rules[{index}] must contain only type: "
                        f"actual={sorted(str(key) for key in rule)!r}"
                    )
                rule_type = rule.get("type")
                if not isinstance(rule_type, str) or not rule_type:
                    blockers.append(f"{label}.rules[{index}].type is missing")
                    continue
                rule_types.append(rule_type)
        if (
            len(rule_types) != len(expected_rules[name])
            or frozenset(rule_types) != expected_rules[name]
        ):
            blockers.append(
                f"{label}.rules mismatch: "
                f"expected={sorted(expected_rules[name])!r} "
                f"actual={sorted(rule_types)!r}"
            )

        bypass_actors = record.get("bypass_actors")
        if not isinstance(bypass_actors, list):
            blockers.append(f"{label}.bypass_actors is missing")
            bypass_actors = []
        expected_bypass = (
            [
                {
                    "actor_id": AUTHORITATIVE_REPLAY_CLAIMER_USER_ID,
                    "actor_type": "User",
                    "bypass_mode": "always",
                }
            ]
            if name == AUTHORITATIVE_REPLAY_CREATE_RULESET_NAME
            else []
        )
        canonical_bypass: list[dict[str, object]] = []
        for index, actor in enumerate(bypass_actors):
            if not isinstance(actor, Mapping):
                blockers.append(f"{label}.bypass_actors[{index}] is not an object")
                continue
            canonical_bypass.append(
                {
                    "actor_id": actor.get("actor_id"),
                    "actor_type": actor.get("actor_type"),
                    "bypass_mode": actor.get("bypass_mode"),
                }
            )
        if canonical_bypass != expected_bypass:
            blockers.append(
                f"{label}.bypass_actors mismatch: "
                f"expected={expected_bypass!r} actual={canonical_bypass!r}"
            )

        observed[name] = {
            "id": record.get("id"),
            "name": record.get("name"),
            "target": record.get("target"),
            "source_type": record.get("source_type"),
            "enforcement": record.get("enforcement"),
            "include": include,
            "exclude": exclude,
            "rule_types": sorted(rule_types),
            "bypass_actors": canonical_bypass,
        }

    return _component_result(
        blockers,
        repository=normalized_repository,
        ref_pattern=AUTHORITATIVE_REPLAY_REF_PATTERN,
        rulesets=observed,
    )


def consume_github_replay_reference(
    adapter: GitHubReferenceAdapter,
    *,
    repository: str,
    replay_digest: str,
    expected_head_sha: str,
) -> dict[str, object]:
    """Atomically consume a replay key in GitHub's durable ref namespace."""

    blockers: list[str] = []
    try:
        normalized_repository = normalize_github_repository(
            repository,
            allow_slug=True,
        )
        if normalized_repository.casefold() != AUTHORITATIVE_REPOSITORY.casefold():
            raise ValueError("replay authority repository is not authoritative")
        if not _SHA256_IDENTITY_RE.fullmatch(replay_digest):
            raise ValueError("replay digest must be canonical sha256")
        if not _SHA40_RE.fullmatch(expected_head_sha):
            raise ValueError("replay target must be an exact lowercase SHA")
        replay_ref = _normalize_github_reference(
            AUTHORITATIVE_REPLAY_REF_PREFIX + replay_digest.removeprefix("sha256:")
        )
    except ValueError as exc:
        return _component_result(
            [str(exc)],
            repository=repository,
            replay_ref=None,
            replay_digest=replay_digest,
            expected_head_sha=expected_head_sha,
            consumed=False,
        )

    created: Mapping[str, object] | None = None
    conflict_status: int | None = None
    try:
        created = adapter.create_git_reference(
            normalized_repository,
            ref=replay_ref,
            sha=expected_head_sha,
        )
    except GitHubHTTPError as exc:
        if exc.status not in {409, 422}:
            blockers.append(f"GitHub replay authority failed: {exc}")
        else:
            conflict_status = exc.status
    except Exception as exc:
        blockers.append(f"GitHub replay authority failed: {exc}")

    if created is not None:
        observed_ref, observed_sha, observed_type = _git_reference_identity(created)
        if observed_ref != replay_ref:
            blockers.append(
                "GitHub replay authority returned the wrong reference: "
                f"expected={replay_ref!r} actual={observed_ref!r}"
            )
        if observed_sha != expected_head_sha or observed_type != "commit":
            blockers.append(
                "GitHub replay authority returned the wrong target: "
                f"expected={expected_head_sha} "
                f"actual={observed_sha!r} type={observed_type!r}"
            )
    else:
        existing: Mapping[str, object] | None = None
        try:
            existing = adapter.get_git_reference(
                normalized_repository,
                ref=replay_ref,
            )
        except Exception as exc:
            blockers.append(
                f"GitHub replay authority could not resolve the failed claim: {exc}"
            )
        if existing is not None:
            observed_ref, observed_sha, observed_type = _git_reference_identity(
                existing
            )
            if observed_ref != replay_ref:
                blockers.append(
                    "GitHub replay authority returned an unrelated existing reference"
                )
            elif observed_sha != expected_head_sha or observed_type != "commit":
                blockers.append(
                    "GitHub replay reference already exists with a conflicting target"
                )
            else:
                blockers.append("GitHub replay digest was already consumed")
        elif conflict_status is not None:
            blockers.append(
                "GitHub replay claim conflicted without an exact existing reference"
            )

    return _component_result(
        blockers,
        authority="github_ref",
        repository=normalized_repository,
        replay_ref=replay_ref,
        replay_digest=replay_digest,
        expected_head_sha=expected_head_sha,
        conflict_status=conflict_status,
        consumed=created is not None and not blockers,
    )


def consume_replay_ledger(
    ledger_path: Path | str,
    replay_digest: str,
    *,
    source_ids: Mapping[str, object],
) -> dict[str, object]:
    """Consume a replay key once using a durable, process-safe ledger update."""

    blockers: list[str] = []
    raw_path = Path(ledger_path).absolute()
    if os.path.lexists(raw_path) and raw_path.is_symlink():
        return _component_result(
            ["replay ledger must not be a symlink"],
            ledger_path=str(raw_path),
            replay_digest=replay_digest,
            consumed=False,
        )
    path = raw_path.resolve(strict=False)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", replay_digest):
        return _component_result(
            ["replay digest must be canonical sha256"],
            ledger_path=str(path),
            replay_digest=replay_digest,
            consumed=False,
        )
    if not path.parent.is_dir():
        return _component_result(
            [f"replay ledger directory is missing: {path.parent}"],
            ledger_path=str(path),
            replay_digest=replay_digest,
            consumed=False,
        )
    if _path_has_symlink_component(path.parent):
        return _component_result(
            ["replay ledger parent contains a symlink"],
            ledger_path=str(path),
            replay_digest=replay_digest,
            consumed=False,
        )
    parent_stat = path.parent.stat()
    if parent_stat.st_uid != os.getuid():
        return _component_result(
            ["replay ledger directory is not owned by the current user"],
            ledger_path=str(path),
            replay_digest=replay_digest,
            consumed=False,
        )

    lock_path = path.with_name(path.name + ".lock")
    temporary_path: Path | None = None
    try:
        if os.path.lexists(lock_path) and lock_path.is_symlink():
            raise ValueError("replay ledger lock must not be a symlink")
        lock_flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            lock_flags |= os.O_NOFOLLOW
        lock_fd = os.open(lock_path, lock_flags, 0o600)
        try:
            lock_stat = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_stat.st_mode):
                raise ValueError("replay ledger lock is not a regular file")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            ledger = _read_replay_ledger(path)
            entries = cast(dict[str, object], ledger["entries"])
            if replay_digest in entries:
                blockers.append("replay digest was already consumed")
                return _component_result(
                    blockers,
                    ledger_path=str(path),
                    replay_digest=replay_digest,
                    consumed=False,
                    previous=entries[replay_digest],
                )
            if len(entries) >= MAX_REPLAY_ENTRIES:
                raise ValueError("replay ledger entry limit exceeded")
            timestamp = _utc_now()
            entries[replay_digest] = {
                "consumed_at": timestamp,
                "source_ids": _json_safe_mapping(source_ids),
            }
            payload = _canonical_json(ledger) + b"\n"
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
            temporary_path = None
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            os.close(lock_fd)
    except (OSError, TypeError, ValueError) as exc:
        blockers.append(f"replay ledger consumption failed closed: {exc}")
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
    return _component_result(
        blockers,
        ledger_path=str(path),
        replay_digest=replay_digest,
        consumed=not blockers,
    )


def attest_pre_live_provenance(
    *,
    repository_dir: Path | str,
    expected_commit: str,
    github_adapter: GitHubSourceAdapter,
    replay_store: ReplayStore,
    issue_number: int,
    pull_number: int,
    run_id: int,
    run_attempt: int,
    job_id: int,
    artifact_id: int,
    expected_head_sha: str,
    build_report_path: Path | str,
    expected_build_dir: Path | str,
    producer_id: str,
    node_executable: Path | str | None = None,
    git_runner: CommandRunner = subprocess.run,
    ctest_runner: CommandRunner = subprocess.run,
    producer_runner: CommandRunner = subprocess.run,
    untrusted_payload: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Aggregate authenticated source, build, execution, and replay evidence."""

    blockers: list[str] = []
    repository_before = attest_repository(
        repository_dir,
        expected_repository=AUTHORITATIVE_REPOSITORY,
        expected_commit=expected_commit,
        command_runner=git_runner,
    )
    build_binding = attest_build_binding(
        build_report_path,
        repository_dir=repository_dir,
        expected_repository_commit=expected_commit,
        expected_build_dir=expected_build_dir,
        command_runner=ctest_runner,
        git_runner=git_runner,
    )
    if expected_head_sha != expected_commit:
        blockers.append(
            "GitHub head SHA must equal the attested repository commit: "
            f"repository={expected_commit} github={expected_head_sha}"
        )
    blockers.extend(_prefixed_blockers("repository", repository_before))
    blockers.extend(_prefixed_blockers("build", build_binding))

    if blockers:
        producer_policy = _component_result(
            ["producer policy not loaded because authenticated prerequisites failed"],
            producer_id=producer_id,
            status="not_loaded",
        )
        local_execution = _component_result(
            ["producer not executed because authenticated prerequisites failed"],
            status="not_run",
        )
        repository_after = repository_before
    else:
        producer_policy = resolve_local_producer_policy(
            repository_dir=repository_dir,
            expected_commit=expected_commit,
            producer_id=producer_id,
            git_runner=git_runner,
            micromachine_binary_path=build_binding.get("binary_path"),
            micromachine_binary_sha256=build_binding.get("binary_sha256"),
            node_executable=node_executable,
        )
        blockers.extend(_prefixed_blockers("producer_policy", producer_policy))
    if blockers:
        github_source = _component_result(
            [
                "GitHub source not attested because authenticated local "
                "prerequisites failed"
            ],
            status="not_evaluated",
        )
    else:
        try:
            github_kwargs = {
                "repository": AUTHORITATIVE_REPOSITORY,
                "expected_repository_id": AUTHORITATIVE_REPOSITORY_ID,
                "issue_number": issue_number,
                "pull_number": pull_number,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "job_id": job_id,
                "artifact_id": artifact_id,
                "expected_head_sha": expected_head_sha,
                "expected_issue_state": "open",
                "expected_pull_state": "open",
            }
            if producer_id == PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID:
                github_source = cast(
                    dict[str, object],
                    _with_pinned_node_executable(
                        producer_policy.get("node_executable_path"),
                        producer_policy.get("node_executable_sha256"),
                        lambda node_descriptor: attest_github_source(
                            github_adapter,
                            **github_kwargs,
                            node_executable=node_descriptor,
                        ),
                    ),
                )
            else:
                github_source = attest_github_source(
                    github_adapter,
                    **github_kwargs,
                )
        except (OSError, TypeError, ValueError) as exc:
            github_source = _component_result(
                [f"GitHub source attestation setup failed: {exc}"],
            )
        blockers.extend(_prefixed_blockers("github", github_source))
    blockers.extend(_production_candidate_producer_blockers(producer_id))

    if blockers:
        local_execution = _component_result(
            ["producer not executed because authenticated prerequisites failed"],
            status="not_run",
        )
        repository_after = repository_before
    if not blockers:
        runtime_sources = _mapping(producer_policy.get("runtime_sources"))
        runtime_source_files = runtime_sources.get("files")
        authenticated_files: list[str] = []
        authenticated_file_digests: dict[str, str] = {}
        if isinstance(runtime_source_files, list):
            for item in runtime_source_files:
                if not isinstance(item, Mapping):
                    continue
                source_path = item.get("path")
                source_digest = item.get("sha256")
                if isinstance(source_path, str) and isinstance(source_digest, str):
                    authenticated_files.append(source_path)
                    authenticated_file_digests[source_path] = source_digest
        local_execution = run_local_producer(
            repository_dir=repository_dir,
            cwd=str(producer_policy["cwd"]),
            argv=cast(list[str], producer_policy["argv"]),
            allowed_argv=(cast(list[str], producer_policy["argv"]),),
            output_artifact=str(producer_policy["output_artifact"]),
            command_runner=producer_runner,
            git_runner=git_runner,
            producer_id=producer_id,
            producer_policy_sha256=str(producer_policy["policy_sha256"]),
            authenticated_files=authenticated_files,
            authenticated_file_digests=authenticated_file_digests,
            pinned_argv_file_digests=(
                _producer_pinned_argv_file_digests(producer_policy)
            ),
        )
        blockers.extend(_prefixed_blockers("local_execution", local_execution))
        repository_after = attest_repository(
            repository_dir,
            expected_repository=AUTHORITATIVE_REPOSITORY,
            expected_commit=expected_commit,
            command_runner=git_runner,
        )
        blockers.extend(_prefixed_blockers("repository_after", repository_after))

    if blockers:
        artifact_binding = _component_result(
            ["artifact binding not evaluated because provenance is blocked"],
            status="not_evaluated",
        )
    else:
        artifact_binding = attest_artifact_local_bindings(
            github_source=github_source,
            build_binding=build_binding,
            producer_policy=producer_policy,
            local_execution=local_execution,
        )
        blockers.extend(_prefixed_blockers("artifact_binding", artifact_binding))

    replay_digest: str | None = None
    if blockers:
        replay = _component_result(
            ["replay key not consumed because provenance is blocked"],
            authority="github_ref",
            replay_digest=None,
            consumed=False,
            status="not_consumed",
        )
    else:
        try:
            replay_digest = canonical_replay_digest(
                github_source,
                build_binding,
                producer_policy,
                local_execution,
            )
        except ValueError as exc:
            replay = _component_result(
                [str(exc)],
                authority="github_ref",
                replay_digest=None,
                consumed=False,
            )
        else:
            try:
                replay_claim = replay_store.consume(
                    repository=AUTHORITATIVE_REPOSITORY,
                    replay_digest=replay_digest,
                    expected_head_sha=expected_head_sha,
                )
            except Exception as exc:
                replay = _component_result(
                    [f"replay authority failed closed: {exc}"],
                    authority="github_ref",
                    replay_digest=replay_digest,
                    consumed=False,
                )
            else:
                replay_blockers = _prefixed_blockers("authority", replay_claim)
                expected_replay_ref = (
                    AUTHORITATIVE_REPLAY_REF_PREFIX
                    + replay_digest.removeprefix("sha256:")
                )
                expected_replay_values = {
                    "authority": "github_ref",
                    "repository": AUTHORITATIVE_REPOSITORY,
                    "replay_ref": expected_replay_ref,
                    "replay_digest": replay_digest,
                    "expected_head_sha": expected_head_sha,
                    "consumed": True,
                }
                for field, expected_value in expected_replay_values.items():
                    actual_value = replay_claim.get(field)
                    if actual_value != expected_value:
                        replay_blockers.append(
                            "replay authority result mismatch for "
                            f"{field}: expected={expected_value!r} "
                            f"actual={actual_value!r}"
                        )
                replay = _component_result(
                    replay_blockers,
                    authority="github_ref",
                    replay_digest=replay_digest,
                    consumed=not replay_blockers,
                    claim=dict(replay_claim),
                )
        blockers.extend(_prefixed_blockers("replay", replay))

    ignored_fields = sorted(
        key for key in (untrusted_payload or {}) if key in UNTRUSTED_STATUS_FIELDS
    )
    ok = not blockers
    authority = dict(_mapping(github_source.get("authority"))) if ok else {}
    accepted_source_ids = dict(_mapping(github_source.get("source_ids"))) if ok else {}
    local_artifact = _mapping(local_execution.get("output_artifact"))
    accepted_digests = (
        {
            "repository_commit": repository_after.get("observed_commit"),
            "github_head_sha": github_source.get("head_sha"),
            "github_artifact_sha256": github_source.get("artifact_sha256"),
            "build_identity": build_binding.get("current_identity"),
            "binary_sha256": build_binding.get("binary_sha256"),
            "local_artifact_sha256": local_artifact.get("sha256"),
            "artifact_binding_sha256": artifact_binding.get("binding_sha256"),
            "replay_digest": replay_digest,
        }
        if ok
        else {}
    )
    return {
        "schema_version": PRE_LIVE_PROVENANCE_SCHEMA_VERSION,
        "ok": ok,
        "status": "candidate_qualified" if ok else "blocked",
        "blockers": blockers,
        "authority": authority,
        "release_authoritative": False,
        "repository": {
            "before": repository_before,
            "after": repository_after,
        },
        "github_source": github_source,
        "build_binding": build_binding,
        "producer_policy": producer_policy,
        "local_execution": local_execution,
        "artifact_binding": artifact_binding,
        "replay": replay,
        "accepted_source_ids": accepted_source_ids,
        "accepted_digests": accepted_digests,
        "ignored_untrusted_fields": ignored_fields,
    }


def require_release_authority(
    evidence: Mapping[str, object],
) -> dict[str, object]:
    """Fail closed until the authenticated post-merge verifier is implemented."""

    authority = _mapping(evidence.get("authority"))
    scope = authority.get("scope")
    blockers = [
        "authenticated post-merge release authority is not implemented; "
        "raw evidence mappings cannot authorize a release"
    ]
    if scope == PRE_LIVE_CANDIDATE_AUTHORITY_SCOPE:
        blockers.append(
            "candidate_pr evidence is qualification-only and cannot authorize a release"
        )
    elif scope not in {None, "release_post_merge"}:
        blockers.append(f"unsupported release authority scope: {scope!r}")
    return _component_result(
        blockers,
        authority={},
        release_authoritative=False,
    )


def _repository_authoritative_upstream_commits(
    repository_root: Path,
    *,
    expected_commit: str,
    git_runner: CommandRunner,
) -> dict[str, object]:
    relative_path = Path(
        "integrations/micromachine/scripts/build_macos_local.sh"
    )
    blockers: list[str] = []
    payload = b""
    try:
        completed = git_runner(
            [
                TRUSTED_GIT_EXECUTABLE,
                "show",
                f"{expected_commit}:{relative_path.as_posix()}",
            ],
            cwd=str(repository_root),
            check=False,
            capture_output=True,
            text=False,
            shell=False,
            env=dict(SANITIZED_GIT_ENV),
        )
    except Exception as exc:
        blockers.append(f"could not read committed build script: {exc}")
    else:
        if int(completed.returncode) != 0:
            blockers.append("committed build script is missing")
        else:
            payload = _as_bytes(completed.stdout)
    commits: dict[str, str] = {}
    if payload:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            blockers.append("committed build script is not UTF-8")
        else:
            for name, commit in _BUILD_SCRIPT_UPSTREAM_COMMIT_RE.findall(text):
                key = (
                    "micromachine_commit"
                    if name == "MICROMACHINE_COMMIT"
                    else "s2client_commit"
                )
                if key in commits:
                    blockers.append(f"committed build script repeats {name}")
                commits[key] = commit
    for key in ("micromachine_commit", "s2client_commit"):
        if key not in commits:
            blockers.append(
                f"committed build script does not pin {key.replace('_commit', '')}"
            )
    return _component_result(
        blockers,
        path=relative_path.as_posix(),
        sha256=hashlib.sha256(payload).hexdigest() if payload else None,
        micromachine_commit=commits.get("micromachine_commit"),
        s2client_commit=commits.get("s2client_commit"),
    )


def _build_config_from_report(
    report: Mapping[str, object],
    *,
    expected_micromachine_commit: str,
    expected_s2client_commit: str,
) -> MicroMachineBuildIdentityConfig:
    paths = report.get("paths")
    expected = report.get("expected")
    if not isinstance(paths, Mapping):
        raise ValueError("paths object is required")
    if not isinstance(expected, Mapping):
        raise ValueError("expected object is required")
    micromachine_commit = expected.get("micromachine_commit")
    s2client_commit = expected.get("s2client_commit")
    if not isinstance(micromachine_commit, str) or not _SHA40_RE.fullmatch(
        micromachine_commit
    ):
        raise ValueError("expected micromachine commit must be a lowercase SHA")
    if not isinstance(s2client_commit, str) or not _SHA40_RE.fullmatch(s2client_commit):
        raise ValueError("expected s2client commit must be a lowercase SHA")
    if micromachine_commit != expected_micromachine_commit:
        raise ValueError(
            "expected micromachine commit is not repository-authoritative: "
            f"expected={expected_micromachine_commit} actual={micromachine_commit}"
        )
    if s2client_commit != expected_s2client_commit:
        raise ValueError(
            "expected s2client commit is not repository-authoritative: "
            f"expected={expected_s2client_commit} actual={s2client_commit}"
        )

    values: dict[str, object] = {
        "micromachine_commit": expected_micromachine_commit,
        "s2client_commit": expected_s2client_commit,
    }
    non_path_fields = {"micromachine_commit", "s2client_commit"}
    for field in fields(MicroMachineBuildIdentityConfig):
        if field.name in non_path_fields:
            continue
        value = paths.get(field.name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"paths.{field.name} is required")
        candidate = Path(value)
        if not candidate.is_absolute():
            raise ValueError(f"paths.{field.name} must be absolute")
        values[field.name] = candidate
    config = MicroMachineBuildIdentityConfig(**values)
    recorded_binary = paths.get("binary")
    if recorded_binary != str(config.binary_path):
        raise ValueError("paths.binary does not match micromachine_build_dir")
    recorded_header = paths.get("embedded_build_identity_header")
    if recorded_header != str(config.embedded_build_identity_header_path):
        raise ValueError(
            "paths.embedded_build_identity_header does not match micromachine_dir"
        )
    return config


def _attest_repository_build_inputs(
    config: MicroMachineBuildIdentityConfig,
    *,
    repository_root: Path,
    expected_commit: str,
    git_runner: CommandRunner,
) -> dict[str, object]:
    blockers: list[str] = []
    observed_paths: dict[str, object] = {}
    if not repository_root.is_dir():
        blockers.append(f"repository directory is missing: {repository_root}")
    if not _SHA40_RE.fullmatch(expected_commit):
        blockers.append("repository build commit must be an exact lowercase SHA")
    if blockers:
        return _component_result(
            blockers,
            repository_commit=expected_commit,
            digest=None,
            paths=observed_paths,
        )

    head = _git_head(repository_root, git_runner)
    if head != expected_commit:
        blockers.append(
            "repository build-input HEAD mismatch: "
            f"expected={expected_commit} actual={head}"
        )
    for field in fields(MicroMachineBuildIdentityConfig):
        if field.name in _EXTERNAL_BUILD_PATH_FIELDS or field.name in {
            "micromachine_commit",
            "s2client_commit",
        }:
            continue
        default_path = field.default
        actual_path = getattr(config, field.name)
        if not isinstance(default_path, Path) or not isinstance(actual_path, Path):
            blockers.append(
                f"build input {field.name} has no authoritative repository path"
            )
            continue
        try:
            relative_path = default_path.resolve().relative_to(
                BUILD_IDENTITY_REPO_ROOT.resolve()
            )
        except ValueError:
            blockers.append(
                f"authoritative build input {field.name} escapes repository root"
            )
            continue
        expected_path = repository_root / relative_path
        if actual_path.resolve() != expected_path.resolve():
            blockers.append(
                f"build input path mismatch for {field.name}: "
                f"expected={expected_path} actual={actual_path}"
            )
            continue
        if _path_has_symlink_component(expected_path, stop=repository_root):
            blockers.append(f"build input path contains a symlink: {relative_path}")
            continue
        try:
            file_stat = expected_path.stat()
        except OSError as exc:
            blockers.append(f"build input is unreadable: {relative_path}: {exc}")
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            blockers.append(f"build input is not a regular file: {relative_path}")
            continue
        try:
            committed = git_runner(
                [
                    TRUSTED_GIT_EXECUTABLE,
                    "show",
                    f"{expected_commit}:{relative_path.as_posix()}",
                ],
                cwd=str(repository_root),
                check=False,
                capture_output=True,
                text=False,
                shell=False,
                env=dict(SANITIZED_GIT_ENV),
            )
        except Exception as exc:
            blockers.append(
                f"could not read committed build input {relative_path}: {exc}"
            )
            continue
        if int(committed.returncode) != 0:
            blockers.append(
                f"build input is not tracked at {expected_commit}: {relative_path}"
            )
            continue
        committed_bytes = _as_bytes(committed.stdout)
        try:
            working_bytes = expected_path.read_bytes()
        except OSError as exc:
            blockers.append(f"could not hash build input {relative_path}: {exc}")
            continue
        committed_digest = hashlib.sha256(committed_bytes).hexdigest()
        working_digest = hashlib.sha256(working_bytes).hexdigest()
        if committed_digest != working_digest:
            blockers.append(
                f"build input differs from {expected_commit}: {relative_path}"
            )
            continue
        observed_paths[field.name] = {
            "path": relative_path.as_posix(),
            "sha256": working_digest,
            "size_bytes": len(working_bytes),
        }

    digest = (
        "sha256:" + hashlib.sha256(_canonical_json(observed_paths)).hexdigest()
        if observed_paths and not blockers
        else None
    )
    return _component_result(
        blockers,
        repository_commit=expected_commit,
        digest=digest,
        paths=observed_paths,
    )


def _resolve_cmake_ctest_path(build_dir: Path) -> Path:
    cache_path = build_dir / "CMakeCache.txt"
    if _path_has_symlink_component(cache_path, stop=build_dir):
        raise ValueError("CMakeCache.txt contains a symlink")
    payload, _ = _read_regular_file_snapshot(
        cache_path,
        maximum=MAX_CMAKE_CACHE_BYTES,
    )
    try:
        cache_text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("CMakeCache.txt is not UTF-8") from exc
    matches = _CMAKE_CTEST_COMMAND_RE.findall(cache_text)
    if len(matches) != 1:
        raise ValueError("CMakeCache.txt must contain one CMAKE_CTEST_COMMAND")
    candidate = Path(matches[0].strip())
    if not candidate.is_absolute() or candidate.name != "ctest":
        raise ValueError("CMAKE_CTEST_COMMAND must be an absolute ctest path")
    return candidate


def _run_ctest(
    build_dir: Path,
    command_runner: CommandRunner,
) -> dict[str, object]:
    build_dir = build_dir.resolve()
    blockers: list[str] = []
    try:
        ctest_path = _resolve_cmake_ctest_path(build_dir)
    except (OSError, ValueError) as exc:
        return _component_result(
            [f"ctest executable could not be authenticated: {exc}"],
            schema_version=PRE_LIVE_CTEST_EVIDENCE_SCHEMA_VERSION,
            argv=None,
            discovery_argv=None,
            ctest_executable=None,
            ctest_executable_sha256=None,
            returncode=None,
            passed=0,
            total=0,
            failures=0,
            test_names=[],
            test_executables={},
            test_manifest_sha256=None,
            registry_sha256=None,
            stdout_sha256=None,
            stderr_sha256=None,
        )
    if (
        not ctest_path.is_file()
        or ctest_path.is_symlink()
        or not os.access(ctest_path, os.X_OK)
        or ctest_path.name != "ctest"
        or _path_has_symlink_component(ctest_path)
    ):
        blockers.append("resolved ctest is missing, linked, or not executable")
    try:
        ctest_payload, ctest_snapshot = _read_regular_file_snapshot(
            ctest_path,
            maximum=MAX_GITHUB_ARTIFACT_BYTES,
        )
    except OSError as exc:
        ctest_payload = b""
        ctest_snapshot = None
        blockers.append(f"could not snapshot the ctest executable: {exc}")
    ctest_sha256_before = ctest_snapshot[4] if ctest_snapshot is not None else None

    pinned_directory = tempfile.TemporaryDirectory(
        prefix=".voi-ctest-",
        dir=build_dir.parent,
    )
    pinned_root = Path(pinned_directory.name)
    pinned_ctest = pinned_root / "ctest"
    pinned_bin = pinned_root / "bin"
    pinned_bin.mkdir(mode=0o700)
    if not blockers:
        _write_private_snapshot_file(pinned_ctest, ctest_payload)
        os.chmod(pinned_ctest, 0o500)
    discovery_argv = (
        str(ctest_path),
        "--test-dir",
        str(build_dir),
        "--show-only=json-v1",
    )
    argv = (
        str(ctest_path),
        "--test-dir",
        str(build_dir),
        "--output-on-failure",
    )
    returncode: int | None = None
    stdout = ""
    stderr = ""
    test_names: list[str] = []
    registry_paths: dict[str, str] = {}
    test_executables: dict[str, dict[str, object]] = {}
    pinned_test_paths: dict[str, Path] = {}
    original_test_snapshots: dict[str, tuple[int, int, int, int, str]] = {}
    if not blockers:
        try:
            registered = command_runner(
                [str(pinned_ctest), *discovery_argv[1:]],
                cwd=str(build_dir),
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                env=dict(SANITIZED_TEST_ENV),
            )
            registry_returncode = int(registered.returncode)
            registry_stdout = _as_text(registered.stdout)
            registry_stderr = _as_text(registered.stderr)
        except Exception as exc:
            blockers.append(f"CTest registry discovery failed: {exc}")
        else:
            if registry_returncode != 0:
                blockers.append(
                    f"CTest registry discovery exited with code {registry_returncode}"
                )
            try:
                registry_payload = json.loads(
                    registry_stdout,
                    object_pairs_hook=_reject_duplicate_json_object_keys,
                    parse_constant=_reject_nonfinite_json,
                )
            except (ValueError, json.JSONDecodeError) as exc:
                blockers.append(
                    f"CTest registry discovery returned malformed JSON: {exc}"
                )
            else:
                registered_tests = (
                    registry_payload.get("tests")
                    if isinstance(registry_payload, Mapping)
                    else None
                )
                if not isinstance(registered_tests, list):
                    blockers.append(
                        "CTest registry discovery did not return a tests list"
                    )
                else:
                    for test in registered_tests:
                        if not isinstance(test, Mapping):
                            blockers.append(
                                "CTest registry discovery returned a non-object test"
                            )
                            continue
                        name = test.get("name")
                        command = test.get("command")
                        if (
                            not isinstance(name, str)
                            or not isinstance(command, list)
                            or len(command) != 1
                            or not isinstance(command[0], str)
                            or name in registry_paths
                        ):
                            blockers.append(
                                "CTest registry discovery returned an invalid test"
                            )
                            continue
                        registry_paths[name] = command[0]
                    expected_registry_paths = {
                        name: str((build_dir / "bin" / executable).resolve())
                        for name, executable in _REQUIRED_CTEST_COMMANDS.items()
                    }
                    if registry_paths != expected_registry_paths:
                        blockers.append(
                            "CTest registry identity mismatch: "
                            f"expected={expected_registry_paths} "
                            f"actual={registry_paths}"
                        )
                    else:
                        test_names = sorted(registry_paths)
            if registry_stderr:
                blockers.append("CTest registry discovery wrote to stderr")
    for name, executable_name in sorted(_REQUIRED_CTEST_COMMANDS.items()):
        command_path = (build_dir / "bin" / executable_name).resolve()
        if (
            command_path.is_symlink()
            or _path_has_symlink_component(command_path, stop=build_dir)
            or not os.access(command_path, os.X_OK)
        ):
            blockers.append(
                f"CTest command is missing, linked, or not executable: {name}"
            )
            continue
        try:
            command_payload, command_snapshot = _read_regular_file_snapshot(
                command_path,
                maximum=MAX_GITHUB_ARTIFACT_BYTES,
            )
        except OSError as exc:
            blockers.append(f"CTest command could not be snapshotted: {name}: {exc}")
            continue
        pinned_path = pinned_bin / executable_name
        _write_private_snapshot_file(pinned_path, command_payload)
        os.chmod(pinned_path, 0o500)
        pinned_test_paths[name] = pinned_path
        original_test_snapshots[name] = command_snapshot
        test_executables[name] = {
            "path": str(command_path),
            "sha256": command_snapshot[4],
            "sha256_after": None,
            "argv": [str(command_path)],
            "returncode": None,
            "stdout_sha256": None,
            "stderr_sha256": None,
        }
    pinned_manifest = pinned_root / "CTestTestfile.cmake"
    if not blockers:
        manifest_lines = []
        for name in sorted(_REQUIRED_CTEST_COMMANDS):
            pinned_path = pinned_test_paths[name]
            manifest_lines.extend(
                (
                    f"add_test([=[{name}]=] [=[{pinned_path}]=])",
                    (
                        f"set_tests_properties([=[{name}]=] PROPERTIES "
                        f"WORKING_DIRECTORY [=[{pinned_root}]=])"
                    ),
                )
            )
        _write_private_snapshot_file(
            pinned_manifest,
            ("\n".join(manifest_lines) + "\n").encode(),
        )
    if not blockers:
        try:
            discovered = command_runner(
                [
                    str(pinned_ctest),
                    "--test-dir",
                    str(pinned_root),
                    "--show-only=json-v1",
                ],
                cwd=str(pinned_root),
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                env=dict(SANITIZED_TEST_ENV),
            )
            discovery_returncode = int(discovered.returncode)
            discovery_stdout = _as_text(discovered.stdout)
            discovery_stderr = _as_text(discovered.stderr)
        except Exception as exc:
            blockers.append(f"CTest discovery failed: {exc}")
        else:
            if discovery_returncode != 0:
                blockers.append(
                    f"CTest discovery exited with code {discovery_returncode}"
                )
            try:
                discovery = json.loads(
                    discovery_stdout,
                    object_pairs_hook=_reject_duplicate_json_object_keys,
                    parse_constant=_reject_nonfinite_json,
                )
            except (ValueError, json.JSONDecodeError) as exc:
                blockers.append(f"CTest discovery returned malformed JSON: {exc}")
            else:
                tests = (
                    discovery.get("tests") if isinstance(discovery, Mapping) else None
                )
                if not isinstance(tests, list):
                    blockers.append("CTest discovery did not return a tests list")
                else:
                    pinned_test_names: list[str] = []
                    for test in tests:
                        if not isinstance(test, Mapping):
                            blockers.append(
                                "CTest discovery returned a non-object test"
                            )
                            continue
                        name = test.get("name")
                        command = test.get("command")
                        if (
                            not isinstance(name, str)
                            or not isinstance(command, list)
                            or not command
                            or not isinstance(command[0], str)
                        ):
                            blockers.append("CTest discovery returned an invalid test")
                            continue
                        pinned_test_names.append(name)
                        if len(command) != 1:
                            blockers.append(
                                f"CTest command contains unexpected arguments: {name}"
                            )
                            continue
                        expected_command = _REQUIRED_CTEST_COMMANDS.get(name)
                        command_path = Path(command[0]).resolve()
                        if expected_command is None:
                            blockers.append(f"unexpected CTest test: {name}")
                            continue
                        expected_path = pinned_test_paths[name].resolve()
                        if command_path != expected_path:
                            blockers.append(
                                f"CTest command mismatch for {name}: "
                                f"expected={expected_path} actual={command_path}"
                            )
                    if set(pinned_test_names) != set(_REQUIRED_CTEST_COMMANDS):
                        blockers.append(
                            "CTest test identity mismatch: "
                            f"expected={sorted(_REQUIRED_CTEST_COMMANDS)} "
                            f"actual={sorted(pinned_test_names)}"
                        )
            if discovery_stderr:
                blockers.append("CTest discovery wrote to stderr")
    if not blockers:
        try:
            completed = command_runner(
                [
                    str(pinned_ctest),
                    "--test-dir",
                    str(pinned_root),
                    "--output-on-failure",
                ],
                cwd=str(pinned_root),
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                env=dict(SANITIZED_TEST_ENV),
            )
            returncode = int(completed.returncode)
            stdout = _as_text(completed.stdout)
            stderr = _as_text(completed.stderr)
        except Exception as exc:
            blockers.append(f"ctest execution failed: {exc}")
    combined = stdout + "\n" + stderr
    summaries = _CTEST_SUMMARY_RE.findall(combined)
    fractions = _CTEST_FRACTION_RE.findall(combined)
    required_count = str(_REQUIRED_CTEST_COUNT)
    exact_required = summaries == [("100", "0", required_count)] or (
        not summaries and fractions == [(required_count, required_count)]
    )
    if returncode is not None and returncode != 0:
        blockers.append(f"ctest exited with code {returncode}")
    if not exact_required:
        blockers.append(
            "ctest did not report an exact "
            f"{_REQUIRED_CTEST_COUNT}/{_REQUIRED_CTEST_COUNT} pass"
        )
    try:
        _, ctest_snapshot_after = _read_regular_file_snapshot(
            ctest_path,
            maximum=MAX_GITHUB_ARTIFACT_BYTES,
        )
    except OSError:
        ctest_snapshot_after = None
    if ctest_snapshot_after != ctest_snapshot:
        blockers.append("ctest executable changed during execution")

    direct_passed = 0
    if test_executables and not blockers:
        for name in sorted(test_executables):
            executable = test_executables[name]
            executable_path = Path(str(executable["path"]))
            try:
                direct = subprocess.run(
                    [str(pinned_test_paths[name])],
                    cwd=str(build_dir),
                    check=False,
                    capture_output=True,
                    text=False,
                    shell=False,
                    env=dict(SANITIZED_TEST_ENV),
                )
            except Exception as exc:
                blockers.append(
                    f"direct CTest command execution failed for {name}: {exc}"
                )
                continue
            direct_stdout = _as_bytes(direct.stdout)
            direct_stderr = _as_bytes(direct.stderr)
            direct_returncode = int(direct.returncode)
            executable["returncode"] = direct_returncode
            executable["stdout_sha256"] = hashlib.sha256(direct_stdout).hexdigest()
            executable["stderr_sha256"] = hashlib.sha256(direct_stderr).hexdigest()
            if direct_returncode != 0:
                blockers.append(
                    f"direct CTest command exited with code {direct_returncode}: {name}"
                )
            else:
                direct_passed += 1
            try:
                _, snapshot_after = _read_regular_file_snapshot(
                    executable_path,
                    maximum=MAX_GITHUB_ARTIFACT_BYTES,
                )
            except OSError:
                snapshot_after = None
            executable["sha256_after"] = (
                snapshot_after[4] if snapshot_after is not None else None
            )
            if snapshot_after != original_test_snapshots[name]:
                blockers.append(f"CTest command changed during execution: {name}")
    test_manifest_sha256 = (
        "sha256:" + hashlib.sha256(_canonical_json(test_executables)).hexdigest()
        if test_executables
        else None
    )
    registry_sha256 = (
        canonical_micromachine_ctest_registry(registry_paths).get("sha256")
        if registry_paths
        else None
    )
    result = _component_result(
        blockers,
        schema_version=PRE_LIVE_CTEST_EVIDENCE_SCHEMA_VERSION,
        argv=list(argv),
        discovery_argv=list(discovery_argv),
        ctest_executable=str(ctest_path),
        ctest_executable_sha256=ctest_sha256_before,
        returncode=returncode,
        passed=(
            _REQUIRED_CTEST_COUNT
            if exact_required and direct_passed == _REQUIRED_CTEST_COUNT
            else direct_passed
        ),
        total=_REQUIRED_CTEST_COUNT,
        failures=(
            0
            if exact_required and direct_passed == _REQUIRED_CTEST_COUNT
            else _REQUIRED_CTEST_COUNT - direct_passed
        ),
        test_names=sorted(test_names),
        test_executables={
            name: test_executables[name] for name in sorted(test_executables)
        },
        test_manifest_sha256=test_manifest_sha256,
        registry_sha256=registry_sha256,
        stdout_sha256=hashlib.sha256(stdout.encode()).hexdigest(),
        stderr_sha256=hashlib.sha256(stderr.encode()).hexdigest(),
    )
    pinned_directory.cleanup()
    return result


def _read_replay_ledger(path: Path) -> dict[str, object]:
    if not path.exists():
        return {
            "schema_version": REPLAY_LEDGER_SCHEMA_VERSION,
            "entries": {},
        }
    if path.is_symlink():
        raise ValueError("replay ledger must not be a symlink")
    file_stat = path.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("replay ledger is not a regular file")
    if file_stat.st_size > MAX_REPLAY_LEDGER_BYTES:
        raise ValueError("replay ledger exceeds the size limit")
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"replay ledger is malformed: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("replay ledger must contain an object")
    if payload.get("schema_version") != REPLAY_LEDGER_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported replay ledger schema: {payload.get('schema_version')!r}"
        )
    entries = payload.get("entries")
    if not isinstance(entries, Mapping):
        raise ValueError("replay ledger entries must be an object")
    normalized_entries: dict[str, object] = {}
    for digest, entry in entries.items():
        if not isinstance(digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            digest,
        ):
            raise ValueError("replay ledger contains an invalid digest key")
        if not isinstance(entry, Mapping):
            raise ValueError("replay ledger contains an invalid entry")
        consumed_at = entry.get("consumed_at")
        source_ids = entry.get("source_ids")
        if _parse_utc(consumed_at) is None or not isinstance(source_ids, Mapping):
            raise ValueError("replay ledger contains malformed provenance")
        normalized_entries[digest] = {
            "consumed_at": consumed_at,
            "source_ids": _json_safe_mapping(source_ids),
        }
    if len(normalized_entries) > MAX_REPLAY_ENTRIES:
        raise ValueError("replay ledger entry limit exceeded")
    return {
        "schema_version": REPLAY_LEDGER_SCHEMA_VERSION,
        "entries": normalized_entries,
    }


def _run_text(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    cwd: Path,
    preserve_whitespace: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    completed = runner(
        list(argv),
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        env=(dict(env) if env is not None else None),
    )
    stdout = _as_text(completed.stdout)
    if not preserve_whitespace:
        stdout = stdout.strip()
    return {
        "returncode": int(completed.returncode),
        "stdout": stdout,
        "stderr": _as_text(completed.stderr),
    }


def _git_head(root: Path, runner: CommandRunner) -> str | None:
    try:
        result = _run_text(
            runner,
            (TRUSTED_GIT_EXECUTABLE, "rev-parse", "HEAD"),
            cwd=root,
            env=SANITIZED_GIT_ENV,
        )
    except Exception:
        return None
    value = str(result["stdout"]).strip()
    if result["returncode"] != 0 or not _SHA40_RE.fullmatch(value):
        return None
    return value


def _attest_committed_file(
    path: Path,
    *,
    repository_root: Path,
    expected_commit: str,
    git_runner: CommandRunner,
) -> dict[str, object]:
    blockers: list[str] = []
    try:
        relative = path.absolute().relative_to(repository_root.absolute())
    except ValueError:
        return _component_result(
            ["file escapes the attested repository"],
            path=str(path),
            relative_path=None,
            sha256=None,
        )
    if _path_has_symlink_component(path, stop=repository_root):
        blockers.append("file path contains a symlink")
    try:
        working_bytes, _ = _read_regular_file_snapshot(
            path,
            maximum=MAX_PRODUCER_SOURCE_BYTES,
        )
    except OSError as exc:
        blockers.append(f"file is unreadable: {exc}")
        working_bytes = b""
    try:
        completed = git_runner(
            [
                TRUSTED_GIT_EXECUTABLE,
                "show",
                f"{expected_commit}:{relative.as_posix()}",
            ],
            cwd=str(repository_root),
            check=False,
            capture_output=True,
            text=False,
            shell=False,
            env=dict(SANITIZED_GIT_ENV),
        )
        if int(completed.returncode) != 0:
            raise ValueError("file is not tracked at the exact commit")
        committed_bytes = _as_bytes(completed.stdout)
    except (TypeError, ValueError) as exc:
        blockers.append(str(exc))
        committed_bytes = b""
    if working_bytes != committed_bytes:
        blockers.append("working file differs from the exact commit")
    return _component_result(
        blockers,
        path=str(path),
        relative_path=relative.as_posix(),
        sha256=(hashlib.sha256(working_bytes).hexdigest() if working_bytes else None),
        size_bytes=len(working_bytes),
    )


def _attest_committed_python_sources(
    module_relative: Path,
    *,
    repository_root: Path,
    expected_commit: str,
    git_runner: CommandRunner,
) -> dict[str, object]:
    """Bind the producer to its exact committed runtime inputs."""

    blockers: list[str] = []
    relative_paths = {module_relative.as_posix()}
    if module_relative == DETERMINISTIC_JOURNEY_MODULE_RELATIVE_PATH:
        relative_paths.add(
            DETERMINISTIC_JOURNEY_MANIFEST_RELATIVE_PATH.as_posix()
        )
    if module_relative.parts and module_relative.parts[0] == "starcraft_commander":
        try:
            completed = git_runner(
                [
                    TRUSTED_GIT_EXECUTABLE,
                    "ls-tree",
                    "-r",
                    "-z",
                    "--name-only",
                    expected_commit,
                    "--",
                    "starcraft_commander",
                ],
                cwd=str(repository_root),
                check=False,
                capture_output=True,
                text=False,
                shell=False,
                env=dict(SANITIZED_GIT_ENV),
            )
            if int(completed.returncode) != 0:
                raise ValueError("could not enumerate committed producer sources")
            for raw_path in _as_bytes(completed.stdout).split(b"\0"):
                if not raw_path:
                    continue
                relative = raw_path.decode("utf-8", errors="strict")
                if relative.endswith(".py"):
                    relative_paths.add(relative)
        except (OSError, UnicodeDecodeError, TypeError, ValueError) as exc:
            blockers.append(str(exc))

    files: list[dict[str, object]] = []
    for relative in sorted(relative_paths):
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or (
                relative_path.suffix != ".py"
                and relative_path
                != DETERMINISTIC_JOURNEY_MANIFEST_RELATIVE_PATH
            )
        ):
            blockers.append(f"invalid producer source path: {relative}")
            continue
        evidence = _attest_committed_file(
            repository_root / relative_path,
            repository_root=repository_root,
            expected_commit=expected_commit,
            git_runner=git_runner,
        )
        blockers.extend(_prefixed_blockers(f"source {relative}", evidence))
        files.append(
            {
                "path": evidence.get("path"),
                "relative_path": relative,
                "sha256": evidence.get("sha256"),
                "size_bytes": evidence.get("size_bytes"),
            }
        )
    digest = (
        "sha256:" + hashlib.sha256(_canonical_json(files)).hexdigest()
        if files and not blockers
        else None
    )
    return _component_result(
        blockers,
        repository_commit=expected_commit,
        files=files,
        digest=digest,
    )


def _path_has_symlink_component(
    path: Path,
    *,
    stop: Path | None = None,
) -> bool:
    current = path.absolute()
    stop_path = stop.absolute() if stop is not None else None
    while True:
        if os.path.lexists(current) and current.is_symlink():
            return True
        if stop_path is not None and current == stop_path:
            return False
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _artifact_state(path: Path) -> dict[str, object] | None:
    try:
        if path.is_symlink():
            return None
        file_stat = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(file_stat.st_mode):
        return None
    digest = _sha256_file(path)
    if digest is None:
        return None
    return {
        "sha256": digest,
        "size_bytes": file_stat.st_size,
        "stat_identity": (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_size,
            file_stat.st_mtime_ns,
            digest,
        ),
    }


def _stable_directory_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        file_stat = path.stat()
    except OSError:
        return None
    if not stat.S_ISDIR(file_stat.st_mode):
        return None
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_uid,
        stat.S_IMODE(file_stat.st_mode),
    )


def _write_private_snapshot_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o400)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_pinned_executable(
    path: Path,
) -> tuple[int, bytes, tuple[int, int, int, int, str]]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError("file is not regular")
        if stat.S_IMODE(file_stat.st_mode) & 0o111 == 0:
            raise OSError("file is not executable")
        payload, snapshot = _read_open_regular_file_snapshot(
            descriptor,
            maximum=MAX_PRODUCER_EXECUTABLE_BYTES,
        )
        return descriptor, payload, snapshot
    except Exception:
        os.close(descriptor)
        raise


def _run_pinned_command(
    command_runner: CommandRunner,
    argv: Sequence[str],
    *,
    executable_payload: bytes | None,
    executable_snapshot: tuple[int, int, int, int, str] | None,
    authenticated_python_sources: Mapping[str, bytes],
    state_dir: Path,
    cwd: str,
    timeout: float,
    inherited_fds: Sequence[int] = (),
) -> subprocess.CompletedProcess[Any]:
    if executable_payload is None or executable_snapshot is None:
        raise OSError("producer executable was not pinned")
    with tempfile.TemporaryDirectory(
        prefix=".producer-executable-",
        dir=state_dir,
    ) as executable_directory:
        executable_path = Path(executable_directory) / "producer"
        _write_private_executable_file(executable_path, executable_payload)
        snapshot_fd, snapshot_payload, snapshot_before = _open_pinned_executable(
            executable_path
        )
        try:
            if (
                snapshot_payload != executable_payload
                or snapshot_before[4] != executable_snapshot[4]
            ):
                raise OSError("private executable snapshot differs from pinned bytes")
            if (
                command_runner is subprocess.run
                and _is_isolated_python_command(argv)
            ):
                with tempfile.TemporaryDirectory(
                    prefix=".native-execution-",
                    dir=state_dir,
                ) as native_execution_root:
                    completed = _run_authenticated_python_exec(
                        argv,
                        executable_path=executable_path,
                        authenticated_python_sources=(
                            authenticated_python_sources
                        ),
                        native_execution_root=Path(native_execution_root),
                        cwd=cwd,
                        timeout=timeout,
                        inherited_fds=inherited_fds,
                    )
            else:
                completed = command_runner(
                    list(argv),
                    executable=str(executable_path),
                    cwd=cwd,
                    check=False,
                    capture_output=True,
                    text=False,
                    shell=False,
                    timeout=timeout,
                    env=dict(SANITIZED_PRODUCER_ENV),
                    pass_fds=tuple(inherited_fds),
                )
            _, snapshot_after = _read_open_regular_file_snapshot(
                snapshot_fd,
                maximum=MAX_PRODUCER_EXECUTABLE_BYTES,
            )
            _, path_after = _read_regular_file_snapshot(
                executable_path,
                maximum=MAX_PRODUCER_EXECUTABLE_BYTES,
            )
            if snapshot_after != snapshot_before or path_after != snapshot_before:
                raise OSError("private executable snapshot changed during execution")
            return completed
        finally:
            os.close(snapshot_fd)


def _is_isolated_python_command(argv: Sequence[str]) -> bool:
    return (
        len(argv) >= 8
        and list(argv[1:6]) == ["-I", "-B", "-S", "-c", ISOLATED_PYTHON_BOOTSTRAP]
        and Path(argv[6]).is_absolute()
        and not Path(argv[7]).is_absolute()
        and ".." not in Path(argv[7]).parts
    )


def _production_candidate_producer_blockers(producer_id: str) -> list[str]:
    if producer_id == PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID:
        return []
    return [
        "production candidate evidence requires producer_id="
        f"{PRE_LIVE_DETERMINISTIC_JOURNEY_PRODUCER_ID!r}; "
        f"received={producer_id!r}"
    ]


def _descriptor_execution_path(descriptor: int) -> str:
    path = Path("/dev/fd") / str(descriptor)
    descriptor_stat = os.fstat(descriptor)
    try:
        path_descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        raise OSError("descriptor execution path is unavailable") from exc
    try:
        path_stat = os.fstat(path_descriptor)
        if (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
        ) != (
            path_stat.st_dev,
            path_stat.st_ino,
        ):
            raise OSError("descriptor execution path changed identity")
    finally:
        os.close(path_descriptor)
    return str(path)


def _run_authenticated_python_exec(
    argv: Sequence[str],
    *,
    executable_path: Path,
    authenticated_python_sources: Mapping[str, bytes],
    native_execution_root: Path,
    cwd: str,
    timeout: float,
    inherited_fds: Sequence[int],
) -> subprocess.CompletedProcess[bytes]:
    """Execute authenticated Python bytes after discarding the parent heap."""

    relative_script = argv[7]
    main_source = authenticated_python_sources.get(relative_script)
    if main_source is None:
        raise RuntimeError("authenticated main Python source is missing")
    source_bundle = json.dumps(
        {
            "main_source": base64.b64encode(main_source).decode("ascii"),
            "schema_version": 1,
            "sources": [
                {
                    "path": relative,
                    "payload": base64.b64encode(payload).decode("ascii"),
                }
                for relative, payload in sorted(
                    authenticated_python_sources.items()
                )
            ],
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    bundle_path = (
        native_execution_root
        / f".authenticated-sources-{os.urandom(16).hex()}"
    )
    _write_private_snapshot_file(bundle_path, source_bundle)
    bundle_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        bundle_flags |= os.O_NOFOLLOW
    source_fd = os.open(bundle_path, bundle_flags)
    try:
        bundle_path.unlink()
        _, source_snapshot = _read_open_regular_file_snapshot(
            source_fd,
            maximum=len(source_bundle),
        )
        if (
            source_snapshot[2] != len(source_bundle)
            or source_snapshot[4] != hashlib.sha256(source_bundle).hexdigest()
        ):
            raise OSError("authenticated source bundle changed before exec")
        environment = dict(SANITIZED_PRODUCER_ENV)
        environment[PINNED_NATIVE_EXEC_ROOT_ENV] = str(
            native_execution_root
        )
        exec_argv = [
            argv[0],
            "-I",
            "-B",
            "-S",
            "-c",
            AUTHENTICATED_PYTHON_EXEC_BOOTSTRAP,
            str(source_fd),
            argv[6],
            relative_script,
            *argv[8:],
        ]
        process = subprocess.Popen(
            exec_argv,
            executable=str(executable_path),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            shell=False,
            env=environment,
            pass_fds=tuple(
                dict.fromkeys((*inherited_fds, source_fd))
            ),
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                list(argv),
                timeout,
                output=stdout,
                stderr=stderr,
            ) from exc
        return subprocess.CompletedProcess(
            list(argv),
            process.returncode,
            stdout,
            stderr,
        )
    finally:
        os.close(source_fd)


def _write_private_executable_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o500)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o500)
    finally:
        os.close(descriptor)


def _write_output_atomically(
    path: Path,
    payload: bytes,
    *,
    expected_parent_identity: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    parent_fd = os.open(path.parent, flags)
    temporary_name = f".{path.name}.{os.urandom(16).hex()}"
    try:
        parent_stat = os.fstat(parent_fd)
        parent_identity = (
            parent_stat.st_dev,
            parent_stat.st_ino,
            parent_stat.st_uid,
            stat.S_IMODE(parent_stat.st_mode),
        )
        if (
            expected_parent_identity is None
            or parent_identity != expected_parent_identity
        ):
            raise OSError("producer output parent changed before publication")
        try:
            current_stat = os.stat(
                path.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            current_stat = None
        if current_stat is not None and stat.S_ISLNK(current_stat.st_mode):
            raise OSError("producer output artifact became a symlink")
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            create_flags |= os.O_NOFOLLOW
        temporary_fd = os.open(
            temporary_name,
            create_flags,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(temporary_fd, payload[offset:])
            os.fsync(temporary_fd)
            temporary_stat = os.fstat(temporary_fd)
            published_identity = (
                temporary_stat.st_dev,
                temporary_stat.st_ino,
                temporary_stat.st_size,
                temporary_stat.st_mtime_ns,
                hashlib.sha256(payload).hexdigest(),
            )
        finally:
            os.close(temporary_fd)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_name = ""
        os.fsync(parent_fd)
        published_stat = os.stat(
            path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            published_stat.st_dev,
            published_stat.st_ino,
            published_stat.st_size,
            published_stat.st_mtime_ns,
        ) != published_identity[:4]:
            raise OSError("producer output identity changed during publication")
        return published_identity
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def _read_open_regular_file_snapshot(
    descriptor: int,
    *,
    maximum: int,
) -> tuple[bytes, tuple[int, int, int, int, str]]:
    stat_before = os.fstat(descriptor)
    if not stat.S_ISREG(stat_before.st_mode):
        raise OSError("file is not regular")
    if stat_before.st_size > maximum:
        raise OSError("file exceeds the size limit")
    chunks: list[bytes] = []
    offset = 0
    while offset <= maximum:
        chunk = os.pread(
            descriptor,
            min(1024 * 1024, maximum + 1 - offset),
            offset,
        )
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    payload = b"".join(chunks)
    if len(payload) > maximum:
        raise OSError("file exceeds the size limit")
    stat_after = os.fstat(descriptor)
    stable_before = (
        stat_before.st_dev,
        stat_before.st_ino,
        stat_before.st_size,
        stat_before.st_mtime_ns,
    )
    stable_after = (
        stat_after.st_dev,
        stat_after.st_ino,
        stat_after.st_size,
        stat_after.st_mtime_ns,
    )
    if stable_after != stable_before or len(payload) != stat_before.st_size:
        raise OSError("file changed while it was being read")
    digest = hashlib.sha256(payload).hexdigest()
    return payload, (*stable_before, digest)


def _read_regular_file_snapshot(
    path: Path,
    *,
    maximum: int,
) -> tuple[bytes, tuple[int, int, int, int, str]]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        return _read_open_regular_file_snapshot(descriptor, maximum=maximum)
    finally:
        os.close(descriptor)


def _write_foundation_evidence(output_path: Path | str) -> None:
    path = Path(output_path).absolute()
    if not path.parent.is_dir() or _path_has_symlink_component(path.parent):
        raise ValueError("foundation evidence parent is missing or linked")
    payload = (
        canonical_json_bytes(
            {
                "evidence_kind": "authenticated_pre_live_provenance_foundation",
                "producer_id": "provenance_qualification",
                "schema_version": PRE_LIVE_PROVENANCE_SCHEMA_VERSION,
            }
        )
        + b"\n"
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _main(argv: Sequence[str]) -> int:
    if len(argv) == 2 and argv[0] == "--emit-foundation-evidence":
        try:
            _write_foundation_evidence(argv[1])
        except (OSError, ValueError) as exc:
            print(f"foundation evidence generation failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if len(argv) == 4 and argv[0] == "--emit-github-actions-bundle":
        try:
            repository = os.environ["GITHUB_REPOSITORY"]
            repository_dir = os.environ["GITHUB_WORKSPACE"]
            expected_commit = os.environ["VOI_RELEASE_COMMIT"]
            workflow_ref = os.environ["GITHUB_WORKFLOW_REF"]
            workflow_sha = os.environ["GITHUB_WORKFLOW_SHA"]
            node_executable = os.environ["VOI_NODE_EXECUTABLE"]
            run_id = int(os.environ["GITHUB_RUN_ID"])
            run_attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
            token = os.environ["GITHUB_TOKEN"]
            api_base_url = os.environ.get(
                "GITHUB_API_URL",
                "https://api.github.com",
            )
        except (KeyError, ValueError) as exc:
            print(
                f"GitHub Actions bundle environment is invalid: {exc}",
                file=sys.stderr,
            )
            return 2
        if repository != AUTHORITATIVE_REPOSITORY:
            print(
                "GitHub Actions bundle repository is not authoritative: "
                f"{repository!r}",
                file=sys.stderr,
            )
            return 1
        report = emit_github_actions_pre_live_bundle(
            adapter=StdlibGitHubRESTAdapter(
                token=token,
                api_base_url=api_base_url,
            ),
            repository_dir=repository_dir,
            expected_commit=expected_commit,
            run_id=run_id,
            run_attempt=run_attempt,
            workflow_ref=workflow_ref,
            workflow_sha=workflow_sha,
            build_report_path=argv[2],
            expected_build_dir=argv[3],
            output_path=argv[1],
            node_executable=node_executable,
        )
        print(canonical_json_bytes(report).decode("utf-8"))
        return 0 if report["ok"] is True else 1

    print(
        "usage: micromachine_pre_live_provenance.py "
        "--emit-foundation-evidence OUTPUT\n"
        "   or: micromachine_pre_live_provenance.py "
        "--emit-github-actions-bundle OUTPUT BUILD_REPORT BUILD_DIR",
        file=sys.stderr,
    )
    return 2


def _component_result(
    blockers: Sequence[str],
    **values: object,
) -> dict[str, object]:
    result = dict(values)
    result["ok"] = not blockers
    result["status"] = "accepted" if not blockers else "blocked"
    result["blockers"] = list(blockers)
    return result


def _prefixed_blockers(
    prefix: str,
    component: Mapping[str, object],
) -> list[str]:
    blockers = component.get("blockers")
    if not isinstance(blockers, list):
        return [f"{prefix}: component did not provide blockers"]
    return [f"{prefix}: {blocker}" for blocker in blockers]


def _positive_id(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _single_repository_closing_issue(
    closing_issues: Sequence[Mapping[str, object]],
    *,
    repository: str,
    repository_id: int,
    blockers: list[str],
) -> tuple[int | None, int | None]:
    if len(closing_issues) != 1:
        blockers.append(
            "GitHub closingIssuesReferences must bind exactly one issue in "
            f"{repository}: actual={len(closing_issues)}"
        )
        return None, None
    candidate = closing_issues[0]
    if not isinstance(candidate, Mapping):
        blockers.append("GitHub closing issue reference must be an object")
        return None, None
    issue_id = _server_positive_id(
        candidate.get("databaseId"),
        "closing_issue.databaseId",
        blockers,
    )
    issue_number = _server_positive_id(
        candidate.get("number"),
        "closing_issue.number",
        blockers,
    )
    issue_repository = _mapping(candidate.get("repository"))
    _expect_server_value(
        issue_repository,
        "databaseId",
        repository_id,
        "closing_issue.repository",
        blockers,
    )
    observed_repository = issue_repository.get("nameWithOwner")
    if (
        not isinstance(observed_repository, str)
        or observed_repository.casefold() != repository.casefold()
    ):
        blockers.append(
            "closing_issue.repository.nameWithOwner mismatch: "
            f"expected={repository!r} actual={observed_repository!r}"
        )
    return issue_id, issue_number


def _server_positive_id(
    value: object,
    name: str,
    blockers: list[str],
) -> int | None:
    try:
        return _positive_id(value, name)
    except ValueError as exc:
        blockers.append(str(exc))
        return None


def _expect_server_value(
    record: Mapping[str, object],
    key: str,
    expected: object,
    label: str,
    blockers: list[str],
) -> None:
    actual = record.get(key)
    if actual != expected:
        blockers.append(
            f"{label}.{key} mismatch: expected={expected!r} actual={actual!r}"
        )


def _server_string(
    record: Mapping[str, object],
    key: str,
    label: str,
    blockers: list[str],
) -> str | None:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        blockers.append(f"{label}.{key} is missing")
        return None
    return value


def _server_utc(
    record: Mapping[str, object],
    key: str,
    label: str,
    blockers: list[str],
) -> datetime | None:
    value = record.get(key)
    parsed = _parse_utc(value)
    if parsed is None:
        blockers.append(f"{label}.{key} is missing or is not an exact UTC timestamp")
    return parsed


def _validate_workflow_pull_request_binding(
    value: object,
    *,
    label: str,
    pull_id: int | None,
    pull_number: int,
    expected_head_sha: str,
    expected_repository_id: int,
    blockers: list[str],
) -> None:
    if not isinstance(value, list) or len(value) != 1:
        blockers.append(f"{label} must contain exactly the selected pull request")
        return
    record = value[0]
    if not isinstance(record, Mapping):
        blockers.append(f"{label} contains a non-object pull request")
        return
    _expect_server_value(record, "id", pull_id, label, blockers)
    _expect_server_value(record, "number", pull_number, label, blockers)
    head = _mapping(record.get("head"))
    _expect_server_value(head, "sha", expected_head_sha, f"{label}.head", blockers)
    head_repository = _mapping(head.get("repo"))
    _expect_server_value(
        head_repository,
        "id",
        expected_repository_id,
        f"{label}.head.repo",
        blockers,
    )


def _workflow_run_matches_candidate(
    record: Mapping[str, object],
    *,
    workflow_id: int,
    workflow_path: str,
    pull_id: int | None,
    pull_number: int,
    head_sha: str,
    head_branch: str | None,
    repository_id: int,
) -> bool:
    try:
        _positive_id(record.get("id"), "workflow_run.id")
        _positive_id(record.get("run_number"), "workflow_run.run_number")
        _positive_id(record.get("run_attempt"), "workflow_run.run_attempt")
    except ValueError:
        return False
    if (
        record.get("workflow_id") != workflow_id
        or record.get("path") != workflow_path
        or record.get("event") != "pull_request"
        or record.get("head_sha") != head_sha
        or record.get("head_branch") != head_branch
    ):
        return False
    head_repository = _mapping(record.get("head_repository"))
    if head_repository.get("id") != repository_id:
        return False
    pull_requests = record.get("pull_requests")
    if not isinstance(pull_requests, list) or len(pull_requests) != 1:
        return False
    pull = pull_requests[0]
    if not isinstance(pull, Mapping):
        return False
    pull_head = _mapping(pull.get("head"))
    pull_repository = _mapping(pull_head.get("repo"))
    return (
        pull.get("id") == pull_id
        and pull.get("number") == pull_number
        and pull_head.get("sha") == head_sha
        and pull_repository.get("id") == repository_id
    )


def _eligible_workflow_runs(
    adapter: GitHubSourceAdapter,
    workflow_runs: Sequence[Mapping[str, object]],
    *,
    repository: str,
    current_run: Mapping[str, object],
    workflow_id: int,
    workflow_path: str,
    pull_id: int | None,
    pull_number: int,
    head_sha: str,
    head_branch: str | None,
    repository_id: int,
    blockers: list[str],
) -> list[Mapping[str, object]]:
    candidates: dict[int, Mapping[str, object]] = {}
    current_run_id = current_run.get("id")
    for summary in workflow_runs:
        if not _workflow_run_summary_matches_candidate(
            summary,
            workflow_id=workflow_id,
            workflow_path=workflow_path,
            head_sha=head_sha,
            head_branch=head_branch,
            repository_id=repository_id,
        ):
            continue
        try:
            run_id = _positive_id(summary.get("id"), "workflow_run.id")
        except ValueError as exc:
            blockers.append(f"listed workflow run is malformed: {exc}")
            continue
        if run_id == current_run_id:
            record = current_run
        else:
            try:
                record = adapter.get_workflow_run(repository, run_id)
            except Exception as exc:
                blockers.append(
                    f"listed workflow run {run_id} hydration failed: {exc}"
                )
                continue
        if (
            record.get("id") != run_id
            or record.get("run_number") != summary.get("run_number")
            or record.get("run_attempt") != summary.get("run_attempt")
        ):
            blockers.append(
                f"listed workflow run {run_id} hydration identity mismatch"
            )
            continue
        if not _workflow_run_matches_candidate(
            record,
            workflow_id=workflow_id,
            workflow_path=workflow_path,
            pull_id=pull_id,
            pull_number=pull_number,
            head_sha=head_sha,
            head_branch=head_branch,
            repository_id=repository_id,
        ):
            blockers.append(
                f"listed workflow run {run_id} failed direct candidate binding"
            )
            continue
        candidates[run_id] = record
    if _workflow_run_matches_candidate(
        current_run,
        workflow_id=workflow_id,
        workflow_path=workflow_path,
        pull_id=pull_id,
        pull_number=pull_number,
        head_sha=head_sha,
        head_branch=head_branch,
        repository_id=repository_id,
    ):
        current_id = current_run.get("id")
        if isinstance(current_id, int) and not isinstance(current_id, bool):
            candidates[current_id] = current_run
    return list(candidates.values())


def _workflow_run_summary_matches_candidate(
    record: Mapping[str, object],
    *,
    workflow_id: int,
    workflow_path: str,
    head_sha: str,
    head_branch: str | None,
    repository_id: int,
) -> bool:
    try:
        _positive_id(record.get("id"), "workflow_run.id")
        _positive_id(record.get("run_number"), "workflow_run.run_number")
        _positive_id(record.get("run_attempt"), "workflow_run.run_attempt")
    except ValueError:
        return False
    if (
        record.get("workflow_id") != workflow_id
        or record.get("path") != workflow_path
        or record.get("event") != "pull_request"
        or record.get("head_sha") != head_sha
        or record.get("head_branch") != head_branch
    ):
        return False
    return _mapping(record.get("head_repository")).get("id") == repository_id


def _workflow_run_order_key(record: Mapping[str, object]) -> tuple[int, int, int]:
    return (
        int(record["run_number"]),
        int(record["run_attempt"]),
        int(record["id"]),
    )


def _validate_comparison_ancestry(
    comparison: Mapping[str, object],
    *,
    base_sha: str | None,
    head_sha: str,
    blockers: list[str],
) -> None:
    _expect_server_value(
        _mapping(comparison.get("base_commit")),
        "sha",
        base_sha,
        "comparison.base_commit",
        blockers,
    )
    _expect_server_value(
        _mapping(comparison.get("merge_base_commit")),
        "sha",
        base_sha,
        "comparison.merge_base_commit",
        blockers,
    )
    status = comparison.get("status")
    if status != "ahead":
        blockers.append(
            "pull request head must descend from the authenticated main base: "
            f"status={status!r}"
        )
    ahead_by = comparison.get("ahead_by")
    if (
        isinstance(ahead_by, bool)
        or not isinstance(ahead_by, int)
        or ahead_by <= 0
    ):
        blockers.append(
            "comparison.ahead_by must be a positive integer: "
            f"actual={ahead_by!r}"
        )
    behind_by = comparison.get("behind_by")
    if (
        isinstance(behind_by, bool)
        or not isinstance(behind_by, int)
        or behind_by != 0
    ):
        blockers.append(
            "comparison.behind_by must be zero: "
            f"actual={behind_by!r}"
        )
    commits = comparison.get("commits")
    if not isinstance(commits, list):
        blockers.append("comparison.commits must be a list")
    elif isinstance(ahead_by, int) and not isinstance(ahead_by, bool):
        if 0 < ahead_by <= 250:
            if len(commits) != ahead_by:
                blockers.append(
                    "comparison commit count mismatch: "
                    f"ahead_by={ahead_by} commits={len(commits)}"
                )
            elif not commits or _mapping(commits[-1]).get("sha") != head_sha:
                blockers.append(
                    "comparison commits do not terminate at the pull-request head: "
                    f"expected={head_sha!r}"
                )


def _validate_workflow_execution_identity(
    *,
    repository: str,
    workflow_path: str | None,
    pull_number: int,
    pull_head_ref: str | None,
    workflow_ref: str | None,
    workflow_sha: str | None,
    blockers: list[str],
) -> str | None:
    if workflow_path is None or pull_head_ref is None:
        blockers.append(
            "workflow execution identity cannot be checked without path and head ref"
        )
        return None
    expected_prefix = f"{repository}/{workflow_path}@"
    allowed_refs = {
        f"refs/pull/{pull_number}/merge",
        f"refs/heads/{pull_head_ref}",
        f"refs/heads/{AUTHORITATIVE_BASE_BRANCH}",
    }
    if (
        not isinstance(workflow_ref, str)
        or not workflow_ref.startswith(expected_prefix)
        or workflow_ref.removeprefix(expected_prefix) not in allowed_refs
    ):
        blockers.append(
            "workflow execution ref is not a runner-authenticated candidate ref: "
            f"actual={workflow_ref!r}"
        )
        workflow_git_ref = None
    else:
        workflow_git_ref = workflow_ref.removeprefix(expected_prefix)
    if not isinstance(workflow_sha, str) or not _SHA40_RE.fullmatch(workflow_sha):
        blockers.append(
            "workflow execution SHA is not an exact runner-authenticated commit"
        )
    return workflow_git_ref


def _validate_workflow_reference_target(
    adapter: GitHubSourceAdapter,
    *,
    repository: str,
    workflow_git_ref: str | None,
    workflow_sha: str | None,
    blockers: list[str],
) -> None:
    if workflow_git_ref is None or not isinstance(workflow_sha, str):
        return
    if _SHA40_RE.fullmatch(workflow_sha) is None:
        return
    try:
        reference = adapter.get_git_reference(
            repository,
            ref=workflow_git_ref,
        )
    except Exception as exc:
        blockers.append(
            "workflow Git reference lookup failed closed: "
            f"ref={workflow_git_ref!r} error={exc}"
        )
        return
    observed_ref, observed_sha, target_type = _git_reference_identity(reference)
    if observed_ref != workflow_git_ref:
        blockers.append(
            "workflow Git reference identity mismatch: "
            f"expected={workflow_git_ref!r} actual={observed_ref!r}"
        )
    if target_type != "commit":
        blockers.append(
            "workflow Git reference does not target a commit: "
            f"actual={target_type!r}"
        )
    if observed_sha != workflow_sha:
        blockers.append(
            "workflow SHA differs from the authenticated Git reference target: "
            f"expected={observed_sha!r} actual={workflow_sha!r}"
        )


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _git_reference_identity(
    payload: Mapping[str, object],
) -> tuple[str | None, str | None, str | None]:
    ref = payload.get("ref")
    target = _mapping(payload.get("object"))
    sha = target.get("sha")
    target_type = target.get("type")
    return (
        ref if isinstance(ref, str) else None,
        sha if isinstance(sha, str) else None,
        target_type if isinstance(target_type, str) else None,
    )


def _normalize_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence of arguments")
    normalized: list[str] = []
    for value in argv:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("argv contains an invalid argument")
        normalized.append(value)
    return tuple(normalized)


def _json_safe_mapping(value: Mapping[str, object]) -> dict[str, object]:
    payload = json.loads(_canonical_json(dict(value)))
    if not isinstance(payload, dict):
        raise ValueError("mapping did not serialize to a JSON object")
    return cast(dict[str, object], payload)


def _reject_duplicate_json_object_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not supported: {value}")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str | None:
    try:
        if path.is_symlink():
            return None
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _as_bytes(value: object) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    raise TypeError(f"command output must be bytes or text, not {type(value)!r}")


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    raise TypeError(f"command output must be bytes or text, not {type(value)!r}")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
