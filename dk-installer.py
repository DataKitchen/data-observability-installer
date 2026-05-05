#!/usr/bin/env python3

import argparse
import base64
import contextlib
import dataclasses
import datetime
import functools
import hashlib
import io
import ipaddress
import json
import logging
import logging.config
import os
import pathlib
import pdb
import platform
import random
import re
import secrets
import shutil
import socket
import ssl
import stat
import string
import subprocess
import sys
import tarfile
import textwrap
import time
import urllib.request
import urllib.parse
import webbrowser
import zipfile
import typing

#
# Initial setup
#

REQ_CHECK_TIMEOUT = 30
DEFAULT_DOCKER_REGISTRY = "docker.io"
DOCKER_NETWORK = "datakitchen-network"
DOCKER_NETWORK_SUBNET = "192.168.60.0/24"
POD_LOG_LIMIT = 10_000
INSTALLER_NAME = pathlib.Path(__file__).name
DEMO_CONFIG_FILE = "demo-config.json"
DEMO_IMAGE = "datakitchen/data-observability-demo:latest"
DEMO_CONTAINER_NAME = "dk-demo"

BASE_API_URL_TPL = "http://host.docker.internal:{}/api"
CREDENTIALS_FILE = "dk-{}-credentials.txt"
TESTGEN_MAJOR_VERSION = "5"
TESTGEN_PYTHON_VERSION = "3.13"
TESTGEN_DEFAULT_IMAGE = f"datakitchen/dataops-testgen:v{TESTGEN_MAJOR_VERSION}"
TESTGEN_PULL_TIMEOUT = 5
TESTGEN_PULL_RETRIES = 3
TESTGEN_DEFAULT_PORT = 8501
TESTGEN_DEFAULT_API_PORT = 8530
TESTGEN_LATEST_VERSIONS_URL = (
    "https://dk-support-external.s3.us-east-1.amazonaws.com/testgen-observability/testgen-latest-versions.json"
)
TESTGEN_PIP_PACKAGE = "dataops-testgen"
TESTGEN_COMPOSE_FILE = "docker-compose.yml"
TESTGEN_LOG_FILE_PATH = pathlib.Path.home() / ".testgen" / "logs" / "app.log"
TESTGEN_CONFIG_ENV_PATH = pathlib.Path.home() / ".testgen" / "config.env"
TESTGEN_APP_READY_TIMEOUT = 120
INSTALL_MARKER_FILE = "dk-{}-install.json"
INSTALL_MODE_DOCKER = "docker"
INSTALL_MODE_PIP = "pip"

UV_VERSION = "0.11.7"
UV_RELEASE_URL_TPL = "https://github.com/astral-sh/uv/releases/download/{version}/{asset}"
UV_DOWNLOAD_TIMEOUT = 120
UV_DOWNLOAD_RETRIES = 3
UV_BIN_SUBDIR = "bin"
# To bump UV_VERSION, refresh the SHA256s here from the dist-manifest:
# https://github.com/astral-sh/uv/releases/download/<version>/dist-manifest.json
# See "Bumping uv" in CLAUDE.md.
UV_ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    # (platform.system(), platform.machine()) -> (asset_name, sha256)
    ("Linux", "x86_64"): (
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        "6681d691eb7f9c00ac6a3af54252f7ab29ae72f0c8f95bdc7f9d1401c23ea868",
    ),
    ("Linux", "aarch64"): (
        "uv-aarch64-unknown-linux-gnu.tar.gz",
        "f2ee1cde9aabb4c6e43bd3f341dadaf42189a54e001e521346dc31547310e284",
    ),
    ("Darwin", "x86_64"): (
        "uv-x86_64-apple-darwin.tar.gz",
        "0a4bc8fcde4974ea3560be21772aeecab600a6f43fa6e58169f9fa7b3b71d302",
    ),
    ("Darwin", "arm64"): (
        "uv-aarch64-apple-darwin.tar.gz",
        "66e37d91f839e12481d7b932a1eccbfe732560f42c1cfb89faddfa2454534ba8",
    ),
    ("Windows", "AMD64"): (
        "uv-x86_64-pc-windows-msvc.zip",
        "fe0c7815acf4fc45f8a5eff58ed3cf7ae2e15c3cf1dceadbd10c816ec1690cc1",
    ),
    ("Windows", "ARM64"): (
        "uv-aarch64-pc-windows-msvc.zip",
        "1387e1c94e15196351196b79fce4c1e6f4b30f19cdaaf9ff85fbd6b046018aa2",
    ),
}

OBS_LATEST_TAG = "v2"
OBS_DEF_BE_IMAGE = f"datakitchen/dataops-observability-be:{OBS_LATEST_TAG}"
OBS_DEF_UI_IMAGE = f"datakitchen/dataops-observability-ui:{OBS_LATEST_TAG}"
OBS_PULL_TIMEOUT = 5
OBS_PULL_RETRIES = 3
OBS_DEFAULT_PORT = 8082

OBS_SERVICES_URLS = (
    ("User Interface", "{}:{}/"),
    ("Event Ingestion API", "{}:{}/api/events/v1"),
    ("Observability API", "{}:{}/api/observability/v1"),
    ("Agent Heartbeat API", "{}:{}/api/agent/v1"),
)

MIXPANEL_TOKEN = "4eff51580bc1685b8ffe79ffb22d2704"
MIXPANEL_URL = "https://api.mixpanel.com"
MIXPANEL_TIMEOUT = 3
INSTANCE_ID_FILE = "instance.txt"

DEFAULT_USER_DATA = {
    "name": "Admin",
    "email": "email@example.com",
    "username": "admin",
}

LOG = logging.getLogger()

COMPOSE_VAR_RE = re.compile(r"\$\{(\w+):-([^\}]*)\}")
TESTGEN_PIP_VERSION_RE = re.compile(rf"^{re.escape(TESTGEN_PIP_PACKAGE)}\s+v(\S+)")

#
# Utility functions
#


def get_tg_url(args, port):
    protocol = "https" if args.ssl_cert_file and args.ssl_key_file else "http"
    return f"{protocol}://localhost:{port}"


def open_app_in_browser(url: str) -> None:
    """Best-effort open the URL in the user's default browser. Silent no-op
    on headless / browser-less environments."""
    try:
        webbrowser.open(url)
    except Exception:
        LOG.exception("Failed to open browser for %s", url)


def collect_images_digest(action, images, env=None):
    if images:
        action.run_cmd(
            "docker",
            "image",
            "inspect",
            *images,
            "--format=DIGEST: {{ index .RepoDigests 0 }} CREATED: {{ .Created }}",
            raise_on_non_zero=False,
            env=env,
        )


def collect_user_input(fields: list[str]) -> dict[str, str]:
    res = {}
    CONSOLE.space()
    try:
        for field in fields:
            while field not in res:
                if value := input(f"{CONSOLE.MARGIN}{field.capitalize()!s: >20}: "):
                    res[field] = value
    except KeyboardInterrupt:
        print("")  # Moving the cursor back to the start
        raise AbortAction
    finally:
        CONSOLE.space()
    return res


def generate_password():
    characters = string.ascii_letters + string.digits
    password = ""
    for _ in range(12):
        password += secrets.choice(characters)
    return password


def remove_path(path: pathlib.Path, label: typing.Optional[str] = None) -> bool:
    """Remove a file or directory tree if it exists.
    When ``label`` is provided, success/failure is also reported via CONSOLE.

    Returns True if something was actually removed.
    """
    if not (path.exists() or path.is_symlink()):
        return False
    LOG.debug("Removing path [%s]", path)
    try:
        if path.is_dir():
            # On Windows, files inside a Postgres data dir are often marked read-only,
            # which causes shutil.rmtree to abort partway through. Clear the read-only
            # bit and retry from the error callback. shutil.rmtree's `onerror` is
            # deprecated in 3.12 and removed in 3.14, replaced by `onexc`; the callback
            # signatures differ on the third arg but we ignore it either way.
            def _retry(func, p, _exc):
                os.chmod(p, stat.S_IWRITE)
                func(p)

            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_retry)
            else:
                shutil.rmtree(path, onerror=_retry)
        else:
            path.unlink()
    except OSError:
        LOG.exception("Failed to remove %s", path)
        if label:
            CONSOLE.msg(f"Could not remove {label} ({path}); remove manually if needed.")
        return False
    if label:
        CONSOLE.msg(f"Removed {label} ({path})")
    return True


@functools.cache
def get_installer_version():
    try:
        return hashlib.md5(pathlib.Path(__file__).read_bytes()).hexdigest()
    except Exception:
        return "N/A"


def resolve_windows_redirected_path(path: pathlib.Path) -> pathlib.Path:
    """If running under Microsoft Store Python, rewrite ``path`` from its
    UWP-virtualized form (what Python sees via ``os.environ['LOCALAPPDATA']``)
    to the real on-disk path users can navigate to in Explorer/PowerShell.
    Returns ``path`` unchanged on non-Windows or non-Store Python.
    """
    exe = pathlib.Path(sys.executable)
    if "WindowsApps" not in exe.parts:
        return path
    parts = exe.parent.name.split("_")
    if len(parts) < 2:
        return path
    pfn = f"{parts[0]}_{parts[-1]}"
    try:
        local_appdata = pathlib.Path(os.environ["LOCALAPPDATA"])
        rel = path.relative_to(local_appdata)
    except (KeyError, ValueError):
        return path
    return local_appdata / "Packages" / pfn / "LocalCache" / "Local" / rel


def simplify_path(path: pathlib.Path) -> pathlib.Path:
    if platform.system() == "Windows":
        path = resolve_windows_redirected_path(path)
    try:
        return path.relative_to(pathlib.Path().absolute())
    except ValueError:
        return path


def command_hint(prod: str, subcmd: str, menu_label: str) -> str:
    """Render a user-facing CLI hint. Under the frozen Windows .exe, the user
    typically has no Python and runs via the menu, so we point them at the
    menu label instead of a command they can't type.
    """
    if getattr(sys, "frozen", False):
        return f"select '{menu_label}' from the menu"
    return f"run `python3 {INSTALLER_NAME} {prod} {subcmd}`"


class InstallMarker:
    """Read/write the TestGen install marker file. Falls back to detecting
    a legacy Docker install (compose file + credentials) from before the
    marker was introduced.
    """

    def __init__(self, data_folder: pathlib.Path, prod: str, compose_file_name: typing.Optional[str] = None):
        self._data_folder = data_folder
        self._prod = prod
        self._compose_file_name = compose_file_name
        self.path = data_folder / INSTALL_MARKER_FILE.format(prod)

    def read(self) -> typing.Optional[str]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
            except Exception:
                LOG.exception("Failed to read install marker at %s", self.path)
            else:
                install_mode = data.get("install_mode")
                if install_mode in (INSTALL_MODE_DOCKER, INSTALL_MODE_PIP):
                    return install_mode
                LOG.warning("Install marker has unexpected install_mode: %r", install_mode)
        if (
            self._compose_file_name
            and (self._data_folder / self._compose_file_name).exists()
            and (self._data_folder / CREDENTIALS_FILE.format(self._prod)).exists()
        ):
            LOG.info("No marker present; detected legacy Docker install in %s", self._data_folder)
            return INSTALL_MODE_DOCKER
        return None

    def write(self, mode: str, **extra) -> None:
        if mode not in (INSTALL_MODE_DOCKER, INSTALL_MODE_PIP):
            raise ValueError(f"Unknown install_mode: {mode}")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        created_on = now
        if self.path.exists():
            try:
                existing = json.loads(self.path.read_text())
                if isinstance(existing.get("created_on"), str):
                    created_on = existing["created_on"]
            except Exception:
                LOG.exception("Failed to read existing install marker at %s", self.path)
        self.path.write_text(
            json.dumps(
                {"install_mode": mode, "created_on": created_on, "last_updated_on": now, **extra},
                indent=2,
            )
        )

    def unlink(self) -> None:
        if self.path.exists():
            self.path.unlink()


@contextlib.contextmanager
def stream_iterator(proc: subprocess.Popen, stream_name: str, file_path: pathlib.Path, timeout: float = 1.0):
    comm_index, exc_attr = {
        "stdout": (0, "output"),
        "stderr": (1, "stderr"),
    }[stream_name]
    buffer = io.TextIOWrapper(io.BytesIO())

    def _iter():
        proc_exited = False
        read_pos = 0
        while not proc_exited:
            try:
                partial = proc.communicate(timeout=timeout)[comm_index]
            except subprocess.TimeoutExpired as exc:
                partial = getattr(exc, exc_attr)
            else:
                proc_exited = True

            if partial is not None:
                buffer.buffer.seek(0)
                buffer.buffer.write(partial)

            buffer.seek(read_pos)
            while True:
                try:
                    line = buffer.readline()
                # When some unicode char is incomplete, we skip yielding
                except UnicodeDecodeError:
                    break

                # When the line is empty we skip yielding
                # When the line is incomplete and the process is still running, we skip yielding
                if not line or (not line.endswith(os.linesep) and not proc_exited):
                    break

                yield line.strip(os.linesep)

                read_pos = buffer.tell()

    iterator = _iter()
    try:
        yield iterator
    finally:
        # Making sure all output was consumed before writing the buffer to the file
        for _ in iterator:
            pass
        if buffer.buffer.tell():
            file_path.write_bytes(buffer.buffer.getvalue())


#
# Core building blocks
#


