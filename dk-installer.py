#!/usr/bin/env python3

import argparse
import base64
import contextlib
import dataclasses
import datetime
import functools
import hashlib
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
import ssl
import string
import subprocess
import sys
import textwrap
import time
import urllib.request
import urllib.parse
import zipfile


#
# Initial setup
#

MINIKUBE_PROFILE = "dk-observability"
MINIKUBE_KUBE_VER = "v1.32.0"
NAMESPACE = "datakitchen"
HELM_REPOS = (("datakitchen", "https://datakitchen.github.io/dataops-observability/"),)
HELM_SERVICES = (
    "dataops-observability-services",
    os.environ.get("HELM_FOLDER", "datakitchen/dataops-") + "observability-services",
)
HELM_APP = (
    "dataops-observability-app",
    os.environ.get("HELM_FOLDER", "datakitchen/dataops-") + "observability-app",
)
HELM_DEFAULT_TIMEOUT = 10
REQ_CHECK_TIMEOUT = 30
DOCKER_COMPOSE_FILE = "docker-compose.yml"
DEFAULT_DOCKER_REGISTRY = "docker.io"
DOCKER_NETWORK = "datakitchen-network"
DOCKER_NETWORK_SUBNET = "192.168.60.0/24"
POD_LOG_LIMIT = 10_000
INSTALLER_NAME = pathlib.Path(__file__).name
DEMO_CONFIG_FILE = "demo-config.json"
DEMO_IMAGE = "datakitchen/data-observability-demo:latest"
DEMO_CONTAINER_NAME = "dk-demo"
SERVICES_LABELS = {
    "observability-ui": "User Interface",
    "event-api": "Event Ingestion API",
    "observability-api": "Observability API",
    "agent-api": "Agent Heartbeat API",
}
SERVICES_URLS = {
    "observability-ui": "{}",
    "event-api": "{}/api/events/v1",
    "observability-api": "{}/api/observability/v1",
    "agent-api": "{}/api/agent/v1",
}
DEFAULT_EXPOSE_PORT = 8082
DEFAULT_OBS_MEMORY = "4096m"
BASE_API_URL_TPL = "{}/api"
CREDENTIALS_FILE = "dk-{}-credentials.txt"
TESTGEN_COMPOSE_NAME = "testgen"
TESTGEN_LATEST_TAG = "v4"
TESTGEN_DEFAULT_IMAGE = f"datakitchen/dataops-testgen:{TESTGEN_LATEST_TAG}"
TESTGEN_PULL_TIMEOUT = 5
TESTGEN_PULL_RETRIES = 3
TESTGEN_DEFAULT_PORT = 8501

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

#
# Utility functions
#


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


def delete_file(file_path):
    LOG.debug("Deleting [%s]", file_path.name)
    file_path.unlink(missing_ok=True)


def get_testgen_status(action):
    compose_installs = action.run_cmd("docker", "compose", "ls", "--format=json", capture_json=True)
    for install in compose_installs:
        if install["Name"] == TESTGEN_COMPOSE_NAME:
            return install
    return {}


def get_testgen_volumes(action):
    volumes = action.run_cmd("docker", "volume", "list", "--format=json", capture_json_lines=True)
    return [v for v in volumes if "com.docker.compose.project=testgen" in v.get("Labels", "")]


@functools.cache
def get_installer_version():
    try:
        return hashlib.md5(pathlib.Path(__file__).read_bytes()).hexdigest()
    except Exception:
        return "N/A"


class StreamIterator:
    def __init__(self, proc, stream, file_path):
        self.proc = proc
        self.stream = stream
        self.file_path = file_path
        self.file = None
        self.bytes_written = 0

    def __iter__(self):
        return self

    def __next__(self):
        for return_anyway in (False, True):
            # We poll the process status before consuming the stream to make sure the StopIteration condition
            # is not vulnerable to a race condition.
            ret = self.proc.poll()
            line = self.stream.readline()
            if line:
                if not self.file:
                    self.file = open(self.file_path, "wb")
                self.file.write(line)
                self.bytes_written += len(line)
                return line
            if ret is not None and not line:
                raise StopIteration
            if not return_anyway:
                time.sleep(0.1)
        return line

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for _ in iter(self):
            pass
        if self.file:
            self.file.close()
        return False


#
# Core building blocks
#


