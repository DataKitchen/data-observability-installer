from pathlib import Path
from subprocess import TimeoutExpired

import pytest
from unittest.mock import Mock, patch, PropertyMock, call, ANY
from contextlib import contextmanager
from installer import Action, CONSOLE, CommandFailed, AbortAction, InstallerError


@pytest.fixture
def proc_mock():
    proc = Mock()
    proc.returncode = 0
    proc.wait.return_value = None
    proc.poll.return_value = 0
    return proc


@pytest.fixture
def stdout_mock():
    return Mock(return_value=[])


@pytest.fixture
def stderr_mock():
    return Mock(return_value=[])


@pytest.fixture
def console_msg_mock():
    with patch.object(CONSOLE, "msg") as mock:
        yield mock


@pytest.fixture
def start_cmd_mock(action, proc_mock, stdout_mock, stderr_mock):

    exit_mock = Mock()

    @contextmanager
    def _start_cmd(*args, **kwargs):
        try:
            yield proc_mock, stdout_mock(), stderr_mock()
        finally:
            exit_mock()

    with patch.object(action, "start_cmd", side_effect=_start_cmd) as mock:
        mock.attach_mock(exit_mock, "__exit__")
        yield mock


@pytest.fixture
def popen_mock(proc_mock):
    with patch("installer.subprocess.Popen") as popen_mock:
        popen_mock.return_value = proc_mock
        yield popen_mock


@pytest.fixture
def stream_iter_mock():
    with patch("installer.StreamIterator") as si_mock:
        si_mock.__enter__.return_value = si_mock
        yield si_mock


@pytest.fixture
def analytics_mock():
    with patch("installer.AnalyticsWrapper") as mock:
        yield mock


@pytest.fixture
def execute_mock(action):
    with patch.object(action, "execute") as mock:
        yield mock


@pytest.fixture
def action():

    class TestAction(Action):
        args_cmd = "test"

    instance = TestAction()
    with (
        patch.object(instance, "session_zip", create=True),
        patch.object(instance, "session_folder", create=True),
        patch.object(instance, "_cmd_idx", create=True, new=0),
        patch.object(instance, "configure_logging", create=True),
        patch.object(instance, "init_session_folder", create=True),
        patch.object(instance, "execute"),
    ):
        yield instance

@pytest.fixture
def args_mock():
    mock = Mock()
    return mock


@pytest.mark.unit
def test_exec_with_log(action, args_mock, analytics_mock, execute_mock):
    action.execute_with_log(args_mock)
    execute_mock.assert_called_once_with(args_mock)


@pytest.mark.unit
@pytest.mark.parametrize("exec_exc,expected_exc,print_msg", (
    (AbortAction(), AbortAction, False),
    (InstallerError(), InstallerError, True),
    (RuntimeError(), InstallerError, True),
    (KeyboardInterrupt(), InstallerError, False),
))
def test_exec_with_log_raises(exec_exc, expected_exc, print_msg, action, args_mock, analytics_mock, execute_mock):
    execute_mock.side_effect = exec_exc

    with patch.object(action, "_msg_unexpected_error") as uncauhgt_msg_mock:
        with pytest.raises(expected_exc) as exc_info:
            action.execute_with_log(args_mock)

    execute_mock.assert_called_once_with(args_mock)
    assert exec_exc in (exc_info.value, exc_info.value.__cause__)
    assert uncauhgt_msg_mock.call_args_list == ([call(exec_exc)] if print_msg else [])


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc_levels, glob_side_effect, expected_calls, expected_return",
    (
        (0, ([], [Path("stdout.txt")]), ("stderr", "stdout"), (ANY, Path("stdout.txt"))),
        (
            1,
            ([Path("stderr.txt"), Path("stderr_2.txt")], [Path("stdout.txt")]),
            ("stderr", "stdout"),
            (ANY, Path("stdout.txt"))
        ),
        (5, ([Path("stderr.txt")], [Path("stdout.txt")]), ("stderr",), (ANY, Path("stderr.txt"))),
        (3, ([], [Path("stdout.txt"), Path("stdout.txt")]), ("stderr", "stdout"), (None, None)),
        (1, ([], []), ("stderr", "stdout"), (None, None)),
    ),
    ids=(
        "direct exception, stdout only",
        "one layer deep, two stderr files",
        "five layers deep, stderr is picked",
        "three layers deep, two stdout",
        "one layers deep, no files",
    ),
)
def test_get_failed_cmd_log(action, exc_levels, glob_side_effect, expected_calls, expected_return):

    cmd_failed_exc = CommandFailed(42, "cmd 42", 2)
    exc = cmd_failed_exc
    for n in range(exc_levels):
        outer_exc = RuntimeError(str(n + 1))
        outer_exc.__cause__ = exc
        exc = outer_exc

    action.session_folder.glob.side_effect = glob_side_effect

    ret = action._get_failed_cmd_log_file_path(exc)

    assert ret == expected_return
    assert ret[0] in (None, cmd_failed_exc)
    action.session_folder.glob.assert_has_calls([call(f"0042-{stream}-*.txt") for stream in expected_calls])


@pytest.mark.unit
def test_run_cmd_text(action, start_cmd_mock, stdout_mock, console_msg_mock):
    stdout_mock.return_value = [b"hi there"]
    result = action.run_cmd("cmd", capture_text=True)
    assert result == "hi there"
    console_msg_mock.assert_not_called()
    start_cmd_mock.assert_called_once()


