import json
from pathlib import Path

import pytest

from tests.installer import (
    CREDENTIALS_FILE,
    INSTALL_MARKER_FILE,
    INSTALL_MODE_DOCKER,
    INSTALL_MODE_PIP,
    InstallMarker,
    TESTGEN_COMPOSE_FILE,
)


@pytest.fixture
def data_folder(tmp_data_folder):
    return Path(tmp_data_folder)


@pytest.mark.unit
def test_read_install_mode_returns_none_when_empty(data_folder):
    assert InstallMarker(data_folder, "tg", "docker-compose.yml").read() is None


@pytest.mark.unit
@pytest.mark.parametrize("install_mode", [INSTALL_MODE_DOCKER, INSTALL_MODE_PIP])
def test_read_install_mode_from_marker(data_folder, install_mode):
    (data_folder / INSTALL_MARKER_FILE.format("tg")).write_text(json.dumps({"install_mode": install_mode}))

    assert InstallMarker(data_folder, "tg", "docker-compose.yml").read() == install_mode


@pytest.mark.unit
def test_read_install_mode_legacy_docker_backfill(data_folder):
    (data_folder / TESTGEN_COMPOSE_FILE).write_text("version: '3'")
    (data_folder / CREDENTIALS_FILE.format("tg")).write_text("admin\n")

    assert InstallMarker(data_folder, "tg", "docker-compose.yml").read() == INSTALL_MODE_DOCKER


@pytest.mark.unit
def test_read_install_mode_legacy_backfill_honors_compose_file_name(data_folder):
    # Verifies the marker isn't TestGen-specific: pass a different product's
    # compose file name and it should detect that product's legacy install.
    (data_folder / "obs-docker-compose.yml").write_text("version: '3'")
    (data_folder / CREDENTIALS_FILE.format("obs")).write_text("admin\n")

    assert InstallMarker(data_folder, "obs", "obs-docker-compose.yml").read() == INSTALL_MODE_DOCKER
    # And does NOT match if we point at the wrong compose file name.
    assert InstallMarker(data_folder, "obs", "docker-compose.yml").read() is None


@pytest.mark.unit
def test_read_install_mode_legacy_requires_both_files(data_folder):
    # Only compose file, missing credentials → not a legacy install
    (data_folder / TESTGEN_COMPOSE_FILE).write_text("version: '3'")

    assert InstallMarker(data_folder, "tg", "docker-compose.yml").read() is None


@pytest.mark.unit
def test_read_install_mode_malformed_marker_falls_back_to_legacy(data_folder):
    (data_folder / INSTALL_MARKER_FILE.format("tg")).write_text("{not valid json")
    (data_folder / TESTGEN_COMPOSE_FILE).write_text("version: '3'")
    (data_folder / CREDENTIALS_FILE.format("tg")).write_text("admin\n")

    assert InstallMarker(data_folder, "tg", "docker-compose.yml").read() == INSTALL_MODE_DOCKER


@pytest.mark.unit
def test_read_install_mode_unknown_value_falls_back(data_folder):
    (data_folder / INSTALL_MARKER_FILE.format("tg")).write_text(json.dumps({"install_mode": "bogus"}))

    assert InstallMarker(data_folder, "tg", "docker-compose.yml").read() is None


@pytest.mark.unit
def test_write_install_marker_round_trip(data_folder):
    InstallMarker(data_folder, "tg", "docker-compose.yml").write(
        INSTALL_MODE_PIP, version="5.9.4", python_version="3.13.1"
    )

    data = json.loads((data_folder / INSTALL_MARKER_FILE.format("tg")).read_text())
    assert data["install_mode"] == INSTALL_MODE_PIP
    assert data["version"] == "5.9.4"
    assert data["python_version"] == "3.13.1"
    assert "created_on" in data
    assert "last_updated_on" in data
    assert InstallMarker(data_folder, "tg", "docker-compose.yml").read() == INSTALL_MODE_PIP


@pytest.mark.unit
def test_write_install_marker_preserves_created_on_across_writes(data_folder):
    InstallMarker(data_folder, "tg").write(INSTALL_MODE_PIP, version="5.9.4")
    initial = json.loads((data_folder / INSTALL_MARKER_FILE.format("tg")).read_text())

    InstallMarker(data_folder, "tg").write(INSTALL_MODE_PIP, version="5.10.0")
    after = json.loads((data_folder / INSTALL_MARKER_FILE.format("tg")).read_text())

    assert after["created_on"] == initial["created_on"]
    assert after["version"] == "5.10.0"


@pytest.mark.unit
def test_write_install_marker_rejects_unknown_type(data_folder):
    with pytest.raises(ValueError, match="Unknown install_mode"):
        InstallMarker(data_folder, "tg").write("sideways")


@pytest.mark.unit
def test_marker_unlink_removes_file(data_folder):
    InstallMarker(data_folder, "tg").write(INSTALL_MODE_PIP)
    assert (data_folder / INSTALL_MARKER_FILE.format("tg")).exists()

    InstallMarker(data_folder, "tg").unlink()
    assert not (data_folder / INSTALL_MARKER_FILE.format("tg")).exists()


@pytest.mark.unit
def test_marker_unlink_is_idempotent(data_folder):
    # No marker present — unlink should be a no-op, not raise.
    InstallMarker(data_folder, "tg").unlink()
