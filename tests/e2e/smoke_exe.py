"""Install TestGen for real with a built installer, prove it runs, then uninstall it.

Run as ``python tests/e2e/smoke_exe.py dist/dk-installer.exe`` (a ``dk-installer.py`` path
works too). Exercises the pip (standalone) path end to end: the uv bootstrap, ``uv tool
install``, the embedded Postgres, ``standalone-setup``, and the orphan sweep in ``tg
delete``. All of that only fails for real -- unit tests reach it through mocks.

The installer is killed rather than interrupted, on purpose. A clean Ctrl+C never reaches
``force_kill_app_tree``, so it does not exercise the sweep at all; only an orphaned tree
makes ``tg delete`` find the processes by command line, which is where the Windows bugs
were. Delivering a real console Ctrl+C from a CI step is a separate problem, left alone.

Readiness is taken from the install marker plus the UI port, never from the installer's
stdout: redirected to a file that output is block-buffered, so the line announcing the app
can sit unflushed for as long as the app runs.

Every check is collected rather than raised, so one run reports everything that is wrong.
Session logs land in ``smoke/`` for the workflow to upload.
"""

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WINDOWS = platform.system() == "Windows"

UI_PORT = 8501
API_PORT = 8530
# The install pulls uv, a Python 3.13 and TestGen's whole dependency tree, then runs initdb.
# 4~8 minutes is what the installer itself promises; allow well past it before calling it a hang.
INSTALL_TIMEOUT = 20 * 60
# Mirrors STANDALONE_PROC_PATTERNS in dk-installer.py, with separators already normalised.
# Duplicated rather than imported: the subject here is the built artifact, not the source
# sitting next to it.
PROC_PATTERNS = ("testgen.*run-app", "tools/dataops-testgen")

OUT_DIR = Path("smoke")

# Module level so a unit test can parse them against the installer's own parser. This
# script runs only on a merge to main, so a rename here (or of a flag it passes) would
# otherwise surface 25 minutes into a release build.
INSTALL_ARGS = ("tg", "install", "--pip", "--no-demo")
DELETE_ARGS = ("tg", "delete")


class Report:
    """Collects checks so a run reports every failure, not just the first."""

    def __init__(self):
        self.failures = []

    def check(self, ok, label, detail=""):
        suffix = f" -- {detail}" if detail else ""
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}{suffix}", flush=True)
        if not ok:
            self.failures.append(f"{label}{suffix}")
        return ok

    def note(self, label, detail=""):
        print(f"  [note] {label}{f' -- {detail}' if detail else ''}", flush=True)


def step(title):
    print(f"\n=== {title} ===", flush=True)


def data_folder(installer):
    """Where the installer keeps the marker, the credentials and its session logs."""
    if WINDOWS:
        return Path(os.environ["LOCALAPPDATA"]) / "DataKitchenApps"
    return installer.resolve().parent


def logs_folder(installer):
    base = data_folder(installer)
    return base / "logs" if WINDOWS else base / ".dk-installer"


def testgen_home():
    return Path(os.environ.get("TG_TESTGEN_HOME", Path.home() / ".testgen"))


def resolve_uv(installer):
    """The uv the installer would use: its own bootstrapped copy, else one on PATH."""
    local = data_folder(installer) / "bin" / ("uv.exe" if WINDOWS else "uv")
    return str(local) if local.exists() else shutil.which("uv")


def run(cmd, **kwargs):
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run(cmd, check=False, **kwargs)


def http_get(url):
    """(status, first bytes of body), or (None, reason) when the request could not be made."""
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, response.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # URLError, timeouts, refused connections
        return None, str(e)


def port_open(port):
    try:
        with socket.create_connection(("localhost", port), timeout=5):
            return True
    except OSError:
        return False


def standalone_pids():
    """PIDs of the processes a standalone install spawns: testgen, streamlit, postgres."""
    if WINDOWS:
        clause = " -or ".join(f"($cmd -match '{p}') -or ($exe -match '{p}')" for p in PROC_PATTERNS)
        script = (
            "$ErrorActionPreference = 'SilentlyContinue'; "
            "Get-CimInstance Win32_Process | Where-Object { "
            "$cmd = ($_.CommandLine -replace '\\\\', '/'); "
            "$exe = ($_.ExecutablePath -replace '\\\\', '/'); " + clause + " } | ForEach-Object { $_.ProcessId }"
        )
        result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    else:
        result = run(["pgrep", "-f", "|".join(PROC_PATTERNS)])
    return sorted(int(line) for line in result.stdout.split() if line.strip().isdigit())