class Console:
    MARGIN = "   | "

    def __init__(self):
        self._last_is_space = False
        self._partial_msg = ""

    def title(self, text):
        LOG.info("Console title: [%s]", text)
        if not self._last_is_space:
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
    def partial(self):
        print(self.MARGIN, end="")

        def console_partial(text):
            print(text, end="")
            sys.stdout.flush()
            self._partial_msg += text

        try:
            yield console_partial
        finally:
            print("")
            LOG.info("Console message: [%s]", self._partial_msg)
            self._partial_msg = ""
            self._last_is_space = False

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
    cmd: tuple[str, ...]
    fail_msg: tuple[str, ...]

    def check_availability(self, action, args):
        try:
            action.run_cmd_retries(
                *(seg.format(**args.__dict__) for seg in self.cmd),
                timeout=REQ_CHECK_TIMEOUT,
                retries=1,
            )
        except CommandFailed:
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
        idx: int | None = None,
        cmd: str | None = None,
        ret_code: int | None = None,
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

    def _hash_value(self, value: bytes | str, digest_size: int = 8) -> str:
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
            try:
                self.data_folder = pathlib.Path(os.environ["LOCALAPPDATA"], "DataKitchenApps")
            except KeyError:
                self.data_folder = pathlib.Path("~", "Documents", "DataKitchenApps").expanduser()
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
    ) -> tuple[CommandFailed, pathlib.Path] | tuple[None, None]:
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
        exception, log_path = self._get_failed_cmd_log_file_path(exception)
        if exception and log_path:
            CONSOLE.msg(f"Command '{exception.cmd}' failed with code {exception.ret_code}. See the output below.")
            CONSOLE.print_log(log_path)

        msg_file_path = self.session_zip.relative_to(pathlib.Path().absolute())
        CONSOLE.space()
        CONSOLE.msg("For assistance, send the logs to open-source-support@datakitchen.io or reach out")
        CONSOLE.msg("to the #support channel on https://data-observability-slack.datakitchen.io/join.")
        CONSOLE.msg(f"The logs can be found in {msg_file_path}.")

    def _check_requirements(self, args):
        missing_reqs = [req.key for req in self.requirements if not req.check_availability(self, args)]
        if missing_reqs:
            self.analytics.additional_properties["missing_requirements"] = missing_reqs
            raise AbortAction

    def execute_with_log(self, args):
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
                self._check_requirements(args)
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
                CONSOLE.space()
                CONSOLE.msg("Processing interrupted. This may result in an inconsistent platform state.")
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
                with self.start_cmd(*cmd, env=env, **popen_args) as (proc, *_):
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
            proc.stdin.close()

            if echo:
                for line in stdout:
                    if line:
                        CONSOLE.msg(line.decode().strip())
            elif capture_text:
                return b"".join(stdout).decode()
            elif capture_json:
                try:
                    return json.loads(b"".join(stdout).decode())
                except json.JSONDecodeError:
                    LOG.warning("Error decoding JSON from stdout")
                    return {}
            elif capture_json_lines:
                json_lines = []
                for idx, output_line in enumerate(stdout):
                    try:
                        json_lines.append(json.loads(output_line.decode()))
                    except json.JSONDecodeError:
                        LOG.warning(f"Error decoding JSON from stdout line #{idx}")
                return json_lines

    @contextlib.contextmanager
    def start_cmd(self, *cmd, raise_on_non_zero=True, env=None, **popen_args):
        started = time.time()
        self._cmd_idx += 1

        cmd_str = " ".join(str(part) for part in cmd)
        LOG.debug("Command [%04d]: [%s]", self._cmd_idx, cmd_str)

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
            raise CommandFailed(self._cmd_idx, cmd_str, None) from e

        slug_cmd = re.sub(r"[^a-zA-Z]+", "-", cmd_str)[:100].strip("-")

        def get_stream_iterator(stream_name):
            file_name = f"{self._cmd_idx:04d}-{stream_name}-{slug_cmd}.txt"
            file_path = self.session_folder.joinpath(file_name)
            return StreamIterator(proc, getattr(proc, stream_name), file_path)

        try:
            with (
                get_stream_iterator("stdout") as stdout_iter,
                get_stream_iterator("stderr") as stderr_iter,
            ):
                try:
                    yield proc, stdout_iter, stderr_iter
                finally:
                    proc.wait()
            if raise_on_non_zero and proc.returncode != 0:
                raise CommandFailed
        # We capture and raise CommandFailed to allow the client code to raise an empty CommandFailed exception
        # but still get a contextualized exception at the end
        except CommandFailed as e:
            raise CommandFailed(self._cmd_idx, cmd_str, proc.returncode) from e.__cause__
        finally:
            elapsed = time.time() - started
            LOG.info(
                "Command [%04d] returned [%d] in [%.3f] seconds. [%d] bytes in STDOUT, [%d] bytes in STDERR",
                self._cmd_idx,
                proc.returncode,
                elapsed,
                stdout_iter.bytes_written,
                stderr_iter.bytes_written,
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
        return self.label or self.__name__


class MultiStepAction(Action):
    steps: list[type[Step]]
    label: str = "Process"
    title: str = ""
    intro_text: list[str] = []

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
        executed_steps: list[Step] = []
        action_fail_exception = None
        action_fail_step = None
        for step in action_steps:
            executed_steps.append(step)
            with CONSOLE.partial() as partial:
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

        for step in reversed(executed_steps):
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
            default=True,
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


def get_minikube_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--profile",
        type=str,
        action="store",
        default=MINIKUBE_PROFILE,
        help="Name of the minikube profile that will be started/deleted. Defaults to '%(default)s'",
    )
    parser.add_argument(
        "--namespace",
        type=str,
        action="store",
        default=NAMESPACE,
        help="Namespace to be given to the kubernetes resources. Defaults to '%(default)s'",
    )
    return parser


minikube_parser = get_minikube_parser()

REQ_HELM = Requirement(
    "HELM",
    ("helm", "version"),
    ("The prerequisite Helm is not available.", "Install Helm and try again."),
)
REQ_MINIKUBE = Requirement(
    "MINIKUBE",
    ("minikube", "version"),
    ("The prerequisite Minikube is not available.", "Install Minikube and try again."),
)
REQ_MINIKUBE_DRIVER = Requirement(
    "MINIKUBE_DRIVER",
    ("{driver}", "-v"),
    (
        "The '{driver}' driver for Minikube is not available",
        "Install '{driver}' and try again.",
    ),
)
REQ_DOCKER = Requirement(
    "DOCKER",
    ("docker", "-v"),
    ("The prerequisite Docker is not available.", "Install Docker and try again."),
)
REQ_DOCKER_DAEMON = Requirement(
    "DOCKER_ENGINE",
    ("docker", "info"),
    ("The Docker engine is not running.", "Start the Docker engine and try again."),
)


class AnalyticsMultiStepAction(MultiStepAction):
    ANALYTICS_DISCLAIMER = [
        "DataKitchen has enabled anonymous aggregate user behavior analytics.",
        "Read the analytics documentation (and how to opt-out) here:",
        "https://docs.datakitchen.io/articles/#!datakitchen-resources/anonymous-analytics",
    ]

    def _print_intro_text(self, args):
        super()._print_intro_text(args)

        if args.send_analytics_data:
            CONSOLE.space()
            for line in self.ANALYTICS_DISCLAIMER:
                CONSOLE.msg(line)


#
# Action and Steps implementations
#


class DockerNetworkStep(Step):
    label = "Creating a Docker network"

    def execute(self, action, args):
        if args.prod == "tg" or args.driver == "docker":
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
        else:
            raise SkipStep


