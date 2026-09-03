"""The exe smoke test only runs on a merge to main, so anything it can get wrong on its own
is worth pinning here: the argv it drives the installer with, and the parsing it does of
Windows command lines.
"""

import pytest

from .e2e.smoke_exe import DELETE_ARGS, INSTALL_ARGS, summarize
from .installer import get_installer_instance


@pytest.mark.unit
def test_smoke_install_args_are_accepted():
    args = get_installer_instance().parser.parse_args(list(INSTALL_ARGS))

    assert args.prod == "tg"
    assert args.install_mode == "pip"
    # The demo step is skipped on purpose: it is `required = False`, so it could not gate the
    # release anyway, and it is the longest part of the install.
    assert args.generate_demo is False


@pytest.mark.unit
def test_smoke_delete_args_are_accepted():
    args = get_installer_instance().parser.parse_args(list(DELETE_ARGS))

    assert args.prod == "tg"
    # Nothing is kept: the smoke test asserts the uninstall left nothing behind.
    assert args.keep_data is False
    assert args.keep_images is False


@pytest.mark.unit
def test_summarize_counts_a_standalone_tree():
    """Shapes taken from a real install: postgres quotes its path, the python children do
    not, and the shim sits in uv's bin dir rather than the tool environment."""
    procs = {
        1: '"C:\\Users\\r\\AppData\\Roaming\\uv\\tools\\dataops-testgen\\Scripts\\postgres.exe" -D C:/pgdata',
        2: '"C:\\Users\\r\\AppData\\Roaming\\uv\\tools\\dataops-testgen\\Scripts\\postgres.exe" -c config',
        3: "C:/Users/r/AppData/Roaming/uv/tools/dataops-testgen/Scripts/python.exe -m streamlit run app.py",
        4: "C:/Users/r/AppData/Roaming/uv/tools/dataops-testgen/Scripts/python.exe -m testgen run-app",
        5: "C:/Users/r/AppData/Roaming/uv/bin/testgen.exe run-app",
    }

    # Ordered by count, so the dominant process kind reads first.
    assert summarize(procs) == "2 postgres, 2 python, 1 testgen"


@pytest.mark.unit
def test_summarize_survives_a_missing_command_line():
    """Win32_Process leaves CommandLine empty for a process the query cannot read, and a
    crash here would fail a release build for a cosmetic reason."""
    assert summarize({1: "", 2: "   "}) == "2 unknown"
    assert summarize({}) == ""


@pytest.mark.unit
def test_summarize_handles_posix_paths():
    procs = {
        1: "/home/t/.local/share/uv/tools/dataops-testgen/bin/python -m streamlit run app.py",
        2: "/home/t/.local/bin/testgen run-app",
    }

    assert summarize(procs) == "1 python, 1 testgen"
