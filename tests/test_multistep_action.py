from itertools import count
from unittest.mock import Mock, call, ANY, patch

import pytest

from tests.installer import (
    AbortAction,
    AbortReason,
    CommandFailed,
    InstallerError,
    MultiStepAction,
    NothingToDo,
    SkipStep,
    Step,
)


@pytest.fixture(autouse=True)
def analytics():
    with patch.object(MultiStepAction, "analytics", create=True) as mock:
        mock.additional_properties = {}
        yield mock


@pytest.fixture
def step_mock():
    yield Mock()


@pytest.fixture
def step_factory(step_mock):
    idx = count()

    def _create_mock(**kwargs):
        attrs = {}
        step_idx = next(idx)
        for step_attr in ("pre_execute", "execute", "on_action_success", "on_action_fail"):
            mock = Mock(side_effect=kwargs.pop(step_attr, None))
            step_mock.attach_mock(mock, f"step_{step_idx}_{step_attr}")
            attrs[step_attr] = mock

        attrs.update(kwargs)

        return type(f"TestStep{step_idx}", (Step,), attrs)

    return _create_mock


@pytest.mark.unit
@pytest.mark.parametrize(
    "step_1_args",
    ({}, {"execute": ValueError, "required": False}, {"execute": SkipStep}),
    ids=("regular execution", "step skipped", "non required step fails"),
)
def test_execute(step_1_args, step_mock, step_factory, args_mock):
    class TestMSAction(MultiStepAction):
        steps = (
            step_factory(),
            step_factory(**step_1_args),
            step_factory(),
        )

    action = TestMSAction()
    action.execute(args_mock)

    step_mock.assert_has_calls(
        [
            call.step_0_pre_execute(ANY, args_mock),
            call.step_1_pre_execute(ANY, args_mock),
            call.step_2_pre_execute(ANY, args_mock),
            call.step_0_execute(ANY, args_mock),
            call.step_1_execute(ANY, args_mock),
            call.step_2_execute(ANY, args_mock),
            call.step_2_on_action_success(ANY, args_mock),
            call.step_1_on_action_success(ANY, args_mock),
            call.step_0_on_action_success(ANY, args_mock),
        ]
    )


@pytest.mark.unit
def test_execute_not_required(step_mock, step_factory, args_mock):
    class TestMSAction(MultiStepAction):
        steps = (
            step_factory(),
            step_factory(),
            step_factory(),
        )

    action = TestMSAction()
    action.execute(args_mock)

    step_mock.assert_has_calls(
        [
            call.step_0_pre_execute(ANY, args_mock),
            call.step_1_pre_execute(ANY, args_mock),
            call.step_2_pre_execute(ANY, args_mock),
            call.step_0_execute(ANY, args_mock),
            call.step_1_execute(ANY, args_mock),
            call.step_2_execute(ANY, args_mock),
            call.step_2_on_action_success(ANY, args_mock),
            call.step_1_on_action_success(ANY, args_mock),
            call.step_0_on_action_success(ANY, args_mock),
        ]
    )


@pytest.mark.unit
def test_execute_post_hook_fail(step_mock, step_factory, args_mock):
    class TestMSAction(MultiStepAction):
        steps = (
            step_factory(on_action_success=RuntimeError),
            step_factory(execute=SkipStep, on_action_success=ValueError),
            step_factory(on_action_success=InstallerError),
        )

    action = TestMSAction()
    action.execute(args_mock)

    step_mock.assert_has_calls(
        [
            call.step_0_pre_execute(ANY, args_mock),
            call.step_1_pre_execute(ANY, args_mock),
            call.step_2_pre_execute(ANY, args_mock),
            call.step_0_execute(ANY, args_mock),
            call.step_1_execute(ANY, args_mock),
            call.step_2_execute(ANY, args_mock),
            call.step_2_on_action_success(ANY, args_mock),
            call.step_1_on_action_success(ANY, args_mock),
            call.step_0_on_action_success(ANY, args_mock),
        ]
    )


@pytest.mark.unit
@pytest.mark.parametrize("exc_class", (AbortAction, InstallerError, ValueError, CommandFailed))
def test_execute_fail(exc_class, step_mock, step_factory, args_mock):
    abort_exc = exc_class()

    class TestMSAction(MultiStepAction):
        steps = (
            step_factory(),
            step_factory(execute=abort_exc),
            step_factory(on_action_fail=RuntimeError),
        )

    action = TestMSAction()

    with pytest.raises(AbortAction if exc_class == AbortAction else InstallerError) as exc_info:
        action.execute(args_mock)

    step_mock.assert_has_calls(
        [
            call.step_0_pre_execute(ANY, args_mock),
            call.step_1_pre_execute(ANY, args_mock),
            call.step_2_pre_execute(ANY, args_mock),
            call.step_0_execute(ANY, args_mock),
            call.step_1_execute(ANY, args_mock),
            call.step_2_on_action_fail(ANY, args_mock),
            call.step_1_on_action_fail(ANY, args_mock),
            call.step_0_on_action_fail(ANY, args_mock),
        ]
    )

    assert exc_info.value.__cause__ == abort_exc