class MinikubeProfileStep(Step):
    label = "Starting a new minikube profile"

    def pre_execute(self, action, args):
        env_json = action.run_cmd(
            "minikube",
            "-p",
            args.profile,
            "status",
            "-o",
            "json",
            capture_json=True,
            raise_on_non_zero=False,
        )
        if "Name" in env_json:
            CONSOLE.msg(
                "Found a minikube profile with the same name. If a previous attempt to run this installer failed,"
            )
            CONSOLE.msg(
                f"please run `python3 {INSTALLER_NAME} {args.prod} delete --profile={args.profile}` before trying again"
            )
            CONSOLE.msg("or choose a different profile name.")
            CONSOLE.space()
            for k, v in env_json.items():
                CONSOLE.msg(f"{k:>10}: {v}")
            raise AbortAction

    def execute(self, action, args):
        action.analytics.additional_properties["minikube_mem"] = args.memory
        action.analytics.additional_properties["minikube_driver"] = args.driver

        action.run_cmd(
            "minikube",
            "start",
            f"--memory={args.memory}",
            f"--profile={args.profile}",
            f"--namespace={args.namespace}",
            f"--driver={args.driver}",
            f"--kubernetes-version={MINIKUBE_KUBE_VER}",
            f"--network={DOCKER_NETWORK}",
            # minikube tries to use gateway + 1 by default, but that may be in use by TestGen - so we pass in a static IP at gateway + 4
            f"--static-ip={str(ipaddress.IPv4Network(DOCKER_NETWORK_SUBNET)[5])}",
            "--embed-certs",
            "--extra-config=apiserver.service-node-port-range=1-65535",
            "--extra-config=kubelet.allowed-unsafe-sysctls=net.core.somaxconn",
        )

    def on_action_fail(self, action, args):
        if args.debug:
            LOG.debug("Skipping deleting the minikube profile on failure because debug is ON")
            return

        action.run_cmd("minikube", "-p", args.profile, "delete")

    def on_action_success(self, action, args):
        action.run_cmd("minikube", "profile", args.profile)


class SetupHelmReposStep(Step):
    label = "Setting up the helm repositories"

    def execute(self, action, args):
        if "HELM_FOLDER" in os.environ:
            raise SkipStep
        for name, url in HELM_REPOS:
            action.run_cmd("helm", "repo", "add", name, url, "--force-update")
        action.run_cmd("helm", "repo", "update")


class HelmInstallStep(Step):
    chart_info: tuple[str, str] = None
    values_arg: str = None

    def execute(self, action, args):
        action.analytics.additional_properties["helm_timeout"] = args.helm_timeout

        release, chart_ref = self.chart_info
        values_file = getattr(args, self.values_arg) if self.values_arg else None
        values = ("--values", values_file) if values_file else ()
        action.run_cmd(
            "helm",
            "install",
            release,
            chart_ref,
            *values,
            f"--namespace={args.namespace}",
            "--create-namespace",
            "--wait",
            f"--timeout={args.helm_timeout}m",
        )

    def on_action_fail(self, action, args):
        release, _ = self.chart_info
        action.run_cmd(
            "helm",
            "status",
            release,
            "-o",
            "json",
            capture_json=True,
            raise_on_non_zero=False,
        )

        pods = action.run_cmd(
            "minikube",
            "kubectl",
            "--profile",
            args.profile,
            "--",
            "--namespace",
            args.namespace,
            "-l",
            f"app.kubernetes.io/instance={release}",
            "get",
            "pods",
            "-o",
            "json",
            capture_json=True,
        )

        if POD_LOG_LIMIT:
            for pod in pods["items"]:
                for container in pod["status"]["containerStatuses"]:
                    if not container["ready"]:
                        action.run_cmd(
                            "minikube",
                            "kubectl",
                            "--profile",
                            args.profile,
                            "--",
                            "--namespace",
                            args.namespace,
                            "logs",
                            pod["metadata"]["name"],
                            "-c",
                            container["name"],
                            "--limit-bytes",
                            str(POD_LOG_LIMIT),
                        )


class ObsHelmInstallServicesStep(HelmInstallStep):
    label = "Installing helm charts for supporting services"
    chart_info = HELM_SERVICES


class ObsHelmInstallPlatformStep(HelmInstallStep):
    label = "Installing helm charts for Observability platform"
    chart_info = HELM_APP
    values_arg = "app_values"

    def execute(self, action, args):
        if args.docker_username and args.docker_password:
            action.run_cmd(
                "minikube",
                "kubectl",
                "--profile",
                args.profile,
                "--",
                "--namespace",
                args.namespace,
                "create",
                "secret",
                "docker-registry",
                "docker-hub-pull-secrets",
                "--docker-username",
                args.docker_username,
                "--docker-password",
                args.docker_password,
            )

        super().execute(action, args)

        if not (
            args.driver == "docker"
            and platform.system()
            in [
                "Darwin",
                "Windows",
            ]
        ):
            try:
                data = action.run_cmd(
                    "minikube",
                    "-p",
                    args.profile,
                    "service",
                    "--namespace",
                    args.namespace,
                    "list",
                    "-o",
                    "json",
                    capture_json=True,
                )
                url = [svc["URLs"][0] for svc in data if svc["Name"] == "observability-ui"][0]
            except Exception:
                pass
            else:
                action.ctx["base_url"] = url

    def on_action_success(self, action, args):
        if not action.ctx.get("base_url"):
            cmd_args = []
            if args.profile != MINIKUBE_PROFILE:
                cmd_args.append(f"--profile={args.profile}")
            if args.namespace != NAMESPACE:
                cmd_args.append(f"--namespace={args.namespace}")

            cred_file_path = action.data_folder.joinpath(CREDENTIALS_FILE.format(args.prod))
            with CONSOLE.tee(cred_file_path, append=True) as console_tee:
                console_tee("Because you are using the docker driver on a Mac or Windows, you have to run")
                console_tee("the following command in order to be able to access the platform.")
                console_tee("")
                console_tee(f"python3 {INSTALLER_NAME} {args.prod} expose {' '.join(cmd_args)}")

        self._collect_images_sha(action, args)

    def on_action_fail(self, action, args):
        self._collect_images_sha(action, args)

    def _collect_images_sha(self, action, args):
        images = action.run_cmd(
            "minikube",
            "-p",
            args.profile,
            "image",
            "list",
            "--format=json",
            capture_json=True,
        )
        image_repo_tags = [img["repoTags"][0] for img in images]
        bash_env = action.run_cmd(
            "minikube",
            "-p",
            args.profile,
            "docker-env",
            "--shell",
            "bash",
            capture_text=True,
        )
        env = dict(re.findall(r'export ([\w_]+)="([^"]+)"', bash_env, re.M))
        collect_images_digest(action, image_repo_tags, env)


