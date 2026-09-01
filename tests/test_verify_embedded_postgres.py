"""Coverage for the embedded-postgres smoke test.

When the bundled postgres cannot load a library it links against, initdb reports it as
"program postgres is needed by initdb but was not found in the same directory" -- naming
the one thing that is not wrong.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.installer import (
    AbortAction,
    CommandFailed,
    SkipStep,
    TestgenInstallAction,
    TestgenVerifyEmbeddedPostgresStep,
    find_embedded_postgres,
    TestgenStandaloneSetupStep,
)

WINDOWS_LAYOUT = "Lib/site-packages/pixeltable_pgserver/pginstall18/bin/postgres.exe"
POSIX_LAYOUT = "lib/python3.13/site-packages/pixeltable_pgserver/pginstall18/bin/postgres"


@pytest.fixture
def action(tmp_path):
    act = MagicMock()
    act.ctx = {"uv_path": "/usr/local/bin/uv"}
    act.data_folder = tmp_path
    act.run_cmd.return_value = str(tmp_path)
    return act


def place_binary(tmp_path, layout):
    target = tmp_path / "dataops-testgen" / layout
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n")
    return target


@pytest.mark.unit
@pytest.mark.parametrize("layout", (WINDOWS_LAYOUT, POSIX_LAYOUT))
def test_finds_the_bundled_binary_in_either_layout(action, tmp_path, layout):
    expected = place_binary(tmp_path, layout)
    assert find_embedded_postgres(action) == expected


@pytest.mark.unit
def test_returns_none_when_not_installed(action):
    assert find_embedded_postgres(action) is None


@pytest.mark.unit
def test_returns_none_when_uv_cannot_be_resolved(action):
    action.ctx = {}
    with patch("tests.installer.resolve_uv_path", return_value=None):
        assert find_embedded_postgres(action) is None


@pytest.mark.unit
def test_step_skips_when_binary_is_absent(action, args_mock):
    """Not finding it says nothing about whether it works -- the real step must still run."""
    with pytest.raises(SkipStep):
        TestgenVerifyEmbeddedPostgresStep().execute(action, args_mock)


@pytest.mark.unit
def test_step_passes_when_postgres_reports_its_version(action, tmp_path, args_mock):
    place_binary(tmp_path, POSIX_LAYOUT)
    TestgenVerifyEmbeddedPostgresStep().execute(action, args_mock)  # must not raise


@pytest.mark.unit
@pytest.mark.parametrize(
    "layout, ret_code",
    (
        # Windows reports STATUS_DLL_NOT_FOUND as an unsigned exit status; any other
        # non-zero exit means the same thing to the person installing.
        (WINDOWS_LAYOUT, 0xC0000135),
        (POSIX_LAYOUT, 1),
    ),
)
def test_step_recommends_docker_whatever_the_cause(action, tmp_path, args_mock, console_msg_mock, layout, ret_code):
    """The exit code is the diagnosis and belongs in the log. What the user needs is the
    way forward, so the console says the same thing however the binary failed."""
    place_binary(tmp_path, layout)
    action.run_cmd.side_effect = [str(tmp_path), CommandFailed(1, "postgres -V", ret_code)]
    action.args_cmd = "install"
    step = TestgenVerifyEmbeddedPostgresStep()

    with pytest.raises(AbortAction):
        step.execute(action, args_mock)
    step.on_action_fail(action, args_mock)

    console_msg_mock.assert_any_msg_contains("cannot run on this machine")
    console_msg_mock.assert_any_msg_contains("--docker")
    # No exit codes or library names in front of the user.
    printed = " ".join(str(c) for c in console_msg_mock.call_args_list)
    assert "0xC0000135" not in printed
    assert "libwinpthread" not in printed


@pytest.mark.unit
def test_upgrade_path_says_to_delete_first(action, tmp_path, args_mock, console_msg_mock):
    """`tg install` refuses while an install marker exists, so telling an upgrading user to
    just run it sends them into a refusal."""
    place_binary(tmp_path, POSIX_LAYOUT)
    action.run_cmd.side_effect = [str(tmp_path), CommandFailed(1, "postgres -V", 1)]
    action.args_cmd = "upgrade"
    step = TestgenVerifyEmbeddedPostgresStep()

    with pytest.raises(AbortAction):
        step.execute(action, args_mock)
    step.on_action_fail(action, args_mock)

    console_msg_mock.assert_any_msg_contains("delete")
    console_msg_mock.assert_any_msg_contains("--docker")


@pytest.mark.unit
def test_step_stays_quiet_when_it_did_not_fail(action, args_mock, console_msg_mock):
    """on_action_fail runs for every step when any step fails -- this one must not chime in
    about a database it never found fault with."""
    TestgenVerifyEmbeddedPostgresStep().on_action_fail(action, args_mock)
    assert not console_msg_mock.call_args_list


@pytest.mark.unit
def test_check_runs_before_anything_uses_the_database():
    """The whole point is to fail before standalone-setup spends time on a broken install."""
    steps = TestgenInstallAction.pip_steps
    assert steps.index(TestgenVerifyEmbeddedPostgresStep) < steps.index(TestgenStandaloneSetupStep)


@pytest.mark.unit
def test_upgrade_checks_too():
    """`uv tool upgrade` replaces the package, so it can pull a broken wheel just as an
    install can -- and the failure would surface the same misleading way."""
    from tests.installer import TestgenStandaloneUpgradeStep, TestgenUpgradeAction

    steps = TestgenUpgradeAction.pip_steps
    assert steps.index(TestgenVerifyEmbeddedPostgresStep) < steps.index(TestgenStandaloneUpgradeStep)