class Console:
    MARGIN = "   | "

    def __init__(self):
        self._last_is_space = False
        self._partial_msg = None

    def title(self, text):
        LOG.info("Console title: [%s]", text)
        # Always blank-line before a title so they are separated from any input() prompts
        print("")
        print(f"  == {text}")
        print("")
        self._last_is_space = True

    def space(self):
        if not self._last_is_space:
            print(self.MARGIN)
            self._last_is_space = True

    def msg(self, text, skip_logging=False):
        if skip_logging:
            LOG.info("Console message omitted from the logs")
        else:
            LOG.info("Console message: [%s]", text)
        print(self.MARGIN, end="")
        print(text)
        self._last_is_space = False

    def print_log(self, log_path: pathlib.Path) -> None:
        with log_path.open() as log_file:
            print("")
            for line in log_file:
                line = line.strip()
                if line:
                    print(line)
            print("")
            self._last_is_space = True

    @contextlib.contextmanager
    def start_partial(self):
        print(self.MARGIN, end="")

        if self._partial_msg is not None:
            raise ValueError("Console partial is already started.")
        self._partial_msg = ""
        try:
            yield self.partial
        finally:
            print("")
            LOG.info("Console message: [%s]", self._partial_msg)
            self._partial_msg = None
            self._last_is_space = False

    def partial(self, text):
        if self._partial_msg is None:
            raise ValueError("Console partial has not been started.")
        print(text, end="")
        sys.stdout.flush()
        self._partial_msg += text

    @contextlib.contextmanager
    def tee(self, file_path, append=False):
        tee_lines = ["" if append else None]

        def console_tee(text, skip_logging=False):
            tee_lines.append(text)
            return self.msg(text, skip_logging=skip_logging)

        self.space()

        try:
            yield console_tee
        finally:
            self.space()
            try:
                with open(file_path, "a" if append else "w") as file:
                    file.writelines([f"{text}\n" for text in tee_lines if text is not None])
            except Exception:
                LOG.exception("Error tee'ing content to %s", file_path)


CONSOLE = Console()


@dataclasses.dataclass
class Requirement:
    key: str
    cmd: tuple[typing.Union[str, pathlib.Path], ...]
    fail_msg: tuple[str, ...]
    label: typing.Optional[str] = None

    def check_availability(self, action, args, quiet=False):
        try:
            action.run_cmd_retries(
                *(seg.format(**args.__dict__) for seg in self.cmd),
                timeout=REQ_CHECK_TIMEOUT,
                retries=1,
            )
        except CommandFailed:
            if not quiet:
                CONSOLE.space()
                for line in self.fail_msg:
                    CONSOLE.msg(line.format(**args.__dict__))
            return False
        else:
            return True


class CommandFailed(Exception):
    """
    Raised when a command returns a non-zero exit code.

    It's useful to prevent the installer logic from having to check the output of each command
    """

    def __init__(
        self,
        idx: typing.Union[int, None] = None,
        cmd: typing.Union[str, None] = None,
        ret_code: typing.Union[int, None] = None,
    ):
        if any((idx, cmd, ret_code)) and not all((idx, cmd)):
            raise ValueError(f"{self.__class__.__name__} requires 'idx' and 'cmd' to be set unless all args are None.")
        self.idx = idx
        self.cmd = cmd
        self.ret_code = ret_code


class InstallerError(Exception):
    """Should be raised when the root cause could not be addressed and the process is unable to continue."""


class AbortAction(InstallerError):
    """Should be raised when the root cause has been addressed but the process is unable to continue."""


class SkipStep(Exception):
    """Should be raised when a given Step does not need to be executed."""


