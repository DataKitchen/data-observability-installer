import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.installer import (
    AbortAction,
    INSTALL_MODE_DOCKER,
    INSTALL_MODE_PIP,
    InstallerError,
    TestgenStartAction,
    start_testgen_app,
    stop_app_tree,
    force_kill_app_tree,
    InstallMarker,
    TESTGEN_STOP_GRACE_PERIOD,
)


# The POSIX stop path signals a process group, which Windows has no equivalent of --
# ``supports_graceful_stop`` keeps the installer off that branch there entirely. These are
# skipped rather than shimmed: synthesizing ``os.killpg``/``signal.SIGKILL`` would also make
# a future capability-based ``supports_graceful_stop`` report True on Windows and quietly
# send the Windows tests down the wrong branch.
posix_only = pytest.mark.skipif(not hasattr(os, "killpg"), reason="needs POSIX process groups")


# --- start_testgen_app helper -------------------------------------------------


@pytest.fixture
def app_action(action, tmp_data_folder, tmp_path):
    """Bare Action with a real session_folder so file redirection works."""
    action.session_folder = tmp_path / "session"
    action.session_folder.mkdir()
    action.analytics = MagicMock()
    action.analytics.additional_properties = {}
    action.ctx = {}
    return action


@pytest.fixture
def proc_running_then_stops():
    """A Popen-like mock that pretends to be alive, then exits cleanly."""
    proc = MagicMock()
    proc.poll.return_value = None  # still alive while running
    proc.wait.return_value = 0
    return proc


@pytest.fixture
def empty_tg_config(monkeypatch):
    """Pretend ~/.testgen/config.env doesn't exist so port/SSL fall to defaults."""
    monkeypatch.setattr("tests.installer.read_testgen_config_env", dict)


@pytest.mark.unit
def test_start_testgen_app_happy_path(app_action, args_mock, proc_running_then_stops, empty_tg_config):
    args_mock.prod = "tg"

    with (
        patch("tests.installer.resolve_testgen_path", return_value="/bin/testgen"),
        patch("tests.installer.subprocess.Popen", return_value=proc_running_then_stops) as popen_mock,
        patch("tests.installer.wait_for_tcp_port", return_value=True) as port_mock,
        # The ``finally`` cleanup is not this test's subject, and on Windows it shells out
        # via ``subprocess.run`` -- which goes through the Popen patched right above and
        # would count as a second app launch.
        patch("tests.installer.stop_app_tree"),
    ):
        start_testgen_app(app_action, args_mock)

    popen_mock.assert_called_once()
    invocation = popen_mock.call_args
    assert invocation.args[0] == ["/bin/testgen", "run-app"]
    # Output is discarded (DEVNULL).
    assert "stdout" in invocation.kwargs
    port_mock.assert_called_once()
    proc_running_then_stops.wait.assert_called()


@pytest.mark.unit
def test_start_testgen_app_uses_port_from_config_env(app_action, args_mock, proc_running_then_stops, monkeypatch):
    """Port + SSL come from ~/.testgen/config.env (the source of truth post-setup)
    — not from args, which doesn't carry these flags on `tg start`."""
    args_mock.prod = "tg"
    monkeypatch.setattr(
        "tests.installer.read_testgen_config_env",
        lambda: {"TG_UI_PORT": "9000", "SSL_CERT_FILE": "/etc/cert", "SSL_KEY_FILE": "/etc/key"},
    )

    with (
        patch("tests.installer.resolve_testgen_path", return_value="/bin/testgen"),
        patch("tests.installer.subprocess.Popen", return_value=proc_running_then_stops),
        patch("tests.installer.wait_for_tcp_port", return_value=True) as port_mock,
    ):
        start_testgen_app(app_action, args_mock)

    # The port we wait for is what config.env says, not args defaults.
    port_mock.assert_called_once()
    assert port_mock.call_args.args[0] == 9000


@pytest.mark.unit
def test_start_testgen_app_aborts_on_port_timeout(app_action, args_mock, proc_running_then_stops, empty_tg_config):
    args_mock.prod = "tg"

    with (
        patch("tests.installer.resolve_testgen_path", return_value="/bin/testgen"),
        patch("tests.installer.subprocess.Popen", return_value=proc_running_then_stops),
        patch("tests.installer.wait_for_tcp_port", return_value=False),
        patch("tests.installer.stop_app_tree") as stop_mock,
        pytest.raises(InstallerError, match="did not start within"),
    ):
        start_testgen_app(app_action, args_mock)

    stop_mock.assert_called_with(proc_running_then_stops, timeout=5)


