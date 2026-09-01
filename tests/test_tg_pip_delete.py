from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.installer import (
    INSTALL_MARKER_FILE,
    INSTALL_MODE_DOCKER,
    INSTALL_MODE_PIP,
    TestgenDeleteAction,
    InstallMarker,
)


@pytest.fixture
def pip_delete_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock, tmp_path):
    """Drive the pip path of TestgenDeleteAction directly via _delete_pip."""
    action = TestgenDeleteAction()
    args_mock.prod = "tg"
    args_mock.action = "delete"
    InstallMarker(action.data_folder, args_mock.prod).write(INSTALL_MODE_PIP)
    # Bypass check_requirements: pre-resolve mode so execute() runs the
    # delete branch directly.
    action._resolved_mode = INSTALL_MODE_PIP
    # Pin Path.home() to tmp_path so .streamlit/ cleanup doesn't touch the
    # real home directory of whoever runs the suite.
    with (
        patch.object(action, "execute", new=partial(action.execute, args_mock)),
        patch("tests.installer.pathlib.Path.home", return_value=tmp_path),
    ):
        yield action


@pytest.mark.integration
def test_pip_delete_removes_uv_tool_and_home(pip_delete_action, start_cmd_mock, tmp_data_folder, tmp_path):
    fake_tg_home = tmp_path / ".testgen"
    fake_tg_home.mkdir()
    (fake_tg_home / "config.env").write_text("TESTGEN_USERNAME=admin\n")

    # Streamlit config dir (if any) must be left alone — user may have other
    # Streamlit projects on this machine.
    fake_streamlit_dir = tmp_path / ".streamlit"
    fake_streamlit_dir.mkdir()
    (fake_streamlit_dir / "credentials.toml").write_text('[general]\nemail = ""\n')

    (Path(tmp_data_folder) / "dk-tg-credentials.txt").write_text("Username: admin\n")

    with (
        patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"),
        patch.dict("tests.installer.os.environ", {"TG_TESTGEN_HOME": str(fake_tg_home)}),
    ):
        pip_delete_action.execute()

    start_cmd_mock.assert_any_call(
        "/usr/local/bin/uv",
        "tool",
        "uninstall",
        "dataops-testgen",
        raise_on_non_zero=True,
        env=None,
    )
    assert not fake_tg_home.exists()
    assert fake_streamlit_dir.exists()
    assert (fake_streamlit_dir / "credentials.toml").exists()
    assert not (Path(tmp_data_folder) / "dk-tg-credentials.txt").exists()
    # Marker is removed too — a subsequent install should start clean.
    assert not (Path(tmp_data_folder) / INSTALL_MARKER_FILE.format("tg")).exists()


@pytest.mark.integration
def test_pip_delete_removes_installer_local_uv(pip_delete_action, start_cmd_mock, tmp_data_folder, tmp_path):
    local_bin = Path(tmp_data_folder) / "bin"
    local_bin.mkdir()
    local_uv = local_bin / "uv"
    local_uv.write_bytes(b"#!/bin/sh\n")

    fake_tg_home = tmp_path / ".testgen"
    fake_tg_home.mkdir()

    with (
        patch("tests.installer.shutil.which", return_value=None),
        patch.dict("tests.installer.os.environ", {"TG_TESTGEN_HOME": str(fake_tg_home)}),
    ):
        pip_delete_action.execute()

    assert not local_uv.exists()
    assert not local_bin.exists()


@pytest.mark.integration
def test_pip_delete_leaves_path_uv_alone(pip_delete_action, start_cmd_mock, tmp_data_folder, tmp_path):
    fake_tg_home = tmp_path / ".testgen"
    fake_tg_home.mkdir()

    with (
        patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"),
        patch.dict("tests.installer.os.environ", {"TG_TESTGEN_HOME": str(fake_tg_home)}),
    ):
        pip_delete_action.execute()

    assert not (Path(tmp_data_folder) / "bin").exists()


@pytest.mark.integration
def test_pip_delete_respects_keep_data(pip_delete_action, start_cmd_mock, tmp_data_folder, args_mock, tmp_path):
    args_mock.keep_data = True
    fake_tg_home = tmp_path / ".testgen"
    fake_tg_home.mkdir()
    (fake_tg_home / "config.env").write_text("TESTGEN_USERNAME=admin\n")

    with (
        patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"),
        patch.dict("tests.installer.os.environ", {"TG_TESTGEN_HOME": str(fake_tg_home)}),
    ):
        pip_delete_action.execute()

    assert fake_tg_home.exists()
    assert (fake_tg_home / "config.env").exists()


