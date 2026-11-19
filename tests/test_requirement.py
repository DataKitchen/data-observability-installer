from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from tests.installer import (
    DOCKER_ENGINE_POLL_TIMEOUT,
    REQ_DOCKER,
    REQ_DOCKER_DAEMON,
    REQ_TESTGEN_IMAGE,
    AbortAction,
    Action,
    CommandFailed,
    Requirement,
    print_access_info,
)


def make_action(failing_cmds):
    """An action whose run_cmd_retries fails for any command whose joined form
    contains one of *failing_cmds*."""
    action = MagicMock()

    def run(*cmd, **kwargs):
        joined = " ".join(str(c) for c in cmd)
        if any(bad in joined for bad in failing_cmds):
            raise CommandFailed()

    action.run_cmd_retries.side_effect = run
    return action


@pytest.mark.unit
def test_requirement_passes_on_primary_without_running_alt():
    req = Requirement("K", ("true", "primary"), ("nope",), alt_cmd=("true", "fallback"))
    action = make_action([])

    assert req.check_availability(action, Namespace()) is True
    # The fallback costs a round trip, so it must not run when the primary already passed.
    assert "fallback" not in str(action.run_cmd_retries.call_args_list)


@pytest.mark.unit
def test_requirement_falls_through_to_alt_cmd():
    req = Requirement("K", ("true", "primary"), ("nope",), alt_cmd=("true", "fallback"))

    assert req.check_availability(make_action(["primary"]), Namespace()) is True


@pytest.mark.unit
def test_requirement_fails_when_neither_command_works(console_msg_mock):
    req = Requirement("K", ("true", "primary"), ("it broke",), alt_cmd=("true", "fallback"))

    assert req.check_availability(make_action(["primary", "fallback"]), Namespace()) is False
    console_msg_mock.assert_any_msg_contains("it broke")


@pytest.mark.unit
def test_requirement_without_alt_cmd_still_fails_cleanly(console_msg_mock):
    """The alt_cmd default must not change how single-command requirements behave."""
    req = Requirement("K", ("true", "primary"), ("it broke",))

    assert req.check_availability(make_action(["primary"]), Namespace()) is False
    console_msg_mock.assert_any_msg_contains("it broke")


@pytest.mark.unit
@pytest.mark.parametrize(
    "available, expected",
    (
        ("local", True),  # built locally, absent from any registry
        ("registry", True),  # normal install: pullable, not yet local
        ("neither", False),  # a typo'd name or an unreachable registry
    ),
)
def test_testgen_image_accepts_local_or_registry(available, expected):
    """A locally built image needs no pull, so it satisfies the check. A name that is
    neither local nor pullable still fails -- that is the signal the check exists for."""
    failing = {
        "local": ["manifest inspect"],
        "registry": ["image inspect"],
        "neither": ["manifest inspect", "image inspect"],
    }[available]

    action = make_action(failing)
    args = Namespace(image="dataops-testgen-local:dev")

    assert REQ_TESTGEN_IMAGE.check_availability(action, args, quiet=True) is expected


@pytest.fixture
def docker_action():
    class DockerAction(Action):
        args_cmd = "test"
        requirements = [REQ_DOCKER, REQ_DOCKER_DAEMON]

    action = DockerAction()
    action.analytics = MagicMock()
    action.analytics.additional_properties = {}
    return action


