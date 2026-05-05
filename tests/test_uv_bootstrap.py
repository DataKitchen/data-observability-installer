import hashlib
import io
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.installer import (
    AbortAction,
    InstallerError,
    UV_ASSETS,
    UV_VERSION,
    UvBootstrapStep,
    get_uv_asset,
)


def _make_tarball(inner_path: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(inner_path)
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _make_zip(inner_path: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(inner_path, payload)
    return buf.getvalue()


@pytest.fixture
def uv_step():
    return UvBootstrapStep()


@pytest.fixture
def bootstrap_action(action, tmp_data_folder):
    action.ctx = {}
    action.analytics = MagicMock()
    action.analytics.additional_properties = {}
    return action


@pytest.fixture
def linux_x86_64(monkeypatch):
    monkeypatch.setattr("tests.installer.platform.system", lambda: "Linux")
    monkeypatch.setattr("tests.installer.platform.machine", lambda: "x86_64")


@pytest.fixture
def windows_amd64(monkeypatch):
    monkeypatch.setattr("tests.installer.platform.system", lambda: "Windows")
    monkeypatch.setattr("tests.installer.platform.machine", lambda: "AMD64")


@pytest.mark.unit
def test_get_uv_asset_known_platform(monkeypatch):
    monkeypatch.setattr("tests.installer.platform.system", lambda: "Darwin")
    monkeypatch.setattr("tests.installer.platform.machine", lambda: "arm64")

    asset, sha256 = get_uv_asset("tg")

    assert asset == "uv-aarch64-apple-darwin.tar.gz"
    assert sha256 == UV_ASSETS[("Darwin", "arm64")][1]


@pytest.mark.unit
def test_get_uv_asset_unknown_platform_raises(monkeypatch, console_msg_mock):
    monkeypatch.setattr("tests.installer.platform.system", lambda: "SunOS")
    monkeypatch.setattr("tests.installer.platform.machine", lambda: "sparc64")

    with pytest.raises(AbortAction):
        get_uv_asset("tg")

    console_msg_mock.assert_any_msg_contains("No prebuilt uv binary available for platform SunOS/sparc64")


@pytest.mark.unit
def test_skips_when_uv_on_path(uv_step, bootstrap_action, args_mock, linux_x86_64):
    # `uv --version` returns a different version than UV_VERSION on purpose:
    # the existing-uv branch must record the *actual* installed version, not
    # what we'd ship if we'd downloaded.
    with (
        patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"),
        patch("tests.installer.urllib.request.urlopen") as urlopen_mock,
        patch.object(bootstrap_action, "run_cmd", return_value="uv 0.4.30 (deadbeef 2024-08-01)"),
    ):
        # MultiStepAction always invokes pre_execute before execute; mirror that.
        uv_step.pre_execute(bootstrap_action, args_mock)
        uv_step.execute(bootstrap_action, args_mock)

    urlopen_mock.assert_not_called()
    assert bootstrap_action.ctx["uv_path"] == "/usr/local/bin/uv"
    assert bootstrap_action.analytics.additional_properties["uv_source"] == "existing"
    assert bootstrap_action.analytics.additional_properties["uv_version"] == "0.4.30"


@pytest.mark.unit
def test_skips_when_uv_local(uv_step, bootstrap_action, args_mock, linux_x86_64):
    local_uv = Path(bootstrap_action.data_folder) / "bin" / "uv"
    local_uv.parent.mkdir(parents=True, exist_ok=True)
    local_uv.write_bytes(b"existing-uv")

    with (
        patch("tests.installer.shutil.which", return_value=None),
        patch("tests.installer.urllib.request.urlopen") as urlopen_mock,
        patch.object(bootstrap_action, "run_cmd", return_value=f"uv {UV_VERSION} (abcd 2024-09-15)"),
    ):
        uv_step.pre_execute(bootstrap_action, args_mock)
        uv_step.execute(bootstrap_action, args_mock)

    urlopen_mock.assert_not_called()
    assert bootstrap_action.ctx["uv_path"] == str(local_uv)
    assert bootstrap_action.analytics.additional_properties["uv_source"] == "existing"
    assert bootstrap_action.analytics.additional_properties["uv_version"] == UV_VERSION


@pytest.mark.unit
def test_downloads_and_extracts_on_linux(uv_step, bootstrap_action, args_mock, linux_x86_64, monkeypatch):
    payload = b"fake-uv-binary-linux"
    tarball = _make_tarball("uv-x86_64-unknown-linux-gnu/uv", payload)
    # Include uvx too to ensure the step picks the right binary.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for inner, data in (
            ("uv-x86_64-unknown-linux-gnu/uv", payload),
            ("uv-x86_64-unknown-linux-gnu/uvx", b"fake-uvx"),
        ):
            info = tarfile.TarInfo(inner)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    tarball = buf.getvalue()

    sha256 = hashlib.sha256(tarball).hexdigest()
    monkeypatch.setitem(UV_ASSETS, ("Linux", "x86_64"), ("uv-x86_64-unknown-linux-gnu.tar.gz", sha256))

    resp = MagicMock()
    resp.read.return_value = tarball
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    with (
        patch("tests.installer.shutil.which", return_value=None),
        patch("tests.installer.urllib.request.urlopen", return_value=resp) as urlopen_mock,
        patch.object(bootstrap_action, "run_cmd", return_value=f"uv {UV_VERSION} (abcd 2024-09-15)"),
    ):
        uv_step.execute(bootstrap_action, args_mock)

    urlopen_mock.assert_called_once()
    called_url = urlopen_mock.call_args.args[0]
    assert UV_VERSION in called_url
    assert called_url.endswith("uv-x86_64-unknown-linux-gnu.tar.gz")

    installed = Path(bootstrap_action.data_folder) / "bin" / "uv"
    assert installed.exists()
    assert installed.read_bytes() == payload
    # Archive cleaned up
    assert not (Path(bootstrap_action.data_folder) / "bin" / "uv-x86_64-unknown-linux-gnu.tar.gz").exists()

    assert bootstrap_action.ctx["uv_path"] == str(installed)
    assert bootstrap_action.analytics.additional_properties["uv_source"] == "download"
    # uv_version comes from parsing `uv --version`, not from the UV_VERSION constant.
    assert bootstrap_action.analytics.additional_properties["uv_version"] == UV_VERSION


@pytest.mark.unit
def test_downloads_and_extracts_on_windows(uv_step, bootstrap_action, args_mock, windows_amd64, monkeypatch):
    payload = b"fake-uv-binary-windows"
    zip_bytes = _make_zip("uv-x86_64-pc-windows-msvc/uv.exe", payload)
    sha256 = hashlib.sha256(zip_bytes).hexdigest()
    monkeypatch.setitem(UV_ASSETS, ("Windows", "AMD64"), ("uv-x86_64-pc-windows-msvc.zip", sha256))

    resp = MagicMock()
    resp.read.return_value = zip_bytes
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    with (
        patch("tests.installer.shutil.which", return_value=None),
        patch("tests.installer.urllib.request.urlopen", return_value=resp),
        patch.object(bootstrap_action, "run_cmd", return_value=f"uv {UV_VERSION} (abcd 2024-09-15)"),
    ):
        uv_step.execute(bootstrap_action, args_mock)

    installed = Path(bootstrap_action.data_folder) / "bin" / "uv.exe"
    assert installed.exists()
    assert installed.read_bytes() == payload


@pytest.mark.unit
def test_sha256_mismatch_fails_fast_without_retry(uv_step, bootstrap_action, args_mock, linux_x86_64, monkeypatch):
    """SHA256 mismatch is deterministic — a corp proxy serving the wrong file
    won't fix itself on retry. Fail fast so the user sees the real error."""
    wrong_bytes = _make_tarball("uv-x86_64-unknown-linux-gnu/uv", b"garbage")
    # UV_ASSETS has the real hash, so the wrong-bytes payload won't match.

    resp = MagicMock()
    resp.read.return_value = wrong_bytes
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    with (
        patch("tests.installer.shutil.which", return_value=None),
        patch("tests.installer.urllib.request.urlopen", return_value=resp) as urlopen_mock,
        patch.object(bootstrap_action, "run_cmd"),
        pytest.raises(InstallerError, match="SHA256 mismatch"),
    ):
        uv_step.execute(bootstrap_action, args_mock)

    assert urlopen_mock.call_count == 1
    assert not (Path(bootstrap_action.data_folder) / "bin" / "uv").exists()


@pytest.mark.unit
def test_download_failure_retries_then_fails(uv_step, bootstrap_action, args_mock, linux_x86_64):
    from tests.installer import UV_DOWNLOAD_RETRIES

    with (
        patch("tests.installer.shutil.which", return_value=None),
        patch(
            "tests.installer.urllib.request.urlopen",
            side_effect=OSError("network down"),
        ) as urlopen_mock,
        patch.object(bootstrap_action, "run_cmd"),
        # Skip the exponential backoff sleeps so the test runs instantly.
        patch("tests.installer.time.sleep") as sleep_mock,
        pytest.raises(InstallerError, match="Failed to bootstrap uv"),
    ):
        uv_step.execute(bootstrap_action, args_mock)

    assert urlopen_mock.call_count == UV_DOWNLOAD_RETRIES
    # Backoff sleeps fire between retries, not after the final failure.
    assert sleep_mock.call_count == UV_DOWNLOAD_RETRIES - 1


@pytest.mark.unit
def test_unsupported_platform_raises(uv_step, bootstrap_action, args_mock, monkeypatch, console_msg_mock):
    monkeypatch.setattr("tests.installer.platform.system", lambda: "SunOS")
    monkeypatch.setattr("tests.installer.platform.machine", lambda: "sparc64")

    with (
        patch("tests.installer.shutil.which", return_value=None),
        patch.object(bootstrap_action, "run_cmd"),
        pytest.raises(AbortAction),
    ):
        uv_step.execute(bootstrap_action, args_mock)

    console_msg_mock.assert_any_msg_contains("No prebuilt uv binary available")
