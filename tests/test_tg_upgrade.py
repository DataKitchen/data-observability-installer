import json
import textwrap
from unittest.mock import call

import pytest

from tests.installer import (
    INSTALL_MODE_DOCKER,
    AbortAction,
    CommandFailed,
    TESTGEN_MAJOR_VERSION,
    TESTGEN_STOP_GRACE_PERIOD,
    TestgenUpgradeAction,
    find_in_block,
    InstallMarker,
)


@pytest.fixture
def tg_upgrade_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock, request):
    action = TestgenUpgradeAction()
    args_mock.prod = "tg"
    args_mock.action = "upgrade"
    # Seed a Docker install marker so the unified upgrade resolves to Docker mode.
    InstallMarker(action.data_folder, args_mock.prod).write(INSTALL_MODE_DOCKER)
    action._resolved_mode = INSTALL_MODE_DOCKER
    action.steps = action.docker_steps
    yield action


@pytest.fixture
def tg_upgrade_stdout_side_effect(stdout_mock):
    side_effect = [
        # Pre-execute calls
        ["TestGen 1.0.0\n"],  # Version check
        # Execute calls
        [],  # Down
        [],  # Pull
        [],  # Up
        [],  # Upgrade DB
        # Post-execute calls
        ["TestGen 1.1.0\n"],  # Confirmation version check
        ["[]"],  # Image data collection
    ]

    stdout_mock.side_effect = side_effect
    yield side_effect


def get_compose_content(*extra_vars, stop_grace=False):
    """A compose file as an older installer would have written it.

    ``stop_grace`` opts into the engine grace period, i.e. a file already current
    in that respect — leave it off to model the installs the upgrade has to patch.
    """
    template = textwrap.dedent("""
        name: testgen

        x-common-variables: &common-variables
          TESTGEN_USERNAME: admin
          TESTGEN_PASSWORD: WOzviKBQJS50
          TG_DECRYPT_SALT: zyIJQsuBImx5
          TG_DECRYPT_PASSWORD: cAEGUVRwxvVg
          TG_JWT_HASHING_KEY: VGVzdEdlbgo=
          TG_METADATA_DB_HOST: postgres
          TG_TARGET_DB_TRUST_SERVER_CERTIFICATE: yes
          TG_EXPORT_TO_OBSERVABILITY_VERIFY_SSL: no
        {}

        services:
          engine:
            image: datakitchen/dataops-testgen:v2.14.5
        {}
    """)

    grace = f"    stop_grace_period: {TESTGEN_STOP_GRACE_PERIOD}s\n" if stop_grace else ""
    return template.format(textwrap.indent("\n".join(extra_vars), "  "), grace)


def set_version_check_mock(version_check_mock, latest_version):
    version_check_mock.return_value.code = 200
    version_values = {"docker": {"datakitchen/dataops-testgen": latest_version}}
    version_check_mock.return_value.read.return_value = json.dumps(version_values).encode("utf-8")


@pytest.mark.integration
def test_tg_upgrade_compose_missing(tg_upgrade_action, args_mock, start_cmd_mock, console_msg_mock):
    start_cmd_mock.__exit__.side_effect = [None, None, None, CommandFailed]

    with pytest.raises(AbortAction, match=""):
        tg_upgrade_action.check_requirements(args_mock)

    console_msg_mock.assert_any_msg_contains("TestGen's Docker configuration file is not available")


@pytest.mark.integration
@pytest.mark.parametrize(
    "skip_verify, latest_version",
    ((True, "1.0.0"), (False, "1.1.0")),
)
def test_tg_upgrade(
    skip_verify,
    latest_version,
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
    version_check_mock,
):
    args_mock.skip_verify = skip_verify
    set_version_check_mock(version_check_mock, latest_version)
    compose_path.write_text(get_compose_content())

    tg_upgrade_action.execute(args_mock)

    compose_args = ("docker", "compose", "-f", compose_path)
    compose_kwargs = dict(raise_on_non_zero=True, env=None)
    start_cmd_mock.assert_has_calls(
        [
            call(*compose_args, "exec", "engine", "testgen", "--help", **compose_kwargs),
            call(*compose_args, "down", **compose_kwargs),
            call(*compose_args, "pull", "--policy", "always"),
            call(*compose_args, "up", "--wait", **compose_kwargs),
            call(*compose_args, "exec", "engine", "testgen", "upgrade-system-version", **compose_kwargs),
        ],
        any_order=True,
    )

    compose_content = compose_path.read_text()

    assert f"image: datakitchen/dataops-testgen:v{TESTGEN_MAJOR_VERSION}" in compose_content
    assert "TG_INSTANCE_ID:" in compose_content


