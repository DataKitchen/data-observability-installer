from unittest.mock import call

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
            ),
        ],
        any_order=True,
    )
