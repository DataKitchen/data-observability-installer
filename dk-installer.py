#!/usr/bin/env python3

import argparse
import contextlib
import dataclasses
import datetime
import ipaddress
import json
import logging
import logging.config
import os
import pathlib
import platform
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
MINIKUBE_KUBE_VER = "v1.29"
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
DEFAULT_OBS_MEMORY = 4096
BASE_API_URL_TPL = "{}/api"
CREDENTIALS_FILE = "dk-{}-credentials.txt"
TESTGEN_COMPOSE_NAME = "testgen"
TESTGEN_LATEST_TAG = "v3"
TESTGEN_DEFAULT_IMAGE = f"datakitchen/dataops-testgen:{TESTGEN_LATEST_TAG}"
TESTGEN_PULL_TIMEOUT = 120
TESTGEN_PULL_RETRIES = 3
TESTGEN_DEFAULT_PORT = 8501

LOG = logging.getLogger()

#
# Utility functions
#


def collect_images_digest(action, images, env=None):
    action.run_cmd(
        "docker",
        "image",
        "inspect",
        *images,
        "--format=DIGEST: {{ index .RepoDigests 0 }} CREATED: {{ .Created }}",
        raise_on_non_zero=False,
        env=env,
    )


def get_recommended_minikube_driver():
    if platform.system() == "Darwin" and platform.processor() == "i386":
        return "hyperkit"
    else:
        return "docker"


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


def write_credentials_file(folder: pathlib.Path, product, lines):
    file_path = folder.joinpath(CREDENTIALS_FILE.format(product))
    try:
        with open(file_path, "w") as file:
            file.writelines([f"{text}\n" for text in lines])
    except Exception:
        pass
    else:
        CONSOLE.msg(f"(Credentials also written to {file_path.name} file)")


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


def do_request(url, method="GET", headers=None, params=None, data=None, verify=True):
    query_params = ""
    if params:
        query_params = "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url + query_params, method=method, headers=headers or {})
    if data:
        request.data = json.dumps(data).encode()
        request.add_header("Content-Type", "application/json")

    ssl_context = None
    if not verify:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(request, context=ssl_context) as response:
        try:
            return json.loads(response.read().decode())
        except:
            return {}


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

    def __enter__(self):
        print(self.MARGIN, end="")
        return self

    def send(self, text):
        print(text, end="")
        sys.stdout.flush()
        self._partial_msg += text

    def __exit__(self, exc_type, exc_val, exc_tb):
        print("")
        LOG.info("Console message: [%s]", self._partial_msg)
        self._partial_msg = ""
        self._last_is_space = False
        return False


CONSOLE = Console()


@dataclasses.dataclass
class Requirement:
    name: str
    cmd: tuple[str, ...]

    def check_availability(self, action, args):
        try:
            action.run_cmd(*(seg.format(**args.__dict__) for seg in self.cmd))
        except CommandFailed:
            CONSOLE.msg(f"The installer could not verify that '{self.name}' is available.")
            return False
        else:
            return True


class CommandFailed(Exception):
    """
    Raised when a command returns a non-zero exit code.

    It's useful to prevent the installer logic from having to check the output of each command
    """

    def __init__(self, idx=None, cmd=None, ret_code=None):
        self.idx = idx
        self.cmd = cmd
        self.ret_code = ret_code


class InstallerError(Exception):
    """Should be raised when the root cause could not be addressed and the process is unable to continue."""


class AbortAction(InstallerError):
    """Should be raised when the root cause has been addressed but the process is unable to continue."""


class SkipStep(Exception):
    """Should be raised when a given Step does not need to be executed."""


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