@pytest.mark.unit
@pytest.mark.parametrize("exc_class", (AbortAction, InstallerError, ValueError, CommandFailed))
def test_pre_execute_fail(exc_class, step_mock, step_factory, args_mock):
    abort_exc = exc_class()

    class TestMSAction(MultiStepAction):
        steps = (
            step_factory(),
            step_factory(execute=abort_exc),
            step_factory(),
        )

    action = TestMSAction()

    with pytest.raises(AbortAction if exc_class == AbortAction else InstallerError) as exc_info:
        action.execute(args_mock)

    step_mock.assert_has_calls(
        [
            call.step_0_pre_execute(ANY, args_mock),
            call.step_1_pre_execute(ANY, args_mock),
        ]
    )

    assert exc_info.value.__cause__ == abort_exc


def executed_steps(step_mock):
    return [name for name, *_ in step_mock.mock_calls if name.endswith("_execute") and "pre_execute" not in name]


@pytest.mark.unit
def test_interrupted_step_cleans_up(step_mock, step_factory, args_mock):
    """Ctrl+C mid-install must not leave containers and volumes behind for the next install to trip over."""

    class TestMSAction(MultiStepAction):
        steps = (step_factory(), step_factory(execute=KeyboardInterrupt), step_factory())

    with pytest.raises(AbortAction) as exc_info:
        TestMSAction().execute(args_mock)

    step_mock.assert_has_calls(
        [
            call.step_1_execute(ANY, args_mock),
            call.step_2_on_action_fail(ANY, args_mock),
            call.step_1_on_action_fail(ANY, args_mock),
            call.step_0_on_action_fail(ANY, args_mock),
        ]
    )
    assert "step_2_execute" not in executed_steps(step_mock)
    assert exc_info.value.__cause__.reason == AbortReason.INTERRUPTED
    assert isinstance(exc_info.value.__cause__.__cause__, KeyboardInterrupt)


@pytest.mark.unit
def test_nothing_to_do_ends_the_action_successfully(step_mock, step_factory, args_mock):
    class TestMSAction(MultiStepAction):
        steps = (step_factory(), step_factory(pre_execute=NothingToDo))

    TestMSAction().execute(args_mock)

    assert executed_steps(step_mock) == []
    assert "on_action_fail" not in str(step_mock.mock_calls)


@pytest.mark.unit
def test_interrupted_optional_step_lets_the_action_finish(step_mock, step_factory, args_mock, analytics):
    """Ctrl+C during demo data must not tear down the install that is already up."""

    class TestMSAction(MultiStepAction):
        steps = (
            step_factory(),
            step_factory(execute=KeyboardInterrupt, required=False, continue_on_interrupt=True),
            step_factory(),
        )

    TestMSAction().execute(args_mock)

    assert executed_steps(step_mock) == ["step_0_execute", "step_1_execute", "step_2_execute"]
    assert "on_action_fail" not in str(step_mock.mock_calls)
    assert analytics.additional_properties["failed_optional_steps"] == ["TestStep1"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "undoes_on_fail,cleanup_error,cleaning_up,final_msg",
    (
        (True, None, True, "The partial installation was removed."),
        (True, CommandFailed, True, "inconsistent application state"),
        (False, None, False, "inconsistent application state"),
    ),
    ids=("cleaned up", "cleanup failed", "nothing to clean up"),
)
def test_interrupt_messages(
    undoes_on_fail, cleanup_error, cleaning_up, final_msg, step_factory, args_mock, console_msg_mock
):
    class TestMSAction(MultiStepAction):
        steps = (step_factory(on_action_fail=cleanup_error), step_factory(execute=KeyboardInterrupt))

    TestMSAction.undoes_on_fail = undoes_on_fail

    with pytest.raises(AbortAction):
        TestMSAction().execute(args_mock)

    messages = [c.args[0] for c in console_msg_mock.call_args_list if c.args]
    assert ("Cleaning up..." in messages) is cleaning_up
    assert final_msg in messages[-1]


@pytest.mark.unit
def test_interrupted_optional_step_cancels_the_action_by_default(step_mock, step_factory, args_mock):
    """Ctrl+C during the image pull, which is optional, still has to cancel the install."""

    class TestMSAction(MultiStepAction):
        steps = (step_factory(), step_factory(execute=KeyboardInterrupt, required=False), step_factory())

    with pytest.raises(AbortAction):
        TestMSAction().execute(args_mock)

    assert "step_2_execute" not in executed_steps(step_mock)
    step_mock.step_0_on_action_fail.assert_called_once()