@pytest.mark.integration
def test_pip_delete_handles_missing_uv(pip_delete_action, start_cmd_mock, tmp_data_folder, tmp_path, console_msg_mock):
    fake_tg_home = tmp_path / ".testgen"
    fake_tg_home.mkdir()

    with (
        patch("tests.installer.shutil.which", return_value=None),
        patch.dict("tests.installer.os.environ", {"TG_TESTGEN_HOME": str(fake_tg_home)}),
    ):
        pip_delete_action.execute()

    for invocation in start_cmd_mock.call_args_list:
        assert "tool" not in invocation.args, f"Unexpected uv invocation: {invocation}"
    console_msg_mock.assert_any_msg_contains("uv not found")
    assert not fake_tg_home.exists()


@pytest.fixture
def delete_action(action_cls, args_mock, tmp_data_folder):
    """A bare TestgenDeleteAction for testing the marker-driven dispatch layer."""
    action = TestgenDeleteAction()
    args_mock.prod = "tg"
    args_mock.action = "delete"
    action.analytics = MagicMock()
    action.analytics.additional_properties = {}
    return action


@pytest.mark.integration
def test_delete_nothing_to_delete(delete_action, args_mock, console_msg_mock):
    delete_action._resolve_install_mode(args_mock)
    delete_action.execute(args_mock)
    console_msg_mock.assert_any_msg_contains("Nothing to delete")
    # No analytics property recorded since there was no install.
    assert "install_mode" not in delete_action.analytics.additional_properties


@pytest.mark.integration
def test_delete_routes_to_pip_and_removes_marker(delete_action, args_mock, tmp_data_folder, tmp_path):
    InstallMarker(Path(tmp_data_folder), "tg").write(INSTALL_MODE_PIP)
    assert (Path(tmp_data_folder) / INSTALL_MARKER_FILE.format("tg")).exists()

    delete_action._resolve_install_mode(args_mock)
    with (
        patch.object(delete_action, "_delete_pip") as pip_branch,
        patch("tests.installer.pathlib.Path.home", return_value=tmp_path),
    ):
        delete_action.execute(args_mock)

    pip_branch.assert_called_once_with(args_mock)
    assert not (Path(tmp_data_folder) / INSTALL_MARKER_FILE.format("tg")).exists()
    assert delete_action.analytics.additional_properties["install_mode"] == INSTALL_MODE_PIP


@pytest.mark.integration
def test_delete_routes_to_docker_legacy(delete_action, args_mock, tmp_data_folder):
    # No marker but legacy Docker files present — read_install_mode falls back
    # to Docker, and the unified action takes the docker branch.
    (Path(tmp_data_folder) / args_mock.compose_file_name).write_text("version: '3'")
    (Path(tmp_data_folder) / "dk-tg-credentials.txt").write_text("admin\n")

    delete_action._resolve_install_mode(args_mock)
    with patch.object(delete_action, "_delete_docker") as docker_branch:
        delete_action.execute(args_mock)

    docker_branch.assert_called_once_with(args_mock)
    assert delete_action.analytics.additional_properties["install_mode"] == INSTALL_MODE_DOCKER


@pytest.mark.integration
def test_pip_delete_reports_what_survived(pip_delete_action, start_cmd_mock, stdout_mock, tmp_path, console_msg_mock):
    """A process we failed to stop can hold a file in the tool environment open, making
    `uv tool uninstall` a no-op. Reporting success then sends the user to a reinstall
    that cannot work either -- so check the disk rather than trusting the exit code."""
    tool_dir, bin_dir = tmp_path / "tools", tmp_path / "bin"
    tool_dir.mkdir()
    bin_dir.mkdir()
    (tool_dir / "dataops-testgen").mkdir()  # survived the uninstall
    (bin_dir / "testgen").write_text("#!/bin/sh\n")  # so did the entry point
    # uv tool uninstall, then `uv tool dir`, then `uv tool dir --bin`
    stdout_mock.side_effect = [[], [str(tool_dir)], [str(bin_dir)]]

    with patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"):
        pip_delete_action.execute()

    console_msg_mock.assert_any_msg_contains("only partly uninstalled")
    console_msg_mock.assert_any_msg_contains("still running and held a file open")
    printed = " ".join(str(c) for c in console_msg_mock.call_args_list)
    assert "dataops-testgen" in printed
    assert "TestGen uninstalled." not in printed