class Action:
    _cmd_idx: int = 0
    args_cmd: str
    args_parser_parents: list = []
    requirements: list = []

    @contextlib.contextmanager
    def init_session_folder(self, prefix):
        if platform.system() == 'Windows':
            self.data_folder = pathlib.Path.home().joinpath("Documents", "DataKitchenApps")
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
                    session_zip.write(session_file, arcname=session_file.relative_to(self.session_zip.parent))
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
                    "": {"handlers": ["file"] + (["console"] if debug else []), "level": "DEBUG"},
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

    def _msg_unexpected_error(self):
        msg_file_path = self.session_zip.relative_to(pathlib.Path().absolute())
        CONSOLE.msg(f"An unexpected error occurred. Please check the logs in {msg_file_path} for details.")
        CONSOLE.msg("")
        CONSOLE.msg("For assistance, reach out the #support channel on https://data-observability-slack.datakitchen.io/join, attaching the logs.")
        CONSOLE.msg("")

    def execute_with_log(self, args):
        with self.init_session_folder(prefix=f"{args.prod}-{self.args_cmd}"), self.configure_logging(debug=args.debug):
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

            try:
                if not all((req.check_availability(self, args) for req in self.requirements)):
                    CONSOLE.msg("Not all requirements are fulfilled")
                    raise AbortAction

                self.execute(args)

            except AbortAction:
                raise
            except InstallerError:
                self._msg_unexpected_error()
                raise
            except Exception as e:
                LOG.exception("Uncaught error: %r", e)
                self._msg_unexpected_error()
                raise InstallerError from e
            except KeyboardInterrupt:
                CONSOLE.msg("Processing interrupted. The platform might be left in a inconsistent state.")
                raise AbortAction

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
            finally:
                retries -= 1

        if cmd_fail_exception and (isinstance(cmd_fail_exception.__cause__, subprocess.TimeoutExpired) or raise_on_non_zero):
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
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, env=env, **popen_args
            )
        except FileNotFoundError as e:
            LOG.error("Command [%04d] failed to find the executable", self._cmd_idx)
            raise CommandFailed(self._cmd_idx, cmd, None) from e

        slug_cmd = re.sub(r"[^a-zA-Z]+", "-", cmd_str)[:100].strip("-")

        def get_stream_iterator(stream_name):
            file_name = f"{self._cmd_idx:04d}-{stream_name}-{slug_cmd}.txt"
            file_path = self.session_folder.joinpath(file_name)
            return StreamIterator(proc, getattr(proc, stream_name), file_path)

        try:
            with get_stream_iterator("stdout") as stdout_iter, get_stream_iterator("stderr") as stderr_iter:
                try:
                    yield proc, stdout_iter, stderr_iter
                finally:
                    proc.wait()
            if raise_on_non_zero and proc.returncode != 0:
                raise CommandFailed
        # We capture and raise CommandFailed to allow the client code to raise an empty CommandFailed exception
        # but still get a contextualized exception at the end
        except CommandFailed as e:
            raise CommandFailed(self._cmd_idx, cmd, proc.returncode) from e.__cause__
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


class MultiStepAction(Action):
    steps: list[Step]
    label = "Process"
    title = ""
    intro_text = ""

    def execute(self, args):
        CONSOLE.title(self.title)
        for step in self.steps:
            try:
                LOG.debug("Running step [%s] pre-execute", step)
                step.pre_execute(self, args)
            except InstallerError:
                raise
            except Exception as e:
                LOG.exception("Step [%s] pre-execute failed", step)
                raise InstallerError from e

        CONSOLE.space()
        if self.intro_text:
            CONSOLE.msg(self.intro_text)
        CONSOLE.space()
        executed_steps: list[Step] = []
        action_fail_exception = None
        for step in self.steps:
            executed_steps.append(step)
            with CONSOLE:
                CONSOLE.send(f"{step.label}... ")
                try:
                    if action_fail_exception:
                        raise SkipStep
                    LOG.debug("Executing step [%s]", step)
                    step.execute(self, args)
                except SkipStep:
                    CONSOLE.send("SKIPPED")
                    continue
                except Exception as e:
                    CONSOLE.send("FAILED")
                    if step.required:
                        action_fail_exception = e
                    else:
                        LOG.warning(f"Non-required step [%s] failed with: %s", step, e)
                else:
                    CONSOLE.send("OK")

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
            except Exception as e:
                LOG.exception("Post-execution of step [%s] failed", step)

        if action_fail_exception:
            raise action_fail_exception


class Installer:
    def __init__(self):
        self.parser = argparse.ArgumentParser(description="DataKitchen Installer")
        self.parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
        self.sub_parsers = self.parser.add_subparsers(help="Products", required=True)

    def run(self, def_args):
        # def_args has to be None to preserve the argparser behavior when only part of the arguments are used
        args = self.parser.parse_args(def_args or None)

        if not hasattr(args, "func"):
            self.parser.print_usage()
            return 2

        CONSOLE.title("DataKitchen Data Observability Installer")

        try:
            args.func(args)
        except AbortAction:
            return 1
        except Exception:
            return 2
        else:
            return 0

    def add_product(self, prefix, actions, defaults=None):
        prod_parser = self.sub_parsers.add_parser(prefix)
        prod_parser.set_defaults(prod=prefix, **(defaults or {}))
        prod_sub_parsers = prod_parser.add_subparsers(required=True)

        for action in actions:
            action.get_parser(prod_sub_parsers)


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

