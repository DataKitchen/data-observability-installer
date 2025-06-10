import textwrap
from unittest.mock import call

import pytest

from tests.installer import AbortAction, CommandFailed, TestgenUpgradeAction, TESTGEN_LATEST_TAG


@pytest.fixture
def tg_upgrade_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock, request):
    action = TestgenUpgradeAction()
    args_mock.prod = "tg"
    args_mock.action = "upgrade"
    yield action


@pytest.fixture
def tg_upgrade_stdout_side_effect(stdout_mock):
    side_effect = [
        # Pre-execute calls
        [b"This version: 1.0.0\n", b"Latest version: 1.1.0\n"],  # Version check
        # Execute calls
        [],  # Down
        [],  # Pull
        [],  # Up
        [],  # Upgrade DB
        # Post-execute calls
        [b"This version: 1.1.0\n", b"Latest version: 1.1.0\n"],  # Confirmation version check
        [b"[]"],  # Image data collection
    ]

    stdout_mock.side_effect = side_effect
    yield side_effect


def get_compose_content(*extra_vars):
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
          TG_DOCKER_RELEASE_CHECK_ENABLED: yes
        {}

        services:
          engine:
            image: datakitchen/dataops-testgen:v2.14.5

    """)

    return template.format(textwrap.indent("\n".join(extra_vars), "  "))


@pytest.mark.integration
def test_tg_upgrade_compose_missing(tg_upgrade_action, args_mock, start_cmd_mock, console_msg_mock):
    start_cmd_mock.__exit__.side_effect = [None, None, CommandFailed]

    with pytest.raises(AbortAction, match=""):
        tg_upgrade_action._check_requirements(args_mock)

    console_msg_mock.assert_any_msg_contains("TestGen's Docker configuration file is not available")


@pytest.mark.integration
def test_tg_upgrade(tg_upgrade_action, compose_path, start_cmd_mock, tg_upgrade_stdout_side_effect, args_mock):
    compose_path.write_text(get_compose_content())

    tg_upgrade_action.execute(args_mock)

    compose_args = ("docker", "compose", "-f", compose_path)
    compose_kwargs = dict(raise_on_non_zero=True, env=None)
    start_cmd_mock.assert_has_calls(
        [
            call(*compose_args, "exec", "engine", "testgen", "--help", **compose_kwargs),
            call(*compose_args, "down", **compose_kwargs),
            call(*compose_args, "pull", "--policy", "always", env=None),
            call(*compose_args, "up", "--wait", **compose_kwargs),
            call(*compose_args, "exec", "engine", "testgen", "upgrade-system-version", **compose_kwargs),
        ],
        any_order=True,
    )

    compose_content = compose_path.read_text()

    assert f"image: datakitchen/dataops-testgen:{TESTGEN_LATEST_TAG}" in compose_content
    assert "TG_INSTANCE_ID:" in compose_content


@pytest.mark.integration
@pytest.mark.parametrize(
    "skip_verify, latest_version",
    ((True, b"1.0.0"), (True, b"1.1.0"), (False, b"1.0.0")),
)
def test_tg_upgrade_abort(
    skip_verify,
    latest_version,
    tg_upgrade_action,
    compose_path,
    start_cmd_mock,
    tg_upgrade_stdout_side_effect,
    args_mock,
):
    args_mock.skip_verify = skip_verify
    tg_upgrade_stdout_side_effect[0][1] = b"Latest version: %b\n" % latest_version
    initial_compose_content = get_compose_content("TG_INSTANCE_ID: test-instance-id")
    compose_path.write_text(initial_compose_content)

    with pytest.raises(AbortAction):
        tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    assert compose_content == initial_compose_content
    assert start_cmd_mock.call_count == 0 if skip_verify else 1


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
):
    tg_upgrade_stdout_side_effect[0][1] = b"Latest version: 1.0.0\n"
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
):
    args_mock.send_analytics_data = False
    tg_upgrade_stdout_side_effect[0][1] = b"Latest version: 1.0.0\n"
    compose_path.write_text(
        get_compose_content("TG_INSTANCE_ID: test-instance-id", "TG_ANALYTICS: yes" if explicitly_enabled else "")
    )

    tg_upgrade_action.execute(args_mock)

    compose_content = compose_path.read_text()
    assert "TG_INSTANCE_ID: test-instance-id" in compose_content
    assert "TG_ANALYTICS: no" in compose_content
    assert "image: datakitchen/dataops-testgen:v2.14.5" in compose_content
    console_msg_mock.assert_any_msg_contains("Application is already up-to-date.")
