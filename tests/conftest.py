import json
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from tests.installer import CONSOLE, Action, TESTGEN_DEFAULT_IMAGE


@pytest.fixture
def proc_mock():
    proc = Mock()
    proc.returncode = 0
    proc.wait.return_value = None
    proc.poll.return_value = 0
    return proc


@pytest.fixture
def stdout_mock():
    return Mock(return_value=[])


@pytest.fixture
def stderr_mock():
    return Mock(return_value=[])


@pytest.fixture
def console_msg_mock():
    with patch.object(CONSOLE, "msg") as mock:

        def _assert_any_msg_contains(text: str):
            assert any(c for c in mock.call_args_list if text in c.args[0]), (
                f"The text '{text}' wasn't found in any of the {len(mock.call_args_list)} message(s) printed."
            )

        mock.assert_any_msg_contains = _assert_any_msg_contains
        yield mock


@pytest.fixture
def start_cmd_mock(action_cls, proc_mock, stdout_mock, stderr_mock):
    exit_mock = Mock()

    @contextmanager
    def _start_cmd(*args, **kwargs):
        try:
            yield proc_mock, stdout_mock(), stderr_mock()
        finally:
            exit_mock()

    with patch.object(action_cls, "start_cmd", side_effect=_start_cmd) as mock:
        mock.attach_mock(exit_mock, "__exit__")
        yield mock


@pytest.fixture
def popen_mock(proc_mock):
    with patch("tests.installer.subprocess.Popen") as popen_mock:
        popen_mock.return_value = proc_mock
        yield popen_mock


@pytest.fixture
def stream_iter_mock():
    with patch("tests.installer.StreamIterator") as si_mock:
        si_mock.__enter__.return_value = si_mock
        yield si_mock


@pytest.fixture
def analytics_mock():
    with patch("tests.installer.AnalyticsWrapper") as mock:
        yield mock


@pytest.fixture
def execute_mock(action_cls):
    with patch.object(action_cls, "execute") as mock:
        yield mock


@pytest.fixture
def execute_with_log_mock(action_cls):
    with patch.object(action_cls, "execute_with_log") as mock:
        yield mock


@pytest.fixture
def tmp_data_folder(action_cls):
    with (
        TemporaryDirectory() as data_folder,
        patch.object(action_cls, "data_folder", new=Path(data_folder), create=True),
    ):
        yield data_folder


@pytest.fixture
def demo_config_path(tmp_data_folder):
    path = Path(tmp_data_folder).joinpath("demo-config.json")
    config = {"api_host": "demo-api-host", "api_key": "demo-api-key"}
    path.write_text(json.dumps(config))
    yield path
    path.unlink()


@pytest.fixture
def compose_path(tmp_data_folder):
    return Path(tmp_data_folder).joinpath("docker-compose.yml")


@pytest.fixture
def action_cls(analytics_mock):
    with (
        patch.object(Action, "session_zip", create=True),
        patch.object(Action, "session_folder", create=True),
        patch.object(Action, "_cmd_idx", create=True, new=0),
        patch.object(Action, "configure_logging", create=True),
        patch.object(Action, "init_session_folder", create=True),
        patch.object(Action, "analytics", create=True, new=analytics_mock),
        patch.object(Action, "execute"),
    ):
        yield Action


@pytest.fixture
def action(action_cls):
    class TestAction(Action):
        args_cmd = "test"

    instance = TestAction()

    yield instance


@pytest.fixture
def args_mock():
    ns = Namespace()

    # Test data
    ns.prod = "test_prod"
    ns.action = "test_action"

    # Common defaults
    ns.send_analytics_data = True
    ns.debug = False

    # TestGen defaults
    ns.pull_timeout = 10
    ns.ssl_key_file = None
    ns.ssl_cert_file = None
    ns.image = TESTGEN_DEFAULT_IMAGE
    ns.port = 8501
    ns.keep_images = False
    ns.keep_config = False
    ns.obs_export = False
    ns.skip_verify = False

    # Observability defaults
    ns.profile = "dk-observability"
    ns.namespace = "datakitchen"
    ns.driver = "docker"
    ns.memory = "4096m"
    ns.helm_timeout = 10
    ns.app_values = None
    ns.docker_username = None
    ns.docker_password = None

    yield ns