@pytest.mark.integration
def test_tg_upgrade_abort(
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
    version_check_mock,
):
    args_mock.skip_verify = False
    set_version_check_mock(version_check_mock, "1.0.0")
    initial_compose_content = get_compose_content(
        "TG_INSTANCE_ID: test-instance-id", "TG_UI_BASE_URL: http://localhost:8501", stop_grace=True
    )
    compose_path.write_text(initial_compose_content)

    with pytest.raises(AbortAction):
        tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    assert compose_content == initial_compose_content
    assert start_cmd_mock.call_count == 1


@pytest.mark.integration
@pytest.mark.parametrize("re_enable", (False, True))
def test_tg_upgrade_enable_analytics(
    re_enable,
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
    console_msg_mock,
    analytics_mock,
    version_check_mock,
):
    set_version_check_mock(version_check_mock, "1.0.0")
    compose_path.write_text(get_compose_content("TG_ANALYTICS: no" if re_enable else ""))
    analytics_mock.get_instance_id.return_value = "test-instance-id"

    tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    assert "TG_INSTANCE_ID: test-instance-id" in compose_content
    assert ("TG_ANALYTICS: no" in compose_content) is re_enable
    assert "image: datakitchen/dataops-testgen:v2.14.5" in compose_content
    console_msg_mock.assert_any_msg_contains("Application is already up-to-date.")


@pytest.mark.integration
@pytest.mark.parametrize("explicitly_enabled", (False, True))
def test_tg_upgrade_disable_analytics(
    explicitly_enabled,
    tg_upgrade_action,
    compose_path,
    tg_upgrade_stdout_side_effect,
    args_mock,
    console_msg_mock,
    version_check_mock,
):
    args_mock.send_analytics_data = False
    set_version_check_mock(version_check_mock, "1.0.0")
    compose_path.write_text(
        get_compose_content("TG_INSTANCE_ID: test-instance-id", "TG_ANALYTICS: yes" if explicitly_enabled else "")
    )

    tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    assert "TG_INSTANCE_ID: test-instance-id" in compose_content
    assert "TG_ANALYTICS: no" in compose_content
    assert "image: datakitchen/dataops-testgen:v2.14.5" in compose_content
    console_msg_mock.assert_any_msg_contains("Application is already up-to-date.")


@pytest.mark.integration
def test_tg_upgrade_adds_base_url(
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
    version_check_mock,
):
    set_version_check_mock(version_check_mock, "1.0.0")
    compose_path.write_text(get_compose_content("TG_INSTANCE_ID: test-instance-id"))

    tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    assert "TG_UI_BASE_URL: http://localhost:8501" in compose_content


@pytest.mark.integration
def test_tg_upgrade_preserves_existing_base_url(
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
    version_check_mock,
):
    args_mock.skip_verify = True
    set_version_check_mock(version_check_mock, "1.1.0")
    compose_path.write_text(
        get_compose_content("TG_INSTANCE_ID: test-instance-id", "TG_UI_BASE_URL: https://custom.example.com")
    )

    tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    assert "TG_UI_BASE_URL: https://custom.example.com" in compose_content
    assert compose_content.count("TG_UI_BASE_URL") == 1


@pytest.mark.integration
def test_tg_upgrade_adds_stop_grace_period(
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
    version_check_mock,
):
    """Existing installs keep their compose file forever — the upgrade is the only
    chance to give them the grace period a running job needs to checkpoint."""
    set_version_check_mock(version_check_mock, "1.0.0")
    compose_path.write_text(get_compose_content("TG_INSTANCE_ID: test-instance-id"))

    tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    lines = compose_content.splitlines()
    image_idx = next(i for i, line in enumerate(lines) if "image: datakitchen/dataops-testgen" in line)
    grace_idx = next(i for i, line in enumerate(lines) if "stop_grace_period" in line)
    # Inside the engine service, right under its image: two comment lines, then the key.
    assert grace_idx == image_idx + 3
    indent = lines[image_idx][: -len(lines[image_idx].lstrip())]
    assert lines[grace_idx] == f"{indent}stop_grace_period: {TESTGEN_STOP_GRACE_PERIOD}s"