@pytest.mark.unit
def test_start_testgen_app_handles_keyboard_interrupt(app_action, args_mock, console_msg_mock, empty_tg_config):
    """User Ctrl+C during run is the expected stop signal — kill the whole
    process tree (postgres + parent), don't propagate the exception, and hint
    at the start command for next time."""
    args_mock.prod = "tg"

    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = [KeyboardInterrupt(), 0]

    with (
        patch("tests.installer.resolve_testgen_path", return_value="/bin/testgen"),
        patch("tests.installer.subprocess.Popen", return_value=proc),
        patch("tests.installer.wait_for_tcp_port", return_value=True),
        # Pinned: the grace-period messages below are POSIX-only behaviour, and the
        # Windows counterpart is test_start_testgen_app_makes_no_grace_promise_on_windows.
        patch("tests.installer.supports_graceful_stop", return_value=True),
        patch("tests.installer.stop_app_tree", return_value=True) as stop_mock,
    ):
        start_testgen_app(app_action, args_mock)

    # Called once for the keyboard-interrupt branch and again in the ``finally``
    # cleanup (timeout=5; no-op since proc already stopped). The first wait gets the
    # full grace period so a running job can reach a checkpoint before being killed.
    assert stop_mock.call_args_list[0].args[0] is proc
    assert stop_mock.call_args_list[0].kwargs == {"timeout": TESTGEN_STOP_GRACE_PERIOD}
    console_msg_mock.assert_any_msg_contains("reach a checkpoint")
    console_msg_mock.assert_any_msg_contains("TestGen stopped.")
    console_msg_mock.assert_any_msg_contains("tg start")


@pytest.mark.unit
def test_start_testgen_app_warns_when_grace_period_expires(app_action, args_mock, console_msg_mock, empty_tg_config):
    """A tree that had to be force-killed lost its checkpoint — say so rather than
    reporting a clean stop the user can't trust."""
    args_mock.prod = "tg"

    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = [KeyboardInterrupt(), 0]

    with (
        patch("tests.installer.resolve_testgen_path", return_value="/bin/testgen"),
        patch("tests.installer.subprocess.Popen", return_value=proc),
        patch("tests.installer.wait_for_tcp_port", return_value=True),
        # Pinned: Windows never promises a grace period, so it never warns one expired.
        patch("tests.installer.supports_graceful_stop", return_value=True),
        patch("tests.installer.stop_app_tree", return_value=False),
    ):
        start_testgen_app(app_action, args_mock)

    console_msg_mock.assert_any_msg_contains("will restart from the beginning")


@pytest.mark.unit
def test_start_testgen_app_makes_no_grace_promise_on_windows(app_action, args_mock, console_msg_mock, empty_tg_config):
    """Windows always force-kills, so promising a wait would be a lie and the
    force-kill warning would fire on every single stop, saying nothing."""
    args_mock.prod = "tg"

    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = [KeyboardInterrupt(), 0]

    with (
        patch("tests.installer.resolve_testgen_path", return_value="/bin/testgen"),
        patch("tests.installer.subprocess.Popen", return_value=proc),
        patch("tests.installer.wait_for_tcp_port", return_value=True),
        patch("tests.installer.supports_graceful_stop", return_value=False),
        patch("tests.installer.stop_app_tree", return_value=False),
    ):
        start_testgen_app(app_action, args_mock)

    printed = " ".join(str(c) for c in console_msg_mock.call_args_list)
    assert "reach a checkpoint" not in printed
    assert "will restart from the beginning" not in printed
    console_msg_mock.assert_any_msg_contains("TestGen stopped.")


# --- stop_app_tree ------------------------------------------------------------


@pytest.mark.unit
def test_stop_app_tree_no_op_when_proc_already_exited():
    """POSIX only. Windows deliberately does not take this shortcut: a console Ctrl+C
    kills the parent along with the installer, so a dead parent there says nothing about
    its children -- see test_stop_app_tree_windows_uses_taskkill_tree."""
    proc = MagicMock()
    proc.poll.return_value = 0  # already exited

    with (
        patch("tests.installer.supports_graceful_stop", return_value=True),
        patch("tests.installer.subprocess.run") as run_mock,
    ):
        stop_app_tree(proc)

    run_mock.assert_not_called()
    proc.wait.assert_not_called()


