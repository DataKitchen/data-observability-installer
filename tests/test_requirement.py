from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from tests.installer import REQ_TESTGEN_IMAGE, CommandFailed, Requirement


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
