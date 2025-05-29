from functools import partial
from unittest.mock import call, patch

import pytest

from tests.installer import ObsDeleteAction


@pytest.fixture
def obs_delete_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = ObsDeleteAction()
    args_mock.prod = "obs"
    args_mock.action = "delete"
    with patch.object(action, "execute", new=partial(action.execute, args_mock)):
        yield action


@pytest.mark.integration
def test_obs_delete(obs_delete_action, start_cmd_mock):
    obs_delete_action.execute()

    def_call = partial(call, raise_on_non_zero=True, env=None)

    start_cmd_mock.assert_has_calls(
        [
            def_call("minikube", "-p", "dk-observability", "delete"),
            def_call("docker", "network", "rm", "datakitchen-network"),
        ],
        any_order=True,
    )
