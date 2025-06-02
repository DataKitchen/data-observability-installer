import json
from functools import partial
from unittest.mock import call, patch

import pytest

from tests.installer import ObsExposeAction, CommandFailed, AbortAction


@pytest.fixture
def obs_expose_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = ObsExposeAction()
    args_mock.prod = "obs"
    args_mock.action = "expose"
    with patch.object(action, "execute", new=partial(action.execute, args_mock)):
        yield action


@pytest.mark.integration
def test_obs_expose(obs_expose_action, start_cmd_mock, stdout_mock, proc_mock, demo_config_path, console_msg_mock):
    proc_mock.poll.side_effect = [None, 0]
    stdout_mock.return_value = [b"some output"]

    obs_expose_action.execute()

    start_cmd_mock.assert_has_calls(
        [
            call(
                "minikube",
                "kubectl",
                "--profile",
                "dk-observability",
                "--",
                "--namespace",
                "datakitchen",
                "--address",
                "0.0.0.0",
                "port-forward",
                "service/observability-ui",
                "8501:http",
                raise_on_non_zero=False,
            ),
        ]
    )
    assert json.loads(demo_config_path.read_text()) == {
        "api_host": "http://host.docker.internal:8501/api",
        "api_key": "demo-api-key",
    }
    console_msg_mock.assert_has_calls(
        [
            call("      User Interface: http://localhost:8501"),
            call(" Event Ingestion API: http://localhost:8501/api/events/v1"),
            call("   Observability API: http://localhost:8501/api/observability/v1"),
            call(" Agent Heartbeat API: http://localhost:8501/api/agent/v1"),
        ],
        any_order=True,
    )


@pytest.mark.integration
def test_obs_expose_abort(obs_expose_action, start_cmd_mock):
    start_cmd_mock.__exit__.side_effect = CommandFailed

    with pytest.raises(AbortAction):
        obs_expose_action.execute()
