"""Local macOS bootstrap for the MyProxy-backed commander cockpit."""

from __future__ import annotations

import argparse
import ast
import ctypes
import errno
import json
import os
import plistlib
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit

from starcraft_commander.llm_interpreter import (
    LLM_TIMEOUT_SECONDS_ENV_VAR,
    MYPROXY_API_KEY_ENV_VAR,
    MYPROXY_MODEL_ENV_VAR,
    MYPROXY_OPENAI_BASE_URL_ENV_VAR,
)
from starcraft_commander.micromachine_build_identity import (
    MicroMachineBuildIdentityConfig,
    build_runtime_install_provenance,
    micromachine_build_ready as strict_micromachine_build_ready,
    resolve_runtime_repository_identity,
    write_runtime_install_provenance,
)
from starcraft_commander.sc2_launch_contract import (
    DEFAULT_SC2_API_PORT,
    REQUIRED_SC2_BASE,
)


DEFAULT_COCKPIT_PORT = 8350
DEFAULT_BUILD_JOBS = 2
DEFAULT_MYPROXY_TIMEOUT_SECONDS = 25
SC2_LAUNCH_RECEIPT_FILE = "sc2-launch-receipt.json"
SC2_LAUNCH_RECEIPT_MAX_AGE_SECONDS = 180
LAUNCHSERVICES_REGISTER_PATH = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
MAX_SC2_LAUNCH_RECEIPT_BYTES = 64 * 1024
MAX_LOCAL_SECRET_BYTES = 16 * 1024
MYPROXY_KEY_ALIASES = (MYPROXY_API_KEY_ENV_VAR, "CODEX_MYPROXY_API_KEY")
LOCAL_RUNTIME_DIR_NAME = "runtime"
LOCAL_RUNTIME_SOURCE_DIRS = (
    "broodwar_commander",
    "integrations",
    "scripts",
    "starcraft_commander",
    "toycraft_commander",
)
LOCAL_RUNTIME_SOURCE_FILES = (
    "LICENSE",
    "MANIFEST.in",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
)


