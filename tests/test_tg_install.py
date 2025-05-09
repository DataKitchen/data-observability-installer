from functools import partial
from pathlib import Path
from unittest.mock import call, patch

import pytest

from tests.installer import (
    TestgenInstallAction,
    AbortAction,
    TestGenVerifyExistingInstallStep,
    TestGenCreateDockerComposeFileStep,
)


@pytest.fixture
def tg_install_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = TestgenInstallAction()
    args_mock.prod = "tg"
    args_mock.action = "install"
    with patch.object(action, "execute", new=partial(action.execute, args_mock)):
        yield action


@pytest.mark.integration
def test_tg_install(tg_install_action, start_cmd_mock, stdout_mock, tmp_data_folder, compose_path):
    tg_install_action.execute()

    docker_call_retry = partial(call, "docker", "compose", "-f", compose_path, env=None)
    docker_call = partial(docker_call_retry, raise_on_non_zero=True)

    start_cmd_mock.assert_has_calls(
        [
            docker_call_retry("pull", "--policy", "always"),
            docker_call("up", "--wait"),
            docker_call("exec", "engine", "testgen", "setup-system-db", "--yes"),
            docker_call("exec", "engine", "testgen", "upgrade-system-version"),
            docker_call("exec", "engine", "testgen", "--help"),
        ],
        any_order=True,
    )

    assert Path(tmp_data_folder).joinpath("docker-compose.yml").stat().st_size > 0
    assert Path(tmp_data_folder).joinpath("dk-tg-credentials.txt").stat().st_size > 0


@pytest.mark.integration
@pytest.mark.parametrize(
    "stdout_effect",
    (
        [[b'[{"Name":"testgen","Status":"running(2)"}]'], []],
        [[], [b'{"Labels":"com.docker.compose.project=testgen,", "Status":"N/A"}']],
    ),
    ids=("container", "volume"),
)
def test_tg_existing_install_abort(stdout_effect, tg_install_action, stdout_mock):
    stdout_mock.side_effect = stdout_effect
    with patch.object(tg_install_action, "steps", new=[TestGenVerifyExistingInstallStep]):
        with pytest.raises(AbortAction):
            tg_install_action.execute()


@pytest.mark.integration
def test_tg_create_compose_file_abort_password(tg_install_action, stdout_mock, compose_path, console_msg_mock):
    compose_path.touch()

    with patch.object(tg_install_action, "steps", new=[TestGenCreateDockerComposeFileStep]):
        with pytest.raises(AbortAction):
            tg_install_action.execute()

    console_msg_mock.assert_any_msg_contains("Unable to retrieve username and password")


@pytest.mark.integration
@pytest.mark.parametrize("arg_to_set", ("ssl_cert_file", "ssl_key_file"))
def test_tg_create_compose_file_abort_args(arg_to_set, tg_install_action, stdout_mock, args_mock, console_msg_mock):
    setattr(args_mock, arg_to_set, "/some/file/path")

    with patch.object(tg_install_action, "steps", new=[TestGenCreateDockerComposeFileStep]):
        with pytest.raises(AbortAction):
            tg_install_action.execute()

    console_msg_mock.assert_any_msg_contains(
        "Both --ssl-cert-file and --ssl-key-file must be provided to use SSL certificates.",
    )