class AnalyticsWrapper:
    def __init__(self, action, args):
        self.action = action
        self.args = args

    def _hash_value(self, value: typing.Union[bytes, str], digest_size: int = 8) -> str:
        if isinstance(value, str):
            value = value.encode()
        return hashlib.blake2b(value, salt=self.get_instance_id().encode(), digest_size=digest_size).hexdigest()

    @functools.cache
    def get_distinct_id(self):
        return self._hash_value(DEFAULT_USER_DATA["username"])

    @functools.cache
    def get_instance_id(self):
        instance_id_file = self.action.logs_folder / INSTANCE_ID_FILE
        try:
            return instance_id_file.read_text().strip()
        except FileNotFoundError:
            instance_id = random.randbytes(8).hex()
            instance_id_file.write_text(f"{instance_id}\n")
        return instance_id

    def __enter__(self):
        self._start = time.time()
        self.additional_properties = {}
        self.action.analytics = self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            event_name = f"{self.args.prod}-{self.action.args_cmd}"
        elif exc_type is AbortAction:
            event_name = "aborted"
        else:
            event_name = "failed"

        if self.args.send_analytics_data:
            properties = {
                "prod": self.args.prod,
                "action": self.action.args_cmd,
                "elapsed": time.time() - self._start,
                "os_version": platform.release(),
                "os_arch": platform.machine(),
                "$os": platform.system(),
                "python_info": f"{platform.python_implementation()} {platform.python_version()}",
                "installer_version": get_installer_version(),
                "distinct_id": self.get_distinct_id(),
                "instance_id": self.get_instance_id(),
                **self.additional_properties,
            }

            error_chain = []
            while exc_val is not None:
                error_chain.append(f"{exc_type.__name__}: {exc_val}")
                exc_val = exc_val.__cause__
            if error_chain:
                properties["error"] = " caused by ".join(error_chain)

            self.send_mp_event(event_name, properties)

        return False

    def get_ssl_context(self):
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    def send_mp_request(self, endpoint, payload):
        post_data = urllib.parse.urlencode({"data": base64.b64encode(json.dumps(payload).encode()).decode()}).encode()

        req = urllib.request.Request(f"{MIXPANEL_URL}/{endpoint}", data=post_data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        resp = urllib.request.urlopen(req, context=self.get_ssl_context(), timeout=MIXPANEL_TIMEOUT)
        if resp.code != 200:
            raise Exception(resp.reason)

    def send_mp_event(self, event_name, properties):
        track_payload = {
            "event": event_name,
            "properties": {
                "token": MIXPANEL_TOKEN,
                **properties,
            },
        }
        try:
            self.send_mp_request("track?ip=1", track_payload)
        except Exception as e:
            LOG.debug("Failed to send analytics event '%s': %s", event_name, e)
        else:
            LOG.debug(
                "Sent analytics event '%s' with properties %s",
                event_name,
                properties.keys(),
            )


class Action:
    _cmd_idx: int = 0
    args_cmd: str
    args_parser_parents: list = []
    requirements: list[Requirement] = []

    @contextlib.contextmanager
    def init_session_folder(self, prefix):
        if "Windows" == platform.system():
            self.data_folder = pathlib.Path(os.environ["LOCALAPPDATA"], "DataKitchenApps")
            self.logs_folder = self.data_folder.joinpath("logs")
        else:
            self.data_folder = pathlib.Path(sys.argv[0]).absolute().parent
            self.logs_folder = self.data_folder.joinpath(".dk-installer")
        self.data_folder.mkdir(exist_ok=True)
        self.logs_folder.mkdir(exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session_folder = self.logs_folder.joinpath(f"{prefix}-{timestamp}")
        self.session_folder.mkdir()

        self.session_zip = self.logs_folder.joinpath(f"{self.session_folder.name}.zip")

        try:
            yield
        finally:
            with zipfile.ZipFile(self.session_zip, "w") as session_zip:
                for session_file in self.session_folder.iterdir():
                    session_zip.write(
                        session_file,
                        arcname=session_file.relative_to(self.session_zip.parent),
                    )
                    session_file.unlink()
            self.session_folder.rmdir()
            self.session_folder = None
            latest = self.logs_folder.joinpath("latest")
            latest.unlink(True)
            latest.symlink_to(self.session_zip.relative_to(latest.parent))

    @contextlib.contextmanager
    def configure_logging(self, debug=False):
        file_path = self.session_folder.joinpath("installer_log.txt")
        logging.config.dictConfig(
            {
                "version": 1,
                "formatters": {
                    "file": {"format": "%(asctime)s %(levelname)8s %(message)s"},
                    "console": {"format": "   :  %(levelname)8s %(message)s"},
                },
                "handlers": {
                    "file": {
                        "level": "DEBUG",
                        "class": "logging.FileHandler",
                        "filename": str(file_path),
                        "formatter": "file",
                    },
                    "console": {
                        "level": "DEBUG",
                        "class": "logging.StreamHandler",
                        "formatter": "console",
                    },
                },
                "loggers": {
                    "": {
                        "handlers": ["file"] + (["console"] if debug else []),
                        "level": "DEBUG",
                    },
                },
            },
        )
        try:
            yield
        finally:
            logging.shutdown()
            logging.config.dictConfig(
                {
                    "version": 1,
                    "disable_existing_loggers": True,
                    "loggers": {
                        "": {"handlers": [], "level": "DEBUG"},
                    },
                }
            )

    def _get_failed_cmd_log_file_path(
        self, exception: Exception
    ) -> typing.Union[tuple[CommandFailed, pathlib.Path], tuple[None, None]]:
        while exception:
            if isinstance(exception, CommandFailed):
                break
            else:
                exception = exception.__cause__

        if exception:
            for stream in ("stderr", "stdout"):
                try:
                    (log_file_path,) = self.session_folder.glob(f"{exception.idx:04d}-{stream}-*.txt")
                except ValueError:
                    continue
                else:
                    return exception, log_file_path

        return None, None

    def _msg_unexpected_error(self, exception: Exception) -> None:
        cmd_exception, log_path = self._get_failed_cmd_log_file_path(exception)
        if cmd_exception and log_path:
            CONSOLE.msg(
                f"Command '{cmd_exception.cmd}' failed with code {cmd_exception.ret_code}. See the output below."
            )
            CONSOLE.print_log(log_path)
        else:
            root = exception
            while root.__cause__ is not None:
                root = root.__cause__
            if str(root).strip():
                CONSOLE.space()
                CONSOLE.msg(f"Error: {root}")

        msg_file_path = simplify_path(self.session_zip)
        CONSOLE.space()
        CONSOLE.msg("For assistance, send the logs to open-source-support@datakitchen.io or reach out")
        CONSOLE.msg("to the #support channel on https://data-observability-slack.datakitchen.io/join.")
        CONSOLE.msg(f"The logs can be found in {msg_file_path}.")

    def get_requirements(self, args) -> list[Requirement]:
        return self.requirements

    def check_requirements(self, args):
        missing_reqs = [req.key for req in self.get_requirements(args) if not req.check_availability(self, args)]
        if missing_reqs:
            self.analytics.additional_properties["missing_requirements"] = missing_reqs
            raise AbortAction

    # Names of instance attributes that hold per-invocation state. Reset
    # before each run so the same Action instance can be re-invoked cleanly
    # in menu mode (Windows .exe) without state from the previous run leaking
    # into the next. Subclasses extend this tuple with their own attrs.
    _per_invocation_attrs: tuple[str, ...] = ("_cmd_idx",)

    def _reset_per_invocation_state(self):
        for attr in self._per_invocation_attrs:
            self.__dict__.pop(attr, None)

    def execute_with_log(self, args):
        self._reset_per_invocation_state()
        with (
            self.init_session_folder(prefix=f"{args.prod}-{self.args_cmd}"),
            self.configure_logging(debug=args.debug),
            AnalyticsWrapper(self, args),
        ):
            # Collecting basic system information for troubleshooting
            LOG.info(
                "System info: %s | %s",
                platform.system(),
                platform.version(),
            )
            LOG.info(
                "Platform info: %s | %s",
                platform.platform(),
                platform.processor(),
            )
            LOG.info(
                "Python info: %s %s",
                platform.python_implementation(),
                platform.python_version(),
            )
            LOG.info("Installer version: %s", get_installer_version())

            try:
                self.check_requirements(args)
                self.execute(args)

            except AbortAction:
                raise
            except InstallerError as e:
                self._msg_unexpected_error(e)
                raise
            except Exception as e:
                LOG.exception("Uncaught error: %r", e)
                self._msg_unexpected_error(e)
                raise InstallerError from e
            except KeyboardInterrupt as e:
                # Reset the cursor to column 0 — the terminal echoed `^C` mid-line.
                print("")
                CONSOLE.msg("Processing interrupted. This may result in an inconsistent application state.")
                raise AbortAction from e

    def get_parser(self, sub_parsers):
        parser = sub_parsers.add_parser(self.args_cmd, parents=self.args_parser_parents)
        parser.set_defaults(func=self.execute_with_log)
        return parser

    def execute(self, args):
        raise NotImplementedError

    def run_cmd_retries(self, *cmd, timeout, retries, raise_on_non_zero=True, env=None, **popen_args):
        cmd_fail_exception = None
        while retries > 0:
            try:
                with self.start_cmd(*cmd, raise_on_non_zero=raise_on_non_zero, env=env, **popen_args) as (proc, *_):
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired as e:
                        LOG.warning("Command timed out. [%d] remaining attempts", retries - 1)
                        proc.kill()
                        raise CommandFailed from e
            except CommandFailed as e:
                cmd_fail_exception = e
            else:
                cmd_fail_exception = None
                break
            finally:
                retries -= 1

        if cmd_fail_exception and (
            isinstance(cmd_fail_exception.__cause__, subprocess.TimeoutExpired) or raise_on_non_zero
        ):
            raise cmd_fail_exception

    def run_cmd(
        self,
        *cmd,
        input=None,
        capture_json=False,
        capture_json_lines=False,
        capture_text=False,
        echo=False,
        raise_on_non_zero=True,
        env=None,
        **popen_args,
    ):
        with self.start_cmd(*cmd, raise_on_non_zero=raise_on_non_zero, env=env, **popen_args) as (proc, stdout, stderr):
            if input:
                proc.stdin.write(input)

            if echo:
                for line in stdout:
                    if line:
                        CONSOLE.msg(line)
            elif capture_text:
                return "\n".join(stdout)
            elif capture_json:
                try:
                    return json.loads("".join(stdout))
                except json.JSONDecodeError:
                    LOG.warning("Error decoding JSON from stdout")
                    return {}
            elif capture_json_lines:
                json_lines = []
                for idx, output_line in enumerate(stdout):
                    try:
                        json_lines.append(json.loads(output_line))
                    except json.JSONDecodeError:
                        LOG.warning(f"Error decoding JSON from stdout line #{idx}")
                return json_lines

    @contextlib.contextmanager
    def start_cmd(self, *cmd, raise_on_non_zero=True, env=None, redact=(), **popen_args):
        started = time.time()
        self._cmd_idx += 1

        # Censor secrets before they reach logs
        log_str = " ".join(str(part) for part in cmd)
        for secret in redact:
            if secret:
                log_str = log_str.replace(str(secret), "***")
        LOG.debug("Command [%04d]: [%s]", self._cmd_idx, log_str)

        if isinstance(env, dict):
            LOG.debug("Command [%04d] extra ENV: [%s]", self._cmd_idx, ", ".join(env.keys()))
            env = {**os.environ, **env}

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                env=env,
                **popen_args,
            )
        except FileNotFoundError as e:
            LOG.error("Command [%04d] failed to find the executable", self._cmd_idx)
            raise CommandFailed(self._cmd_idx, log_str, None) from e

        slug_cmd = re.sub(r"[^a-zA-Z]+", "-", log_str)[:100].strip("-")

        stdout_path, stderr_path = [
            self.session_folder.joinpath(f"{self._cmd_idx:04d}-{stream_name}-{slug_cmd}.txt")
            for stream_name in ("stdout", "stderr")
        ]

        try:
            try:
                with (
                    stream_iterator(proc, "stdout", stdout_path) as stdout_iter,
                    stream_iterator(proc, "stderr", stderr_path) as stderr_iter,
                ):
                    yield proc, stdout_iter, stderr_iter
            finally:
                proc.wait()
            if raise_on_non_zero and proc.returncode != 0:
                raise CommandFailed
        # We capture and raise CommandFailed to allow the client code to raise an empty CommandFailed exception
        # but still get a contextualized exception at the end
        except CommandFailed as e:
            raise CommandFailed(self._cmd_idx, log_str, proc.returncode) from e.__cause__
        finally:
            elapsed = time.time() - started
            LOG.info(
                "Command [%04d] returned [%s] in [%.3f] seconds. [%d] bytes in STDOUT, [%d] bytes in STDERR",
                self._cmd_idx,
                proc.returncode,
                elapsed,
                stdout_path.stat().st_size if stdout_path.exists() else 0,
                stderr_path.stat().st_size if stderr_path.exists() else 0,
            )


class Step:
    required: bool = True
    label = None

    def pre_execute(self, action, args):
        pass

    def execute(self, action, args):
        pass

    def on_action_success(self, action, args):
        pass

    def on_action_fail(self, action, args):
        pass

    def __str__(self):
        return self.label or self.__class__.__name__


class MultiStepAction(Action):
    steps: list[type[Step]]
    label: str = "Process"
    title: str = ""
    intro_text: list[str] = []

    def __init__(self):
        super().__init__()
        self.ctx = {}

    def _reset_per_invocation_state(self):
        super()._reset_per_invocation_state()
        self.ctx = {}

    def _print_intro_text(self, args):
        CONSOLE.space()
        for line in self.intro_text:
            CONSOLE.msg(line)

    def execute(self, args):
        CONSOLE.title(self.title)
        action_steps = [step_class() for step_class in self.steps]
        for step in action_steps:
            try:
                LOG.debug("Running step [%s] pre-execute", step)
                step.pre_execute(self, args)
            except AbortAction as e:
                LOG.info("Step [%s] pre-execute caused the action to abort", step)
                raise AbortAction(f"Step '{step.__class__.__name__}' pre-execute aborted") from e
            except InstallerError as e:
                LOG.info("Step [%s] pre-execute failed", step)
                raise e.__class__(f"Step '{step.__class__.__name__}' pre-execute failed") from e
            except Exception as e:
                LOG.exception("Step [%s] pre-execute had an unexpected error", step)
                raise InstallerError(f"Step '{step.__class__.__name__}' had an unexpected error") from e

        self._print_intro_text(args)
        CONSOLE.space()
        action_fail_exception = None
        action_fail_step = None
        for step in action_steps:
            with CONSOLE.start_partial() as partial:
                partial(f"{step.label}... ")
                try:
                    if action_fail_exception:
                        raise SkipStep
                    LOG.debug("Executing step [%s]", step)
                    step.execute(self, args)
                except SkipStep:
                    partial("SKIPPED")
                    continue
                except Exception as e:
                    partial("FAILED")
                    if not isinstance(e, InstallerError):
                        LOG.exception("Unexpected Exception executing step [%s]", step)
                    if step.required:
                        action_fail_exception = e
                        action_fail_step = step
                    else:
                        LOG.warning("Non-required step [%s] failed with: %s", step, e)
                else:
                    partial("OK")

        if action_fail_exception:
            CONSOLE.title(f"{self.label} FAILED")
        else:
            CONSOLE.title(f"{self.label} SUCCEEDED")

        for step in reversed(action_steps):
            try:
                if action_fail_exception is None:
                    LOG.debug("Running [%s] on-action-success", step)
                    step.on_action_success(self, args)
                else:
                    LOG.debug("Running [%s] on-action-fail", step)
                    step.on_action_fail(self, args)
            except Exception:
                LOG.exception("Post-execution of step [%s] failed", step)

        if action_fail_exception:
            exc_msg = f"Failed step: {action_fail_step.__class__.__name__}"
            exc_class = AbortAction if isinstance(action_fail_exception, AbortAction) else InstallerError
            raise exc_class(exc_msg) from action_fail_exception


class Installer:
    def __init__(self):
        self.parser = argparse.ArgumentParser(description="DataKitchen Installer")
        self.parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
        self.parser.add_argument(
            "--no-analytics",
            default=os.getenv("DK_INSTALLER_ANALYTICS", "yes").lower() == "yes",
            dest="send_analytics_data",
            action="store_false",
            help="Disable from sending anonymous analytics data to Datakitchen. Default is to send.",
        )
        self.sub_parsers = self.parser.add_subparsers(
            help="Products",
            required=True,
            title="product",
            description="Select which product to install or perform other actions",
        )

    def run(self, def_args=None):
        # def_args has to be None to preserve the argparser behavior when only part of the arguments are used
        args = self.parser.parse_args(def_args or None)

        if not hasattr(args, "func"):
            self.parser.print_usage()
            return 2

        CONSOLE.title("DataKitchen DataOps Installer")

        try:
            args.func(args)
        except AbortAction:
            return 1
        except Exception:
            if args.debug:
                pdb.post_mortem()
            return 2
        else:
            return 0

    def add_product(self, prefix, actions, defaults=None):
        prod_parser = self.sub_parsers.add_parser(prefix)
        prod_parser.set_defaults(prod=prefix, **(defaults or {}))
        prod_sub_parsers = prod_parser.add_subparsers(required=True, title="action")

        for action in actions:
            action.get_parser(prod_sub_parsers)


class Menu:
    def __init__(self, callback, name, title=None, width=40):
        self.callback = callback
        self.name = name
        self.title = name if title is None else title
        self.width = width
        self.options = []

    def add_option(self, label, *args, **kwargs):
        self.options.append((label, None, args, kwargs))

    def add_submenu(self, label, menu):
        self.options.append((label, menu, None, None))

    def _print_option(self, option, label):
        print(
            textwrap.fill(
                label,
                width=self.width,
                initial_indent=f" {option:>2}. ",
                subsequent_indent="     ",
            )
        )

    def run(self, parent=None):
        while True:
            print("")
            print("=" * self.width)
            print(self.title.center(self.width))
            print("=" * self.width)
            for opt, (label, *_) in enumerate(self.options, 1):
                self._print_option(opt, label)
            if parent:
                self._print_option(opt + 1, f"Return to {parent.name} menu")
            self._print_option(0, "Exit")
            print("=" * self.width)
            print("")

            while True:
                try:
                    chosen_opt = input("Enter your choice: ")
                except (KeyboardInterrupt, EOFError):
                    chosen_opt = "0"

                if chosen_opt in [str(n + 1) for n in range(len(self.options))]:
                    _, menu, args, kwargs = self.options[int(chosen_opt) - 1]
                    if menu:
                        if menu.run(self):
                            break
                        else:
                            return False
                    else:
                        self.callback(*args, **kwargs)
                        break
                elif parent and chosen_opt == str(len(self.options) + 1):
                    return True
                elif chosen_opt in ("0", "q"):
                    return False
                elif chosen_opt:
                    print(f"'{chosen_opt}' is not a valid option.", end=" ")


#
# Common blocks shared by more than one step/action
#

REQ_DOCKER = Requirement(
    "DOCKER",
    ("docker", "-v"),
    ("The prerequisite Docker is not available.", "Install Docker and try again."),
    label="Docker installed",
)
REQ_DOCKER_DAEMON = Requirement(
    "DOCKER_ENGINE",
    ("docker", "system", "events", "--since=0m", "--until=0m"),
    ("The Docker engine is not running.", "Start the Docker engine and try again."),
    label="Docker engine running",
)
REQ_TESTGEN_IMAGE = Requirement(
    "TESTGEN_IMAGE",
    ("docker", "manifest", "inspect", "{image}"),
    (
        "The Docker engine could not access TestGen's image.",
        "Make sure your networking policy allows Docker to pull the {image} image.",
    ),
    label="TestGen image reachable",
)


def get_uv_asset(prod: str) -> tuple[str, str]:
    """Return (asset_name, sha256) for the current platform, or raise AbortAction."""
    key = (platform.system(), platform.machine())
    try:
        return UV_ASSETS[key]
    except KeyError:
        supported = ", ".join(f"{s}/{m}" for s, m in UV_ASSETS)
        CONSOLE.msg(f"No prebuilt uv binary available for platform {key[0]}/{key[1]}.")
        CONSOLE.msg(f"Supported: {supported}.")
        CONSOLE.msg(
            "Install uv manually (https://docs.astral.sh/uv/getting-started/installation/) and re-run, "
            f"or {command_hint(prod, 'install --docker', 'Install TestGen')} to use Docker."
        )
        raise AbortAction


def resolve_uv_path(data_folder: pathlib.Path) -> typing.Optional[str]:
    """Return the path to a usable ``uv`` binary, preferring ``PATH`` then the
    installer-local download from a prior bootstrap. Returns ``None`` if neither
    is available — callers decide whether that's fatal.
    """
    if uv_on_path := shutil.which("uv"):
        return uv_on_path
    bin_name = "uv.exe" if platform.system() == "Windows" else "uv"
    local_uv = data_folder / UV_BIN_SUBDIR / bin_name
    if local_uv.exists():
        return str(local_uv)
    return None


class UvBootstrapStep(Step):
    label = "Preparing the Python environment"

    def pre_execute(self, action, args):
        # Resolve uv eagerly so later steps' pre_execute hooks can use it.
        # If uv has to be downloaded, ctx stays unset until execute runs
        # and this step's download path populates it.
        if uv_path := resolve_uv_path(action.data_folder):
            action.ctx["uv_path"] = uv_path

    def execute(self, action, args):
        if uv_path := action.ctx.get("uv_path"):
            LOG.info("Using existing uv at %s", uv_path)
            action.analytics.additional_properties["uv_source"] = "existing"
            self._capture_uv_version(action, uv_path)
            return

        asset_name, expected_sha256 = get_uv_asset(args.prod)
        url = UV_RELEASE_URL_TPL.format(version=UV_VERSION, asset=asset_name)
        action.analytics.additional_properties["uv_source"] = "download"

        bin_name = "uv.exe" if platform.system() == "Windows" else "uv"
        target_path = action.data_folder / UV_BIN_SUBDIR / bin_name

        last_exc = None
        for attempt in range(1, UV_DOWNLOAD_RETRIES + 1):
            try:
                self._download_and_install(action, url, asset_name, expected_sha256)
            except InstallerError:
                # Deterministic failure (SHA256 mismatch, malformed archive,
                # unknown asset format) — retrying won't help, and on a
                # SHA256 mismatch, repeated attempts waste minutes only to
                # report the same MITM-or-corrupted-release error.
                if target_path.exists():
                    target_path.unlink()
                raise
            except Exception as e:
                LOG.warning("uv bootstrap attempt %d/%d failed: %s", attempt, UV_DOWNLOAD_RETRIES, e)
                last_exc = e
                if target_path.exists():
                    target_path.unlink()
                # Exponential backoff between attempts to ride out transient
                # network blips and registry rate-limits.
                if attempt < UV_DOWNLOAD_RETRIES:
                    time.sleep(2**attempt)
            else:
                action.ctx["uv_path"] = str(target_path)
                self._capture_uv_version(action, str(target_path))
                return
        raise InstallerError(f"Failed to bootstrap uv after {UV_DOWNLOAD_RETRIES} attempts: {last_exc}") from last_exc

    def _download_and_install(self, action, url: str, asset_name: str, expected_sha256: str) -> None:
        bin_dir = action.data_folder / UV_BIN_SUBDIR
        bin_dir.mkdir(parents=True, exist_ok=True)
        archive_path = bin_dir / asset_name

        try:
            LOG.info("Downloading uv %s from %s", UV_VERSION, url)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(url, timeout=UV_DOWNLOAD_TIMEOUT, context=ssl_context) as resp:
                archive_path.write_bytes(resp.read())

            actual_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            if actual_sha256 != expected_sha256:
                raise InstallerError(
                    f"SHA256 mismatch for {asset_name}: expected {expected_sha256}, got {actual_sha256}"
                )

            self._extract_uv_binary(archive_path, asset_name, bin_dir)

            if platform.system() != "Windows":
                (bin_dir / "uv").chmod(0o755)
        finally:
            if archive_path.exists():
                archive_path.unlink()

    @staticmethod
    def _extract_uv_binary(archive_path: pathlib.Path, asset_name: str, bin_dir: pathlib.Path) -> None:
        if asset_name.endswith(".tar.gz"):
            with tarfile.open(archive_path, "r:gz") as tf:
                for member in tf.getmembers():
                    if member.isfile() and pathlib.PurePosixPath(member.name).name == "uv":
                        src = tf.extractfile(member)
                        if src is None:
                            continue
                        (bin_dir / "uv").write_bytes(src.read())
                        return
            raise InstallerError(f"Could not find 'uv' binary in archive {asset_name}")
        if asset_name.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                for name in zf.namelist():
                    if pathlib.PurePosixPath(name).name == "uv.exe":
                        (bin_dir / "uv.exe").write_bytes(zf.read(name))
                        return
            raise InstallerError(f"Could not find 'uv.exe' in archive {asset_name}")
        raise InstallerError(f"Unexpected asset format: {asset_name}")

    @staticmethod
    def _capture_uv_version(action, uv_path: str) -> None:
        try:
            output = action.run_cmd(uv_path, "--version", capture_text=True, raise_on_non_zero=False)
        except Exception:
            LOG.exception("Failed to capture uv version")
            return
        # `uv --version` prints e.g. "uv 0.11.7 (abcd1234 2024-09-15)".
        if output and (match := re.match(r"uv\s+(\S+)", output.strip())):
            action.analytics.additional_properties["uv_version"] = match.group(1)


class AnalyticsMultiStepAction(MultiStepAction):
    ANALYTICS_DISCLAIMER = [
        "DataKitchen has enabled anonymous aggregate user behavior analytics.",
        "Read the analytics documentation (and how to opt-out) here:",
        "https://docs.datakitchen.io/testgen/anonymous-analytics/",
    ]

    def _print_intro_text(self, args):
        super()._print_intro_text(args)

        if args.send_analytics_data:
            CONSOLE.space()
            for line in self.ANALYTICS_DISCLAIMER:
                CONSOLE.msg(line)


class ComposeActionMixin:
    def get_compose_file_path(self, args):
        return simplify_path(self.data_folder.joinpath(args.compose_file_name))

    def get_status(self, args) -> dict[str, str]:
        compose_installs = self.run_cmd("docker", "compose", "ls", "--format=json", capture_json=True)
        for install in compose_installs:
            if install["Name"] == args.compose_project_name:
                return install
        return {}

    def get_volumes(self, args) -> list[dict[str, str]]:
        label = f"com.docker.compose.project={args.compose_project_name}"
        volumes = self.run_cmd("docker", "volume", "list", "--format=json", capture_json_lines=True)
        return [v for v in volumes if label in v.get("Labels", "")]

    def delete_compose_containers(self, args):
        CONSOLE.title(f"Delete {args.prod_name} instance")
        try:
            self.run_cmd(
                "docker",
                "compose",
                "-f",
                self.get_compose_file_path(args),
                "down",
                *([] if args.keep_images else ["--rmi", "all"]),
                "--volumes",
                echo=True,
                raise_on_non_zero=True,
            )
        except CommandFailed:
            CONSOLE.msg("Could NOT delete the Docker resources")
            raise AbortAction
        else:
            if not args.keep_config:
                remove_path(self.get_compose_file_path(args))
            remove_path(self.data_folder / CREDENTIALS_FILE.format(args.prod))
            CONSOLE.msg("Docker containers and volumes deleted")

    def delete_compose_network(self):
        try:
            self.run_cmd("docker", "network", "rm", DOCKER_NETWORK, raise_on_non_zero=True)
        except CommandFailed:
            LOG.info(f"Could not delete Docker network '{DOCKER_NETWORK}'")
        else:
            CONSOLE.msg("Docker network deleted")

    def delete_compose_volumes(self, args):
        if volumes := self.get_volumes(args):
            try:
                self.run_cmd(
                    "docker",
                    "volume",
                    "rm",
                    *[v["Name"] for v in volumes],
                )
            except CommandFailed:
                CONSOLE.msg("Could NOT delete docker volumes. Please delete them manually")
                raise AbortAction
            else:
                CONSOLE.msg("Docker volumes deleted")


class ComposeDeleteAction(Action, ComposeActionMixin):
    args_cmd = "delete"
    requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON]

    def execute(self, args):
        if self.get_compose_file_path(args).exists():
            self.delete_compose_containers(args)
            self.delete_compose_network()
        else:
            # Trying to delete the network before any exception
            self.delete_compose_network()
            # Trying to delete dangling volumes
            self.delete_compose_volumes(args)

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--keep-images",
            action="store_true",
            help="Does not delete the images when deleting the installation",
        )
        parser.add_argument(
            "--keep-config",
            action="store_true",
            help="Does not delete the compose config file when deleting the installation",
        )
        return parser


