from itertools import count
from unittest.mock import Mock, call, ANY

import pytest

from tests.installer import MultiStepAction, Step, AbortAction, SkipStep, InstallerError, CommandFailed


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
