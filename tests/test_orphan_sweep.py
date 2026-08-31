"""Selector coverage for ``stop_standalone_orphans``.

The command lines below are captured from real standalone installs, only the user
directory renamed. The image-name match this replaced could see the ``testgen.exe``
shim and nothing else: every child is ``python``.
"""

import re
from unittest.mock import patch

import pytest

from tests.installer import STANDALONE_PROC_PATTERNS, stop_standalone_orphans

TOOLS = r"D:\Users\dev\AppData\Roaming\uv\tools\dataops-testgen"

# Every process the install spawned, by role.
SPAWNED = {
    "shim": r"D:\Users\dev\.local\bin\testgen.exe run-app",
    "shim-trampoline": rf'"{TOOLS}\Scripts\python.exe" "D:\Users\dev\.local\bin\testgen.exe" run-app',
    "ui": rf"{TOOLS}\Scripts\python.exe -m testgen run-app ui",
    "scheduler": rf"{TOOLS}\Scripts\python.exe -m testgen run-app scheduler",
    "server": rf"{TOOLS}\Scripts\python.exe -m testgen run-app server",
    # The one that survived: no "run-app" anywhere, and spawned into its own session.
    "streamlit": (
        rf"{TOOLS}\Scripts\python.exe -m streamlit run "
        rf"{TOOLS}\Lib\site-packages\testgen\ui/app.py --server.port=8501"
    ),
    # postgres writes forward slashes, and hangs off a detached cmd.exe whose parent has
    # already exited -- so no tree walk reaches it.
    "postgres-wrapper": (
        r'"C:\Windows\system32\cmd.exe" /C ""D:/Users/dev/AppData/Roaming/uv/tools/'
        r'dataops-testgen/Lib/site-packages/pixeltable_pgserver/pginstall18/bin/postgres.exe" '
        r'-D "D:/Users/dev/.testgen/pgdata" -h "127.0.0.1" -p 57028"'
    ),
    "postgres": (
        r'"D:/Users/dev/AppData/Roaming/uv/tools/dataops-testgen/Lib/site-packages/'
        r'pixeltable_pgserver/pginstall18/bin/postgres.exe" -D "D:/Users/dev/.testgen/pgdata"'
    ),
    "postgres-forkchild": (
        r'"D:/Users/dev/AppData/Roaming/uv/tools/dataops-testgen/Lib/site-packages/'
        r'pixeltable_pgserver/pginstall18/bin/postgres.exe" --forkchild="bgworker" 5720'
    ),
}

# The same tree on Linux. Kept alongside the Windows set because the POSIX sweep matches
# the same patterns via `pkill -f`.
POSIX_TOOLS = "/home/tester/.local/share/uv/tools/dataops-testgen"
SPAWNED_POSIX = {
    "shim": f"{POSIX_TOOLS}/bin/python /home/tester/.local/bin/testgen run-app",
    "ui": f"{POSIX_TOOLS}/bin/python -m testgen run-app ui",
    "scheduler": f"{POSIX_TOOLS}/bin/python -m testgen run-app scheduler",
    "server": f"{POSIX_TOOLS}/bin/python -m testgen run-app server",
    "streamlit": (
        f"{POSIX_TOOLS}/bin/python -m streamlit run "
        f"{POSIX_TOOLS}/lib/python3.13/site-packages/testgen/ui/app.py --server.port=8501"
    ),
    "postgres": (
        f"{POSIX_TOOLS}/lib/python3.13/site-packages/pixeltable_pgserver/pginstall18/bin/postgres "
        f"-D /home/tester/.testgen/pgdata -h  -k /home/tester/.testgen/pgdata"
    ),
}

# Must survive the sweep: killing either of these is killing ourselves.
SPARED = {
    "installer-exe": r'"D:\Users\dev\Downloads\dk-installer.exe" tg start',
    "installer-py": "python3 dk-installer.py tg install --pip",
}


def matches(command_line):
    return any(re.search(p, command_line) for p in STANDALONE_PROC_PATTERNS)


@pytest.mark.unit
@pytest.mark.parametrize("role", sorted(SPAWNED))
def test_sweep_matches_every_spawned_process(role):
    assert matches(SPAWNED[role]), f"{role} would be left running"


@pytest.mark.unit
@pytest.mark.parametrize("role", sorted(SPARED))
def test_sweep_spares_the_installer(role):
    assert not matches(SPARED[role])


@pytest.mark.unit
@pytest.mark.parametrize("role", sorted(SPAWNED_POSIX))
def test_sweep_matches_every_spawned_process_on_posix(role):
    assert matches(SPAWNED_POSIX[role]), f"{role} would be left running"


@pytest.mark.unit
def test_streamlit_needs_the_tool_env_pattern():
    """The regression that motivated this: the UI carries no 'run-app', so the original
    pattern missed it -- and it is the process holding port 8501."""
    for cmdline in (SPAWNED["streamlit"], SPAWNED_POSIX["streamlit"]):
        assert not re.search(r"testgen.*run-app", cmdline)
        assert matches(cmdline)


@pytest.mark.unit
def test_posix_sweep_runs_every_pattern(tmp_path):
    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.pathlib.Path.home", return_value=tmp_path),
        patch("tests.installer.subprocess.run") as run_mock,
    ):
        stop_standalone_orphans()

    patterns = [c.args[0][-1] for c in run_mock.call_args_list if c.args[0][0] == "pkill"]
    assert patterns == list(STANDALONE_PROC_PATTERNS)


@pytest.mark.unit
def test_windows_sweep_spares_the_installer_pid(tmp_path):
    with (
        patch("tests.installer.platform.system", return_value="Windows"),
        patch("tests.installer.pathlib.Path.home", return_value=tmp_path),
        patch("tests.installer.os.getpid", return_value=4242),
        patch("tests.installer.subprocess.run") as run_mock,
    ):
        stop_standalone_orphans()

    script = next(c.args[0][-1] for c in run_mock.call_args_list if c.args[0][0] == "powershell")
    assert "@(4242)" in script
    assert "PIDS_TO_SPARE" not in script  # substitution actually happened
    # Kills by PID rather than walking a tree that re-parents mid-teardown.
    assert "Stop-Process -Id" in script
    assert "/T" not in script


@pytest.mark.unit
def test_windows_sweep_no_longer_matches_by_image_name(tmp_path):
    """`taskkill /IM testgen.exe` could not see the python children at all."""
    with (
        patch("tests.installer.platform.system", return_value="Windows"),
        patch("tests.installer.pathlib.Path.home", return_value=tmp_path),
        patch("tests.installer.subprocess.run") as run_mock,
    ):
        stop_standalone_orphans()

    assert not any("/IM" in str(c.args[0]) for c in run_mock.call_args_list)


@pytest.mark.unit
def test_sweep_never_raises(tmp_path):
    """Best-effort cleanup: it runs on the install and delete paths, and must not be able
    to crash either."""
    with (
        patch("tests.installer.platform.system", return_value="Linux"),
        patch("tests.installer.pathlib.Path.home", return_value=tmp_path),
        patch("tests.installer.subprocess.run", side_effect=OSError("boom")),
    ):
        stop_standalone_orphans()  # must not raise