class ComposeVerifyExistingInstallStep(Step):
    label = "Verifying existing installation"

    def pre_execute(self, action, args):
        status = action.get_status(args)
        volumes = action.get_volumes(args)
        if status or volumes:
            CONSOLE.msg(
                f"Found {args.prod_name} docker compose containers and/or volumes. If a previous attempt to run this",
            )
            CONSOLE.msg(
                f"installer failed, {command_hint(args.prod, 'delete', f'Uninstall {args.prod_name}')} before trying again."
            )
            CONSOLE.space()
            if volumes:
                status["Volumes"] = ", ".join([v.get("Name", "N/A") for v in volumes])
            for k, v in status.items():
                CONSOLE.msg(f"{k:>15}: {v}")
            raise AbortAction


class ComposePullImagesStep(Step):
    label = "Pulling docker images"
    required = False

    def execute(self, action, args):
        action.analytics.additional_properties["pull_timeout"] = args.pull_timeout

        try:
            with action.start_cmd(
                "docker",
                "compose",
                "-f",
                action.get_compose_file_path(args),
                "pull",
                "--policy",
                "always",
            ) as (proc, _, stderr):
                complete_re = re.compile(r"^ ([0-9a-f]{12}) (Already exists|Pull complete)")
                hash_discovery_re = re.compile(r"^ ([0-9a-f]{12}) (Already exists|Pulling fs layer|Waiting)")
                discovering = True
                hashes: set[str] = set()
                completed_count = 0
                reported = 0
                try:
                    for line in stderr:
                        if disc_match := hash_discovery_re.match(line):
                            hashes.add(disc_match.group(1))
                        elif hashes and discovering:
                            discovering = False
                        if complete_re.match(line):
                            completed_count += 1
                        if not discovering:
                            to_be_reported = list(range(reported, int(completed_count * 100 / len(hashes)) + 1, 20))[1:]
                            for progress in to_be_reported:
                                CONSOLE.partial(f"{progress}% ")
                                reported = progress
                except Exception:
                    pass
        except CommandFailed:
            # Pulling the images before starting is not mandatory, so we just proceed if it fails
            raise SkipStep

    def on_action_fail(self, action, args):
        images = action.run_cmd(
            "docker",
            "compose",
            "-f",
            action.get_compose_file_path(args),
            "images",
            "--format",
            "json",
            capture_json=True,
        )
        image_repo_tags = [":".join((img["Repository"], img["Tag"])) for img in images]
        collect_images_digest(action, image_repo_tags)


class ComposeStartStep(Step):
    label = "Starting docker compose application"

    def execute(self, action, args):
        action.run_cmd(
            "docker",
            "compose",
            "-f",
            action.get_compose_file_path(args),
            "up",
            "--wait",
        )

    def on_action_fail(self, action, args):
        if action.args_cmd == "install":
            action.run_cmd(
                "docker",
                "compose",
                "-f",
                action.get_compose_file_path(args),
                "down",
                "--volumes",
            )


class ComposeStopStep(Step):
    label = "Stopping docker compose application"

    def execute(self, action, args):
        action.run_cmd(
            "docker",
            "compose",
            "-f",
            action.get_compose_file_path(args),
            "down",
        )


class DockerNetworkStep(Step):
    label = "Creating a Docker network"

    def execute(self, action, args):
        try:
            action.run_cmd(
                "docker",
                "network",
                "inspect",
                DOCKER_NETWORK,
            )
            LOG.info(f"Re-using existing Docker network '{DOCKER_NETWORK}'")
            raise SkipStep
        except CommandFailed:
            LOG.info(f"Creating Docker network '{DOCKER_NETWORK}'")
            action.run_cmd(
                "docker",
                "network",
                "create",
                "--subnet",
                DOCKER_NETWORK_SUBNET,
                "--gateway",
                # IP at index 0 is unavailable
                str(ipaddress.IPv4Network(DOCKER_NETWORK_SUBNET)[1]),
                DOCKER_NETWORK,
            )


class CreateComposeFileStepBase(Step):
    label = "Creating the docker-compose definition file"

    def pre_execute(self, action, args):
        compose_path = action.get_compose_file_path(args)
        using_existing = compose_path.exists()

        action.ctx["using_existing"] = using_existing
        action.analytics.additional_properties["existing_compose_file"] = using_existing

    def execute(self, action, args):
        compose_path = action.get_compose_file_path(args)
        if action.ctx.get("using_existing"):
            LOG.info("Re-using existing [%s]", compose_path)
            raise SkipStep
        else:
            LOG.info("Creating [%s]", compose_path)
            compose_contents = self.get_compose_file_contents(action, args)
            compose_path.write_text(compose_contents)

    def get_compose_file_contents(self, action, args) -> str:
        raise NotImplementedError

    def on_action_success(self, action, args):
        CONSOLE.space()
        if action.ctx.get("using_existing"):
            CONSOLE.msg(f"Used existing compose file: {action.get_compose_file_path(args)}.")
        else:
            CONSOLE.msg(f"Created new {args.compose_file_name} file.")

    def on_action_fail(self, action, args):
        # We keep the file around for inspection when in debug mode
        if not args.debug and not action.ctx.get("using_existing"):
            remove_path(action.get_compose_file_path(args))


def get_observability_version(action, args):
    installed_packages = action.run_cmd(
        "docker",
        "compose",
        "-f",
        action.get_compose_file_path(args),
        "exec",
        "-it",
        "observability_backend",
        "/usr/local/bin/pip",
        "list",
        "--format=json",
        capture_json=True,
    )

    try:
        return [p["version"] for p in installed_packages if p["name"].startswith("Observability")][0]
    except Exception:
        pass


#
# Action and Steps implementations
#


class ObsComposeStartStep(ComposeStartStep):
    def on_action_success(self, action, args):
        super().on_action_success(action, args)
        if version := get_observability_version(action, args):
            if version_before := action.ctx.get("before_upgrade_version"):
                CONSOLE.msg(f"Upgraded from version {version_before} to {version}.")
            else:
                CONSOLE.msg(f"Installed version: {version}")


class ObsFetchCurrentVersionStep(ComposeStartStep):
    label = "Checking current version"

    def execute(self, action, args):
        try:
            action.ctx["before_upgrade_version"] = get_observability_version(action, args)
        except CommandFailed:
            raise AbortAction
        else:
            action.ctx["obs_running"] = True

    def on_action_fail(self, action, args):
        if not action.ctx.get("obs_running"):
            CONSOLE.msg("Failed to fetch the current version. Observability has to be running to be upgraded.")


class ObsUpgradeAction(MultiStepAction, ComposeActionMixin):
    label = "Upgrade"
    title = "Upgrade Observability"
    args_cmd = "upgrade"
    requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON]

    steps = [
        ObsFetchCurrentVersionStep,
        ComposePullImagesStep,
        ObsComposeStartStep,
    ]

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--pull-timeout",
            type=int,
            action="store",
            default=OBS_PULL_TIMEOUT,
            help=(
                "Maximum amount of time in minutes that Docker will be allowed to pull the images. "
                "Defaults to '%(default)s'"
            ),
        )
        return parser


class ObsDataInitializationStep(Step):
    label = "Initializing the database"
    _user_data = {}

    def execute(self, action, args):
        self._user_data = {"password": generate_password(), **DEFAULT_USER_DATA}
        action.ctx["init_data"] = action.run_cmd(
            "docker",
            "compose",
            "-f",
            action.get_compose_file_path(args),
            "exec",
            "-it",
            "observability_backend",
            "/dk/bin/cli",
            "init",
            "--demo",
            "--topics",
            "--json",
            input=json.dumps(self._user_data).encode(),
            capture_json=True,
        )

    def on_action_success(self, action, args):
        cred_file_path = action.data_folder.joinpath(CREDENTIALS_FILE.format(args.prod))
        with CONSOLE.tee(cred_file_path) as console_tee:
            for service, url_tpl in OBS_SERVICES_URLS:
                console_tee(f"{service:>20}: {url_tpl.format('http://localhost', args.port)}")
            console_tee("")
            console_tee(f"Username: {self._user_data['username']}")
            console_tee(f"Password: {self._user_data['password']}", skip_logging=True)

        CONSOLE.msg(f"(Credentials also written to {simplify_path(cred_file_path)})")


class ObsGenerateDemoConfigStep(Step):
    label = "Generating the demo configuration"
    required = False

    def execute(self, action, args):
        try:
            init_data = action.ctx["init_data"]
        except KeyError:
            LOG.info("Skipping generating the demo config file because the initialization data is not available")
            raise SkipStep
        else:
            config = {
                "api_key": init_data["service_account_key"],
                "project_id": init_data["project_id"],
                "cloud_provider": "azure",
                "api_host": BASE_API_URL_TPL.format(args.port),
            }
            with open(action.data_folder / DEMO_CONFIG_FILE, "w") as file:
                file.write(json.dumps(config))