@pytest.mark.integration
def test_pip_delete_reports_success_when_nothing_survives(
    pip_delete_action, start_cmd_mock, stdout_mock, tmp_path, console_msg_mock
):
    tool_dir, bin_dir = tmp_path / "tools", tmp_path / "bin"
    tool_dir.mkdir()
    bin_dir.mkdir()
    # uv tool uninstall, then `uv tool dir`, then `uv tool dir --bin`
    stdout_mock.side_effect = [[], [str(tool_dir)], [str(bin_dir)]]

    with patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"):
        pip_delete_action.execute()

    console_msg_mock.assert_any_msg_contains("TestGen uninstalled.")
    printed = " ".join(str(c) for c in console_msg_mock.call_args_list)
    assert "only partly uninstalled" not in printed


@pytest.mark.integration
def test_pip_delete_keeps_the_marker_when_incomplete(
    pip_delete_action, start_cmd_mock, stdout_mock, tmp_data_folder, tmp_path, console_msg_mock
):
    """The retry we recommend has to be able to do something. Dropping the marker makes the
    next `tg delete` report "nothing to delete", and removing the installer-local uv leaves
    it with nothing to uninstall with."""
    tool_dir, bin_dir = tmp_path / "tools", tmp_path / "bin"
    tool_dir.mkdir()
    bin_dir.mkdir()
    (tool_dir / "dataops-testgen").mkdir()
    local_uv = Path(tmp_data_folder) / "bin" / "uv"
    local_uv.parent.mkdir(parents=True, exist_ok=True)
    local_uv.write_text("#!/bin/sh\n")
    stdout_mock.side_effect = [[], [str(tool_dir)], [str(bin_dir)]]

    with patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"):
        pip_delete_action.execute()

    console_msg_mock.assert_any_msg_contains("only partly uninstalled")
    assert (Path(tmp_data_folder) / INSTALL_MARKER_FILE.format("tg")).exists(), "marker was dropped"
    assert local_uv.exists(), "installer-local uv was removed, so the retry has no uv"


@pytest.mark.integration
def test_pip_delete_ignores_a_foreign_shim(
    pip_delete_action, start_cmd_mock, stdout_mock, tmp_data_folder, tmp_path, console_msg_mock
):
    """uv's bin directory is shared (commonly ~/.local/bin). A `testgen` there when the tool
    environment is gone is somebody else's -- reporting it would call a clean uninstall a
    failure, and would then strand the user via the kept marker."""
    tool_dir, bin_dir = tmp_path / "tools", tmp_path / "bin"
    tool_dir.mkdir()
    bin_dir.mkdir()
    (bin_dir / "testgen").write_text("#!/bin/sh\n")  # not ours
    stdout_mock.side_effect = [[], [str(tool_dir)], [str(bin_dir)]]

    with patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"):
        pip_delete_action.execute()

    console_msg_mock.assert_any_msg_contains("TestGen uninstalled.")
    assert not (Path(tmp_data_folder) / INSTALL_MARKER_FILE.format("tg")).exists()


@pytest.mark.integration
def test_pip_delete_survives_a_broken_uv(pip_delete_action, start_cmd_mock, stdout_mock, tmp_path, tmp_data_folder):
    """The leftover check runs before the rest of the cleanup, so a uv that cannot be spawned
    must not abort the delete and leave more behind than before."""
    fake_tg_home = tmp_path / ".testgen"
    fake_tg_home.mkdir()
    (fake_tg_home / "config.env").write_text("x=1\n")
    start_cmd_mock.side_effect = PermissionError("blocked by security software")

    with (
        patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"),
        patch.dict("tests.installer.os.environ", {"TG_TESTGEN_HOME": str(fake_tg_home)}),
    ):
        pip_delete_action.execute()

    assert not fake_tg_home.exists(), "delete stopped early and left the data directory"