def wait_until_running(proc, installer, report):
    """Wait for the marker the installer writes just before it starts the app, then the port."""
    marker = data_folder(installer) / "dk-tg-install.json"
    deadline = time.time() + INSTALL_TIMEOUT
    while time.time() < deadline:
        if marker.exists() and port_open(UI_PORT):
            return True
        if proc.poll() is not None:
            report.check(False, "installer stayed up until the app was ready", f"exited {proc.returncode}")
            return False
        time.sleep(5)
    report.check(False, "install finished within the timeout", f"{INSTALL_TIMEOUT}s elapsed")
    return False


def check_running_install(installer, report):
    """What must hold while the app is up, checked before anything is torn down."""
    status, _ = http_get(f"http://localhost:{UI_PORT}/")
    report.check(status == 200, f"UI answers on port {UI_PORT}", f"status {status}")

    # Streamlit's own liveness endpoint. Its path has moved between releases, so this is
    # reported rather than required -- the UI check above is the gate.
    for path in ("/_stcore/health", "/healthz"):
        health, body = http_get(f"http://localhost:{UI_PORT}{path}")
        if health == 200:
            report.note(f"health endpoint {path}", body.strip()[:40])
            break

    # TCP only: the API's routes are TestGen's to define, and pinning one here would break
    # this gate on an unrelated rename. Tighten once we have seen what it serves.
    report.check(port_open(API_PORT), f"API port {API_PORT} accepts connections")

    postmaster = testgen_home() / "pgdata" / "postmaster.pid"
    report.check(postmaster.exists(), "embedded Postgres is running", str(postmaster))

    config = testgen_home() / "config.env"
    config_text = config.read_text(encoding="utf-8", errors="replace") if config.exists() else ""
    report.check(f"TG_UI_PORT={UI_PORT}" in config_text, "standalone-setup persisted the UI port")

    marker = data_folder(installer) / "dk-tg-install.json"
    mode = json.loads(marker.read_text()).get("install_mode") if marker.exists() else None
    report.check(mode == "pip", "install marker records pip mode", f"got {mode!r}")

    creds = data_folder(installer) / "dk-tg-credentials.txt"
    report.check(creds.exists() and creds.stat().st_size > 0, "credentials file written")

    uv_path = resolve_uv(installer)
    listed = run([uv_path, "tool", "list"]).stdout if uv_path else ""
    report.check("dataops-testgen" in listed, "uv reports the tool installed", listed.strip()[:80])

    app_log = testgen_home() / "logs" / "app.log"
    app_text = app_log.read_text(encoding="utf-8", errors="replace") if app_log.exists() else ""
    report.check("Traceback" not in app_text, "no traceback in the app log")


def uv_tool_paths(installer):
    """uv's tool env and shim for TestGen, resolved before the delete removes uv itself."""
    uv_path = resolve_uv(installer)
    if not uv_path:
        return []
    paths = []
    tool_dir = run([uv_path, "tool", "dir"]).stdout.strip()
    if tool_dir:
        paths.append(Path(tool_dir) / "dataops-testgen")
    bin_dir = run([uv_path, "tool", "dir", "--bin"]).stdout.strip()
    if bin_dir:
        paths.append(Path(bin_dir) / ("testgen.exe" if WINDOWS else "testgen"))
    return paths


def kill_installer(proc):
    """Kill only the installer, orphaning its app tree -- the sweep's whole reason to exist."""
    if WINDOWS:
        # Deliberately no /T: killing the tree would do the sweep's job for it.
        run(["taskkill", "/F", "/PID", str(proc.pid)])
    else:
        os.kill(proc.pid, 9)
    proc.wait(timeout=30)
    time.sleep(3)


