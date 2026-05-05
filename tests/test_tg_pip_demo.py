from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from tests.installer import (
    AbortAction,
    INSTALL_MODE_DOCKER,
    INSTALL_MODE_PIP,
    TestgenDeleteDemoAction,
    TestgenRunDemoAction,
    write_install_marker,
)


UV_PATH = "/usr/local/bin/uv"
TESTGEN_PATH = "/Users/test/.local/bin/testgen"


@pytest.fixture
def pip_run_demo_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    """Drive the pip path of TestgenRunDemoAction."""
    action = TestgenRunDemoAction()
    args_mock.prod = "tg"
    args_mock.action = "run-demo"
    write_install_marker(action.data_folder, args_mock.prod, INSTALL_MODE_PIP)
    # Bypass check_requirements: pre-resolve mode so execute() runs directly.
    action._resolved_mode = INSTALL_MODE_PIP
    with (
        patch("tests.installer.shutil.which", return_value=UV_PATH),
        patch("tests.installer.resolve_testgen_path", return_value=TESTGEN_PATH),
        patch.object(action, "execute", new=partial(action.execute, args_mock)),
    ):
        yield action


@pytest.mark.integration
def test_pip_run_demo_without_export(pip_run_demo_action, start_cmd_mock, args_mock):
    args_mock.obs_export = False

    pip_run_demo_action.execute()

    start_cmd_mock.assert_any_call(
        TESTGEN_PATH,
        "quick-start",
        raise_on_non_zero=True,
        env=None,
    )
    for invocation in start_cmd_mock.call_args_list:
        assert "export-observability" not in invocation.args
        assert "datakitchen/data-observability-demo:latest" not in invocation.args


@pytest.mark.integration
def test_pip_run_demo_with_export(pip_run_demo_action, start_cmd_mock, args_mock, demo_config_path, tmp_data_folder):
    args_mock.obs_export = True

    pip_run_demo_action.execute()

    start_cmd_mock.assert_has_calls(
        [
            call(
                TESTGEN_PATH,
                "quick-start",
                "--observability-api-url",
                "demo-api-host",
                "--observability-api-key",
                "demo-api-key",
                raise_on_non_zero=True,
                env=None,
            ),
            call(
                TESTGEN_PATH,
                "export-observability",
                "--project-key",
                "DEFAULT",
                "--test-suite-key",
                "default-suite-1",
                raise_on_non_zero=True,
                env=None,
            ),
            call(
                "docker",
                "run",
                "--rm",
                "--mount",
                f"type=bind,source={str(demo_config_path)},target=/dk/demo-config.json",
                "--name",
                "dk-demo",
                "--network",
                "datakitchen-network",
                "--add-host",
                "host.docker.internal:host-gateway",
                "datakitchen/data-observability-demo:latest",
                "tg-run-demo",
                raise_on_non_zero=True,
                env=None,
            ),
        ],
        any_order=True,
    )


@pytest.mark.integration
def test_pip_run_demo_export_aborts_when_demo_config_missing(pip_run_demo_action, args_mock, console_msg_mock):
    args_mock.obs_export = True

    with pytest.raises(AbortAction):
        pip_run_demo_action.execute()

    console_msg_mock.assert_any_msg_contains("Observability demo configuration missing")


@pytest.mark.integration
def test_pip_run_demo_aborts_without_uv(action_cls, args_mock, tmp_data_folder, start_cmd_mock, console_msg_mock):
    action = TestgenRunDemoAction()
    args_mock.prod = "tg"
    args_mock.action = "run-demo"
    args_mock.obs_export = False
    write_install_marker(action.data_folder, args_mock.prod, INSTALL_MODE_PIP)
    action._resolved_mode = INSTALL_MODE_PIP

    with (
        patch("tests.installer.shutil.which", return_value=None),
        patch.object(action, "execute", new=partial(action.execute, args_mock)),
        pytest.raises(AbortAction),
    ):
        action.execute()

    console_msg_mock.assert_any_msg_contains("uv not found")