@pytest.mark.unit
def test_stop_app_tree_windows_uses_taskkill_tree():
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.wait.return_value = 0

    with (
        patch("tests.installer.platform.system", return_value="Windows"),
        patch("tests.installer.subprocess.run") as run_mock,
    ):
        stop_app_tree(proc, timeout=3)

    cmd = run_mock.call_args.args[0]
    assert cmd[:4] == ["taskkill", "/F", "/T", "/PID"]
    assert cmd[4] == "4242"
    proc.wait.assert_called_with(timeout=3)


@pytest.mark.unit
@posix_only
def test_stop_app_tree_posix_signals_process_group():
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.wait.return_value = 0

    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.os.killpg") as killpg_mock,
        patch("tests.installer.os.getpgid", return_value=4242),
    ):
        stop_app_tree(proc, timeout=3)

    killpg_mock.assert_called_once()
    assert killpg_mock.call_args.args[0] == 4242  # pgid
    proc.wait.assert_called_with(timeout=3)


@pytest.mark.unit
@posix_only
def test_stop_app_tree_falls_through_to_kill_on_timeout():
    """If SIGTERM doesn't take, escalate to SIGKILL / proc.kill()."""
    import subprocess as sp

    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.wait.side_effect = [sp.TimeoutExpired(cmd="x", timeout=1), 0]

    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.os.killpg"),
        patch("tests.installer.os.getpgid", return_value=4242),
    ):
        forced = stop_app_tree(proc, timeout=1)

    proc.kill.assert_called_once()
    assert forced is False  # had to be force-killed


@pytest.mark.unit
@posix_only
def test_stop_app_tree_reports_graceful_stop():
    """Exiting within the grace period is the signal the caller uses to decide
    whether a running job got to checkpoint."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.wait.return_value = 0

    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.os.killpg"),
        patch("tests.installer.os.getpgid", return_value=4242),
    ):
        assert stop_app_tree(proc, timeout=3) is True


@pytest.mark.unit
@posix_only
def test_stop_app_tree_swallows_second_interrupt():
    """A second Ctrl+C while we wait means 'stop now'. It must force-kill here rather
    than escaping to the caller's ``finally``, which would re-signal the tree — TestGen
    reads a second signal as 'hurry up' and cuts the job mid-pause."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.wait.side_effect = [KeyboardInterrupt(), 0]

    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.os.killpg") as killpg_mock,
        patch("tests.installer.os.getpgid", return_value=4242),
    ):
        forced = stop_app_tree(proc, timeout=90)

    assert forced is False
    proc.kill.assert_called_once()
    # SIGTERM first, then the escalation to SIGKILL.
    assert [c.args[1] for c in killpg_mock.call_args_list] == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.unit
def test_stop_app_tree_windows_stop_is_always_forced():
    """``taskkill /F`` gives the tree no chance to checkpoint, so the caller must not be
    told the stop was graceful."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.wait.return_value = 0

    with (
        patch("tests.installer.platform.system", return_value="Windows"),
        patch("tests.installer.subprocess.run"),
    ):
        assert stop_app_tree(proc, timeout=90) is False


@pytest.mark.unit
@posix_only
def test_force_kill_sweeps_orphans_outside_the_process_group():
    """``run-app all`` starts its ui/scheduler children in their own sessions, so killpg
    on the parent leaves them holding the port and the data dir — breaking the next
    ``tg start``. The sweep is what actually finishes them off."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242

    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.os.killpg"),
        patch("tests.installer.os.getpgid", return_value=4242),
        patch("tests.installer.stop_standalone_orphans") as sweep_mock,
    ):
        force_kill_app_tree(proc)

    sweep_mock.assert_called_once()


@pytest.mark.unit
@posix_only
def test_second_interrupt_still_sweeps_orphans():
    """The second-Ctrl+C path force-kills, so it owes the same cleanup."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.wait.side_effect = [KeyboardInterrupt(), 0]

    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.os.killpg"),
        patch("tests.installer.os.getpgid", return_value=4242),
        patch("tests.installer.stop_standalone_orphans") as sweep_mock,
    ):
        assert stop_app_tree(proc, timeout=90) is False

    sweep_mock.assert_called_once()


@pytest.mark.unit
@posix_only
def test_graceful_stop_does_not_sweep_orphans():
    """A tree that stopped on its own has nothing left behind — don't reach for pkill."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    proc.wait.return_value = 0

    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.os.killpg"),
        patch("tests.installer.os.getpgid", return_value=4242),
        patch("tests.installer.stop_standalone_orphans") as sweep_mock,
    ):
        assert stop_app_tree(proc, timeout=3) is True

    sweep_mock.assert_not_called()


