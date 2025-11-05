from unittest.mock import call, patch

import pytest

from tests.installer import ObsRunDemoAction, ObsRunHeartbeatDemoAction, ObsDeleteDemoAction


@pytest.mark.integration
@pytest.mark.parametrize(
    "action_class,arg_action,demo_cmd",
    (
        (ObsRunDemoAction, "run-demo", "obs-run-demo"),
        (ObsDeleteDemoAction, "delete-demo", "obs-delete-demo"),
        (ObsRunHeartbeatDemoAction, "run-heartbeat-demo", "obs-heartbeat-demo"),
    ),
    ids=("run-demo", "delete-demo", "run-heartbeat-demo"),
)
def test_obs_demo_action(action_class, arg_action, demo_cmd, args_mock, start_cmd_mock, demo_config_path):
    action = action_class()
    args_mock.prod = "obs"
    args_mock.action = arg_action

    action.execute(args_mock)

    start_cmd_mock.assert_has_calls(
        [
            call(
                "docker",
                "run",
                "--rm",
                "--mount",
                f"type=bind,source={demo_config_path},target=/dk/demo-config.json",
                "--name",
                "dk-demo",
                "--network",
                "datakitchen-network",
                "--add-host",
                "host.docker.internal:host-gateway",
                "datakitchen/data-observability-demo:latest",
                demo_cmd,
                raise_on_non_zero=True,
                env=None,
            ),
        ],
        any_order=True,
    )


@pytest.mark.unit
def test_obs_heartbeat_demo_stop(args_mock, start_cmd_mock, demo_config_path):
    action = ObsRunHeartbeatDemoAction()
    with patch.object(action, "run_dk_demo_container") as run_demo_cmd_mock:
        run_demo_cmd_mock.side_effect = KeyboardInterrupt

        action.execute(args_mock)

    run_demo_cmd_mock.assert_called_with("obs-heartbeat-demo")
