from functools import partial
from unittest.mock import call, patch

import pytest

from tests.installer import TestgenDeleteAction, AbortAction, CommandFailed


@pytest.fixture
def tg_delete_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = TestgenDeleteAction()
    args_mock.prod = "tg"
    args_mock.action = "delete"
    with patch.object(action, "execute", new=partial(action.execute, args_mock)):
        yield action


@pytest.mark.integration
@pytest.mark.parametrize("fail_network", (False, True))
def test_tg_delete(fail_network, tg_delete_action, start_cmd_mock, stdout_mock):
    stdout_mock.side_effect = [
        [],
        [b'{"Labels":"com.docker.compose.project=testgen,", "Status":"N/A", "Name": "postgresql"}'],
        [],
    ]
    start_cmd_mock.__exit__.side_effect = [CommandFailed if fail_network else None, None, None]

    tg_delete_action.execute()

    kwargs = dict(raise_on_non_zero=True, env=None)
    start_cmd_mock.assert_has_calls(
        [
            call("docker", "network", "rm", "datakitchen-network", **kwargs),
            call("docker", "volume", "list", "--format=json", **kwargs),
            call("docker", "volume", "rm", "postgresql", **kwargs),
        ],
        any_order=True,
    )


@pytest.mark.integration
@pytest.mark.parametrize("keep_images, expected_down_args", ((False, ["--rmi", "all"]), (True, [])))
@pytest.mark.parametrize("keep_config", (False, True))
@pytest.mark.parametrize("fail_network", (False, True))
def test_tg_delete_compose(
    fail_network,
    keep_config,
    keep_images,
    expected_down_args,
    tg_delete_action,
    start_cmd_mock,
    stdout_mock,
    args_mock,
    compose_path,
):
    args_mock.keep_config = keep_config
    args_mock.keep_images = keep_images
    compose_path.touch()
    start_cmd_mock.__exit__.side_effect = [None, CommandFailed if fail_network else None]

    tg_delete_action.execute()

    kwargs = dict(raise_on_non_zero=True, env=None)
    start_cmd_mock.assert_has_calls(
        [
            call("docker", "compose", "-f", compose_path, "down", *expected_down_args, "--volumes", **kwargs),
            call("docker", "network", "rm", "datakitchen-network", **kwargs),
        ],
        any_order=True,
    )

    assert compose_path.exists() is keep_config


@pytest.mark.integration
def test_tg_delete_abort(tg_delete_action, start_cmd_mock, compose_path, stdout_mock, console_msg_mock):
    stdout_mock.side_effect = [
        [],
        [b'{"Labels":"com.docker.compose.project=testgen,", "Status":"N/A", "Name": "postgresql"}'],
        [],
    ]
    start_cmd_mock.__exit__.side_effect = [None, None, CommandFailed]
    with pytest.raises(AbortAction):
        tg_delete_action.execute()

    console_msg_mock.assert_any_msg_contains("Could NOT delete docker volumes. Please delete them manually")


@pytest.mark.integration
def test_tg_delete_compose_abort(tg_delete_action, start_cmd_mock, compose_path, console_msg_mock):
    compose_path.touch()
    start_cmd_mock.__exit__.side_effect = [CommandFailed, None]
    with pytest.raises(AbortAction):
        tg_delete_action.execute()

    console_msg_mock.assert_any_msg_contains("Could NOT delete the Docker resources")