class ObsCreateComposeFileStep(CreateComposeFileStepBase):
    def get_compose_file_contents(self, action, args):
        action.analytics.additional_properties["used_custom_image"] = any(
            (
                args.ui_image != OBS_DEF_UI_IMAGE,
                args.be_image != OBS_DEF_BE_IMAGE,
            )
        )
        compose_file_content = textwrap.dedent(
            """
            name: ${DK_OBSERVABILITY_COMPOSE_NAME:-}

            x-database-config: &database_config
              MYSQL_USER: ${DK_OBSERVABILITY_MYSQL_USER:-observability}
              MYSQL_PASSWORD: ${DK_OBSERVABILITY_MYSQL_PASSWORD:-}

            x-database-client-config: &database_client_config
              MYSQL_SERVICE_HOST: ${DK_OBSERVABILITY_MYSQL_HOST:-database}
              MYSQL_SERVICE_PORT: ${DK_OBSERVABILITY_MYSQL_PORT:-3306}

            services:
              broker:
                container_name: kafka
                image: apache/kafka:3.9.1
                restart: always
                expose: ["9092", "9093"]
                environment:
                  # Core KRaft mode
                  KAFKA_PROCESS_ROLES: broker,controller
                  KAFKA_NODE_ID: 1
                  KAFKA_CONTROLLER_QUORUM_VOTERS: 1@broker:9093
                  KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
                  # Networking
                  KAFKA_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093
                  KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://broker:9092
                  KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
                  KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
                  KAFKA_LOG4J_LOGGER_kafka_server_DefaultAutoTopicCreationManager: WARN
                  # Transactions
                  KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
                  KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
                  # Topics
                  KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
                  # Other
                  KAFKA_LOG4J_LOGGERS: "kafka=WARN,org.apache.kaf*ka=WARN"
                volumes:
                  - kafka_data:/var/lib/kafka/data
                healthcheck:
                  test: [ "CMD", "/opt/kafka/bin/kafka-topics.sh", "--bootstrap-server", "localhost:9092", "--list" ]
                  interval: 10s
                  timeout: 5s
                  retries: 5

              database:
                container_name: mysql
                image: mysql:8.4
                restart: always
                expose: ["3306"]
                environment:
                  MYSQL_ROOT_PASSWORD: ${DK_OBSERVABILITY_MYSQL_ROOT_PASSWORD:-}
                  MYSQL_DATABASE: datakitchen
                  <<: *database_config
                volumes:
                  - mysql_data:/var/lib/mysql

                healthcheck:
                  test: [ "CMD", "mysqladmin", "ping", "-h", "localhost" ]
                  interval: 5s
                  retries: 5

              observability_data_init:
                container_name: data-init
                image: ${DK_OBSERVABILITY_BE_IMAGE:-}
                restart: on-failure
                depends_on:
                  database:
                    condition: service_healthy
                  broker:
                    condition: service_healthy
                environment:
                  OBSERVABILITY_CONFIG: minikube
                  <<: [*database_config, *database_client_config]
                entrypoint: /dk/bin/cli
                command:  migrate

              observability_backend:
                container_name: back-end
                image: ${DK_OBSERVABILITY_BE_IMAGE:-}
                restart: always
                depends_on:
                  observability_data_init:
                    condition: service_completed_successfully
                expose: ["5000", "5001", "5003"]
                environment:
                  OBSERVABILITY_CONFIG: minikube
                  KAFKA_SERVICE_HOST: ${DK_OBSERVABILITY_KAFKA_HOST:-broker}
                  KAFKA_SERVICE_PORT: ${DK_OBSERVABILITY_KAFKA_PORT:-9092}
                  <<: [*database_config, *database_client_config]
                healthcheck:
                  test: [ "CMD", "/bin/sh", "-c", "supervisorctl -c /dk/supervisord.conf status | grep -q RUNNING" ]
                  interval: 5s
                  retries: 10

              observability_ui:
                container_name: user-interface
                image: ${DK_OBSERVABILITY_UI_IMAGE:-}
                restart: always
                depends_on:
                  observability_backend:
                    condition: service_healthy
                environment:
                  OBSERVABILITY_AUTH_METHOD: ${DK_OBSERVABILITY_AUTH_METHOD:-basic}
                links:
                  - "observability_backend:observability-api"
                  - "observability_backend:event-api"
                  - "observability_backend:agent-api"
                ports:
                  - "${DK_OBSERVABILITY_HTTP_PORT:-8082}:8082"

            networks:
              datakitchen:
                name: "${DK_OBSERVABILITY_NETWORK_NAME:-}"
                external: true

            volumes:
              mysql_data:
              kafka_data:
            """
        )

        defaults = {
            "DK_OBSERVABILITY_COMPOSE_NAME": args.compose_project_name,
            "DK_OBSERVABILITY_MYSQL_PASSWORD": generate_password(),
            "DK_OBSERVABILITY_MYSQL_ROOT_PASSWORD": generate_password(),
            "DK_OBSERVABILITY_HTTP_PORT": str(args.port),
            "DK_OBSERVABILITY_UI_IMAGE": args.ui_image,
            "DK_OBSERVABILITY_BE_IMAGE": args.be_image,
            "DK_OBSERVABILITY_NETWORK_NAME": DOCKER_NETWORK,
        }

        compose_file_content = COMPOSE_VAR_RE.sub(
            lambda m: f"${{{m.group(1)}:-{defaults.get(m.group(1), m.group(2))}}}",
            compose_file_content,
        )

        return compose_file_content


class ObsInstallAction(AnalyticsMultiStepAction, ComposeActionMixin):
    steps = [
        ComposeVerifyExistingInstallStep,
        DockerNetworkStep,
        ObsCreateComposeFileStep,
        ComposePullImagesStep,
        ObsComposeStartStep,
        ObsDataInitializationStep,
        ObsGenerateDemoConfigStep,
    ]

    label = "Installation"
    title = "Install Observability"
    intro_text = ["This process may take 5~15 minutes depending on your system resources and network speed."]

    args_cmd = "install"
    requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON]

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--port",
            dest="port",
            action="store",
            default=OBS_DEFAULT_PORT,
            help="Which port will be used to access Observability UI. Defaults to %(default)s",
        )
        parser.add_argument(
            "--pull-timeout",
            type=int,
            action="store",
            default=OBS_PULL_TIMEOUT,
            help=(
                "Maximum amount of time in minutes that Docker will be allowed to pull the images. "
                "Defaults to '%(default)s'"
            ),
        )
        parser.add_argument(
            "--be-image",
            dest="be_image",
            action="store",
            default=OBS_DEF_BE_IMAGE,
            help="Observability backend image to use for the install. Defaults to %(default)s",
        )
        parser.add_argument(
            "--ui-image",
            dest="ui_image",
            action="store",
            default=OBS_DEF_UI_IMAGE,
            help="Observability UI image to use for the install. Defaults to %(default)s",
        )


class DemoContainerAction(Action):
    requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON]

    def run_dk_demo_container(self, command: str):
        self.run_cmd(
            "docker",
            "run",
            "--rm",
            "--mount",
            f"type=bind,source={self.data_folder / DEMO_CONFIG_FILE},target=/dk/{DEMO_CONFIG_FILE}",
            "--name",
            DEMO_CONTAINER_NAME,
            "--network",
            DOCKER_NETWORK,
            "--add-host",
            "host.docker.internal:host-gateway",
            DEMO_IMAGE,
            command,
            echo=True,
        )


class ObsRunDemoAction(DemoContainerAction):
    args_cmd = "run-demo"

    def execute(self, args):
        CONSOLE.title("Run Observability demo")
        CONSOLE.msg("This process may take 2~5 minutes depending on your system resources and network speed.")
        CONSOLE.space()

        try:
            self.run_dk_demo_container("obs-run-demo")
        except Exception:
            CONSOLE.title("Demo FAILED")
            CONSOLE.space()
            CONSOLE.msg(
                f"To retry the demo, first {command_hint(args.prod, 'delete-demo', f'Delete {args.prod_name} demo data')}."
            )
        else:
            CONSOLE.title("Demo SUCCEEDED")


class ObsDeleteDemoAction(DemoContainerAction):
    args_cmd = "delete-demo"

    def execute(self, args):
        CONSOLE.title("Delete Observability demo")
        self.run_dk_demo_container("obs-delete-demo")
        CONSOLE.title("Demo data DELETED")


class ObsRunHeartbeatDemoAction(DemoContainerAction):
    args_cmd = "run-heartbeat-demo"

    def execute(self, args):
        CONSOLE.title("Run Observability Heartbeat demo")
        try:
            self.run_dk_demo_container("obs-heartbeat-demo")
        except KeyboardInterrupt:
            # Reset the cursor to column 0 — the terminal echoed `^C` mid-line.
            print("")
            CONSOLE.msg("Observability Heartbeat demo stopped.")


class UpdateComposeFileStep(Step):
    label = "Updating the Docker compose file"

    def __init__(self):
        self.update_version = False
        self.update_analytics = False
        self.update_token = False
        self.update_base_url = False
        self.update_api_port = False
        super().__init__()

    def pre_execute(self, action, args):
        action.analytics.additional_properties["version_verify_skipped"] = args.skip_verify

        CONSOLE.space()

        contents = action.get_compose_file_path(args).read_text()
        if args.skip_verify:
            self.update_version = True
        else:
            try:
                output = run_testgen_cli(action, args, "--help", capture_text=True)
                version_match = re.search(r"TestGen\s(?:[a-zA-Z]+\s)*([0-9.]*)", output)
                current_version = version_match.group(1)

                image_match = re.search(r"image:\s*(datakitchen.+):.+\n", contents)
                docker_image = image_match.group(1)
                latest_version = "unknown"

                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                resp = urllib.request.urlopen(TESTGEN_LATEST_VERSIONS_URL, timeout=3, context=ssl_context)
                if resp.code == 200:
                    json_data = json.loads(resp.read().decode("utf-8"))
                    latest_version = json_data.get("docker", {}).get(docker_image)
            except Exception:
                CONSOLE.msg("Current version: unknown")
                CONSOLE.msg("Latest version: unknown")
                self.update_version = True
            else:
                CONSOLE.msg(f"Current version: {current_version}")
                CONSOLE.msg(f"Latest version: {latest_version}")

                if current_version != latest_version:
                    self.update_version = True
                else:
                    CONSOLE.msg("Application is already up-to-date.")

        if args.send_analytics_data:
            self.update_analytics = "TG_INSTANCE_ID" not in contents
        else:
            if not re.findall(r"TG_ANALYTICS:\s*no", contents):
                self.update_analytics = True
                CONSOLE.msg("Analytics will be disabled.")

        self.update_token = "TG_JWT_HASHING_KEY" not in contents

        self.update_base_url = "TG_UI_BASE_URL" not in contents
        if self.update_base_url:
            port_match = re.search(rf"- (\d+):{TESTGEN_DEFAULT_PORT}", contents)
            port = port_match.group(1) if port_match else str(TESTGEN_DEFAULT_PORT)
            protocol = "https" if "SSL_CERT_FILE" in contents else "http"
            self._base_url = f"{protocol}://localhost:{port}"

        self.update_api_port = bool(
            re.search(rf"- \d+:{TESTGEN_DEFAULT_PORT}\b", contents)
            and not re.search(rf"- \d+:{TESTGEN_DEFAULT_API_PORT}\b", contents)
        )

        if not any(
            (
                self.update_version,
                self.update_analytics,
                self.update_token,
                self.update_base_url,
                self.update_api_port,
            )
        ):
            CONSOLE.msg("No changes will be applied.")
            raise AbortAction

    def execute(self, action, args):
        if not any(
            (
                self.update_version,
                self.update_analytics,
                self.update_token,
                self.update_base_url,
                self.update_api_port,
            )
        ):
            raise SkipStep

        contents = action.get_compose_file_path(args).read_text()
        if self.update_version:
            contents = re.sub(r"(image:\s*datakitchen.+:).+\n", rf"\1v{TESTGEN_MAJOR_VERSION}\n", contents)

        if self.update_analytics:
            if args.send_analytics_data:
                if "TG_INSTANCE_ID" not in contents:
                    match = re.search(r"^([ \t]+)TG_METADATA_DB_HOST:.*$", contents, flags=re.M)
                    var = f"\n{match.group(1)}TG_INSTANCE_ID: {action.analytics.get_instance_id()}"
                    contents = contents[0 : match.end()] + match.group(1) + var + contents[match.end() :]
            else:
                if "TG_ANALYTICS" in contents:
                    contents = re.sub(r"^(\s*TG_ANALYTICS:).*$", r"\1 no", contents, flags=re.M)
                else:
                    match = re.search(r"^([ \t]+)TG_METADATA_DB_HOST:.*$", contents, flags=re.M)
                    contents = (
                        contents[0 : match.end()]
                        + match.group(1)
                        + f"\n{match.group(1)}TG_ANALYTICS: no"
                        + contents[match.end() :]
                    )

        if self.update_token:
            match = re.search(r"^([ \t]+)TG_METADATA_DB_HOST:.*$", contents, flags=re.M)
            var = f"\n{match.group(1)}TG_JWT_HASHING_KEY: {str(base64.b64encode(random.randbytes(32)), 'ascii')}"
            contents = contents[0 : match.end()] + match.group(1) + var + contents[match.end() :]

        if self.update_base_url:
            match = re.search(r"^([ \t]+)TG_METADATA_DB_HOST:.*$", contents, flags=re.M)
            var = f"\n{match.group(1)}TG_UI_BASE_URL: {self._base_url}"
            contents = contents[0 : match.end()] + var + contents[match.end() :]

        if self.update_api_port:
            match = re.search(rf"^([ \t]+)- \d+:{TESTGEN_DEFAULT_PORT}\b.*$", contents, flags=re.M)
            new_mapping = f"\n{match.group(1)}- {TESTGEN_DEFAULT_API_PORT}:{TESTGEN_DEFAULT_API_PORT}"
            contents = contents[0 : match.end()] + new_mapping + contents[match.end() :]

        action.get_compose_file_path(args).write_text(contents)


