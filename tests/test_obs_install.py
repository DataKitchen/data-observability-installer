from functools import partial
from itertools import count
from pathlib import Path
from unittest.mock import call, patch

import pytest

from tests.installer import ObsInstallAction, AbortAction, ComposeVerifyExistingInstallStep


@pytest.fixture
def obs_install_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = ObsInstallAction()
    args_mock.prod = "obs"
    args_mock.action = "install"
    with (
        patch.object(action, "execute", new=partial(action.execute, args_mock)),
        patch("platform.system", return_value="Linux"),
    ):
        yield action


@pytest.mark.integration
def test_obs_install(obs_install_action, start_cmd_mock, tmp_data_folder, stdout_mock, compose_path):
    def _stdout_side_effect():
        for idx in count():
            if idx == 0:
                yield ["{}"]
            elif idx == 5:
                yield ['{"service_account_key": "demo-account-key", "project_id": "test-project-id"}']
            else:
                yield []

    stdout_mock.side_effect = iter(_stdout_side_effect())
    obs_install_action.execute()

    def_call = partial(call, raise_on_non_zero=True, env=None)
    start_cmd_mock.assert_has_calls(
        [
            def_call("docker", "compose", "ls", "--format=json"),
            def_call("docker", "network", "inspect", "datakitchen-network"),
            call("docker", "compose", "-f", compose_path, "pull", "--policy", "always"),
            def_call("docker", "compose", "-f", compose_path, "up", "--wait"),
            def_call(
                "docker",
                "compose",
                "-f",
                compose_path,
                "exec",
                "-it",
                "observability_backend",
                "/dk/bin/cli",
                "init",
                "--demo",
                "--topics",
                "--json",
            ),
        ],
        any_order=True,
    )

    assert Path(tmp_data_folder).joinpath("dk-obs-credentials.txt").stat().st_size > 0
    assert Path(tmp_data_folder).joinpath("demo-config.json").stat().st_size > 0


@pytest.mark.integration
def test_obs_existing_install_abort(obs_install_action, compose_path, stdout_mock):
    stdout_mock.side_effect = [
        [f'[{{"Name":"test-project","Status":"running(4)","ConfigFiles":"{compose_path}"}}]'],
        [],
    ]
    with patch.object(obs_install_action, "steps", new=[ComposeVerifyExistingInstallStep]):
        with pytest.raises(AbortAction):
            obs_install_action.execute()