@pytest.mark.unit
def test_waits_for_the_docker_engine_once_the_user_starts_it(docker_action):
    with (
        patch.object(REQ_DOCKER, "check_availability", return_value=True),
        patch.object(REQ_DOCKER_DAEMON, "check_availability", side_effect=[False, False, True]),
        patch("tests.installer.sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="") as input_mock,
        patch("tests.installer.time.sleep"),
    ):
        docker_action.check_requirements(Namespace())

    input_mock.assert_called_once()


@pytest.mark.unit
def test_does_not_wait_for_the_docker_engine_without_a_terminal(docker_action):
    with (
        patch.object(REQ_DOCKER, "check_availability", return_value=True),
        patch.object(REQ_DOCKER_DAEMON, "check_availability", return_value=False),
        patch("tests.installer.sys.stdin.isatty", return_value=False),
        patch("builtins.input") as input_mock,
        pytest.raises(AbortAction),
    ):
        docker_action.check_requirements(Namespace())

    input_mock.assert_not_called()


@pytest.mark.unit
def test_gives_up_waiting_for_the_docker_engine(docker_action):
    with (
        patch.object(REQ_DOCKER, "check_availability", return_value=True),
        patch.object(REQ_DOCKER_DAEMON, "check_availability", return_value=False),
        patch("tests.installer.sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value=""),
        patch("tests.installer.time.sleep"),
        patch("tests.installer.time.time", side_effect=[0, 60, 121]),
        pytest.raises(AbortAction),
    ):
        docker_action.check_requirements(Namespace())

    assert docker_action.analytics.additional_properties["docker_engine_wait"] == "timed_out"


@pytest.mark.unit
def test_access_info_keeps_the_password_out_of_the_logs(tmp_path):
    tmp_path.joinpath("dk-tg-credentials.txt").write_text("Username: admin\nPassword: s3cret\n")

    with patch("tests.installer.CONSOLE.msg") as msg_mock:
        assert print_access_info(tmp_path, Namespace(prod="tg")) is True

    password_call = next(c for c in msg_mock.call_args_list if "s3cret" in c.args[0])
    assert password_call.kwargs["skip_logging"] is True


@pytest.mark.unit
def test_polls_the_docker_engine_with_a_short_timeout(docker_action):
    with (
        patch.object(REQ_DOCKER, "check_availability", return_value=True),
        patch.object(REQ_DOCKER_DAEMON, "check_availability", side_effect=[False, True]) as daemon_mock,
        patch("tests.installer.sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value=""),
    ):
        docker_action.check_requirements(Namespace())

    assert daemon_mock.call_args.kwargs["timeout"] == DOCKER_ENGINE_POLL_TIMEOUT
    assert docker_action.analytics.additional_properties["docker_engine_wait"] == "started"


@pytest.mark.unit
@pytest.mark.parametrize("interrupt", (KeyboardInterrupt, EOFError))
def test_interrupting_the_docker_engine_prompt(docker_action, interrupt):
    with (
        patch.object(REQ_DOCKER, "check_availability", return_value=True),
        patch.object(REQ_DOCKER_DAEMON, "check_availability", return_value=False),
        patch("tests.installer.sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=interrupt),
        pytest.raises(AbortAction),
    ):
        docker_action.check_requirements(Namespace())

    assert docker_action.analytics.additional_properties["docker_engine_wait"] == "interrupted"


@pytest.mark.unit
def test_interrupting_the_docker_engine_wait(docker_action):
    with (
        patch.object(REQ_DOCKER, "check_availability", return_value=True),
        patch.object(REQ_DOCKER_DAEMON, "check_availability", side_effect=[False, KeyboardInterrupt]),
        patch("tests.installer.sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value=""),
        pytest.raises(AbortAction),
    ):
        docker_action.check_requirements(Namespace())

    assert docker_action.analytics.additional_properties["docker_engine_wait"] == "interrupted"


@pytest.mark.unit
def test_does_not_wait_for_the_docker_engine_when_docker_is_missing(docker_action):
    with (
        patch.object(REQ_DOCKER, "check_availability", return_value=False),
        patch.object(REQ_DOCKER_DAEMON, "check_availability", return_value=False),
        patch("tests.installer.sys.stdin.isatty", return_value=True),
        patch("builtins.input") as input_mock,
        pytest.raises(AbortAction),
    ):
        docker_action.check_requirements(Namespace())

    input_mock.assert_not_called()
    assert docker_action.analytics.additional_properties["missing_requirements"] == [
        REQ_DOCKER.key,
        REQ_DOCKER_DAEMON.key,
    ]
