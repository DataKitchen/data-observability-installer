from functools import partial
from unittest.mock import call, patch

import pytest

from tests.installer import AbortAction, TestgenRunDemoAction


@pytest.fixture
def tg_run_demo_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = TestgenRunDemoAction()
    args_mock.prod = "tg"
    args_mock.action = "run-demo"
    with patch.object(action, "execute", new=partial(action.execute, args_mock)):
        yield action


@pytest.mark.integration
@pytest.mark.parametrize("obs_export", (False, True))
def test_tg_run_demo(obs_export, tg_run_demo_action, args_mock, start_cmd_mock, stdout_mock, compose_path, request):
    args_mock.obs_export = obs_export
    stdout_mock.side_effect = [[b'[{"Name":"testgen","Status":"running(2)"}]']] + [[]] * 10

    compose_args = ("docker", "compose", "-f", compose_path, "exec", "engine", "testgen")
    kwargs = dict(raise_on_non_zero=True, env=None)
    expected_calls = [
        call(*compose_args, "run-profile", "--table-group-id", "0ea85e17-acbe-47fe-8394-9970725ad37d", **kwargs),
        call(
            *compose_args, "run-test-generation", "--table-group-id", "0ea85e17-acbe-47fe-8394-9970725ad37d", **kwargs
        ),
        call(*compose_args, "run-tests", "--project-key", "DEFAULT", "--test-suite-key", "default-suite-1", **kwargs),
        call(*compose_args, "quick-start", "--simulate-fast-forward", **kwargs),
    ]

    if obs_export:
        demo_cfg_path = request.getfixturevalue("demo_config_path")
        expected_calls += [
            call(
                *compose_args,
                "quick-start",
                "--delete-target-db",
                "--observability-api-url",
                "demo-api-host",
                "--observability-api-key",
                "demo-api-key",
                **kwargs,
            ),
            call(
                *compose_args,
                "export-observability",
                "--project-key",
                "DEFAULT",
                "--test-suite-key",
                "default-suite-1",
                **kwargs,
            ),
            call(
                "docker",
                "run",
                "--rm",
                "--mount",
                f"type=bind,source={str(demo_cfg_path)},target=/dk/demo-config.json",
                "--name",
                "dk-demo",
                "--network",
                "datakitchen-network",
                "--add-host",
                "host.docker.internal:host-gateway",
                "datakitchen/data-observability-demo:latest",
                "tg-run-demo",
            ),
        ]
    else:
        expected_calls += [
            call(*compose_args, "quick-start", "--delete-target-db", **kwargs),
        ]

    tg_run_demo_action.execute()

    start_cmd_mock.assert_has_calls(expected_calls, any_order=True)


@pytest.mark.integration
def test_tg_run_demo_abort_not_running(tg_run_demo_action, start_cmd_mock, console_msg_mock):
    with pytest.raises(AbortAction):
        tg_run_demo_action.execute()

    console_msg_mock.assert_any_msg_contains("Running the TestGen demo requires the platform to be running.")


@pytest.mark.integration
def test_tg_run_demo_abort_missing_config(tg_run_demo_action, args_mock, start_cmd_mock, stdout_mock, console_msg_mock):
    stdout_mock.side_effect = [[b'[{"Name":"testgen","Status":"running(2)"}]']] + [[]] * 10
    args_mock.obs_export = True

    with pytest.raises(AbortAction):
        tg_run_demo_action.execute()

    console_msg_mock.assert_any_msg_contains("Observability demo configuration missing.")