def _clone_file(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> bool:
    """Clone one file on APFS without allocating a second full data copy."""

    if sys.platform != "darwin":
        return False
    try:
        clonefile = ctypes.CDLL(None, use_errno=True).clonefile
    except (AttributeError, OSError):
        return False
    clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    clonefile.restype = ctypes.c_int
    if clonefile(os.fsencode(source), os.fsencode(destination), 0) == 0:
        return True
    error_number = ctypes.get_errno()
    if error_number in {
        errno.EACCES,
        errno.EINVAL,
        errno.ENOSYS,
        errno.ENOTSUP,
        errno.EPERM,
        errno.EXDEV,
    }:
        return False
    raise OSError(
        error_number,
        os.strerror(error_number),
        os.fspath(source),
        os.fspath(destination),
    )


def _runtime_copy_file(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
) -> str:
    """Prefer copy-on-write clones and retain a portable copy fallback."""

    if _clone_file(source, destination):
        return os.fspath(destination)
    return shutil.copy2(source, destination)


@dataclass(frozen=True)
class MyProxySettings:
    """Non-secret MyProxy settings read from the local Codex configuration."""

    model: str
    base_url: str
    env_key: str


@dataclass(frozen=True)
class LocalCockpitPaths:
    """Filesystem paths used by the idempotent local bootstrap."""

    repo_root: Path
    venv_python: Path
    build_script: Path
    micromachine_binary: Path
    build_identity_report: Path
    state_dir: Path
    log_dir: Path
    secret_file: Path

    @classmethod
    def from_repo_root(cls, repo_root: Path) -> "LocalCockpitPaths":
        root = repo_root.resolve()
        state_dir = Path(
            os.environ.get(
                "VOI_LOCAL_COCKPIT_STATE_DIR",
                str(Path.home() / "Library" / "Application Support" / "voiStarcraft2"),
            )
        ).expanduser()
        log_dir = Path(
            os.environ.get(
                "VOI_LOCAL_COCKPIT_LOG_DIR",
                str(Path.home() / "Library" / "Logs" / "voiStarcraft2"),
            )
        ).expanduser()
        build_dir = Path(
            os.environ.get(
                "MICROMACHINE_BUILD_DIR",
                "/private/tmp/voi-micromachine-runtime/MicroMachine/build-latest-api",
            )
        ).expanduser()
        return cls(
            repo_root=root,
            venv_python=root / ".venv" / "bin" / "python",
            build_script=(
                root / "integrations" / "micromachine" / "scripts" / "build_macos_local.sh"
            ),
            micromachine_binary=build_dir / "bin" / "MicroMachine",
            build_identity_report=build_dir / "voi_build_identity.json",
            state_dir=state_dir,
            log_dir=log_dir,
            secret_file=state_dir / "myproxy.key",
        )

    @property
    def sc2_launch_receipt(self) -> Path:
        return self.state_dir / SC2_LAUNCH_RECEIPT_FILE


def resolve_required_sc2_executable(
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the exact supported SC2 binary without falling back to stale bases."""

    values = environment if environment is not None else os.environ
    configured = str(
        values.get("SC2_ROOT", "") or values.get("SC2PATH", "") or ""
    ).strip()
    roots = []
    if configured:
        roots.append(Path(configured).expanduser())
    roots.extend(
        (
            Path.home() / "Desktop" / "StarCraft2" / "StarCraft II",
            Path("/Applications/StarCraft II"),
            Path.home() / "Applications" / "StarCraft II",
        )
    )
    unique_roots = list(dict.fromkeys(root.resolve() for root in roots))
    for root in unique_roots:
        executable = (
            root
            / "Versions"
            / f"Base{REQUIRED_SC2_BASE}"
            / "SC2.app"
            / "Contents"
            / "MacOS"
            / "SC2"
        )
        if os.access(executable, os.X_OK):
            return executable
    root = unique_roots[0]
    return (
        root
        / "Versions"
        / f"Base{REQUIRED_SC2_BASE}"
        / "SC2.app"
        / "Contents"
        / "MacOS"
        / "SC2"
    )


def _sc2_receipt_process_matches(
    pid: int,
    executable: Path,
    port: int,
) -> bool:
    executable_result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "comm="],
        check=False,
        capture_output=True,
        text=True,
    )
    command_result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    if executable_result.returncode != 0 or command_result.returncode != 0:
        return False
    observed_executable = executable_result.stdout.strip()
    command_line = command_result.stdout.strip()
    if not observed_executable or not command_line.startswith(observed_executable):
        return False
    arguments = command_line[len(observed_executable) :].split()
    if not arguments:
        return False
    return bool(
        os.path.realpath(observed_executable) == os.path.realpath(executable)
        and "-port" in arguments
        and str(port) in arguments
        and "-listen" in arguments
        and "127.0.0.1" in arguments
    )


def read_sc2_launch_receipt(
    path: Path,
    nonce: str,
    *,
    now_unix: float | None = None,
    require_live_process: bool = True,
) -> dict[str, object]:
    """Read and validate one fresh native SC2 launch receipt."""

    expected_nonce = nonce.strip()
    if not expected_nonce:
        raise RuntimeError("SC2 native launch receipt nonce is missing.")
    candidate = path.expanduser()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as error:
        raise RuntimeError("SC2 native launch receipt is unavailable.") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("SC2 native launch receipt is not a regular file.")
        if metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise RuntimeError("SC2 native launch receipt ownership is invalid.")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise RuntimeError("SC2 native launch receipt permissions must be 0600.")
        payload = os.read(descriptor, MAX_SC2_LAUNCH_RECEIPT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_SC2_LAUNCH_RECEIPT_BYTES:
        raise RuntimeError("SC2 native launch receipt is too large.")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("SC2 native launch receipt is invalid JSON.") from error
    if not isinstance(document, dict):
        raise RuntimeError("SC2 native launch receipt must be a JSON object.")
    if str(document.get("nonce", "") or "") != expected_nonce:
        raise RuntimeError("SC2 native launch receipt nonce does not match.")
    created_at_unix_ms = document.get("created_at_unix_ms")
    if (
        not isinstance(created_at_unix_ms, (int, float))
        or isinstance(created_at_unix_ms, bool)
    ):
        raise RuntimeError("SC2 native launch receipt timestamp is missing.")
    now = time.time() if now_unix is None else float(now_unix)
    age_seconds = now - (float(created_at_unix_ms) / 1000.0)
    if age_seconds < -5 or age_seconds > SC2_LAUNCH_RECEIPT_MAX_AGE_SECONDS:
        raise RuntimeError("SC2 native launch receipt is stale.")
    bootstrap_true_fields = (
        "process_created",
        "api_ready",
        "window_created",
        "window_onscreen",
    )
    missing = [
        field_name
        for field_name in bootstrap_true_fields
        if document.get(field_name) is not True
    ]
    if document.get("screen_locked") is not False:
        missing.append("screen_unlocked")
    if missing:
        raise RuntimeError(
            "SC2 visible launch proof is incomplete: " + ", ".join(missing)
        )
    if document.get("accepted") is not True:
        raise RuntimeError("SC2 native launch was not accepted.")
    if document.get("port") != DEFAULT_SC2_API_PORT:
        raise RuntimeError("SC2 native launch receipt uses an unexpected port.")
    if document.get("base") != REQUIRED_SC2_BASE:
        raise RuntimeError("SC2 native launch receipt uses an unsupported base.")
    executable = resolve_required_sc2_executable()
    receipt_executable = Path(
        str(document.get("executable_path", "") or "")
    ).expanduser()
    if os.path.realpath(receipt_executable) != os.path.realpath(executable):
        raise RuntimeError("SC2 native launch receipt executable does not match.")
    pid = document.get("pid")
    if type(pid) is not int or pid <= 1:
        raise RuntimeError("SC2 native launch receipt PID is invalid.")
    if require_live_process and not _sc2_receipt_process_matches(
        pid,
        executable,
        DEFAULT_SC2_API_PORT,
    ):
        raise RuntimeError("SC2 native launch receipt process is no longer live.")
    return document


def _strip_toml_comment(line: str) -> str:
    quote = ""
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character == "#":
            return line[:index]
    return line


def _parse_toml_string(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or cleaned[0] not in {"'", '"'}:
        return ""
    try:
        parsed = ast.literal_eval(cleaned)
    except (SyntaxError, ValueError):
        return ""
    return parsed if isinstance(parsed, str) else ""


def read_codex_myproxy_settings(config_path: Path) -> MyProxySettings:
    """Read only the active provider, model, URL, and key alias from Codex TOML."""

    try:
        payload = config_path.expanduser().read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError("Codex local configuration is not readable.") from error

    section = ""
    top_level: dict[str, str] = {}
    provider_values: dict[str, str] = {}
    for raw_line in payload.splitlines():
        line = _strip_toml_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = _parse_toml_string(raw_value)
        if not value:
            continue
        if not section:
            top_level[key] = value
        elif section == "model_providers.myproxy":
            provider_values[key] = value

    if top_level.get("model_provider", "").strip().lower() != "myproxy":
        raise RuntimeError("Codex is not configured to use the MyProxy provider.")
    model = top_level.get("model", "").strip()
    base_url = provider_values.get("base_url", "").strip()
    env_key = provider_values.get("env_key", "").strip() or MYPROXY_API_KEY_ENV_VAR
    if not model or not base_url:
        raise RuntimeError("Codex MyProxy model or endpoint is not configured.")
    parsed_url = urlsplit(base_url)
    if parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc:
        raise RuntimeError("Codex MyProxy endpoint is not a valid HTTP URL.")
    if parsed_url.scheme == "http" and parsed_url.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Remote MyProxy endpoints must use HTTPS.")
    return MyProxySettings(model=model, base_url=base_url, env_key=env_key)


def _validate_secret_metadata(metadata: os.stat_result, path: Path) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"Local MyProxy secret is not a regular file: {path}")
    if metadata.st_uid != os.getuid():
        raise RuntimeError(f"Local MyProxy secret is not owned by the current user: {path}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RuntimeError(f"Local MyProxy secret permissions must be 0600: {path}")
    if metadata.st_nlink != 1:
        raise RuntimeError(f"Local MyProxy secret must have exactly one link: {path}")


def read_local_secret(path: Path) -> str:
    """Read one owner-only secret file without following symbolic links."""

    candidate = path.expanduser()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except FileNotFoundError:
        return ""
    except OSError as error:
        raise RuntimeError(f"Local MyProxy secret is not readable: {candidate}") from error
    try:
        metadata = os.fstat(descriptor)
        _validate_secret_metadata(metadata, candidate)
        payload = os.read(descriptor, MAX_LOCAL_SECRET_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_LOCAL_SECRET_BYTES:
        raise RuntimeError("Local MyProxy secret exceeds the supported size.")
    try:
        secret = payload.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError("Local MyProxy secret is not valid UTF-8.") from error
    if not secret:
        raise RuntimeError("Local MyProxy secret is empty.")
    return secret


def store_local_secret(secret: str, path: Path) -> None:
    """Atomically store one owner-only secret outside the source checkout."""

    cleaned = secret.strip()
    if not cleaned:
        raise RuntimeError("No MyProxy key is available to store.")
    payload = (cleaned + "\n").encode("utf-8")
    if len(payload) > MAX_LOCAL_SECRET_BYTES:
        raise RuntimeError("MyProxy key exceeds the supported size.")

    candidate = path.expanduser()
    directory = candidate.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory_metadata = directory.lstat()
    if not stat.S_ISDIR(directory_metadata.st_mode) or directory.is_symlink():
        raise RuntimeError(f"Local cockpit state path is not a regular directory: {directory}")
    if directory_metadata.st_uid != os.getuid():
        raise RuntimeError(f"Local cockpit state path has the wrong owner: {directory}")
    directory.chmod(0o700)
    if candidate.exists() or candidate.is_symlink():
        existing = candidate.lstat()
        if not stat.S_ISREG(existing.st_mode):
            raise RuntimeError(f"Refusing to replace non-regular secret path: {candidate}")
        if existing.st_uid != os.getuid():
            raise RuntimeError(f"Refusing to replace secret owned by another user: {candidate}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".myproxy.key.",
        dir=directory,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, candidate)
        candidate.chmod(0o600)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    if read_local_secret(candidate) != cleaned:
        raise RuntimeError("Local MyProxy secret verification failed.")


def resolve_myproxy_key(
    settings: MyProxySettings,
    environment: Mapping[str, str],
    *,
    local_secret_path: Path | None = None,
) -> str:
    """Resolve the secret from process environment first, then local storage."""

    candidates = (settings.env_key, *MYPROXY_KEY_ALIASES)
    for variable in dict.fromkeys(candidates):
        secret = environment.get(variable, "")
        if secret.strip():
            return secret.strip()
    if local_secret_path is not None:
        secret = read_local_secret(local_secret_path)
        if secret.strip():
            return secret.strip()
    raise RuntimeError(
        "MyProxy API key is missing from the current environment and local cockpit storage."
    )


def child_environment(
    settings: MyProxySettings,
    secret: str,
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Return the web process environment without persisting private values."""

    result = dict(environment)
    result[MYPROXY_MODEL_ENV_VAR] = settings.model
    result[MYPROXY_OPENAI_BASE_URL_ENV_VAR] = settings.base_url
    result[MYPROXY_API_KEY_ENV_VAR] = secret
    if settings.env_key:
        result[settings.env_key] = secret
    result.setdefault("BUILD_JOBS", str(DEFAULT_BUILD_JOBS))
    result.setdefault(
        LLM_TIMEOUT_SECONDS_ENV_VAR,
        str(DEFAULT_MYPROXY_TIMEOUT_SECONDS),
    )
    return result


def _python_has_openai(python: Path) -> bool:
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('openai') else 1)",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _python_has_pip(python: Path) -> bool:
    completed = subprocess.run(
        [str(python), "-m", "pip", "--version"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _find_python_310() -> str:
    candidates = [
        sys.executable,
        "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10",
        shutil.which("python3") or "",
    ]
    for candidate in dict.fromkeys(candidates):
        if not candidate:
            continue
        completed = subprocess.run(
            [
                candidate,
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            return candidate
    raise RuntimeError("Python 3.10 or newer is required.")


def ensure_python_environment(paths: LocalCockpitPaths) -> None:
    """Create the venv if needed and install the declared LLM dependencies."""

    if not paths.venv_python.is_file():
        host_python = _find_python_310()
        subprocess.run(
            [host_python, "-m", "venv", str(paths.venv_python.parents[1])],
            cwd=paths.repo_root,
            check=True,
        )
    if not _python_has_pip(paths.venv_python):
        subprocess.run(
            [str(paths.venv_python), "-m", "ensurepip", "--upgrade"],
            cwd=paths.repo_root,
            check=True,
        )
    if _python_has_openai(paths.venv_python):
        return
    subprocess.run(
        [
            str(paths.venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-e",
            ".[llm]",
        ],
        cwd=paths.repo_root,
        check=True,
    )


def micromachine_build_ready(paths: LocalCockpitPaths) -> bool:
    """Return whether the binary and receipt match all current build inputs."""

    if not os.access(paths.micromachine_binary, os.X_OK):
        return False
    build_dir = paths.micromachine_binary.parent.parent
    micromachine_dir = build_dir.parent
    s2client_dir = micromachine_dir.parent / "s2client-api"
    return strict_micromachine_build_ready(
        MicroMachineBuildIdentityConfig(
            micromachine_dir=micromachine_dir,
            s2client_dir=s2client_dir,
            micromachine_build_dir=build_dir,
            s2client_build_dir=s2client_dir / "build-latest",
        ),
        paths.build_identity_report,
    )


def ensure_micromachine_build(
    paths: LocalCockpitPaths,
    *,
    force: bool = False,
    build_jobs: int = DEFAULT_BUILD_JOBS,
) -> None:
    """Build the patched runtime only when no admitted local build is present."""

    if not force and micromachine_build_ready(paths):
        return
    environment = os.environ.copy()
    environment["BUILD_JOBS"] = str(max(1, int(build_jobs)))
    subprocess.run(
        [str(paths.build_script)],
        cwd=paths.repo_root,
        env=environment,
        check=True,
    )
    if not micromachine_build_ready(paths):
        raise RuntimeError("MicroMachine build completed without a passing identity report.")


def cockpit_url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def cockpit_is_ready(port: int, timeout: float = 1.0) -> bool:
    request = urllib.request.Request(
        cockpit_url(port) + "/api/llm",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return False
            document = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return (
        isinstance(document, dict)
        and document.get("configured") is True
        and document.get("available") is True
    )


def _port_is_bound(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.25)
        return client.connect_ex(("127.0.0.1", int(port))) == 0


def _stop_owned_cockpit(
    paths: LocalCockpitPaths,
    port: int | None,
    *,
    wait_seconds: float = 5.0,
) -> bool:
    """Stop only the cockpit recorded by this bootstrap.

    Passing ``None`` stops the recorded process regardless of which localhost
    port an older app version selected.
    """

    pid_path = paths.state_dir / "cockpit.pid"
    try:
        metadata = pid_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            return False
        pid = int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError):
        return False
    if pid <= 1:
        return False

    completed = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
        return True
    command = completed.stdout.strip()
    expected_markers = [
        "-m starcraft_commander.web_gui",
        f"--micromachine-cwd {paths.repo_root}",
    ]
    if port is not None:
        expected_markers.append(f"--port {int(port)}")
    if not all(marker in command for marker in expected_markers):
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return False
    deadline = time.monotonic() + max(0.25, wait_seconds)
    while time.monotonic() < deadline:
        if port is not None and not _port_is_bound(port):
            try:
                pid_path.unlink()
            except FileNotFoundError:
                pass
            return True
        if port is None:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                try:
                    pid_path.unlink()
                except FileNotFoundError:
                    pass
                return True
            except OSError:
                return False
        time.sleep(0.1)
    return False


def _stop_owned_app(
    paths: LocalCockpitPaths,
    app_path: Path,
    *,
    wait_seconds: float = 5.0,
) -> bool:
    """Stop only the app instance recorded by the installed launcher."""

    pid_path = paths.state_dir / "app.pid"
    try:
        metadata = pid_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            return False
        pid = int(pid_path.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        return True
    except (OSError, UnicodeError, ValueError):
        return False
    if pid <= 1:
        return False

    completed = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "comm="],
        check=False,
        capture_output=True,
        text=True,
    )
    expected_executable = str(
        app_path.resolve()
        / "Contents"
        / "MacOS"
        / "voiStarcraft2"
    )
    if completed.returncode != 0:
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
        return True
    if os.path.realpath(completed.stdout.strip()) != os.path.realpath(
        expected_executable
    ):
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        return False
    deadline = time.monotonic() + max(0.25, wait_seconds)
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            try:
                pid_path.unlink()
            except FileNotFoundError:
                pass
            return True
        except OSError:
            return False
        time.sleep(0.1)
    return False


def start_cockpit(
    paths: LocalCockpitPaths,
    environment: Mapping[str, str],
    *,
    port: int = DEFAULT_COCKPIT_PORT,
    sc2_launch_receipt: Path | None = None,
    wait_seconds: float = 45.0,
) -> str:
    """Start one detached cockpit process and wait for its health endpoint."""

    url = cockpit_url(port)
    if cockpit_is_ready(port):
        return url
    if _port_is_bound(port) and not _stop_owned_cockpit(paths, port):
        raise RuntimeError(f"Port {port} is already used by another local process.")

    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = paths.log_dir / "cockpit.log"
    pid_path = paths.state_dir / "cockpit.pid"
    child_env = dict(environment)
    child_env["VOI_SC2_LAUNCH_RECEIPT"] = str(
        (sc2_launch_receipt or paths.sc2_launch_receipt).expanduser()
    )
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                str(paths.venv_python),
                "-u",
                "-m",
                "starcraft_commander.web_gui",
                "--dry-run",
                "--port",
                str(int(port)),
                "--micromachine-script",
                str(
                    paths.repo_root
                    / "integrations"
                    / "micromachine"
                    / "scripts"
                    / "smoke_macos_local.sh"
                ),
                "--micromachine-cwd",
                str(paths.repo_root),
            ],
            cwd=paths.repo_root,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
    pid_path.write_text(f"{process.pid}\n", encoding="ascii")
    pid_path.chmod(0o600)

    deadline = time.monotonic() + max(1.0, wait_seconds)
    while time.monotonic() < deadline:
        if cockpit_is_ready(port):
            return url
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"Commander cockpit exited during startup with status {return_code}."
            )
        time.sleep(0.25)
    process.terminate()
    raise RuntimeError("Commander cockpit did not become ready before the timeout.")


def _runtime_copy_ignore(_directory: str, names: Sequence[str]) -> set[str]:
    return {
        name
        for name in names
        if name in {"__pycache__", ".DS_Store"}
        or name.endswith((".pyc", ".pyo"))
    }


def _remove_editable_install_metadata(runtime_root: Path) -> None:
    site_packages_roots = tuple(
        (runtime_root / ".venv" / "lib").glob("python*/site-packages")
    )
    if len(site_packages_roots) != 1:
        raise RuntimeError("Installed runtime Python site-packages is unavailable.")
    site_packages = site_packages_roots[0]
    for pattern in ("__editable__*", "voistarcraft2-*.dist-info"):
        for path in site_packages.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def _verify_local_runtime(runtime_root: Path) -> None:
    python = runtime_root / ".venv" / "bin" / "python"
    smoke_script = (
        runtime_root
        / "integrations"
        / "micromachine"
        / "scripts"
        / "smoke_macos_local.sh"
    )
    if not os.access(python, os.X_OK):
        raise RuntimeError("Installed runtime Python is not executable.")
    if not os.access(smoke_script, os.X_OK):
        raise RuntimeError("Installed MicroMachine launcher is not executable.")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(runtime_root)
    completed = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import pathlib, starcraft_commander, openai; "
                "root = pathlib.Path(__import__('sys').argv[1]).resolve(); "
                "loaded = pathlib.Path(starcraft_commander.__file__).resolve(); "
                "raise SystemExit(0 if root in loaded.parents else 1)"
            ),
            str(runtime_root),
        ],
        cwd=runtime_root,
        env=environment,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise RuntimeError("Installed runtime import verification failed.")


def install_local_runtime(
    repo_root: Path,
    *,
    destination: Path | None = None,
) -> Path:
    """Install an owner-local runtime that does not depend on the source checkout."""

    source_root = repo_root.resolve()
    runtime_root = (
        destination.expanduser()
        if destination is not None
        else (
            Path.home()
            / "Library"
            / "Application Support"
            / "voiStarcraft2"
            / LOCAL_RUNTIME_DIR_NAME
        )
    )
    parent = runtime_root.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent.chmod(0o700)

    required = (
        *(source_root / name for name in LOCAL_RUNTIME_SOURCE_DIRS),
        *(source_root / name for name in LOCAL_RUNTIME_SOURCE_FILES),
        source_root / ".venv",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(
            "Local runtime source is incomplete: " + ", ".join(missing)
        )

    staging = Path(tempfile.mkdtemp(prefix=".runtime.", dir=parent))
    backup: Path | None = None
    try:
        for name in LOCAL_RUNTIME_SOURCE_DIRS:
            shutil.copytree(
                source_root / name,
                staging / name,
                symlinks=True,
                ignore=_runtime_copy_ignore,
                copy_function=_runtime_copy_file,
            )
        for name in LOCAL_RUNTIME_SOURCE_FILES:
            shutil.copy2(source_root / name, staging / name)
        licenses = source_root / "LICENSES"
        if licenses.is_dir():
            shutil.copytree(
                licenses,
                staging / "LICENSES",
                symlinks=True,
                ignore=_runtime_copy_ignore,
                copy_function=_runtime_copy_file,
            )
        shutil.copytree(
            source_root / ".venv",
            staging / ".venv",
            symlinks=True,
            ignore=_runtime_copy_ignore,
            copy_function=_runtime_copy_file,
        )
        _remove_editable_install_metadata(staging)
        source_identity = resolve_runtime_repository_identity(source_root)
        provenance = build_runtime_install_provenance(
            staging,
            repo_head_sha=str(source_identity["repo_head_sha"]),
        )
        write_runtime_install_provenance(staging, provenance)
        _verify_local_runtime(staging)

        if runtime_root.exists():
            backup = Path(tempfile.mkdtemp(prefix=".runtime-backup.", dir=parent))
            backup.rmdir()
            os.replace(runtime_root, backup)
        os.replace(staging, runtime_root)
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
    except Exception:
        if backup is not None and not runtime_root.exists():
            os.replace(backup, runtime_root)
            backup = None
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup is not None and backup.exists():
            shutil.rmtree(backup)
    return runtime_root


def install_macos_application(
    repo_root: Path,
    *,
    destination: Path | None = None,
    runtime_destination: Path | None = None,
) -> Path:
    """Install a Finder-launchable app that runs the idempotent bootstrap."""

    app_path = (
        destination.expanduser()
        if destination is not None
        else Path.home() / "Applications" / "voiStarcraft2.app"
    )
    if runtime_destination is None:
        installed_runtime = (
            Path.home()
            / "Library"
            / "Application Support"
            / "voiStarcraft2"
            / LOCAL_RUNTIME_DIR_NAME
        )
        installed_paths = LocalCockpitPaths.from_repo_root(installed_runtime)
        if not _stop_owned_app(installed_paths, app_path):
            raise RuntimeError(
                "The installed voiStarcraft2 app could not be stopped safely."
            )
        recorded_cockpit = installed_paths.state_dir / "cockpit.pid"
        if recorded_cockpit.exists() and not _stop_owned_cockpit(
            installed_paths, None
        ):
            raise RuntimeError(
                "The installed voiStarcraft2 cockpit could not be stopped safely."
            )
        if _port_is_bound(DEFAULT_COCKPIT_PORT):
            raise RuntimeError(
                "Port 8350 is used by a process that is not the installed cockpit."
            )
    runtime_root = install_local_runtime(
        repo_root,
        destination=runtime_destination,
    )
    app_parent = app_path.parent
    app_parent.mkdir(parents=True, exist_ok=True)
    runtime = str(runtime_root.resolve())
    sc2_executable = str(resolve_required_sc2_executable())
    sc2_receipt = str(
        Path.home()
        / "Library"
        / "Application Support"
        / "voiStarcraft2"
        / SC2_LAUNCH_RECEIPT_FILE
    )
    staging_root = Path(tempfile.mkdtemp(prefix=".voiStarcraft2-app.", dir=app_parent))
    staged_app = staging_root / app_path.name
    backup: Path | None = None
    installed_new_app = False
    try:
        contents = staged_app / "Contents"
        macos_dir = contents / "MacOS"
        resources = contents / "Resources"
        macos_dir.mkdir(parents=True, exist_ok=True)
        resources.mkdir(parents=True, exist_ok=True)
        bootstrap = resources / "bootstrap.sh"
        bootstrap.write_text(
            "#!/bin/zsh\n"
            "set -u\n"
            f"runtime={json.dumps(runtime)}\n"
            'log_dir="$HOME/Library/Logs/voiStarcraft2"\n'
            'mkdir -p "$log_dir"\n'
            'python="$runtime/.venv/bin/python"\n'
            'if [[ ! -x "$python" ]]; then\n'
            '  printf "Installed runtime is missing: %s\\n" "$runtime" '
            '>>"$log_dir/bootstrap.log"\n'
            "  exit 1\n"
            "fi\n"
            'cd "$runtime" || exit 1\n'
            'nohup /usr/bin/env PYTHONPATH="$runtime" '
            '"$python" "$runtime/scripts/start_local_cockpit.py" '
            '--repo-root "$runtime" --no-open '
            '--sc2-launch-receipt "$HOME/Library/Application Support/'
            f'voiStarcraft2/{SC2_LAUNCH_RECEIPT_FILE}" '
            '>>"$log_dir/bootstrap.log" 2>&1 </dev/null &\n'
            "exit 0\n",
            encoding="utf-8",
        )
        bootstrap.chmod(0o755)

        launcher_source = resources / "launcher.swift"
        launcher_source.write_text(
            """import Cocoa
import CoreGraphics
import Darwin
import WebKit

private let cockpitURL = URL(string: "http://127.0.0.1:8350")!
private let appPIDURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/voiStarcraft2/app.pid")
private let sc2ExecutableURL = URL(fileURLWithPath: __SC2_EXECUTABLE__)
private let sc2ApplicationURL = sc2ExecutableURL
    .deletingLastPathComponent()
    .deletingLastPathComponent()
    .deletingLastPathComponent()
private let sc2ReceiptURL = URL(fileURLWithPath: __SC2_RECEIPT__)
private let sc2Port = __SC2_PORT__
private let sc2Base = __SC2_BASE__
private let launchArguments = ProcessInfo.processInfo.arguments
private let autoCommandArgument: String? = {
    guard let index = launchArguments.firstIndex(of: "--auto-command"),
          index + 1 < launchArguments.count else {
        return nil
    }
    let command = launchArguments[index + 1]
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return command.isEmpty ? nil : command
}()

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate,
    WKUIDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var readinessAttempt = 0
    private var backendHealthFailures = 0
    private var backendRecoveryInFlight = false
    private var backendMonitorStarted = false
    private var sc2PID: pid_t = 0
    private var sc2LaunchInFlight = false
    private let autoStartMicroMachine = launchArguments
        .contains("--auto-start-micromachine") || autoCommandArgument != nil
    private var autoStartTriggered = false
    private var autoCommandTriggered = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        recordAppPID()
        createWindow()
        launchBackend()
        waitForCockpit()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        guard let recorded = try? String(contentsOf: appPIDURL, encoding: .ascii),
              recorded.trimmingCharacters(in: .whitespacesAndNewlines)
                == String(ProcessInfo.processInfo.processIdentifier) else {
            return
        }
        try? FileManager.default.removeItem(at: appPIDURL)
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        return true
    }

    private func createWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "sc2Launch")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.loadHTMLString(
            "<!doctype html><meta charset='utf-8'><style>body{margin:0;display:grid;place-items:center;height:100vh;background:#07171d;color:#65f3df;font:700 18px -apple-system,sans-serif}p{padding:24px}</style><p>voiStarcraft2 조종석을 준비하고 있습니다...</p>",
            baseURL: nil
        )
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 720),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "voiStarcraft2"
        window.minSize = NSSize(width: 480, height: 560)
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
    }

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard message.name == "sc2Launch",
              let body = message.body as? [String: Any],
              let nonce = body["nonce"] as? String,
              nonce.range(
                of: "^[A-Za-z0-9-]{16,128}$",
                options: .regularExpression
              ) != nil else {
            resolveSC2Launch(
                nonce: "",
                accepted: false,
                error: "SC2 native launch request is invalid."
            )
            return
        }
        launchSC2(nonce: nonce)
    }

    private func launchSC2(nonce: String) {
        guard !sc2LaunchInFlight else {
            resolveSC2Launch(
                nonce: nonce,
                accepted: false,
                error: "SC2 launch is already in progress."
            )
            return
        }
        sc2LaunchInFlight = true
        try? FileManager.default.removeItem(at: sc2ReceiptURL)
        guard FileManager.default.isExecutableFile(
            atPath: sc2ExecutableURL.path
        ) else {
            finishSC2Launch(
                nonce: nonce,
                pid: 0,
                processCreated: false,
                error: "Base\\(sc2Base) SC2 executable is not available."
            )
            return
        }
        if let running = exactRunningSC2Application() {
            sc2PID = running.processIdentifier
            if autoCommandArgument != nil {
                hideCockpitAndFocusSC2()
            } else {
                running.activate(
                    options: [.activateAllWindows, .activateIgnoringOtherApps]
                )
            }
            verifyVisibleSC2(nonce: nonce, pid: sc2PID, processCreated: true)
            return
        }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.arguments = [
            "-listen", "127.0.0.1",
            "-port", String(sc2Port),
            "-displayMode", "0"
        ]
        configuration.activates = true
        configuration.addsToRecentItems = false
        configuration.createsNewApplicationInstance = true
        NSWorkspace.shared.openApplication(
            at: sc2ApplicationURL,
            configuration: configuration
        ) { [weak self] application, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                guard error == nil, let application = application else {
                    self.finishSC2Launch(
                        nonce: nonce,
                        pid: 0,
                        processCreated: false,
                        error: error?.localizedDescription
                            ?? "LaunchServices did not return an SC2 process."
                    )
                    return
                }
                self.sc2PID = application.processIdentifier
                if autoCommandArgument != nil {
                    self.hideCockpitAndFocusSC2()
                } else {
                    application.activate(
                        options: [.activateAllWindows, .activateIgnoringOtherApps]
                    )
                }
                self.verifyVisibleSC2(
                    nonce: nonce,
                    pid: application.processIdentifier,
                    processCreated: true
                )
            }
        }
    }

    private func exactRunningSC2Application() -> NSRunningApplication? {
        let expected = sc2ExecutableURL.standardizedFileURL.path
        return NSWorkspace.shared.runningApplications.first { application in
            application.executableURL?.standardizedFileURL.path == expected
                && !application.isTerminated
        }
    }

    private func verifyVisibleSC2(
        nonce: String,
        pid: pid_t,
        processCreated: Bool
    ) {
        let deadline = Date().addingTimeInterval(180)
        func inspect() {
            let state = self.visibleSC2State(pid: pid)
            if state.accepted {
                self.finishSC2Launch(
                    nonce: nonce,
                    pid: pid,
                    processCreated: processCreated,
                    visibleState: state,
                    error: ""
                )
                return
            }
            if Date() >= deadline {
                self.finishSC2Launch(
                    nonce: nonce,
                    pid: pid,
                    processCreated: processCreated,
                    visibleState: state,
                    error: state.screenLocked
                        ? "macOS 화면이 잠겨 있어 SC2 표시를 검증할 수 없습니다."
                        : "SC2 프로세스/API/온스크린 창 검증 시간이 초과되었습니다."
                )
                return
            }
            if !state.frontmost,
               let application = NSRunningApplication(processIdentifier: pid) {
                application.activate(
                    options: [.activateAllWindows, .activateIgnoringOtherApps]
                )
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                inspect()
            }
        }
        inspect()
    }

    private struct VisibleSC2State {
        let apiReady: Bool
        let windowCreated: Bool
        let windowOnscreen: Bool
        let frontmost: Bool
        let screenLocked: Bool
        let windowID: CGWindowID
        let width: Int
        let height: Int

        var bootstrapAccepted: Bool {
            apiReady && windowCreated && windowOnscreen && !screenLocked
        }

        var accepted: Bool {
            bootstrapAccepted
        }
    }

    private func visibleSC2State(pid: pid_t) -> VisibleSC2State {
        let screenLocked = currentScreenLocked()
        let frontmost = NSWorkspace.shared.frontmostApplication?
            .processIdentifier == pid
        let windows = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements],
            kCGNullWindowID
        ) as? [[String: Any]] ?? []
        var selectedWindowID: CGWindowID = 0
        var selectedWidth = 0
        var selectedHeight = 0
        for info in windows {
            guard let ownerPID = info[kCGWindowOwnerPID as String] as? Int,
                  ownerPID == Int(pid),
                  let number = info[kCGWindowNumber as String] as? Int,
                  let bounds = info[kCGWindowBounds as String] as? [String: Any],
                  let width = bounds["Width"] as? Double,
                  let height = bounds["Height"] as? Double else {
                continue
            }
            let layer = info[kCGWindowLayer as String] as? Int ?? 0
            let alpha = info[kCGWindowAlpha as String] as? Double ?? 1
            guard layer == 0, alpha > 0.01 else { continue }
            if Int(width * height) > selectedWidth * selectedHeight {
                selectedWindowID = CGWindowID(number)
                selectedWidth = Int(width)
                selectedHeight = Int(height)
            }
        }
        let windowCreated = selectedWindowID != 0
        let windowOnscreen = windowCreated && selectedWidth >= 480
            && selectedHeight >= 360
        return VisibleSC2State(
            apiReady: tcpPortReady(sc2Port),
            windowCreated: windowCreated,
            windowOnscreen: windowOnscreen,
            frontmost: frontmost,
            screenLocked: screenLocked,
            windowID: selectedWindowID,
            width: selectedWidth,
            height: selectedHeight
        )
    }

    private func currentScreenLocked() -> Bool {
        guard let session = CGSessionCopyCurrentDictionary() as? [String: Any]
        else {
            return true
        }
        return session["CGSSessionScreenIsLocked"] as? Bool ?? false
    }

    private func tcpPortReady(_ port: Int) -> Bool {
        let descriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else { return false }
        defer { close(descriptor) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(UInt16(port).bigEndian)
        address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        return withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(
                to: sockaddr.self,
                capacity: 1
            ) { socketAddress in
                connect(
                    descriptor,
                    socketAddress,
                    socklen_t(MemoryLayout<sockaddr_in>.size)
                ) == 0
            }
        }
    }

    private func finishSC2Launch(
        nonce: String,
        pid: pid_t,
        processCreated: Bool,
        visibleState: VisibleSC2State? = nil,
        error: String = ""
    ) {
        let state = visibleState ?? VisibleSC2State(
            apiReady: false,
            windowCreated: false,
            windowOnscreen: false,
            frontmost: false,
            screenLocked: currentScreenLocked(),
            windowID: 0,
            width: 0,
            height: 0
        )
        let accepted = processCreated && state.accepted && error.isEmpty
        let receipt = sc2Receipt(
            nonce: nonce,
            pid: pid,
            processCreated: processCreated,
            visibleState: state,
            bootstrapAccepted: processCreated
                && state.bootstrapAccepted
                && error.isEmpty,
            accepted: accepted,
            error: error
        )
        do {
            try writeSC2Receipt(receipt)
        } catch {
            sc2LaunchInFlight = false
            resolveSC2Launch(
                nonce: nonce,
                accepted: false,
                error: "SC2 launch receipt write failed: \\(error.localizedDescription)"
            )
            return
        }
        sc2LaunchInFlight = false
        resolveSC2Launch(nonce: nonce, accepted: accepted, error: error)
    }

    private func sc2Receipt(
        nonce: String,
        pid: pid_t,
        processCreated: Bool,
        visibleState state: VisibleSC2State,
        bootstrapAccepted: Bool,
        accepted: Bool,
        error: String
    ) -> [String: Any] {
        return [
            "schema": "voi-sc2-visible-launch/v1",
            "nonce": nonce,
            "created_at_unix_ms": Int(Date().timeIntervalSince1970 * 1000),
            "accepted": accepted,
            "bootstrap_accepted": bootstrapAccepted,
            "pid": Int(pid),
            "port": sc2Port,
            "base": sc2Base,
            "bundle_path": sc2ApplicationURL.path,
            "executable_path": sc2ExecutableURL.path,
            "process_created": processCreated,
            "api_ready": state.apiReady,
            "window_created": state.windowCreated,
            "window_onscreen": state.windowOnscreen,
            "frontmost": state.frontmost,
            "screen_locked": state.screenLocked,
            "screen_capture_authorized": false,
            "render_verified": false,
            "window_id": Int(state.windowID),
            "window_width": state.width,
            "window_height": state.height,
            "error": error
        ]
    }

    private func writeSC2Receipt(_ receipt: [String: Any]) throws {
        let directory = sc2ReceiptURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let data = try JSONSerialization.data(
            withJSONObject: receipt,
            options: [.sortedKeys]
        )
        try data.write(to: sc2ReceiptURL, options: [.atomic])
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: sc2ReceiptURL.path
        )
    }

    private func resolveSC2Launch(
        nonce: String,
        accepted: Bool,
        error: String
    ) {
        guard let data = try? JSONSerialization.data(
            withJSONObject: [
                "nonce": nonce,
                "accepted": accepted,
                "error": error
            ],
            options: []
        ), let payload = String(data: data, encoding: .utf8) else {
            return
        }
        webView.evaluateJavaScript(
            "window.voiNativeSC2LaunchResolved(\\(payload));",
            completionHandler: nil
        )
    }

    private func recordAppPID() {
        let directory = appPIDURL.deletingLastPathComponent()
        try? FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        let value = "\\(ProcessInfo.processInfo.processIdentifier)\\n"
        try? value.write(to: appPIDURL, atomically: true, encoding: .ascii)
        try? FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: appPIDURL.path
        )
    }

    private func launchBackend() {
        guard let bootstrap = Bundle.main.path(
            forResource: "bootstrap",
            ofType: "sh"
        ) else {
            showFailure("앱 백엔드 실행 파일을 찾을 수 없습니다.")
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/zsh")
        process.arguments = [bootstrap]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            showFailure("앱 백엔드를 시작하지 못했습니다: \\(error.localizedDescription)")
        }
    }

    private func waitForCockpit() {
        readinessAttempt += 1
        var request = URLRequest(url: cockpitURL.appendingPathComponent("api/llm"))
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            let ready = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async {
                guard let self = self else { return }
                if ready {
                    self.readinessAttempt = 0
                    self.backendHealthFailures = 0
                    self.backendRecoveryInFlight = false
                    self.webView.load(URLRequest(url: cockpitURL))
                    self.startBackendMonitor()
                    return
                }
                if self.readinessAttempt == 120 {
                    self.showFailure(
                        "조종석 시작 시간이 초과되었습니다. "
                        + "백엔드 준비를 계속 기다리고 있으며 준비되면 자동 복구합니다. "
                        + "$HOME/Library/Logs/voiStarcraft2/bootstrap.log에서 진행 상황을 확인할 수 있습니다."
                    )
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    self.waitForCockpit()
                }
            }
        }.resume()
    }

    private func startBackendMonitor() {
        guard !backendMonitorStarted else { return }
        backendMonitorStarted = true
        scheduleBackendHealthCheck()
    }

    private func scheduleBackendHealthCheck() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            self.checkBackendHealth()
        }
    }

    private func checkBackendHealth() {
        var request = URLRequest(url: cockpitURL.appendingPathComponent("api/llm"))
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) {
            [weak self] data, response, _ in
            let document = data.flatMap {
                try? JSONSerialization.jsonObject(with: $0) as? [String: Any]
            }
            let ready = (
                (response as? HTTPURLResponse)?.statusCode == 200
                && document?["configured"] as? Bool == true
                && document?["available"] as? Bool == true
            )
            DispatchQueue.main.async {
                guard let self = self else { return }
                if ready {
                    self.backendHealthFailures = 0
                    self.backendRecoveryInFlight = false
                } else {
                    self.backendHealthFailures += 1
                    if self.backendHealthFailures >= 3,
                       !self.backendRecoveryInFlight {
                        self.backendRecoveryInFlight = true
                        self.readinessAttempt = 0
                        self.launchBackend()
                        self.waitForCockpit()
                    }
                }
                self.scheduleBackendHealthCheck()
            }
        }.resume()
    }

    private func showFailure(_ message: String) {
        let encoded = message
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        webView?.loadHTMLString(
            "<!doctype html><meta charset='utf-8'><style>body{margin:0;display:grid;place-items:center;height:100vh;background:#1c0b0b;color:#ffd8d3;font:700 16px -apple-system,sans-serif}p{max-width:640px;padding:28px;line-height:1.6}</style><p>\\(encoded)</p>",
            baseURL: nil
        )
    }

    private func submitAutoCommand(_ command: String) {
        guard let data = try? JSONEncoder().encode(command),
              let commandJSON = String(data: data, encoding: .utf8) else {
            return
        }
        let script = [
            "(function submitAutoCommand() {",
            "  var input = document.getElementById('command-input');",
            "  var form = document.getElementById('command-form');",
            "  if (!input || !form) { return; }",
            "  input.value = \\(commandJSON);",
            "  form.requestSubmit();",
            "})();",
        ].joined(separator: "\\n")
        webView.evaluateJavaScript(script, completionHandler: nil)
    }

    private func hideCockpitAndFocusSC2() {
        window.orderOut(nil)
        if let application = NSRunningApplication(processIdentifier: sc2PID) {
            application.activate(
                options: [.activateAllWindows, .activateIgnoringOtherApps]
            )
        }
    }

    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        if navigationAction.targetFrame == nil {
            webView.load(navigationAction.request)
        }
        return nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let compactController = ["/", "/index.html", "/companion"].contains(
            webView.url?.path ?? ""
        )
        window.setContentSize(NSSize(width: 520, height: 720))
        window.center()
        if compactController, autoCommandArgument != nil {
            hideCockpitAndFocusSC2()
        } else {
            window.makeKeyAndOrderFront(nil)
        }
        let cockpitLoaded = webView.url?.host == "127.0.0.1"
        if cockpitLoaded, compactController, autoStartMicroMachine,
           !autoStartTriggered {
            autoStartTriggered = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                webView.evaluateJavaScript(
                    "document.getElementById('runtime-start')?.click();",
                    completionHandler: nil
                )
            }
        }
        if compactController, let command = autoCommandArgument,
           !autoCommandTriggered {
            autoCommandTriggered = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                self.submitAutoCommand(command)
            }
        }
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
"""
            .replace("__SC2_EXECUTABLE__", json.dumps(sc2_executable))
            .replace("__SC2_RECEIPT__", json.dumps(sc2_receipt))
            .replace("__SC2_PORT__", str(DEFAULT_SC2_API_PORT))
            .replace("__SC2_BASE__", str(REQUIRED_SC2_BASE)),
            encoding="utf-8",
        )
        executable = macos_dir / "voiStarcraft2"
        module_cache = staging_root / "swift-module-cache"
        module_cache.mkdir()
        try:
            subprocess.run(
                [
                    "/usr/bin/swiftc",
                    "-O",
                    "-swift-version",
                    "5",
                    "-module-cache-path",
                    str(module_cache),
                    "-framework",
                    "Cocoa",
                    "-framework",
                    "CoreGraphics",
                    "-framework",
                    "WebKit",
                    str(launcher_source),
                    "-o",
                    str(executable),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as error:
            compiler_error = (error.stderr or "").strip()
            raise RuntimeError(
                "voiStarcraft2 macOS launcher compilation failed"
                + (f": {compiler_error}" if compiler_error else ".")
            ) from error

        info = {
            "CFBundleDevelopmentRegion": "ko",
            "CFBundleDisplayName": "voiStarcraft2",
            "CFBundleExecutable": "voiStarcraft2",
            "CFBundleIdentifier": "local.voi.starcraft2.cockpit",
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": "voiStarcraft2",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "4",
            "LSMultipleInstancesProhibited": True,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        }
        info_path = contents / "Info.plist"
        with info_path.open("wb") as handle:
            plistlib.dump(info, handle, sort_keys=True)

        if not os.access(executable, os.X_OK):
            raise RuntimeError("Compiled macOS app executable is unavailable.")
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--deep",
                "--sign",
                "-",
                str(staged_app),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        if app_path.exists():
            backup = Path(tempfile.mkdtemp(prefix=".voiStarcraft2-app-backup.", dir=app_parent))
            backup.rmdir()
            os.replace(app_path, backup)
        os.replace(staged_app, app_path)
        installed_new_app = True
        if LAUNCHSERVICES_REGISTER_PATH.is_file():
            try:
                subprocess.run(
                    [
                        str(LAUNCHSERVICES_REGISTER_PATH),
                        "-f",
                        str(app_path),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                registration_error = str(
                    getattr(error, "stderr", "") or error
                ).strip()
                raise RuntimeError(
                    "voiStarcraft2 LaunchServices registration failed"
                    + (
                        f": {registration_error}"
                        if registration_error
                        else "."
                    )
                ) from error
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
    except Exception:
        if backup is not None:
            if app_path.exists():
                shutil.rmtree(app_path)
            os.replace(backup, app_path)
            backup = None
        elif installed_new_app and app_path.exists():
            shutil.rmtree(app_path)
        raise
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        if backup is not None and backup.exists():
            shutil.rmtree(backup)
    return app_path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and start the local MyProxy MicroMachine cockpit."
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--codex-config",
        default=str(Path.home() / ".codex" / "config.toml"),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_COCKPIT_PORT)
    parser.add_argument("--build-jobs", type=int, default=DEFAULT_BUILD_JOBS)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--store-key-from-env", action="store_true")
    parser.add_argument("--install-app", action="store_true")
    parser.add_argument("--launch-app", action="store_true")
    parser.add_argument(
        "--sc2-launch-receipt",
        default="",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    paths = LocalCockpitPaths.from_repo_root(Path(args.repo_root))
    try:
        settings = read_codex_myproxy_settings(Path(args.codex_config))
        secret = resolve_myproxy_key(
            settings,
            os.environ,
            local_secret_path=None if args.store_key_from_env else paths.secret_file,
        )
        if args.store_key_from_env:
            store_local_secret(secret, paths.secret_file)
            print("MyProxy key saved to owner-only local cockpit storage.")

        print("[1/4] MyProxy local configuration is ready.")
        ensure_python_environment(paths)
        print("[2/4] Python LLM dependencies are ready.")
        if not args.no_build:
            ensure_micromachine_build(
                paths,
                force=args.force_build,
                build_jobs=args.build_jobs,
            )
            print("[3/4] MicroMachine runtime is ready.")
        else:
            print("[3/4] MicroMachine build was skipped.")

        if args.install_app:
            app_path = install_macos_application(paths.repo_root)
            print(f"One-click app installed: {app_path}")
            if args.launch_app:
                subprocess.run(["/usr/bin/open", str(app_path)], check=False)

        if args.prepare_only:
            print("[4/4] Local cockpit preparation completed.")
            return 0

        environment = child_environment(settings, secret, os.environ)
        url = start_cockpit(
            paths,
            environment,
            port=args.port,
            sc2_launch_receipt=(
                Path(args.sc2_launch_receipt)
                if args.sc2_launch_receipt
                else None
            ),
        )
        print(f"[4/4] Commander cockpit is ready: {url}")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Local cockpit setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