class ObsDataInitializationStep(Step):
    label = "Initializing the database"
    _user_data = {}

    def execute(self, action, args):
        self._user_data = {"password": generate_password(), **DEFAULT_USER_DATA}
        action.ctx["init_data"] = action.run_cmd(
            "minikube",
            "kubectl",
            "--profile",
            args.profile,
            "--",
            "--namespace",
            args.namespace,
            "exec",
            "-i",
            "deployments/agent-api",
            "--",
            "/dk/bin/cli",
            "init",
            "--demo",
            "--json",
            input=json.dumps(self._user_data).encode(),
            capture_json=True,
        )

    def on_action_success(self, action, args):
        cred_file_path = action.data_folder.joinpath(CREDENTIALS_FILE.format(args.prod))
        with CONSOLE.tee(cred_file_path) as console_tee:
            if url := action.ctx.get("base_url"):
                for service, label in SERVICES_LABELS.items():
                    console_tee(f"{label:>20}: {SERVICES_URLS[service].format(url)}")
                console_tee("")

            console_tee(f"Username: {self._user_data['username']}")
            console_tee(f"Password: {self._user_data['password']}", skip_logging=True)

        CONSOLE.msg(f"(Credentials also written to {cred_file_path.name} file)")


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
            base_url = action.ctx.get("base_url", f"http://host.docker.internal:{DEFAULT_EXPOSE_PORT}")
            config = {
                "api_key": init_data["service_account_key"],
                "project_id": init_data["project_id"],
                "cloud_provider": "azure",
                "api_host": BASE_API_URL_TPL.format(base_url),
            }
            with open(action.data_folder / DEMO_CONFIG_FILE, "w") as file:
                file.write(json.dumps(config))


class ObsInstallAction(AnalyticsMultiStepAction):
    steps = [
        DockerNetworkStep,
        MinikubeProfileStep,
        SetupHelmReposStep,
        ObsHelmInstallServicesStep,
        ObsHelmInstallPlatformStep,
        ObsDataInitializationStep,
        ObsGenerateDemoConfigStep,
    ]

    label = "Installation"
    title = "Install Observability"
    intro_text = ["This process may take 5~30 minutes depending on your system resources and network speed."]

    args_cmd = "install"
    args_parser_parents = [minikube_parser]
    requirements = [REQ_HELM, REQ_MINIKUBE, REQ_MINIKUBE_DRIVER]

    def __init__(self):
        super().__init__()
        self.ctx = {}

    def execute_with_log(self, args):
        if args.driver == "docker":
            self.requirements.append(REQ_DOCKER_DAEMON)

        return super().execute_with_log(args)

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--memory",
            type=str,
            action="store",
            default=DEFAULT_OBS_MEMORY,
            help="Memory to be used for minikube cluster. Defaults to '%(default)s'",
        )
        parser.add_argument(
            "--driver",
            type=str,
            action="store",
            default="docker",
            help="Minikube driver to be used. Defaults to '%(default)s'",
        )
        parser.add_argument(
            "--helm-timeout",
            type=int,
            action="store",
            default=HELM_DEFAULT_TIMEOUT,
            help=(
                "Maximum amount of time in minutes that helm will be allowed to install a release. "
                "Defaults to '%(default)s'"
            ),
        )
        parser.add_argument(
            "--app-values",
            type=str,
            action="store",
            help="Override values for Helm app install. Specify path to a YAML file or URL.",
        )
        parser.add_argument(
            "--docker-username",
            type=str,
            action="store",
            help="Docker username for pulling app images.",
        )
        parser.add_argument(
            "--docker-password",
            type=str,
            action="store",
            help="Docker password for pulling app images.",
        )
        return parser


class ObsExposeAction(Action):
    args_cmd = "expose"
    args_parser_parents = [minikube_parser]
    requirements = [REQ_MINIKUBE]

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--port",
            type=int,
            action="store",
            default=DEFAULT_EXPOSE_PORT,
            help="Which port to listen to",
        )
        return parser

    def execute(self, args):
        CONSOLE.title("Expose Observability ports")

        try:
            with self.start_cmd(
                "minikube",
                "kubectl",
                "--profile",
                args.profile,
                "--",
                "--namespace",
                args.namespace,
                "--address",
                "0.0.0.0",
                "port-forward",
                "service/observability-ui",
                f"{args.port}:http",
                raise_on_non_zero=False,
            ) as (proc, stdout, stderr):
                for output in stdout:
                    if output:
                        break

                if proc.poll() is None:
                    url = f"http://localhost:{args.port}"
                    for service, label in SERVICES_LABELS.items():
                        CONSOLE.msg(f"{label:>20}: {SERVICES_URLS[service].format(url)}")
                    CONSOLE.space()
                    CONSOLE.msg("Listening on all interfaces (0.0.0.0)")
                    CONSOLE.msg("Keep this process running while using the above URLs")
                    CONSOLE.msg("Press Ctrl + C to stop exposing the ports")

                    try:
                        with open(self.data_folder / DEMO_CONFIG_FILE, "r") as file:
                            json_config = json.load(file)
                            json_config["api_host"] = BASE_API_URL_TPL.format(
                                f"http://host.docker.internal:{args.port}"
                            )

                        with open(self.data_folder / DEMO_CONFIG_FILE, "w") as file:
                            file.write(json.dumps(json_config))
                    except Exception:
                        LOG.exception(f"Unable to update {DEMO_CONFIG_FILE} file with exposed port")
                else:
                    for output in stderr:
                        if output:
                            CONSOLE.msg(output.decode().strip())
                    raise CommandFailed

                try:
                    while proc.poll() is None:
                        time.sleep(10)
                except KeyboardInterrupt:
                    # The empty print forces the terminal cursor to move to the first column
                    print()
                    pass

                proc.terminate()

            CONSOLE.msg("The services are no longer exposed.")

        except Exception as e:
            LOG.exception("Something went wrong exposing the services ports")
            CONSOLE.space()
            CONSOLE.msg("The platform could not have its ports exposed.")
            CONSOLE.msg(
                f"Verify if the platform is running and installer has permission to listen at the port {args.port}."
            )
            CONSOLE.space()
            CONSOLE.msg(f"If port {args.port} is in use, use the command option --port to specify an alternate value.")
            raise AbortAction from e