REQ_HELM = Requirement("Helm", ("helm", "version"))
REQ_MINIKUBE = Requirement("minikube", ("minikube", "version"))
REQ_MINIKUBE_DRIVER = Requirement("minikube driver", ("{driver}", "-v"))
REQ_DOCKER = Requirement("Docker", ("docker", "-v"))
REQ_DOCKER_DAEMON = Requirement("Docker daemon process", ("docker", "info"))
REQ_TESTGEN_CONFIG = Requirement(f"TestGen {DOCKER_COMPOSE_FILE}", ("docker", "compose", "config"))

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
        action.run_cmd("helm", "status", release, "-o", "json", capture_json=True, raise_on_non_zero=False)

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

            CONSOLE.space()
            CONSOLE.msg("Because you are using the docker driver on a Mac or Windows, you have to run")
            CONSOLE.msg("the following command in order to be able to access the platform.")
            CONSOLE.space()
            CONSOLE.msg(f"python3 {INSTALLER_NAME} {args.prod} expose {' '.join(cmd_args)}")

        self._collect_images_sha(action, args)

    def on_action_fail(self, action, args):
        self._collect_images_sha(action, args)

    def _collect_images_sha(self, action, args):
        images = action.run_cmd("minikube", "-p", args.profile, "image", "list", "--format=json", capture_json=True)
        image_repo_tags = [img["repoTags"][0] for img in images]
        bash_env = action.run_cmd("minikube", "-p", args.profile, "docker-env", "--shell", "bash", capture_text=True)
        env = dict(re.findall(r'export ([\w_]+)="([^"]+)"', bash_env, re.M))
        collect_images_digest(action, image_repo_tags, env)


class ObsDataInitializationStep(Step):
    label = "Initializing the database"
    _user_data = {}

    def execute(self, action, args):
        self._user_data = {
            "name": "Admin",
            "email": "email@example.com",
            "username": "admin",
            "password": generate_password(),
        }

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
        info_lines = []
        if url := action.ctx.get("base_url"):
            for service, label in SERVICES_LABELS.items():
                info_lines.append(f"{label:>20}: {SERVICES_URLS[service].format(url)}")
            info_lines.append("")

        info_lines.extend(
            [
                f"Username: {self._user_data['username']}",
                f"Password: {self._user_data['password']}",
                "",
            ]
        )

        CONSOLE.space()
        for line in info_lines:
            CONSOLE.msg(line, skip_logging="Password" in line) if line else CONSOLE.space()

        write_credentials_file(action.data_folder, args.prod, info_lines)


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


class ObsInstallAction(MultiStepAction):
    steps = [
        DockerNetworkStep(),
        MinikubeProfileStep(),
        SetupHelmReposStep(),
        ObsHelmInstallServicesStep(),
        ObsHelmInstallPlatformStep(),
        ObsDataInitializationStep(),
        ObsGenerateDemoConfigStep(),
    ]

    label = "Installation"
    title = "Install Observability"
    intro_text = "This process may take 5~30 minutes depending on your system resources and network speed."

    args_cmd = "install"
    args_parser_parents = [minikube_parser]
    requirements = [REQ_HELM, REQ_MINIKUBE, REQ_MINIKUBE_DRIVER]

    def __init__(self):
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
            default=get_recommended_minikube_driver(),
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
                            json_config["api_host"] = BASE_API_URL_TPL.format(f"http://host.docker.internal:{args.port}")

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

        except Exception:
            LOG.exception("Something went wrong exposing the services ports")
            CONSOLE.space()
            CONSOLE.msg("The platform could not have its ports exposed.")
            CONSOLE.msg(
                f"Verify if the platform is running and installer has permission to listen at the port {args.port}."
            )
            CONSOLE.space()
            CONSOLE.msg(
                f"If port {args.port} is in use, use the command option --port to specify an alternate value."
            )
            raise AbortAction


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


