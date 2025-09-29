import itertools
import pathlib
import subprocess

import pytest

from tests.installer import stream_iterator, AbortAction, CommandFailed


@pytest.fixture
def popen_stdout_buffer(popen_mock):
    buffer = "\n".join(["🔷🔶🔺🔻"[i % 4] + " xxxx" * 20 for i in range(100)]).encode()
    popen_mock.communicate.side_effect = [
        *[subprocess.TimeoutExpired("cmd", 1, output=buffer[:idx]) for idx in range(0, len(buffer), 38)],
        (buffer, b""),
    ]
    return buffer


@pytest.mark.unit
def test_stream_iterator(popen_mock, popen_stdout_buffer, tmp_logs_folder):
    cmd_log_path = pathlib.Path(tmp_logs_folder) / "cmd-log.txt"

    with stream_iterator(popen_mock, "stdout", cmd_log_path) as stdout_iter:
        for stdout_line, buffer_line in itertools.zip_longest(stdout_iter, popen_stdout_buffer.splitlines()):
            assert stdout_line == buffer_line.decode()

    assert cmd_log_path.read_bytes() == popen_stdout_buffer


@pytest.mark.unit
@pytest.mark.parametrize("exception", (CommandFailed(2, "cmd", 1), AbortAction(), RuntimeError()))
def test_stream_iterator_exception(exception, popen_mock, popen_stdout_buffer, tmp_logs_folder):
    cmd_log_path = pathlib.Path(tmp_logs_folder) / "cmd-log.txt"

    with pytest.raises(exception.__class__):
        with stream_iterator(popen_mock, "stdout", cmd_log_path) as stdout_iter:
            for _ in itertools.islice(stdout_iter, 200):
                pass
            raise exception

    assert cmd_log_path.read_bytes() == popen_stdout_buffer


@pytest.mark.unit
def test_stream_iterator_partially_consumed(popen_mock, popen_stdout_buffer, tmp_logs_folder):
    cmd_log_path = pathlib.Path(tmp_logs_folder) / "cmd-log.txt"

    with stream_iterator(popen_mock, "stdout", cmd_log_path) as stdout_iter:
        for _ in itertools.islice(stdout_iter, 200):
            pass

    assert cmd_log_path.read_bytes() == popen_stdout_buffer
