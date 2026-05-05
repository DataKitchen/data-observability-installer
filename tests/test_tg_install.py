from functools import partial
from pathlib import Path
from unittest.mock import call, patch

import pytest

from tests.installer import (
    INSTALL_MODE_DOCKER,
    TestgenInstallAction,
    AbortAction,
    TestGenCreateDockerComposeFileStep,
    ComposeVerifyExistingInstallStep,
)


@pytest.fixture
def tg_install_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = TestgenInstallAction()
    args_mock.prod = "tg"
    args_mock.action = "install"
    args_mock.install_mode = INSTALL_MODE_DOCKER
    # Bypass check_requirements: pre-resolve mode to Docker and seed the step
    # list so execute() runs the Docker pipeline directly.
    action._resolved_mode = INSTALL_MODE_DOCKER
    action.steps = action.docker_steps
    with patch.object(action, "execute", new=partial(action.execute, args_mock)):
        yield action


@pytest.mark.integration
def test_tg_install(tg_install_action, start_cmd_mock, stdout_mock, tmp_data_folder, compose_path):
    tg_install_action.execute()

    docker_call_start = partial(call, "docker", "compose", "-f", compose_path)
    docker_call_run = partial(docker_call_start, raise_on_non_zero=True, env=None)

    start_cmd_mock.assert_has_calls(
        [
            docker_call_start("pull", "--policy", "always"),
            docker_call_run("up", "--wait"),
            docker_call_run("exec", "engine", "testgen", "setup-system-db", "--yes"),
            docker_call_run("exec", "engine", "testgen", "upgrade-system-version"),
            docker_call_run("exec", "engine", "testgen", "--help"),
        ],
        any_order=True,
    )

    assert Path(tmp_data_folder).joinpath("test-compose.yml").stat().st_size > 0
    assert Path(tmp_data_folder).joinpath("dk-tg-credentials.txt").stat().st_size > 0
    marker = Path(tmp_data_folder).joinpath("dk-tg-install.json")
    assert marker.exists()
    import json as _json

    assert _json.loads(marker.read_text())["install_mode"] == "docker"


@pytest.mark.integration
@pytest.mark.parametrize(
    "stdout_effect",
    (
        [['[{"Name":"test-project","Status":"running(2)","ConfigFiles":"<COMPOSE>"}]'], []],
        [[], ['{"Labels":"com.docker.compose.project=test-project,", "Status":"N/A"}']],
    ),
    ids=("container", "volume"),
)
def test_tg_existing_install_abort(stdout_effect, tg_install_action, stdout_mock, compose_path):
    stdout_mock.side_effect = [
        [line.replace("<COMPOSE>", str(compose_path)) for line in output] for output in stdout_effect
    ]
    compose_path.touch()

    with patch.object(tg_install_action, "steps", new=[ComposeVerifyExistingInstallStep]):
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


@pytest.mark.integration
def test_tg_compose_contains_base_url(tg_install_action, start_cmd_mock, stdout_mock, compose_path):
    tg_install_action.execute()
    contents = compose_path.read_text()
    assert "TG_UI_BASE_URL: http://localhost:8501" in contents


@pytest.mark.integration
def test_tg_compose_base_url_custom_port(tg_install_action, start_cmd_mock, stdout_mock, args_mock, compose_path):
    args_mock.port = 9000
    tg_install_action.execute()
    contents = compose_path.read_text()
    assert "TG_UI_BASE_URL: http://localhost:9000" in contents


@pytest.mark.integration
def test_tg_compose_base_url_ssl(tg_install_action, start_cmd_mock, stdout_mock, args_mock, compose_path):
    args_mock.ssl_cert_file = "/path/to/cert.crt"
    args_mock.ssl_key_file = "/path/to/cert.key"
    tg_install_action.execute()
    contents = compose_path.read_text()
    assert "TG_UI_BASE_URL: https://localhost:8501" in contents


@pytest.mark.integration
def test_tg_docker_install_auto_runs_demo(tg_install_action, start_cmd_mock, compose_path):
    """Docker install also generates demo data so the user has something on first launch."""
    tg_install_action.execute()

    start_cmd_mock.assert_any_call(
        "docker",
        "compose",
        "-f",
        compose_path,
        "exec",
        "engine",
        "testgen",
        "quick-start",
        raise_on_non_zero=True,
        env=None,
    )


@pytest.mark.integration
def test_tg_docker_install_no_demo_flag_skips_quick_start(tg_install_action, args_mock, start_cmd_mock):
    """--no-demo opts out of the auto-demo step in Docker mode."""
    args_mock.generate_demo = False

    tg_install_action.execute()

    for invocation in start_cmd_mock.call_args_list:
        assert "quick-start" not in invocation.args, f"quick-start should be skipped, got: {invocation}"