class TestGenVerifyVersionStep(Step):
    label = "Verifying latest version"

    def pre_execute(self, action, args):
        if args.skip_verify:
            return
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
            CONSOLE.msg(f"Current version: unknown")
            CONSOLE.msg(f"Latest version: unknown")
            pass
        else:
            CONSOLE.msg(f"Current version: {current_version}")
            CONSOLE.msg(f"Latest version: {latest_version}")

            if current_version == latest_version:
                CONSOLE.space()
                CONSOLE.msg("Application is already up-to-date.")
                raise AbortAction

    def execute(self, action, args):
        if args.skip_verify:
            raise SkipStep
        
        contents = action.docker_compose_file_path.read_text()
        new_contents = re.sub(r"(image:\s*datakitchen.+:).+\n", fr"\1{TESTGEN_LATEST_TAG}\n", contents)
        action.docker_compose_file_path.write_text(new_contents)


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
            self.username = "admin"
            self.password = generate_password()

        if not all([self.username, self.password]):
            CONSOLE.msg(f"Unable to retrieve username and password from {action.docker_compose_file_path.absolute()}")
            raise AbortAction

        if args.ssl_cert_file and not args.ssl_key_file or not args.ssl_cert_file and args.ssl_key_file:
            CONSOLE.msg("Both --ssl-cert-file and --ssl-key-file must be provided to use SSL certificates.")
            raise AbortAction

    def execute(self, action, args):
        if action.using_existing:
            LOG.info("Re-using existing [%s]", action.docker_compose_file_path)
        else:
            LOG.info("Creating [%s] for image [%s]", action.docker_compose_file_path, args.image)
            self.create_compose_file(
                action,
                self.username,
                self.password,
                args.port,
                image=args.image,
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
        info_lines = [
            f"User Interface: {protocol}://localhost:{args.port}",
            "CLI Access: docker compose exec engine bash",
            "",
            f"Username: {self.username}",
            f"Password: {self.password}",
        ]
        CONSOLE.space()
        for line in info_lines:
            CONSOLE.msg(line, skip_logging="Password" in line)
        write_credentials_file(action.data_folder, args.prod, info_lines)

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

    def create_compose_file(self, action, username, password, port, image, ssl_cert_file, ssl_key_file):
        ssl_variables = """
              SSL_CERT_FILE: /dk/ssl/cert.crt
              SSL_KEY_FILE: /dk/ssl/cert.key
        """ if ssl_cert_file and ssl_key_file else ""
        ssl_volumes = f"""
                  - type: bind
                    source: {ssl_cert_file}
                    target: /dk/ssl/cert.crt
                  - type: bind
                    source: {ssl_key_file}
                    target: /dk/ssl/cert.key 
        """ if ssl_cert_file and ssl_key_file else ""

        action.docker_compose_file_path.write_text(
            textwrap.dedent(
                f"""
            name: testgen

            x-common-variables: &common-variables
              TESTGEN_USERNAME: {username}
              TESTGEN_PASSWORD: {password}
              TG_DECRYPT_SALT: {generate_password()}
              TG_DECRYPT_PASSWORD: {generate_password()}
              TG_METADATA_DB_HOST: postgres
              TG_TARGET_DB_TRUST_SERVER_CERTIFICATE: yes
              TG_EXPORT_TO_OBSERVABILITY_VERIFY_SSL: no
              TG_DOCKER_RELEASE_CHECK_ENABLED: yes
              {ssl_variables}

            services:
              engine:
                image: {image}
                container_name: testgen
                environment: *common-variables
                volumes:
                  - testgen_data:/var/lib/testgen
                  {ssl_volumes}      
                ports:
                  - {port}:{TESTGEN_DEFAULT_PORT}
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
        try:
            action.run_cmd_retries(
                "docker",
                "compose",
                "-f",
                action.docker_compose_file_path,
                "pull",
                "--policy",
                "always",
                timeout=TESTGEN_PULL_TIMEOUT,
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
            action.run_cmd("docker", "compose", "-f", action.docker_compose_file_path, "down", "--volumes")


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
        CONSOLE.space()


class TestgenActionMixin:

    @property
    def docker_compose_file_path(self):
        return self.data_folder.joinpath(DOCKER_COMPOSE_FILE)


class TestgenInstallAction(MultiStepAction, TestgenActionMixin):
    steps = [
        TestGenVerifyExistingInstallStep(),
        DockerNetworkStep(),
        TestGenCreateDockerComposeFileStep(),
        TestGenPullImagesStep(),
        TestGenStartStep(),
        TestGenSetupDatabaseStep(),
        TestGenUpgradeDatabaseStep(),
    ]

    label = "Installation"
    title = "Install TestGen"
    intro_text = "This process may take 5~10 minutes depending on your system resources and network speed."

    args_cmd = "install"
    requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON]

    def __init__(self):
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


class TestgenUpgradeAction(MultiStepAction, TestgenActionMixin):
    steps = [
        TestGenVerifyVersionStep(),
        TestGenStopStep(),
        TestGenPullImagesStep(),
        TestGenStartStep(),
        TestGenUpgradeDatabaseStep(),
    ]

    label = "Upgrade"
    title = "Upgrade TestGen"
    intro_text = "This process may take 5~10 minutes depending on your system resources and network speed."

    args_cmd = "upgrade"
    requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON, REQ_TESTGEN_CONFIG]

    def get_parser(self, sub_parsers):
        parser = super().get_parser(sub_parsers)
        parser.add_argument(
            "--skip-verify",
            dest="skip_verify",
            action="store_true",
            help="Whether to skip the version check before upgrading.",
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
                self.run_cmd("docker", "volume", "rm", *[v["Name"] for v in volumes], raise_on_non_zero=True)
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
        CONSOLE.title("Run TestGen demo")

        tg_status = get_testgen_status(self)
        if not tg_status or not re.match(".*running.*", tg_status["Status"], re.I):
            CONSOLE.msg("Running the TestGen demo requires the platform to be running.")
            raise AbortAction

        if args.obs_export:
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
            self.run_cmd("docker", "compose", "-f", self.docker_compose_file_path, "exec", "engine", *command)

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


#
# Entrypoint
#

def show_menu():
    print("\n" + "=" * 20)
    print("  Choose a Product   ")
    print("=" * 20)
    print(" 1. TestGen            ")
    print(" 2. Observability      ")
    print(" 0. Exit               ")
    print("=" * 20)
    print()


def get_menu_choice():
    while True:
        try:
            choice = int(input("Enter your choice (0-2): "))
            print("")
            if choice == 0:
                print("Exiting...")
                exit(0)

            elif choice == 1:
                print("\n" + "=" * 30)
                print("        TestGen Menu        ")
                print("=" * 30)
                print(" 1. Install TestGen          ")
                print(" 2. Upgrade TestGen          ")
                print(" 3. Install TestGen demo data")
                print(" 4. Delete TestGen demo data ")
                print(" 5. Uninstall TestGen        ")
                print(" 6. Return to main menu      ")
                print(" 0. Exit                     ")
                print("=" * 30)
                print()
                action = int(input("Enter your choice (0-6): "))
                if action == 6:
                    return []
                elif action == 1:
                    return ["tg", "install"]
                elif action == 2:
                    return ['tg', 'upgrade']
                elif action == 5:
                    return ['tg', 'delete']
                elif action == 3:
                    return ['tg', 'run-demo']
                elif action == 4:
                    return ['tg', 'delete-demo']
                elif action == 0:
                    print("exiting...")
                    exit(0)

            elif choice == 2:
                print("\n" + "=" * 35)
                print("        Observability Menu        ")
                print("=" * 35)
                print("You selected Observability.")
                print("1. Install Observability")
                print("2. Upgrade Observability")
                print("3. Install Observability demo data")
                print("4. Delete Observability demo data")
                print("5. Run heartbeat demo")
                print("6. Delete Observability")
                print("7. Return main menu")
                print("0. Exit")
                print("=" * 35)
                print()
                action = int(input("Enter your choice (0-7): "))
                if action == 7:
                    return []
                elif action == 1:
                    return ['obs', 'install']
                elif action == 2:
                    return ['obs', 'upgrade']
                elif action == 6:
                    return ['obs', 'delete']
                elif action == 3:
                    return ['obs', 'run-demo']
                elif action == 4:
                    return ['obs', 'delete-demo']
                elif action == 5:
                    return ['obs', 'run-heartbeat-demo']
                elif action == 0:
                    print("exiting...")
                    exit(0)
            else:
                print("Invalid option. Please choose a number between 0 and 7.")
        except ValueError:
            print("Invalid input. Please enter a number.")


def get_installer_instance():
    installer_instance = Installer()
    installer_instance.add_product(
        "obs",
        [
            ObsInstallAction(),
            ObsExposeAction(),
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
            TestgenDeleteAction(),
            TestgenRunDemoAction(),
            TestgenDeleteDemoAction(),
        ],
    )
    return installer_instance


if __name__ == "__main__":
    installer = get_installer_instance()
    args = []

    # Show the menu when running from the windows .exe without arguments
    if getattr(sys, 'frozen', False) and len(sys.argv) == 1:
        print("DataKitchen Installer")
        while not args:
            show_menu()
            args = get_menu_choice()

    exit(installer.run(args))