class ObsDeleteAction(Action):
    args_cmd = "delete"
    args_parser_parents = [minikube_parser]
    requirements = [REQ_MINIKUBE]

    def execute(self, args):
        CONSOLE.title("Delete Observability instance")
        try:
            self.run_cmd("minikube", "-p", args.profile, "delete")
        except CommandFailed:
            LOG.exception("Error deleting minikube profile")
            CONSOLE.msg("Could NOT delete the minikube profile")
        else:
            delete_file(self.data_folder / DEMO_CONFIG_FILE)
            delete_file(self.data_folder / CREDENTIALS_FILE.format(args.prod))

            try:
                self.run_cmd(
                    "docker",
                    "network",
                    "rm",
                    DOCKER_NETWORK,
                )
            except CommandFailed:
                LOG.info(f"Could not delete Docker network '{DOCKER_NETWORK}'")
                pass

            CONSOLE.msg("Minikube profile deleted")


class DemoContainerAction(Action):
    requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON]

    def run_dk_demo_container(self, command: str):
        with self.start_cmd(
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
        ) as (proc, stdout, stderr):
            try:
                for line in stdout:
                    if line:
                        CONSOLE.msg(line.decode().strip())
            except KeyboardInterrupt:
                print("")
                proc.terminate()


class ObsRunDemoAction(DemoContainerAction):
    args_cmd = "run-demo"

    def execute(self, args):
        CONSOLE.title("Run Observability demo")
        try:
            self.run_dk_demo_container("obs-run-demo")
        except Exception:
            CONSOLE.title("Demo FAILED")
            CONSOLE.space()
            CONSOLE.msg(f"To retry the demo, first run `python3 {INSTALLER_NAME} {args.prod} delete-demo`")
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
        self.run_dk_demo_container("obs-heartbeat-demo")
        CONSOLE.msg("Observability Heartbeat demo stopped")


class TestGenVerifyExistingInstallStep(Step):
    label = "Verifying existing installation"

    def pre_execute(self, action, args):
        tg_status = get_testgen_status(action)
        tg_volumes = get_testgen_volumes(action)
        if tg_status or tg_volumes:
            CONSOLE.msg("Found TestGen docker compose containers and/or volumes. If a previous attempt to run this")
            CONSOLE.msg(
                f"installer failed, please run `python3 {INSTALLER_NAME} {args.prod} delete` before trying again."
            )
            CONSOLE.space()
            if tg_volumes:
                tg_status["Volumes"] = ", ".join([v.get("Name", "N/A") for v in tg_volumes])
            for k, v in tg_status.items():
                CONSOLE.msg(f"{k:>15}: {v}")
            raise AbortAction


class UpdateComposeFileStep(Step):
    label = "Updating the Docker compose file"

    def __init__(self):
        self.update_version = None
        self.update_analytics = False
        self.update_token = False
        super().__init__()

    def pre_execute(self, action, args):
        action.analytics.additional_properties["version_verify_skipped"] = args.skip_verify

        CONSOLE.space()

        if not args.skip_verify:
            try:
                output = action.run_cmd(
                    "docker",
                    "compose",
                    "-f",
                    action.docker_compose_file_path,
                    "exec",
                    "engine",
                    "testgen",
                    "--help",
                    capture_text=True,
                )
                match = re.search(r"This version:(.*)\s+Latest version:(.*)\s", output)
                current_version = match.group(1)
                latest_version = match.group(2)
            except Exception:
                CONSOLE.msg("Current version: unknown")
                CONSOLE.msg("Latest version: unknown")
                pass
            else:
                CONSOLE.msg(f"Current version: {current_version}")
                CONSOLE.msg(f"Latest version: {latest_version}")

                if current_version != latest_version:
                    self.update_version = latest_version
                else:
                    CONSOLE.msg("Application is already up-to-date.")

        contents = action.docker_compose_file_path.read_text()
        if args.send_analytics_data:
            self.update_analytics = "TG_INSTANCE_ID" not in contents
        else:
            if not re.findall(r"TG_ANALYTICS:\s*no", contents):
                self.update_analytics = True
                CONSOLE.msg("Analytics will be disabled.")

        self.update_token = "TG_JWT_HASHING_KEY" not in contents

        if not any((self.update_version, self.update_analytics, self.update_token)):
            CONSOLE.msg("No changes will be applied.")
            raise AbortAction

    def execute(self, action, args):
        if not any((self.update_version, self.update_analytics, self.update_token)):
            raise SkipStep

        contents = action.docker_compose_file_path.read_text()
        if self.update_version:
            contents = re.sub(r"(image:\s*datakitchen.+:).+\n", rf"\1{TESTGEN_LATEST_TAG}\n", contents)

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

        action.docker_compose_file_path.write_text(contents)