class TestGenCreateDockerComposeFileStep(CreateComposeFileStepBase):
    label = "Creating the docker-compose definition file"

    def __init__(self):
        self.username = None
        self.password = None

    def pre_execute(self, action, args):
        super().pre_execute(action, args)
        if action.ctx.get("using_existing"):
            self.username, self.password = self.get_credentials_from_compose_file(
                action.get_compose_file_path(args).read_text()
            )
        else:
            self.username = DEFAULT_USER_DATA["username"]
            self.password = generate_password()

        if not all([self.username, self.password]):
            CONSOLE.msg(
                f"Unable to retrieve username and password from {action.get_compose_file_path(args).absolute()}"
            )
            raise AbortAction

        if args.ssl_cert_file and not args.ssl_key_file or not args.ssl_cert_file and args.ssl_key_file:
            CONSOLE.msg("Both --ssl-cert-file and --ssl-key-file must be provided to use SSL certificates.")
            raise AbortAction

    def on_action_success(self, action, args):
        super().on_action_success(action, args)
        cred_file_path = action.data_folder.joinpath(CREDENTIALS_FILE.format(args.prod))
        with CONSOLE.tee(cred_file_path) as console_tee:
            console_tee(f"User Interface: {get_tg_url(args, args.port)}")
            console_tee(f"API & MCP:      {get_tg_url(args, args.api_port)}")
            console_tee("")
            console_tee(f"Username: {self.username}")
            console_tee(f"Password: {self.password}", skip_logging=True)

        CONSOLE.msg(f"(Credentials also written to {simplify_path(cred_file_path)})")

    def get_credentials_from_compose_file(self, file_contents):
        username = None
        password = None
        for line in file_contents.split("\n"):
            if line.strip().startswith("TESTGEN_USERNAME:"):
                username = line.replace("TESTGEN_USERNAME:", "").strip()
            if line.strip().startswith("TESTGEN_PASSWORD:"):
                password = line.replace("TESTGEN_PASSWORD:", "").strip()
            if username and password:
                break
        return username, password

    def get_compose_file_contents(self, action, args):
        action.analytics.additional_properties["used_custom_cert"] = args.ssl_cert_file and args.ssl_key_file
        action.analytics.additional_properties["used_custom_image"] = args.image != TESTGEN_DEFAULT_IMAGE

        ssl_variables = (
            """
              SSL_CERT_FILE: /dk/ssl/cert.crt
              SSL_KEY_FILE: /dk/ssl/cert.key
        """
            if args.ssl_cert_file and args.ssl_key_file
            else ""
        )
        ssl_volumes = (
            f"""
                  - type: bind
                    source: {args.ssl_cert_file}
                    target: /dk/ssl/cert.crt
                  - type: bind
                    source: {args.ssl_key_file}
                    target: /dk/ssl/cert.key 
        """
            if args.ssl_cert_file and args.ssl_key_file
            else ""
        )

        compose_contents = textwrap.dedent(f"""
            name: {args.compose_project_name}

            x-common-variables: &common-variables
              TESTGEN_USERNAME: {self.username}
              TESTGEN_PASSWORD: {self.password}
              TG_DECRYPT_SALT: {generate_password()}
              TG_DECRYPT_PASSWORD: {generate_password()}
              TG_JWT_HASHING_KEY: {str(base64.b64encode(random.randbytes(32)), "ascii")}
              TG_METADATA_DB_HOST: postgres
              TG_TARGET_DB_TRUST_SERVER_CERTIFICATE: yes
              TG_EXPORT_TO_OBSERVABILITY_VERIFY_SSL: no
              TG_INSTANCE_ID: {action.analytics.get_instance_id()}
              TG_ANALYTICS: {"yes" if args.send_analytics_data else "no"}
              TG_UI_BASE_URL: {get_tg_url(args, args.port)}
              {ssl_variables}

            services:
              engine:
                image: {args.image}
                container_name: testgen
                environment: *common-variables
                volumes:
                  - testgen_data:/var/lib/testgen
                  {ssl_volumes}      
                ports:
                  - {args.port}:{TESTGEN_DEFAULT_PORT}
                  - {args.api_port}:{TESTGEN_DEFAULT_API_PORT}
                extra_hosts:
                  - host.docker.internal:host-gateway
                depends_on:
                  postgres:
                    condition: service_healthy
                networks:
                  - datakitchen

              postgres:
                image: postgres:14.1-alpine
                restart: always
                environment:
                  - POSTGRES_USER={self.username}
                  - POSTGRES_PASSWORD={self.password}
                volumes:
                  - postgres_data:/var/lib/postgresql/data
                healthcheck:
                  test: ["CMD-SHELL", "pg_isready -U {self.username}"]
                  interval: 8s
                  timeout: 5s
                  retries: 3
                networks:
                  - datakitchen

            volumes:
              postgres_data:
              testgen_data:

            networks:
              datakitchen:
                name: {DOCKER_NETWORK}
                external: true
            """)
        return compose_contents


class TestGenUpdateVolumeStep(Step):
    label = "Updating docker volume"

    def execute(self, action, args):
        try:
            # Set testgen user as volume owner in case UID changes
            action.run_cmd(
                "docker",
                "compose",
                "-f",
                action.get_compose_file_path(args),
                "run",
                "--entrypoint",
                "/bin/sh -c",
                "--user",
                "root",
                "--rm",
                "engine",
                "chown -R testgen:testgen /var/lib/testgen",
            )
        except CommandFailed:
            raise SkipStep


class TestGenSetupDatabaseStep(Step):
    label = "Initializing the application database"

    def execute(self, action, args):
        run_testgen_cli(action, args, "setup-system-db", "--yes")


class TestGenUpgradeDatabaseStep(Step):
    label = "Upgrading the application database"

    def pre_execute(self, action, args):
        self.required = action.args_cmd == "upgrade"

    def execute(self, action, args):
        if action.args_cmd == "install" and action.ctx.get("using_existing"):
            raise SkipStep
        run_testgen_cli(action, args, "upgrade-system-version")

    def on_action_success(self, action, args):
        output = run_testgen_cli(action, args, "--help", capture_text=True)

        match = re.search(r"TestGen\s(?:[a-zA-Z]+\s)*([0-9.]*)", output)
        CONSOLE.msg(f"Application version: {match.group(1)}")


class UvToolInstallStep(Step):
    label = "Installing TestGen"

    def execute(self, action, args):
        uv_path = action.ctx["uv_path"]
        major = int(TESTGEN_MAJOR_VERSION)
        constraint = f"{TESTGEN_PIP_PACKAGE}[standalone]>={major},<{major + 1}"
        action.run_cmd(
            uv_path,
            "tool",
            "install",
            "--force",
            "--python",
            TESTGEN_PYTHON_VERSION,
            constraint,
        )
        # Add uv's tool bin dir to the user's shell rc so future shells pick
        # up the testgen entry point. Non-fatal if the shell isn't recognized.
        action.run_cmd(uv_path, "tool", "update-shell", raise_on_non_zero=False)
        if version := read_installed_testgen_version(action):
            action.analytics.additional_properties["testgen_version"] = version


def read_installed_testgen_version(action) -> typing.Optional[str]:
    """Parse ``uv tool list`` for the installed dataops-testgen version, or
    ``None`` if uv is unavailable or the tool isn't listed.
    """
    uv_path = action.ctx.get("uv_path")
    if uv_path is None:
        return None
    try:
        output = action.run_cmd(uv_path, "tool", "list", capture_text=True, raise_on_non_zero=False)
    except Exception:
        LOG.exception("Failed to read uv tool list")
        return None
    for line in (output or "").splitlines():
        match = TESTGEN_PIP_VERSION_RE.match(line.strip())
        if match:
            return match.group(1)
    return None


def run_testgen_cli(action, args, *cmd_args, **run_cmd_kwargs):
    """Run a ``testgen`` CLI subcommand in the appropriate mode based on
    ``action._resolved_mode``: pip mode invokes the testgen script directly;
    Docker mode runs it inside the engine container via ``docker compose exec``.
    Extra keyword arguments are forwarded to ``action.run_cmd`` (e.g.
    ``capture_text=True``) and the return value of ``run_cmd`` is returned.
    """
    if action._resolved_mode == INSTALL_MODE_PIP:
        testgen_path = resolve_testgen_path(action, args)
        return action.run_cmd(testgen_path, *cmd_args, **run_cmd_kwargs)
    return action.run_cmd(
        "docker",
        "compose",
        "-f",
        action.get_compose_file_path(args),
        "exec",
        "engine",
        "testgen",
        *cmd_args,
        **run_cmd_kwargs,
    )


def resolve_testgen_path(action, args) -> str:
    """Return the absolute path to the ``testgen`` script that ``uv tool install``
    placed in uv's bin dir. Calling this script directly (instead of via
    ``uv tool run --from ...``) executes inside the persistent tool venv —
    necessary for steps like ``standalone-setup`` whose side effects
    (e.g., Streamlit patching) must apply to the venv ``run-app`` later uses.
    """
    ctx = getattr(action, "ctx", None) or {}
    uv_path = ctx.get("uv_path") or resolve_uv_path(action.data_folder)
    if uv_path is None:
        CONSOLE.msg(f"uv not found. To install TestGen, {command_hint(args.prod, 'install', 'Install TestGen')}.")
        raise AbortAction
    bin_dir = action.run_cmd(uv_path, "tool", "dir", "--bin", capture_text=True)
    if not bin_dir:
        raise InstallerError("Could not determine uv tool bin directory.")
    bin_name = "testgen.exe" if platform.system() == "Windows" else "testgen"
    testgen_path = pathlib.Path(bin_dir.strip()) / bin_name
    if not testgen_path.exists():
        CONSOLE.msg(f"testgen script not found at {testgen_path}.")
        CONSOLE.msg(
            f"Try {command_hint(args.prod, 'delete', 'Uninstall TestGen')}, "
            f"then {command_hint(args.prod, 'install', 'Install TestGen')}."
        )
        raise AbortAction
    return str(testgen_path)


