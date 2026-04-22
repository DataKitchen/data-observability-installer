import json
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from tests.installer import (
    AbortAction,
    INSTALL_MODE_DOCKER,
    INSTALL_MODE_PIP,
    TestgenUpgradeAction,
    write_install_marker,
)


@pytest.fixture
def pip_upgrade_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    """Drive the pip path of TestgenUpgradeAction end-to-end."""
    action = TestgenUpgradeAction()
    args_mock.prod = "tg"
    args_mock.action = "upgrade"
    write_install_marker(action.data_folder, args_mock.prod, INSTALL_MODE_PIP)
    action._resolved_mode = INSTALL_MODE_PIP
    action.steps = action.pip_steps
    with (
        patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"),
        patch(
            "tests.installer.resolve_testgen_path",
            return_value="/Users/test/.local/bin/testgen",
        ),
        patch.object(action, "execute", new=partial(action.execute, args_mock)),
    ):
        yield action


@pytest.mark.integration
def test_tg_pip_upgrade_happy_path(pip_upgrade_action, start_cmd_mock, stdout_mock, tmp_data_folder, console_msg_mock):
    # Step pipeline: pre_execute uv tool list → execute uv --version
    # (UvBootstrapStep records uv version) → uv tool upgrade →
    # testgen upgrade-system-version → on_action_success uv tool list.
    stdout_mock.side_effect = [
        ["dataops-testgen v5.10.0", "- testgen"],
        ["uv 0.11.7"],
        [],
        [],
        ["dataops-testgen v5.10.0", "- testgen"],
    ]

    pip_upgrade_action.execute()

    start_cmd_mock.assert_has_calls(
        [
            call(
                "/usr/local/bin/uv",
                "--no-cache",
                "tool",
                "upgrade",
                "dataops-testgen",
                raise_on_non_zero=True,
                env=None,
            ),
            call(
                "/Users/test/.local/bin/testgen",
                "upgrade-system-version",
                raise_on_non_zero=True,
                env=None,
            ),
        ],
        any_order=True,
    )

    console_msg_mock.assert_any_msg_contains("Current version: v5.10.0")
    console_msg_mock.assert_any_msg_contains("already up-to-date (v5.10.0)")

    marker_path = Path(tmp_data_folder) / "dk-tg-install.json"
    data = json.loads(marker_path.read_text())
    assert data["install_mode"] == INSTALL_MODE_PIP


@pytest.mark.integration
def test_tg_pip_upgrade_reports_version_change(pip_upgrade_action, start_cmd_mock, stdout_mock, console_msg_mock):
    stdout_mock.side_effect = [
        ["dataops-testgen v5.10.0", "- testgen"],
        ["uv 0.11.7"],
        [],
        [],
        ["dataops-testgen v5.10.1", "- testgen"],
    ]

    pip_upgrade_action.execute()

    console_msg_mock.assert_any_msg_contains("Current version: v5.10.0")
    console_msg_mock.assert_any_msg_contains("Updated to v5.10.1")


@pytest.mark.integration
def test_tg_pip_upgrade_marker_preserves_created_on(pip_upgrade_action, stdout_mock, tmp_data_folder):
    """Upgrading must not reset the original install's created_on timestamp."""
    marker_path = Path(tmp_data_folder) / "dk-tg-install.json"
    initial = json.loads(marker_path.read_text())
    original_created_on = initial["created_on"]
    # Force a different "now" via filesystem rewrite by burning a tick on disk.
    stdout_mock.side_effect = [
        ["dataops-testgen v5.10.0", "- testgen"],
        ["uv 0.11.7"],
        [],
        [],
        ["dataops-testgen v5.10.0", "- testgen"],
    ]

    pip_upgrade_action.execute()

    after = json.loads(marker_path.read_text())
    assert after["created_on"] == original_created_on
    assert "last_updated_on" in after


@pytest.fixture
def upgrade_action(action_cls, args_mock, tmp_data_folder):
    """A bare TestgenUpgradeAction for testing the mode-resolution layer."""
    action = TestgenUpgradeAction()
    args_mock.prod = "tg"
    args_mock.action = "upgrade"
    action.analytics = MagicMock()
    action.analytics.additional_properties = {}
    return action


@pytest.mark.integration
def test_upgrade_aborts_with_no_install(upgrade_action, args_mock, console_msg_mock):
    with pytest.raises(AbortAction):
        upgrade_action._resolve_install_mode(args_mock)

    console_msg_mock.assert_any_msg_contains("tg install")


@pytest.mark.integration
def test_upgrade_resolves_to_pip_when_marker_says_pip(upgrade_action, args_mock, tmp_data_folder):
    write_install_marker(Path(tmp_data_folder), "tg", INSTALL_MODE_PIP)

    upgrade_action._resolve_install_mode(args_mock)

    assert upgrade_action._resolved_mode == INSTALL_MODE_PIP
    assert upgrade_action.steps == TestgenUpgradeAction.pip_steps
    assert upgrade_action.analytics.additional_properties["install_mode"] == INSTALL_MODE_PIP


@pytest.mark.integration
def test_upgrade_resolves_to_docker_when_marker_says_docker(upgrade_action, args_mock, tmp_data_folder):
    write_install_marker(Path(tmp_data_folder), "tg", INSTALL_MODE_DOCKER)

    upgrade_action._resolve_install_mode(args_mock)

    assert upgrade_action._resolved_mode == INSTALL_MODE_DOCKER
    assert upgrade_action.steps == TestgenUpgradeAction.docker_steps
    assert upgrade_action.analytics.additional_properties["install_mode"] == INSTALL_MODE_DOCKER