class TestGenCreateDockerComposeFileStep(Step):
    label = "Creating the docker-compose definition file"

    def __init__(self):
        self.username = None
        self.password = None

    def pre_execute(self, action, args):
        if action.docker_compose_file_path.exists():
            self.username, self.password = self.get_credentials_from_compose_file(
                action.docker_compose_file_path.read_text()
            )
            action.using_existing = True
        else:
            self.username = DEFAULT_USER_DATA["username"]
            self.password = generate_password()

        if not all([self.username, self.password]):
            CONSOLE.msg(f"Unable to retrieve username and password from {action.docker_compose_file_path.absolute()}")
            raise AbortAction

        if args.ssl_cert_file and not args.ssl_key_file or not args.ssl_cert_file and args.ssl_key_file:
            CONSOLE.msg("Both --ssl-cert-file and --ssl-key-file must be provided to use SSL certificates.")
            raise AbortAction

    def execute(self, action, args):
        action.analytics.additional_properties["used_custom_cert"] = args.ssl_cert_file and args.ssl_key_file
        action.analytics.additional_properties["existing_compose_file"] = action.using_existing
        action.analytics.additional_properties["used_custom_image"] = bool(args.image)

        if action.using_existing:
            LOG.info("Re-using existing [%s]", action.docker_compose_file_path)
        else:
            LOG.info(
                "Creating [%s] for image [%s]",
                action.docker_compose_file_path,
                args.image,
            )
            self.create_compose_file(
                action,
                args,
                self.username,
                self.password,
                ssl_cert_file=args.ssl_cert_file,
                ssl_key_file=args.ssl_key_file,
            )

    def on_action_success(self, action, args):
        CONSOLE.space()
        if action.using_existing:
            CONSOLE.msg(f"Used existing compose file: {action.docker_compose_file_path}")
        else:
            CONSOLE.msg(f"Created new {DOCKER_COMPOSE_FILE} file using image {args.image}")

        protocol = "https" if args.ssl_cert_file and args.ssl_key_file else "http"
        cred_file_path = action.data_folder.joinpath(CREDENTIALS_FILE.format(args.prod))
        with CONSOLE.tee(cred_file_path) as console_tee:
            console_tee(f"User Interface: {protocol}://localhost:{args.port}")
            console_tee("CLI Access: docker compose exec engine bash")
            console_tee("")
            console_tee(f"Username: {self.username}")
            console_tee(f"Password: {self.password}")

        CONSOLE.msg(f"(Credentials also written to {cred_file_path.name} file)")

    def on_action_fail(self, action, args):
        # We keep the file around for inspection when in debug mode
        if not args.debug and not action.using_existing:
            delete_file(action.docker_compose_file_path)

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

    def create_compose_file(self, action, args, username, password, ssl_cert_file, ssl_key_file):
        ssl_variables = (
            """
              SSL_CERT_FILE: /dk/ssl/cert.crt
              SSL_KEY_FILE: /dk/ssl/cert.key
        """
            if ssl_cert_file and ssl_key_file
            else ""
        )
        ssl_volumes = (
            f"""
                  - type: bind
                    source: {ssl_cert_file}
                    target: /dk/ssl/cert.crt
                  - type: bind
                    source: {ssl_key_file}
                    target: /dk/ssl/cert.key 
        """
            if ssl_cert_file and ssl_key_file
            else ""
        )

        action.docker_compose_file_path.write_text(
            textwrap.dedent(
                f"""
            name: testgen

            x-common-variables: &common-variables
              TESTGEN_USERNAME: {username}
              TESTGEN_PASSWORD: {password}
              TG_DECRYPT_SALT: {generate_password()}
              TG_DECRYPT_PASSWORD: {generate_password()}
              TG_JWT_HASHING_KEY: {str(base64.b64encode(random.randbytes(32)), "ascii")}
              TG_METADATA_DB_HOST: postgres
              TG_TARGET_DB_TRUST_SERVER_CERTIFICATE: yes
              TG_EXPORT_TO_OBSERVABILITY_VERIFY_SSL: no
              TG_DOCKER_RELEASE_CHECK_ENABLED: yes
              TG_INSTANCE_ID: {action.analytics.get_instance_id()}
              TG_ANALYTICS: {"yes" if args.send_analytics_data else "no"}
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
                extra_hosts:
                  - host.docker.internal:host-gateway
                depends_on:
                  - postgres
                networks:
                  - datakitchen

              postgres:
                image: postgres:14.1-alpine
                restart: always
                environment:
                  - POSTGRES_USER={username}
                  - POSTGRES_PASSWORD={password}
                volumes:
                  - postgres_data:/var/lib/postgresql/data
                healthcheck:
                  test: ["CMD-SHELL", "pg_isready -U {username}"]
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
            """
            )
        )


class TestGenPullImagesStep(Step):
    label = "Pulling docker images"

    def execute(self, action, args):
        action.analytics.additional_properties["pull_timeout"] = args.pull_timeout

        try:
            action.run_cmd_retries(
                "docker",
                "compose",
                "-f",
                action.docker_compose_file_path,
                "pull",
                "--policy",
                "always",
                timeout=args.pull_timeout * 60,
                retries=TESTGEN_PULL_RETRIES,
            )
        except CommandFailed:
            # Pulling the images before starting is not mandatory, so we just proceed if it fails
            raise SkipStep

    def _collect_images_sha(self, action):
        images = action.run_cmd(
            "docker",
            "compose",
            "-f",
            action.docker_compose_file_path,
            "images",
            "--format",
            "json",
            capture_json=True,
        )
        image_repo_tags = [":".join((img["Repository"], img["Tag"])) for img in images]
        collect_images_digest(action, image_repo_tags)

    def on_action_fail(self, action, args):
        self._collect_images_sha(action)

    def on_action_success(self, action, args):
        self._collect_images_sha(action)