def check_delete(installer, output, tool_paths, report):
    """The delete's own report has to match what is actually left on disk."""
    survived = []
    pids = standalone_pids()
    if pids:
        survived.append(f"processes {pids}")
    for path in [*tool_paths, testgen_home()]:
        if path.exists():
            survived.append(str(path))
    for name in ("dk-tg-install.json", "dk-tg-credentials.txt"):
        if (data_folder(installer) / name).exists():
            survived.append(name)

    report.check(not survived, "nothing survived the delete", "; ".join(survived))

    claimed = "TestGen uninstalled." in output
    if survived:
        # The bug this guards against: a success message printed while the tool environment
        # and shim were still on disk, because a live process held them open.
        report.check(not claimed, "delete did not claim a success it cannot back up")
    else:
        report.check(claimed, "delete reported success")


def collect_logs(installer):
    OUT_DIR.mkdir(exist_ok=True)
    session_logs = logs_folder(installer)
    if session_logs.exists():
        for zipped in sorted(session_logs.glob("*.zip")):
            shutil.copy2(zipped, OUT_DIR / zipped.name)
    app_log = testgen_home() / "logs" / "app.log"
    if app_log.exists():
        shutil.copy2(app_log, OUT_DIR / "testgen-app.log")


def refuse_outside_ci(force):
    """This script is destructive. Make running it by hand on a workstation deliberate.

    It kills every TestGen process on the machine (the installer's own sweep does that
    before standalone-setup) and ``tg delete`` then removes the TestGen data directory and
    uv's tool environment -- including an install that was already there.
    """
    if force or os.environ.get("CI"):
        return
    sys.exit(
        "Refusing to run outside CI. This installs TestGen for real, kills every TestGen\n"
        f"process on this machine, and deletes {testgen_home()} along with uv's\n"
        "dataops-testgen tool environment -- an existing install included.\n"
        "Run it in a throwaway container, or pass --force if you mean it."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path, help="the dk-installer executable (or .py) to test")
    parser.add_argument("--force", action="store_true", help="run outside CI, destroying any local install")
    args = parser.parse_args()

    refuse_outside_ci(args.force)

    installer = args.installer
    if not installer.exists():
        sys.exit(f"No installer at {installer}")

    # Opt out of analytics for every child: this is the default source for the top-level
    # --no-analytics flag, and unlike the flag it cannot be passed in the wrong position.
    os.environ["DK_INSTALLER_ANALYTICS"] = "no"

    OUT_DIR.mkdir(exist_ok=True)
    install_log = OUT_DIR / "install.log"
    report = Report()

    command = [sys.executable, str(installer)] if installer.suffix == ".py" else [str(installer.resolve())]

    step("install (backgrounded: the installer blocks running the app)")
    with install_log.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [*command, *INSTALL_ARGS],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            # Unbuffered so the log is useful while the run is still going; readiness does
            # not depend on it either way.
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        running = wait_until_running(proc, installer, report)

    print(install_log.read_text(encoding="utf-8", errors="replace")[-3000:], flush=True)

    if not running:
        collect_logs(installer)
        sys.exit("\n".join(["the install never reached a running app:", *report.failures]))

    step("what a finished install must look like")
    check_running_install(installer, report)
    tool_paths = uv_tool_paths(installer)

    step("kill the installer, orphaning the app tree")
    kill_installer(proc)
    orphans = standalone_pids()
    if orphans:
        report.note("orphans left by the dirty exit", str(orphans))
    else:
        report.note("nothing survived the kill", "the sweep has nothing to prove in this run")

    step("delete")
    deleted = run([*command, *DELETE_ARGS])
    print(deleted.stdout[-3000:], flush=True)
    (OUT_DIR / "delete.log").write_text(deleted.stdout + deleted.stderr, encoding="utf-8")
    report.check(deleted.returncode == 0, "tg delete exited cleanly", f"rc {deleted.returncode}")
    check_delete(installer, deleted.stdout, tool_paths, report)

    collect_logs(installer)

    step("result")
    if report.failures:
        sys.exit("\n".join([f"{len(report.failures)} check(s) failed:", *(f"  - {f}" for f in report.failures)]))
    print("  all checks passed", flush=True)


if __name__ == "__main__":
    main()