@pytest.mark.unit
def test_run_cmd_json(action, start_cmd_mock, stdout_mock):
    stdout_mock.return_value = [b"{\"foo\": 123}"]
    result = action.run_cmd("cmd", capture_json=True)
    assert result == {"foo": 123}
    start_cmd_mock.assert_called_once()


@pytest.mark.unit
def test_run_cmd_invalid_json(action, start_cmd_mock, stdout_mock):
    stdout_mock.return_value = [b"no JSON here"]
    result = action.run_cmd("cmd", capture_json=True)
    assert result == {}
    start_cmd_mock.assert_called_once()


@pytest.mark.unit
def test_run_cmd_json_lines(action, start_cmd_mock, stdout_mock):
    stdout_mock.return_value = [b"{\"foo\": 123}", b"something else", b"{\"foo\": 321}"]
    result = action.run_cmd("cmd", capture_json_lines=True)
    assert result == [{"foo": 123}, {"foo": 321}]
    start_cmd_mock.assert_called_once()


@pytest.mark.unit
def test_run_cmd_echo(action, start_cmd_mock, stdout_mock, console_msg_mock):
    stdout_mock.return_value = [b"some output", b"will be echoed"]
    result = action.run_cmd("cmd", echo=True)
    assert result is None
    assert console_msg_mock.call_count == 2
    assert console_msg_mock.call_args_list[0].args[0] == "some output"
    assert console_msg_mock.call_args_list[1].args[0] == "will be echoed"
    start_cmd_mock.assert_called_once()


@pytest.mark.unit
def test_run_cmd_retries(action, start_cmd_mock, proc_mock):
    start_cmd_mock.__exit__.side_effect = [CommandFailed, None, CommandFailed, None, None]
    proc_mock.wait.side_effect = [None, TimeoutExpired(Mock(), 5), None, None, None]

    action.run_cmd_retries("cmd", timeout=5, retries=5)

    assert start_cmd_mock.call_count == 4
    assert proc_mock.wait.call_count == 4
    start_cmd_mock.assert_called_with("cmd", env=None)
    proc_mock.kill.assert_called_once_with()
    proc_mock.wait.assert_called_with(timeout=5)


@pytest.mark.unit
def test_run_cmd_retries_raises(action, start_cmd_mock, proc_mock):
    start_cmd_mock.__exit__.side_effect = CommandFailed

    with pytest.raises(CommandFailed):
        action.run_cmd_retries("cmd", timeout=5, retries=10)

    assert start_cmd_mock.call_count == 10
    proc_mock.kill.assert_not_called()
    proc_mock.wait.assert_called_with(timeout=5)


@pytest.mark.unit
def test_start_cmd(action, popen_mock, stream_iter_mock):
    with action.start_cmd(
        "cmd", "arg", env={"var": "val"}, popen_extra=True, raise_on_non_zero=False
    ) as (proc_mock, stdout_mock, stderr_mock):
        proc_mock.returncode = 55

    stream_iter_mock.assert_has_calls(
        [
            call(popen_mock(), popen_mock().stdout, ANY),
            call(popen_mock(), popen_mock().stderr, ANY),
            call().__enter__(),
            call().__enter__(),
            call().__exit__(None, None, None),
            call().__exit__(None, None, None),
        ], any_order=True,
    )

    assert proc_mock.wait.call_count == 1
    assert popen_mock.call_args_list[0].kwargs["env"]["var"] == "val"
    assert popen_mock.call_args_list[0].kwargs["popen_extra"] is True


@pytest.mark.unit
def test_start_cmd_raises_non_zero(action, popen_mock, proc_mock, stream_iter_mock):
    proc_mock.returncode = 143
    with pytest.raises(CommandFailed) as exc_info:
        with action.start_cmd("cmd", "arg"):
            pass

    assert exc_info.value.idx == 1
    assert exc_info.value.cmd == "cmd arg"
    assert exc_info.value.ret_code == 143


@pytest.mark.unit
def test_start_cmd_file_not_found(action, popen_mock, stream_iter_mock):
    popen_mock.side_effect = FileNotFoundError
    with pytest.raises(CommandFailed) as exc_info:
        with action.start_cmd("cmd", "arg"):
            assert False, "this should not be reachable"

    assert exc_info.value.idx == 1
    assert exc_info.value.cmd == "cmd arg"
    assert exc_info.value.ret_code is None


@pytest.mark.unit
def test_start_cmd_enhance_command_failed(action, popen_mock, proc_mock, stream_iter_mock):
    with pytest.raises(CommandFailed) as exc_info:
        with action.start_cmd("cmd", "arg"):
            raise CommandFailed

    assert proc_mock.wait.call_count == 1
    assert exc_info.value.idx == 1
    assert exc_info.value.cmd == "cmd arg"
    assert exc_info.value.ret_code == 0


@pytest.mark.unit
def test_start_cmd_wait_on_exception(action, popen_mock, stream_iter_mock):
    with pytest.raises(RuntimeError) as exc_info:
        with action.start_cmd("cmd", "arg") as (proc_mock, _, _):
            proc_mock.returncode = 0
            raise RuntimeError("something went wrong")

    assert proc_mock.wait.call_count == 1
    assert exc_info.value.args == ("something went wrong",)