class TestGenStartStep(Step):
    label = "Starting docker compose application"

    def execute(self, action, args):
        action.run_cmd(
            "docker",
            "compose",
            "-f",
            action.docker_compose_file_path,
            "up",
            "--wait",
        )

    def on_action_fail(self, action, args):
        if action.args_cmd == "install":
            action.run_cmd(
                "docker",
                "compose",
                "-f",
                action.docker_compose_file_path,
                "down",
                "--volumes",
            )


class TestGenStopStep(Step):
    label = "Stopping docker compose application"

    def execute(self, action, args):
        action.run_cmd(
            "docker",
            "compose",
            "-f",
            action.docker_compose_file_path,
            "down",
        )


class TestGenSetupDatabaseStep(Step):
    label = "Initializing the platform database"

    def execute(self, action, args):
        action.run_cmd(
            "docker",
            "compose",
            "-f",
            action.docker_compose_file_path,
            "exec",
            "engine",
            "testgen",
            "setup-system-db",
            "--yes",
        )


class TestGenUpgradeDatabaseStep(Step):
    label = "Upgrading the platform database"

    def pre_execute(self, action, args):
        self.required = action.args_cmd == "upgrade"

    def execute(self, action, args):
        if action.args_cmd == "install" and action.using_existing:
            raise SkipStep
        else:
            action.run_cmd(
                "docker",
                "compose",
                "-f",
                action.docker_compose_file_path,
                "exec",
                "engine",
                "testgen",
                "upgrade-system-version",
            )

    def on_action_success(self, action, args):
        output = action.run_cmd(
            "docker",
            "compose",
            "-f",
            action.docker_compose_file_path,
            "exec",
            "engine",
            "testgen",
            "--help",
            capture_text=True,
        )

        match = re.search("This version:(.*)", output)
        CONSOLE.msg(f"Application version: {match.group(1)}")


class TestgenActionMixin:
    @property
    def docker_compose_file_path(self):
        compose_path = self.data_folder.joinpath(DOCKER_COMPOSE_FILE)
        try:
            compose_path = compose_path.relative_to(pathlib.Path().absolute())
        except ValueError:
            pass
        return compose_path


class TestgenInstallAction(TestgenActionMixin, AnalyticsMultiStepAction):
    steps = [
        TestGenVerifyExistingInstallStep,
        DockerNetworkStep,
        TestGenCreateDockerComposeFileStep,
        TestGenPullImagesStep,
        TestGenStartStep,
        TestGenSetupDatabaseStep,
        TestGenUpgradeDatabaseStep,
    ]

    label = "Installation"
    title = "Install TestGen"
    intro_text = ["This process may take 5~10 minutes depending on your system resources and network speed."]

    args_cmd = "install"
    requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON]

    def __init__(self):
        super().__init__()
        self.using_existing = False

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--port",
            dest="port",
            action="store",
            default=TESTGEN_DEFAULT_PORT,
            help="Which port will be used to access Testgen UI. Defaults to %(default)s",
        )
        parser.add_argument(
            "--image",
            dest="image",
            action="store",
            default=TESTGEN_DEFAULT_IMAGE,
            help="TestGen image to use for the install. Defaults to %(default)s",
        )
        parser.add_argument(
            "--pull-timeout",
            type=int,
            action="store",
            default=TESTGEN_PULL_TIMEOUT,
            help=(
                "Maximum amount of time in minutes that Docker will be allowed to pull the images. "
                "Defaults to '%(default)s'"
            ),
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
        return parser


class TestgenUpgradeAction(TestgenActionMixin, AnalyticsMultiStepAction):
    steps = [
        UpdateComposeFileStep,
        TestGenStopStep,
        TestGenPullImagesStep,
        TestGenStartStep,
        TestGenUpgradeDatabaseStep,
    ]

    label = "Upgrade"
    title = "Upgrade TestGen"
    intro_text = ["This process may take 5~10 minutes depending on your system resources and network speed."]

    args_cmd = "upgrade"

    @property
    def requirements(self):
        return [
            REQ_DOCKER,
            REQ_DOCKER_DAEMON,
            Requirement(
                "TG_COMPOSE_FILE",
                (
                    "docker",
                    "compose",
                    "-f",
                    str(self.docker_compose_file_path),
                    "config",
                ),
                (
                    "TestGen's Docker compose file is not available.",
                    "Re-install TestGen and try again.",
                ),
            ),
        ]

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--skip-verify",
            dest="skip_verify",
            action="store_true",
            help="Whether to skip the version check before upgrading.",
        )
        parser.add_argument(
            "--pull-timeout",
            type=int,
            action="store",
            default=TESTGEN_PULL_TIMEOUT,
            help=(
                "Maximum amount of time in minutes that Docker will be allowed to pull the images. "
                "Defaults to '%(default)s'"
            ),
        )


