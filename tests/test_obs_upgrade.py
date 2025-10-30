from functools import partial
from itertools import count
from unittest.mock import call, patch

import pytest

from tests.installer import ObsUpgradeAction


@pytest.fixture
def obs_upgrade_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = ObsUpgradeAction()
    args_mock.prod = "obs"
    args_mock.action = "upgrade"
    with (
        patch.object(action, "execute", new=partial(action.execute, args_mock)),
        patch("platform.system", return_value="Linux"),
    ):
        yield action


@pytest.mark.integration
def test_obs_upgrade(obs_upgrade_action, start_cmd_mock, tmp_data_folder, stdout_mock, compose_path):
    def _stdout_side_effect():
        for idx in count():
            if idx == 0:
                yield ["{}"]
            else:
                yield []

    stdout_mock.side_effect = iter(_stdout_side_effect())
    obs_upgrade_action.execute()

    compose_call = partial(call, "docker", "compose", "-f", compose_path)
    start_cmd_mock.assert_has_calls(
        [
            compose_call(
                "exec",
                "-it",
                "observability_backend",
                "/usr/local/bin/pip",
                "list",
                "--format=json",
                raise_on_non_zero=True,
                env=None,
            ),
            compose_call("pull", "--policy", "always"),
            compose_call("up", "--wait", raise_on_non_zero=True, env=None),
            compose_call(
                "exec",
                "-it",
                "observability_backend",
                "/usr/local/bin/pip",
                "list",
                "--format=json",
                raise_on_non_zero=True,
                env=None,
            ),
        ],
        any_order=True,
    )