@pytest.mark.integration
def test_tg_upgrade_preserves_existing_stop_grace_period(
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
    version_check_mock,
):
    """A user who tuned the value keeps it, and repeated upgrades don't stack duplicates."""
    args_mock.skip_verify = True
    set_version_check_mock(version_check_mock, "1.1.0")
    compose_path.write_text(
        get_compose_content("TG_INSTANCE_ID: test-instance-id").replace(
            "image: datakitchen/dataops-testgen:v2.14.5",
            "image: datakitchen/dataops-testgen:v2.14.5\n    stop_grace_period: 300s",
        )
    )

    tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    assert "stop_grace_period: 300s" in compose_content
    assert compose_content.count("stop_grace_period") == 1


@pytest.mark.integration
def test_tg_upgrade_adds_stop_grace_period_to_custom_image(
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
    version_check_mock,
):
    """``tg install --image`` accepts a private mirror, so the anchor can't assume the
    image is a datakitchen one — those installs need the grace period just as much."""
    args_mock.skip_verify = True
    set_version_check_mock(version_check_mock, "1.1.0")
    compose_path.write_text(
        get_compose_content("TG_INSTANCE_ID: test-instance-id").replace(
            "datakitchen/dataops-testgen:v2.14.5", "registry.internal.example.com/mirror/testgen:v2.14.5"
        )
    )

    tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    lines = compose_content.splitlines()
    image_idx = next(i for i, line in enumerate(lines) if "image:" in line)
    grace_idx = next(i for i, line in enumerate(lines) if "stop_grace_period" in line)
    assert grace_idx == image_idx + 3
    assert lines[grace_idx].strip() == f"stop_grace_period: {TESTGEN_STOP_GRACE_PERIOD}s"


@pytest.mark.integration
def test_tg_upgrade_ignores_stop_grace_period_on_another_service(
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
    version_check_mock,
):
    """A grace period set on postgres says nothing about the service that runs the
    scheduler — the engine must still get its own."""
    args_mock.skip_verify = True
    set_version_check_mock(version_check_mock, "1.1.0")
    compose_path.write_text(
        # The stray comment matters too: a mention anywhere else in the file must not make
        # the engine look already-patched.
        "# note: stop_grace_period is managed by the installer\n"
        + get_compose_content("TG_INSTANCE_ID: test-instance-id")
        + "\n  postgres:\n    image: postgres:14.1-alpine\n    stop_grace_period: 30s\n"
    )

    tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    engine_block, _, postgres_block = compose_content.partition("  postgres:")
    assert f"stop_grace_period: {TESTGEN_STOP_GRACE_PERIOD}s" in engine_block
    # The user's postgres value is left exactly as they set it.
    assert "stop_grace_period: 30s" in postgres_block
    # engine's, postgres', and the stray comment.
    assert compose_content.count("stop_grace_period") == 3


COMPOSE_TWO_SERVICES = """name: testgen
# a stray mention of stop_grace_period above the services section
services:
  engine:
    image: datakitchen/dataops-testgen:v5
    depends_on:
      postgres:
        condition: service_healthy

  postgres:
    image: postgres:14.1-alpine
    stop_grace_period: 30s
"""


@pytest.mark.unit
@pytest.mark.parametrize(
    "block, key, expected",
    (
        ("engine", "image", "image: datakitchen/dataops-testgen:v5"),
        ("postgres", "image", "image: postgres:14.1-alpine"),
        ("postgres", "stop_grace_period", "stop_grace_period: 30s"),
        # Scoping is the whole point: postgres' grace period is not the engine's, and a
        # mention in a comment above `services:` is not a setting on any service.
        ("engine", "stop_grace_period", None),
        ("engine", "nonexistent", None),
        ("nonexistent", "image", None),
    ),
)
def test_find_in_block_is_scoped_to_the_block(block, key, expected):
    match = find_in_block(COMPOSE_TWO_SERVICES, block, key)
    assert (match.group(0).strip() if match else None) == expected


@pytest.mark.unit
def test_find_in_block_offsets_are_absolute():
    """Callers splice around the match, so its offsets must index the whole file."""
    match = find_in_block(COMPOSE_TWO_SERVICES, "postgres", "image")
    assert COMPOSE_TWO_SERVICES[match.start() : match.end()] == "    image: postgres:14.1-alpine"
    assert match.group(1) == "    "