class TestgenDeleteAction(Action, TestgenActionMixin):
    args_cmd = "delete"
    requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON]

    def execute(self, args):
        if self.docker_compose_file_path.exists():
            self._delete_containers(args)
            self._delete_network()
        else:
            # Trying to delete the network before any exception
            self._delete_network()
            # Trying to delete dangling volumes
            self._delete_volumes()

    def _delete_containers(self, args):
        CONSOLE.title("Delete TestGen instance")
        try:
            self.run_cmd(
                "docker",
                "compose",
                "-f",
                self.docker_compose_file_path,
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
                delete_file(self.docker_compose_file_path)
            delete_file(self.data_folder / CREDENTIALS_FILE.format(args.prod))
            CONSOLE.msg("Docker containers and volumes deleted")

    def _delete_network(self):
        try:
            self.run_cmd("docker", "network", "rm", DOCKER_NETWORK, raise_on_non_zero=True)
        except CommandFailed:
            LOG.info(f"Could not delete Docker network '{DOCKER_NETWORK}'")
        else:
            CONSOLE.msg("Docker network deleted")

    def _delete_volumes(self):
        if volumes := get_testgen_volumes(self):
            try:
                self.run_cmd(
                    "docker",
                    "volume",
                    "rm",
                    *[v["Name"] for v in volumes],
                    raise_on_non_zero=True,
                )
            except CommandFailed:
                CONSOLE.msg("Could NOT delete docker volumes. Please delete them manually")
                raise AbortAction
            else:
                CONSOLE.msg("Docker volumes deleted")

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


class TestgenRunDemoAction(DemoContainerAction, TestgenActionMixin):
    args_cmd = "run-demo"

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

    def execute(self, args):
        self.analytics.additional_properties["obs_export"] = args.obs_export

        CONSOLE.title("Run TestGen demo")

        tg_status = get_testgen_status(self)
        if not tg_status or not re.match(".*running.*", tg_status["Status"], re.I):
            CONSOLE.msg("Running the TestGen demo requires the platform to be running.")
            raise AbortAction

        if args.obs_export:
            if not (self.data_folder / DEMO_CONFIG_FILE).exists():
                CONSOLE.msg("Observability demo configuration missing.")
                raise AbortAction

            self.run_dk_demo_container("tg-run-demo")

        quick_start_command = [
            "testgen",
            "quick-start",
            "--delete-target-db",
        ]
        if args.obs_export:
            with open(self.data_folder / DEMO_CONFIG_FILE, "r") as file:
                json_config = json.load(file)

            quick_start_command.extend(
                [
                    "--observability-api-url",
                    json_config["api_host"],
                    "--observability-api-key",
                    json_config["api_key"],
                ]
            )

        cli_commands = [
            quick_start_command,
            [
                "testgen",
                "run-profile",
                "--table-group-id",
                "0ea85e17-acbe-47fe-8394-9970725ad37d",
            ],
            [
                "testgen",
                "run-test-generation",
                "--table-group-id",
                "0ea85e17-acbe-47fe-8394-9970725ad37d",
            ],
            [
                "testgen",
                "run-tests",
                "--project-key",
                "DEFAULT",
                "--test-suite-key",
                "default-suite-1",
            ],
        ]
        if args.obs_export:
            cli_commands.append(
                [
                    "testgen",
                    "export-observability",
                    "--project-key",
                    "DEFAULT",
                    "--test-suite-key",
                    "default-suite-1",
                ]
            )

        cli_commands.append(["testgen", "quick-start", "--simulate-fast-forward"])

        if args.obs_export:
            cli_commands.append(
                [
                    "testgen",
                    "export-observability",
                    "--project-key",
                    "DEFAULT",
                    "--test-suite-key",
                    "default-suite-1",
                ]
            )

        for command in cli_commands:
            CONSOLE.msg(f"Running command : docker compose exec engine {' '.join(command)}")
            self.run_cmd(
                "docker",
                "compose",
                "-f",
                self.docker_compose_file_path,
                "exec",
                "engine",
                *command,
            )

        CONSOLE.msg("Completed creating demo!")


class TestgenDeleteDemoAction(DemoContainerAction, TestgenActionMixin):
    args_cmd = "delete-demo"

    def execute(self, args):
        CONSOLE.title("Delete TestGen demo")
        try:
            self.run_dk_demo_container("tg-delete-demo")
        except Exception:
            pass

        CONSOLE.msg("Cleaning up system database..")
        tg_status = get_testgen_status(self)
        if tg_status:
            self.run_cmd(
                "docker",
                "compose",
                "-f",
                self.docker_compose_file_path,
                "exec",
                "engine",
                "testgen",
                "setup-system-db",
                "--delete-db",
                "--yes",
            )

            CONSOLE.title("Demo data DELETED")
        else:
            CONSOLE.msg("TestGen must be running for its demo data to be cleaned.")
            raise AbortAction


class AccessInstructionsAction(Action):
    args_cmd = "access-info"

    def execute(self, args):
        try:
            info = self.data_folder.joinpath(CREDENTIALS_FILE.format(args.prod)).read_text()
        except Exception:
            CONSOLE.msg("No Access Information found. Is the platform installed?")
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
    tg_menu.add_option("Upgrade TestGen", ["tg", "upgrade"])
    tg_menu.add_option("Access Instructions", ["tg", "access-info"])
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
    obs_menu.add_option("Access Instructions", ["obs", "access-info"])
    obs_menu.add_option("Expose web access", ["obs", "expose"])
    obs_menu.add_option("Install Observability demo data", ["obs", "run-demo"])
    obs_menu.add_option("Delete Observability demo data", ["obs", "delete-demo"])
    obs_menu.add_option("Run heartbeat demo", ["obs", "run-heartbeat-demo"])
    obs_menu.add_option("Uninstall Observability", ["obs", "delete"])

    cfg_menu = Menu(add_config, "Configuration")
    cfg_menu.add_option(
        "Disable sending analytics data",
        key="send_analytics_data",
        value="--no-analytics",
        msg="Sending analytcs data has been disabled for this session.",
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
            ObsExposeAction(),
            AccessInstructionsAction(),
            ObsDeleteAction(),
            ObsRunDemoAction(),
            ObsDeleteDemoAction(),
            ObsRunHeartbeatDemoAction(),
        ],
    )

    installer_instance.add_product(
        "tg",
        [
            TestgenInstallAction(),
            TestgenUpgradeAction(),
            AccessInstructionsAction(),
            TestgenDeleteAction(),
            TestgenRunDemoAction(),
            TestgenDeleteDemoAction(),
        ],
    )
    return installer_instance


if __name__ == "__main__":
    installer = get_installer_instance()

    # Show the menu when running from the Windows .exe without arguments
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        try:
            output = subprocess.check_output('systeminfo | findstr /B /C:"OS Name"', shell=True, text=True)
        except Exception:
            pass
        else:
            if "Pro" not in output:
                print("\nWARNING: Your Windows edition is not compatible with Docker.")

        show_menu(installer)
    else:
        sys.exit(installer.run())