@pytest.fixture
def pip_delete_demo_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = TestgenDeleteDemoAction()
    args_mock.prod = "tg"
    args_mock.action = "delete-demo"
    write_install_marker(action.data_folder, args_mock.prod, INSTALL_MODE_PIP)
    action._resolved_mode = INSTALL_MODE_PIP
    with (
        patch("tests.installer.shutil.which", return_value=UV_PATH),
        patch("tests.installer.resolve_testgen_path", return_value=TESTGEN_PATH),
        patch.object(action, "execute", new=partial(action.execute, args_mock)),
    ):
        yield action


@pytest.mark.integration
def test_pip_delete_demo(pip_delete_demo_action, start_cmd_mock):
    pip_delete_demo_action.execute()

    start_cmd_mock.assert_any_call(
        TESTGEN_PATH,
        "setup-system-db",
        "--delete-db",
        "--yes",
        raise_on_non_zero=True,
        env=None,
    )


# Marker-driven dispatch tests --------------------------------------------


@pytest.fixture
def run_demo_action(action_cls, args_mock, tmp_data_folder):
    action = TestgenRunDemoAction()
    args_mock.prod = "tg"
    args_mock.action = "run-demo"
    args_mock.obs_export = False
    action.analytics = MagicMock()
    action.analytics.additional_properties = {}
    return action


@pytest.mark.integration
def test_run_demo_aborts_without_install(run_demo_action, args_mock, console_msg_mock):
    with pytest.raises(AbortAction):
        run_demo_action._resolve_install_mode(args_mock)

    console_msg_mock.assert_any_msg_contains("tg install")


@pytest.mark.integration
@pytest.mark.parametrize("install_mode", [INSTALL_MODE_PIP, INSTALL_MODE_DOCKER])
def test_run_demo_routes_by_marker(run_demo_action, args_mock, tmp_data_folder, install_mode, start_cmd_mock):
    write_install_marker(Path(tmp_data_folder), "tg", install_mode)
    run_demo_action._resolve_install_mode(args_mock)

    with (
        patch("tests.installer.shutil.which", return_value=UV_PATH),
        patch("tests.installer.resolve_testgen_path", return_value=TESTGEN_PATH),
        patch.object(run_demo_action, "get_status", return_value={"Status": "running(2)"}),
    ):
        run_demo_action.execute(args_mock)

    # Inspect the actual quick-start invocation to confirm dispatch.
    quick_start_calls = [c for c in start_cmd_mock.call_args_list if "quick-start" in c.args]
    assert len(quick_start_calls) == 1
    if install_mode == INSTALL_MODE_PIP:
        assert quick_start_calls[0].args[0] == TESTGEN_PATH
    else:
        assert quick_start_calls[0].args[:2] == ("docker", "compose")
        assert "exec" in quick_start_calls[0].args and "engine" in quick_start_calls[0].args
    assert run_demo_action.analytics.additional_properties["install_mode"] == install_mode


@pytest.fixture
def delete_demo_action(action_cls, args_mock, tmp_data_folder):
    action = TestgenDeleteDemoAction()
    args_mock.prod = "tg"
    args_mock.action = "delete-demo"
    action.analytics = MagicMock()
    action.analytics.additional_properties = {}
    return action


@pytest.mark.integration
@pytest.mark.parametrize("install_mode", [INSTALL_MODE_PIP, INSTALL_MODE_DOCKER])
def test_delete_demo_routes_by_marker(delete_demo_action, args_mock, tmp_data_folder, install_mode, start_cmd_mock):
    write_install_marker(Path(tmp_data_folder), "tg", install_mode)

    delete_demo_action._resolve_install_mode(args_mock)
    with (
        patch("tests.installer.shutil.which", return_value=UV_PATH),
        patch("tests.installer.resolve_testgen_path", return_value=TESTGEN_PATH),
        patch.object(delete_demo_action, "get_status", return_value={"Status": "running"}),
    ):
        delete_demo_action.execute(args_mock)

    assert delete_demo_action.analytics.additional_properties["install_mode"] == install_mode

    if install_mode == INSTALL_MODE_PIP:
        # Pip branch invokes testgen directly.
        start_cmd_mock.assert_any_call(
            TESTGEN_PATH,
            "setup-system-db",
            "--delete-db",
            "--yes",
            raise_on_non_zero=True,
            env=None,
        )
    else:
        # Docker branch invokes via docker compose exec.
        assert any(
            "docker" in c.args and "exec" in c.args and "setup-system-db" in c.args
            for c in start_cmd_mock.call_args_list
        )