@pytest.mark.unit
def test_force_kill_falls_back_when_taskkill_fails():
    """taskkill can be denied (elevated child). Without the proc.kill() backstop the
    installer would report a stop that never happened."""
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242

    with (
        patch("tests.installer.platform.system", return_value="Windows"),
        patch("tests.installer.subprocess.run", side_effect=OSError("Access denied")),
    ):
        force_kill_app_tree(proc, timeout=3)

    proc.kill.assert_called_once()


# --- TestgenStartAction -------------------------------------------------------


@pytest.fixture
def start_action(action_cls, args_mock, tmp_data_folder):
    action = TestgenStartAction()
    args_mock.prod = "tg"
    args_mock.action = "start"
    action.analytics = MagicMock()
    action.analytics.additional_properties = {}
    return action


@pytest.mark.integration
def test_start_action_aborts_with_no_install(start_action, args_mock, console_msg_mock):
    with pytest.raises(AbortAction):
        start_action._resolve_install_mode(args_mock)

    console_msg_mock.assert_any_msg_contains("No TestGen installation found")
    console_msg_mock.assert_any_msg_contains("tg install")


@pytest.mark.integration
def test_start_action_runs_compose_up_in_docker_mode(
    start_action, args_mock, tmp_data_folder, start_cmd_mock, compose_path
):
    InstallMarker(Path(tmp_data_folder), "tg").write(INSTALL_MODE_DOCKER)

    start_action._resolve_install_mode(args_mock)
    start_action.execute(args_mock)

    start_cmd_mock.assert_any_call(
        "docker",
        "compose",
        "-f",
        compose_path,
        "up",
        "--wait",
        raise_on_non_zero=True,
        env=None,
    )
    assert start_action.analytics.additional_properties["install_mode"] == INSTALL_MODE_DOCKER


@pytest.mark.integration
def test_start_action_routes_to_helper_in_pip_mode(start_action, args_mock, tmp_data_folder):
    InstallMarker(Path(tmp_data_folder), "tg").write(INSTALL_MODE_PIP)

    start_action._resolve_install_mode(args_mock)
    with patch("tests.installer.start_testgen_app") as start_helper:
        start_action.execute(args_mock)

    start_helper.assert_called_once_with(start_action, args_mock)
    assert start_action.analytics.additional_properties["install_mode"] == INSTALL_MODE_PIP


@pytest.fixture
def console_msg_mock():
    """Local override of the project-wide fixture so this file is self-contained."""
    from tests.installer import CONSOLE

    with patch.object(CONSOLE, "msg") as mock:

        def _assert_any_msg_contains(text: str):
            assert any(c for c in mock.call_args_list if text in c.args[0]), (
                f"The text '{text}' wasn't found in any of the {len(mock.call_args_list)} message(s) printed."
            )

        mock.assert_any_msg_contains = _assert_any_msg_contains
        yield mock


@pytest.mark.unit
def test_windows_sweeps_even_when_the_parent_is_already_gone():
    """A console Ctrl+C reaches everything in the console's process group, so the parent is
    usually dead before we are called -- while the UI, spawned into its own group, is not.
    Returning early on a dead parent is what left Streamlit holding port 8501."""
    proc = MagicMock()
    proc.poll.return_value = 0  # parent already exited
    proc.pid = 4242

    with (
        patch("tests.installer.platform.system", return_value="Windows"),
        patch("tests.installer.subprocess.run"),
        patch("tests.installer.stop_standalone_orphans") as sweep_mock,
    ):
        assert stop_app_tree(proc, timeout=90) is False

    sweep_mock.assert_called_once()


@pytest.mark.unit
def test_posix_still_trusts_a_dead_parent():
    """On POSIX the parent forwards the signal to its children before exiting, so a dead one
    really does mean a stopped tree -- no need to reach for pkill on every clean stop."""
    proc = MagicMock()
    proc.poll.return_value = 0
    proc.pid = 4242

    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.stop_standalone_orphans") as sweep_mock,
    ):
        assert stop_app_tree(proc, timeout=90) is True

    sweep_mock.assert_not_called()