def wait_for_tcp_port(port: int, timeout: int) -> bool:
    """Poll ``localhost:port`` until a TCP connection succeeds or timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def read_testgen_config_env() -> dict[str, str]:
    """Parse ``~/.testgen/config.env`` (key=value lines). The source of truth
    for the port + SSL settings TestGen uses, since ``standalone-setup``
    persists them there at install time.
    """
    config: dict[str, str] = {}
    if not TESTGEN_CONFIG_ENV_PATH.exists():
        return config
    for line in TESTGEN_CONFIG_ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            config[key.strip()] = value.strip().strip('"').strip("'")
    return config


def start_testgen_app(action, args) -> None:
    """Start ``testgen run-app`` and block until the user interrupts.

    stdout/stderr are discarded — TestGen writes its own logs to
    ``TESTGEN_LOG_FILE_PATH`` (configured at standalone-setup time) and the
    App Logs dialog in the UI surfaces them. Capturing the subprocess streams
    here would just duplicate that and bloat the support zip.

    Using ``Popen`` directly with ``DEVNULL`` rather than going through
    ``start_cmd`` — for an indefinite-running process, ``start_cmd``'s pipe-
    based capture would deadlock once the OS pipe buffer fills.
    """
    testgen_path = resolve_testgen_path(action, args)
    # Resolve port + SSL state from the standalone-setup-persisted config so
    # the URL we display matches what TestGen actually binds to (and so this
    # works for ``tg start`` where args has no port flags registered).
    config = read_testgen_config_env()
    port = int(config.get("TG_UI_PORT") or TESTGEN_DEFAULT_PORT)
    has_ssl = bool(config.get("SSL_CERT_FILE") and config.get("SSL_KEY_FILE"))
    url = f"{'https' if has_ssl else 'http'}://localhost:{port}"

    LOG.debug("Starting TestGen: %s run-app", testgen_path)

    CONSOLE.space()
    CONSOLE.msg("Starting TestGen...")

    try:
        proc = subprocess.Popen(
            [testgen_path, "run-app"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise InstallerError(f"Could not start TestGen: {e}") from e

    try:
        if not wait_for_tcp_port(port, timeout=TESTGEN_APP_READY_TIMEOUT):
            proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
            raise InstallerError(
                f"TestGen did not start within {TESTGEN_APP_READY_TIMEOUT} seconds. "
                f"See {simplify_path(TESTGEN_LOG_FILE_PATH)} for details."
            )

        CONSOLE.msg(f"TestGen is running at {url}.")
        # During ``tg install`` the credentials file already lists the log path;
        # only repeat it for ``tg start`` invocations where there's no creds file print.
        if action.args_cmd == "start":
            CONSOLE.msg(f"Logs: {simplify_path(TESTGEN_LOG_FILE_PATH)}")
        CONSOLE.space()
        CONSOLE.msg("Press Ctrl+C to stop the app.")
        CONSOLE.msg(f"To start it again later, {command_hint(args.prod, 'start', 'Start TestGen')}.")
        CONSOLE.space()

        try:
            proc.wait()
        except KeyboardInterrupt:
            # Reset the cursor to column 0 — the terminal echoed `^C` mid-line.
            print("")
            CONSOLE.msg("Stopping TestGen...")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            CONSOLE.msg("TestGen stopped.")
            CONSOLE.msg(f"To start it again, {command_hint(args.prod, 'start', 'Start TestGen')}.")
    finally:
        if proc.poll() is None:
            proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)


class UvToolUpgradeStep(Step):
    label = "Upgrading TestGen"

    def __init__(self):
        self.current_version = None

    def pre_execute(self, action, args):
        self.current_version = read_installed_testgen_version(action)
        if self.current_version:
            CONSOLE.msg(f"Current version: v{self.current_version}")

    def execute(self, action, args):
        uv_path = action.ctx["uv_path"]
        # ``--no-cache`` (top-level option) bypasses uv's cached PyPI index for
        # this one invocation so a release that was just published is picked up
        # immediately instead of being served from a stale cache.
        action.run_cmd(
            uv_path,
            "--no-cache",
            "tool",
            "upgrade",
            TESTGEN_PIP_PACKAGE,
        )

    def on_action_success(self, action, args):
        new_version = read_installed_testgen_version(action)
        if new_version is None:
            return
        action.analytics.additional_properties["testgen_version"] = new_version
        if new_version == self.current_version:
            CONSOLE.msg(f"Application is already up-to-date (v{new_version}).")
        else:
            CONSOLE.msg(f"Updated to v{new_version}.")


class TestgenStandaloneSetupStep(Step):
    label = "Initializing TestGen"

    def __init__(self):
        self.username = None
        self.password = None

    def pre_execute(self, action, args):
        self.username = DEFAULT_USER_DATA["username"]
        self.password = generate_password()

    def execute(self, action, args):
        # standalone-setup persists these env vars to ~/.testgen/config.env so
        # subsequent ``testgen run-app`` invocations pick them up automatically.
        # TESTGEN_LOG_FILE_PATH lets the App Logs dialog in the UI surface logs.
        env = {
            "TG_UI_PORT": str(args.port),
            "TG_API_PORT": str(args.api_port),
            "TESTGEN_LOG_FILE_PATH": str(TESTGEN_LOG_FILE_PATH),
        }
        if args.ssl_cert_file:
            env["SSL_CERT_FILE"] = args.ssl_cert_file
        if args.ssl_key_file:
            env["SSL_KEY_FILE"] = args.ssl_key_file
        run_testgen_cli(
            action,
            args,
            "standalone-setup",
            "--username",
            self.username,
            "--password",
            self.password,
            env=env,
            redact=(self.password,),
        )

    def on_action_success(self, action, args):
        cred_file_path = action.data_folder.joinpath(CREDENTIALS_FILE.format(args.prod))
        log_path = simplify_path(TESTGEN_LOG_FILE_PATH)
        with CONSOLE.tee(cred_file_path) as console_tee:
            console_tee(f"User Interface: {get_tg_url(args, args.port)}")
            console_tee(f"API & MCP:      {get_tg_url(args, args.api_port)}")
            console_tee(f"Logs:           {log_path}")
            console_tee("")
            console_tee(f"Username: {self.username}")
            console_tee(f"Password: {self.password}", skip_logging=True)
        CONSOLE.msg(f"(Credentials also written to {simplify_path(cred_file_path)})")
        CONSOLE.space()


class TestgenQuickStartStep(Step):
    """Generate demo data so the user has something to look at right after
    install. Mode-agnostic — dispatches via ``run_testgen_cli``. Non-blocking:
    failure here logs and continues; the user can run ``tg run-demo`` later.
    """

    label = "Generating demo data"
    required = False

    def execute(self, action, args):
        if not getattr(args, "generate_demo", True):
            raise SkipStep
        run_testgen_cli(action, args, "quick-start")


class TestgenInstallAction(ComposeActionMixin, AnalyticsMultiStepAction):
    """Install TestGen via either pip (uv-managed) or Docker Compose.

    Mode is chosen by ``--pip`` / ``--docker``, or auto-detected when neither
    is given (defaults to pip if Docker prerequisites are not met, otherwise
    prompts).
    """

    pip_steps = [UvBootstrapStep, UvToolInstallStep, TestgenStandaloneSetupStep, TestgenQuickStartStep]
    docker_steps = [
        ComposeVerifyExistingInstallStep,
        DockerNetworkStep,
        TestGenCreateDockerComposeFileStep,
        ComposePullImagesStep,
        ComposeStartStep,
        TestGenSetupDatabaseStep,
        TestGenUpgradeDatabaseStep,
        TestgenQuickStartStep,
    ]
    pip_intro = [
        "Installing TestGen with pip.",
        "The process may take 2~5 minutes depending on your system resources and network speed.",
    ]
    docker_intro = [
        "Installing TestGen with Docker Compose.",
        "This process may take 5~10 minutes depending on your system resources and network speed.",
    ]
    docker_requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON, REQ_TESTGEN_IMAGE]

    args_cmd = "install"
    label = "Installation"
    title = "Install TestGen"
    _per_invocation_attrs = (*MultiStepAction._per_invocation_attrs, "_resolved_mode", "steps", "intro_text")

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        mode_group = parser.add_mutually_exclusive_group()
        mode_group.add_argument(
            "--pip",
            dest="install_mode",
            action="store_const",
            const=INSTALL_MODE_PIP,
            help="Install TestGen with pip.",
        )
        mode_group.add_argument(
            "--docker",
            dest="install_mode",
            action="store_const",
            const=INSTALL_MODE_DOCKER,
            help="Install TestGen with Docker Compose.",
        )
        parser.set_defaults(install_mode=None)
        # Args supported by both modes
        parser.add_argument(
            "--port",
            dest="port",
            action="store",
            default=TESTGEN_DEFAULT_PORT,
            help="Which port will be used to access TestGen UI. Defaults to %(default)s",
        )
        parser.add_argument(
            "--api-port",
            dest="api_port",
            action="store",
            default=TESTGEN_DEFAULT_API_PORT,
            help="Which port will be used to access TestGen's API and MCP server. Defaults to %(default)s",
        )
        parser.add_argument(
            "--ssl-cert-file",
            dest="ssl_cert_file",
            action="store",
            default=None,
            help="Path to SSL certificate file.",
        )
        parser.add_argument(
            "--ssl-key-file",
            dest="ssl_key_file",
            action="store",
            default=None,
            help="Path to SSL key file.",
        )
        parser.add_argument(
            "--no-demo",
            dest="generate_demo",
            action="store_false",
            help="Skip generating demo data after install. Default is to generate.",
        )
        # Docker-only args
        parser.add_argument(
            "--image",
            dest="image",
            action="store",
            default=TESTGEN_DEFAULT_IMAGE,
            help="(Docker mode only) TestGen image to use for the install. Defaults to %(default)s",
        )
        parser.add_argument(
            "--pull-timeout",
            type=int,
            action="store",
            default=TESTGEN_PULL_TIMEOUT,
            help=(
                "(Docker mode only) Maximum amount of time in minutes that Docker will be allowed to pull the images. "
                "Defaults to '%(default)s'"
            ),
        )
        return parser

    def check_requirements(self, args):
        if not hasattr(self, "_resolved_mode"):
            self._resolve_install_mode(args)
        super().check_requirements(args)

    def get_requirements(self, args):
        if self._resolved_mode == INSTALL_MODE_DOCKER:
            return self.docker_requirements
        return []

    def _resolve_install_mode(self, args):
        existing = InstallMarker(self.data_folder, args.prod, args.compose_file_name).read()
        if existing:
            CONSOLE.msg(f"Found an existing TestGen {existing} installation in {self.data_folder}.")
            CONSOLE.space()
            CONSOLE.msg(f"To update it, {command_hint(args.prod, 'upgrade', 'Upgrade TestGen')}.")
            CONSOLE.msg(f"To remove it and start over, {command_hint(args.prod, 'delete', 'Uninstall TestGen')}.")
            CONSOLE.space()
            raise AbortAction

        if args.install_mode in (INSTALL_MODE_PIP, INSTALL_MODE_DOCKER):
            mode = args.install_mode
        else:
            mode = self._auto_select_mode(args)

        self._resolved_mode = mode
        self.steps = self.pip_steps if mode == INSTALL_MODE_PIP else self.docker_steps
        self.analytics.additional_properties["install_mode"] = mode
        LOG.info("tg install resolved to %s mode", mode)

    def _auto_select_mode(self, args):
        # Probe each Docker prerequisite individually so we can show per-prereq
        # status to the user. If a prereq fails (e.g. Docker engine not running),
        # the user can decide whether to fix it or fall back to pip.
        prereq_results = [(req, req.check_availability(self, args, quiet=True)) for req in self.docker_requirements]
        docker_ready = all(ok for _, ok in prereq_results)

        CONSOLE.space()
        CONSOLE.msg("TestGen offers two installation modes:")
        CONSOLE.space()
        CONSOLE.msg("[d] Docker Compose (Recommended)")
        CONSOLE.msg("    The most stable TestGen experience for persistent use.")
        CONSOLE.msg("    Provides a fully managed environment with an isolated PostgreSQL container.")
        prereq_status = "   ".join(f"{'(✓)' if ok else '(X)'} {req.label or req.key}" for req, ok in prereq_results)
        CONSOLE.msg(f"    Prerequisites: {prereq_status}")
        CONSOLE.space()
        CONSOLE.msg("[p] Pip + embedded PostgreSQL")
        CONSOLE.msg("    A lightweight Python installation suited for evaluation.")
        CONSOLE.msg(
            "    Sets up an isolated Python environment and manages the PostgreSQL database on the file system."
        )
        CONSOLE.space()

        if docker_ready:
            prompt = f"{CONSOLE.MARGIN}Install with Docker [d] or pip [p]? (default: d): "
            valid_default = "d"
        else:
            CONSOLE.msg("To install with Docker, fix the prerequisites and run the install again.")
            CONSOLE.space()
            prompt = f"{CONSOLE.MARGIN}Install with pip [p]? (default: p): "
            valid_default = "p"

        while True:
            try:
                choice = input(prompt).strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("")
                raise AbortAction
            if choice == "":
                choice = valid_default
            if docker_ready and choice in ("d", "docker"):
                return INSTALL_MODE_DOCKER
            if choice in ("p", "pip"):
                return INSTALL_MODE_PIP
            print(f"'{choice}' is not a valid option.")

    def execute(self, args):
        self.intro_text = self.pip_intro if self._resolved_mode == INSTALL_MODE_PIP else self.docker_intro
        super().execute(args)
        InstallMarker(self.data_folder, args.prod).write(self._resolved_mode)
        # Pip mode: keep the app running so the user has a one-command install
        # experience. Docker mode already runs as detached containers via
        # ``docker compose up --wait``, so no need to start anything here —
        # but open the browser for parity with pip's Streamlit auto-launch.
        if self._resolved_mode == INSTALL_MODE_PIP:
            start_testgen_app(self, args)
        else:
            open_app_in_browser(get_tg_url(args, args.port))


class TestgenStandaloneUpgradeStep(Step):
    label = "Upgrading the application database"

    def execute(self, action, args):
        run_testgen_cli(action, args, "upgrade-system-version")


class TestgenUpgradeAction(ComposeActionMixin, AnalyticsMultiStepAction):
    """Upgrade an existing TestGen install. Mode is read from the install marker."""

    pip_steps = [UvBootstrapStep, UvToolUpgradeStep, TestgenStandaloneUpgradeStep]
    docker_steps = [
        UpdateComposeFileStep,
        ComposeStopStep,
        ComposePullImagesStep,
        TestGenUpdateVolumeStep,
        ComposeStartStep,
        TestGenUpgradeDatabaseStep,
    ]

    args_cmd = "upgrade"
    label = "Upgrade"
    title = "Upgrade TestGen"
    intro_text = ["This process may take 5~10 minutes depending on your system resources and network speed."]
    _per_invocation_attrs = (*MultiStepAction._per_invocation_attrs, "_resolved_mode", "steps", "intro_text")

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        # Docker-only args - ignored in pip mode
        parser.add_argument(
            "--skip-verify",
            dest="skip_verify",
            action="store_true",
            help="(Docker mode only) Whether to skip the version check before upgrading.",
        )
        parser.add_argument(
            "--pull-timeout",
            type=int,
            action="store",
            default=TESTGEN_PULL_TIMEOUT,
            help=(
                "(Docker mode only) Maximum amount of time in minutes that Docker will be allowed to pull the images. "
                "Defaults to '%(default)s'"
            ),
        )
        return parser

    def check_requirements(self, args):
        if not hasattr(self, "_resolved_mode"):
            self._resolve_install_mode(args)
        super().check_requirements(args)

    def get_requirements(self, args):
        if self._resolved_mode == INSTALL_MODE_PIP:
            return []
        return [
            REQ_DOCKER,
            REQ_DOCKER_DAEMON,
            Requirement(
                "TG_COMPOSE_FILE",
                (
                    "docker",
                    "compose",
                    "-f",
                    str(self.get_compose_file_path(args)),
                    "config",
                ),
                (
                    f"TestGen's Docker configuration file is not available at "
                    f"{self.data_folder.joinpath(self.get_compose_file_path(args))}.",
                    "Re-install TestGen and try again.",
                ),
            ),
        ]

    def _resolve_install_mode(self, args):
        mode = InstallMarker(self.data_folder, args.prod, args.compose_file_name).read()
        if mode is None:
            CONSOLE.msg(f"No TestGen installation found in {self.data_folder}.")
            CONSOLE.msg(f"To install TestGen, {command_hint(args.prod, 'install', 'Install TestGen')}.")
            CONSOLE.space()
            raise AbortAction
        self._resolved_mode = mode
        self.steps = self.pip_steps if mode == INSTALL_MODE_PIP else self.docker_steps
        self.analytics.additional_properties["install_mode"] = mode
        LOG.info("tg upgrade resolved to %s mode", mode)

    def execute(self, args):
        super().execute(args)
        InstallMarker(self.data_folder, args.prod).write(self._resolved_mode)


class TestgenStartAction(Action, ComposeActionMixin):
    """Start a previously-installed TestGen app.

    Companion to the auto-start at the end of ``tg install``. For pip mode,
    runs ``testgen run-app`` and blocks until Ctrl+C. For docker mode, runs
    ``docker compose up --wait`` (detached) so the user can bring containers
    back up after a reboot or a manual stop.
    """

    args_cmd = "start"
    _per_invocation_attrs = (*Action._per_invocation_attrs, "_resolved_mode")

    def check_requirements(self, args):
        if not hasattr(self, "_resolved_mode"):
            self._resolve_install_mode(args)
        super().check_requirements(args)

    def get_requirements(self, args):
        if self._resolved_mode == INSTALL_MODE_DOCKER:
            return [REQ_DOCKER, REQ_DOCKER_DAEMON]
        return []

    def _resolve_install_mode(self, args):
        mode = InstallMarker(self.data_folder, args.prod, args.compose_file_name).read()
        if mode is None:
            CONSOLE.msg(f"No TestGen installation found in {self.data_folder}.")
            CONSOLE.msg(f"To install TestGen, {command_hint(args.prod, 'install', 'Install TestGen')}.")
            CONSOLE.space()
            raise AbortAction
        self._resolved_mode = mode
        self.analytics.additional_properties["install_mode"] = mode

    def execute(self, args):
        if self._resolved_mode == INSTALL_MODE_DOCKER:
            CONSOLE.title("Start TestGen")
            compose_path = self.get_compose_file_path(args)
            self.run_cmd("docker", "compose", "-f", compose_path, "up", "--wait")
            CONSOLE.msg("TestGen containers are running.")
            CONSOLE.msg(
                f"For the URL and credentials, {command_hint(args.prod, 'access-info', 'Access Installed App')}."
            )
            # Match pip-mode parity: open the browser to the configured UI URL.
            # Best-effort — if the compose file is malformed or missing the env,
            # we just skip the browser launch (the URL is still in the creds file).
            try:
                for line in compose_path.read_text().splitlines():
                    stripped = line.strip()
                    if stripped.startswith("TG_UI_BASE_URL:"):
                        open_app_in_browser(stripped.split(":", 1)[1].strip())
                        break
            except OSError:
                LOG.exception("Could not read TG_UI_BASE_URL from %s", compose_path)
        else:
            start_testgen_app(self, args)


class TestgenDeleteAction(Action, ComposeActionMixin):
    """Delete an existing TestGen install — pip or Docker — based on the marker.

    Reuses the ``delete_compose_*`` helpers on ``ComposeActionMixin`` for the
    Docker path. The marker is removed at the end so a subsequent
    ``tg install`` starts fresh.
    """

    args_cmd = "delete"
    _per_invocation_attrs = (*Action._per_invocation_attrs, "_resolved_mode")

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--keep-images",
            action="store_true",
            help="(Docker mode only) Does not delete the images when deleting the installation",
        )
        parser.add_argument(
            "--keep-config",
            action="store_true",
            help="(Docker mode only) Does not delete the compose config file when deleting the installation",
        )
        parser.add_argument(
            "--keep-data",
            action="store_true",
            help="(Pip mode only) Keep the embedded Postgres data directory (~/.testgen by default).",
        )
        return parser

    def check_requirements(self, args):
        if not hasattr(self, "_resolved_mode"):
            self._resolve_install_mode(args)
        super().check_requirements(args)

    def get_requirements(self, args):
        if self._resolved_mode == INSTALL_MODE_DOCKER:
            return [REQ_DOCKER, REQ_DOCKER_DAEMON]
        return []

    def _resolve_install_mode(self, args):
        # Unlike install/upgrade, "no install found" is not an abort here —
        # ``tg delete`` is idempotent. execute() handles the None case.
        mode = InstallMarker(self.data_folder, args.prod, args.compose_file_name).read()
        self._resolved_mode = mode
        if mode is not None:
            self.analytics.additional_properties["install_mode"] = mode

    def execute(self, args):
        if self._resolved_mode is None:
            CONSOLE.msg(f"No TestGen installation found in {self.data_folder}.")
            CONSOLE.msg("Nothing to delete.")
            CONSOLE.space()
            return

        if self._resolved_mode == INSTALL_MODE_DOCKER:
            self._delete_docker(args)
        else:
            self._delete_pip(args)
        InstallMarker(self.data_folder, args.prod, args.compose_file_name).unlink()

    def _delete_docker(self, args):
        if self.get_compose_file_path(args).exists():
            self.delete_compose_containers(args)
            self.delete_compose_network()
        else:
            self.delete_compose_network()
            self.delete_compose_volumes(args)

    def _delete_pip(self, args):
        CONSOLE.title("Delete TestGen instance")

        uv_path = resolve_uv_path(self.data_folder)
        if uv_path:
            try:
                self.run_cmd(uv_path, "tool", "uninstall", TESTGEN_PIP_PACKAGE)
            except CommandFailed:
                LOG.exception("Failed to uninstall testgen via uv")
                CONSOLE.msg(
                    "Note: 'uv tool uninstall testgen' reported an error "
                    "(it may already be uninstalled); see session logs."
                )
        else:
            LOG.info("uv not found; skipping uv tool uninstall")
            CONSOLE.msg("uv not found; skipping 'uv tool uninstall testgen'.")

        if not getattr(args, "keep_data", False):
            tg_home = pathlib.Path(os.environ.get("TG_TESTGEN_HOME", pathlib.Path.home() / ".testgen"))
            remove_path(tg_home, label="TestGen data directory")

        # Don't touch ~/.streamlit — Streamlit is widely used and the user
        # may have other Streamlit projects on this machine. The config dir
        # is tiny and harmless if left behind.

        # Remove the installer-local uv binary if we downloaded one. A
        # pre-existing uv on PATH is left alone.
        local_uv = self.data_folder / UV_BIN_SUBDIR / ("uv.exe" if platform.system() == "Windows" else "uv")
        if remove_path(local_uv, label="installer-local uv"):
            with contextlib.suppress(OSError):
                local_uv.parent.rmdir()

        remove_path(self.data_folder / CREDENTIALS_FILE.format(args.prod))
        CONSOLE.space()
        CONSOLE.msg("TestGen uninstalled.")
        CONSOLE.space()


class TestgenRunDemoAction(DemoContainerAction, ComposeActionMixin):
    """Generate TestGen demo data — Docker-exec or pip-direct based on the marker."""

    args_cmd = "run-demo"
    _per_invocation_attrs = (*Action._per_invocation_attrs, "_resolved_mode")

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--export",
            dest="obs_export",
            action="store_true",
            default=False,
            help="Export test results to Observability. Defaults to False",
        )
        return parser

    def check_requirements(self, args):
        if not hasattr(self, "_resolved_mode"):
            self._resolve_install_mode(args)
        super().check_requirements(args)

    def get_requirements(self, args):
        # Docker mode requires Docker. For pip mode, Docker is only needed when
        # the user asked to export to Observability (the dk-demo container
        # generates the export payload).
        if self._resolved_mode == INSTALL_MODE_DOCKER or getattr(args, "obs_export", False):
            return [REQ_DOCKER, REQ_DOCKER_DAEMON]
        return []

    def _resolve_install_mode(self, args):
        mode = InstallMarker(self.data_folder, args.prod, args.compose_file_name).read()
        if mode is None:
            CONSOLE.msg(f"No TestGen installation found in {self.data_folder}.")
            CONSOLE.msg(f"To install TestGen, {command_hint(args.prod, 'install', 'Install TestGen')}.")
            CONSOLE.space()
            raise AbortAction
        self._resolved_mode = mode
        self.analytics.additional_properties["install_mode"] = mode

    def execute(self, args):
        self.analytics.additional_properties["obs_export"] = args.obs_export

        CONSOLE.title("Run TestGen demo")

        if args.obs_export and not (self.data_folder / DEMO_CONFIG_FILE).exists():
            CONSOLE.msg("Observability demo configuration missing.")
            raise AbortAction

        if self._resolved_mode == INSTALL_MODE_DOCKER:
            tg_status = self.get_status(args)
            if not tg_status or not re.match(".*running.*", tg_status["Status"], re.I):
                CONSOLE.msg("Running the TestGen demo requires the application to be running.")
                raise AbortAction

        CONSOLE.msg("This process may take up to 3 minutes depending on your system resources and network speed.")
        CONSOLE.space()

        export_args = []
        if args.obs_export:
            self.run_dk_demo_container("tg-run-demo")
            with open(self.data_folder / DEMO_CONFIG_FILE, "r") as file:
                json_config = json.load(file)
            export_args = [
                "--observability-api-url",
                json_config["api_host"],
                "--observability-api-key",
                json_config["api_key"],
            ]

        run_testgen_cli(self, args, "quick-start", *export_args)
        if args.obs_export:
            run_testgen_cli(
                self,
                args,
                "export-observability",
                "--project-key",
                "DEFAULT",
                "--test-suite-key",
                "default-suite-1",
            )

        CONSOLE.title("Demo SUCCEEDED")


class TestgenDeleteDemoAction(DemoContainerAction, ComposeActionMixin):
    """Delete TestGen demo data — Docker-exec or pip-direct based on the marker."""

    args_cmd = "delete-demo"
    _per_invocation_attrs = (*Action._per_invocation_attrs, "_resolved_mode")

    def check_requirements(self, args):
        if not hasattr(self, "_resolved_mode"):
            self._resolve_install_mode(args)
        super().check_requirements(args)

    def get_requirements(self, args):
        # Docker mode requires Docker. For pip mode, the dk-demo container
        # call below is wrapped in try/except so Docker absence is non-fatal.
        return [REQ_DOCKER, REQ_DOCKER_DAEMON] if self._resolved_mode == INSTALL_MODE_DOCKER else []

    def _resolve_install_mode(self, args):
        # Like delete: idempotent, so "no install" returns rather than aborts.
        mode = InstallMarker(self.data_folder, args.prod, args.compose_file_name).read()
        self._resolved_mode = mode
        if mode is not None:
            self.analytics.additional_properties["install_mode"] = mode

    def execute(self, args):
        if self._resolved_mode is None:
            CONSOLE.msg(f"No TestGen installation found in {self.data_folder}.")
            CONSOLE.msg("Nothing to delete.")
            CONSOLE.space()
            return

        CONSOLE.title("Delete TestGen demo")
        try:
            self.run_dk_demo_container("tg-delete-demo")
        except Exception:
            pass

        CONSOLE.msg("Cleaning up application database..")
        if self._resolved_mode == INSTALL_MODE_DOCKER:
            tg_status = self.get_status(args)
            if not tg_status:
                CONSOLE.msg("TestGen must be running for its demo data to be cleaned.")
                raise AbortAction

        run_testgen_cli(self, args, "setup-system-db", "--delete-db", "--yes")

        CONSOLE.title("Demo data DELETED")


class AccessInstructionsAction(Action):
    args_cmd = "access-info"

    def execute(self, args):
        credendials_path = self.data_folder.joinpath(CREDENTIALS_FILE.format(args.prod))
        try:
            info = credendials_path.read_text()
        except Exception:
            CONSOLE.msg(
                f"No {args.prod_name} access information found in {credendials_path}. Is {args.prod_name} installed?"
            )
        else:
            for line in info.splitlines():
                CONSOLE.msg(line)


#
# Entrypoint
#


def show_menu(installer):
    cfg_options = {}

    def add_config(key, value, msg=None):
        cfg_options[key] = value
        if msg:
            print(f"\n{msg}\n")

    def run_installer(args):
        cfg_args = args[:]
        for value in cfg_options.values():
            if value is not None:
                cfg_args.insert(0, value)
        installer.run(cfg_args)

    tg_menu = Menu(run_installer, "TestGen")
    tg_menu.add_option("Install TestGen", ["tg", "install"])
    tg_menu.add_option("Start TestGen", ["tg", "start"])
    tg_menu.add_option("Upgrade TestGen", ["tg", "upgrade"])
    tg_menu.add_option("Access Installed App", ["tg", "access-info"])
    tg_menu.add_option("Install TestGen demo data", ["tg", "run-demo"])
    tg_menu.add_option(
        "Install TestGen demo data with Observability export",
        ["tg", "run-demo", "--export"],
    )
    tg_menu.add_option("Delete TestGen demo data", ["tg", "delete-demo"])
    tg_menu.add_option("Uninstall TestGen", ["tg", "delete"])

    obs_menu = Menu(run_installer, "Observability")
    obs_menu.add_option("Install Observability", ["obs", "install"])
    obs_menu.add_option("Upgrade Observability", ["obs", "upgrade"])
    obs_menu.add_option("Access Installed App", ["obs", "access-info"])
    obs_menu.add_option("Install Observability demo data", ["obs", "run-demo"])
    obs_menu.add_option("Delete Observability demo data", ["obs", "delete-demo"])
    obs_menu.add_option("Run heartbeat demo", ["obs", "run-heartbeat-demo"])
    obs_menu.add_option("Uninstall Observability", ["obs", "delete"])

    cfg_menu = Menu(add_config, "Configuration")
    cfg_menu.add_option(
        "Disable sending analytics data",
        key="send_analytics_data",
        value="--no-analytics",
        msg="Sending analytics data has been disabled for this session.",
    )
    cfg_menu.add_option(
        "Enable sending analytics data",
        key="send_analytics_data",
        value=None,
        msg="Sending analytics data has been enabled for this session.",
    )

    main_menu = Menu(None, "Main", "DataKitchen Installer")
    main_menu.add_submenu("TestGen", tg_menu)
    main_menu.add_submenu("Observability", obs_menu)
    main_menu.add_submenu("Installer Configuration", cfg_menu)
    main_menu.run()


def get_installer_instance():
    installer_instance = Installer()
    installer_instance.add_product(
        "obs",
        [
            ObsInstallAction(),
            ObsUpgradeAction(),
            AccessInstructionsAction(),
            ComposeDeleteAction(),
            ObsRunDemoAction(),
            ObsDeleteDemoAction(),
            ObsRunHeartbeatDemoAction(),
        ],
        defaults={
            "prod_name": "Observability",
            "compose_file_name": "obs-docker-compose.yml",
            "compose_project_name": "dataops-observability",
        },
    )

    installer_instance.add_product(
        "tg",
        [
            TestgenInstallAction(),
            TestgenStartAction(),
            TestgenUpgradeAction(),
            AccessInstructionsAction(),
            TestgenDeleteAction(),
            TestgenRunDemoAction(),
            TestgenDeleteDemoAction(),
        ],
        defaults={
            "prod_name": "TestGen",
            "compose_file_name": TESTGEN_COMPOSE_FILE,
            "compose_project_name": "dataops-testgen",
        },
    )
    return installer_instance


if __name__ == "__main__":
    installer = get_installer_instance()

    if platform.system() == "Windows":
        # For backward compatibility - move the old folder to the new path
        try:
            new_folder = pathlib.Path(os.environ["LOCALAPPDATA"], "DataKitchenApps")
            old_folder = pathlib.Path("~", "Documents", "DataKitchenApps").expanduser()
            old_folder.rename(new_folder)
        except Exception:
            pass

    # Show the menu when running from the Windows .exe without arguments
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        show_menu(installer)
    else:
        sys.exit(installer.run())
