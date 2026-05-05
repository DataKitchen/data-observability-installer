import json
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from tests.installer import (
    AbortAction,
    INSTALL_MODE_DOCKER,
    INSTALL_MODE_PIP,
    TESTGEN_MAJOR_VERSION,
    TESTGEN_PYTHON_VERSION,
    TestgenInstallAction,
    InstallMarker,
)


@pytest.fixture
def pip_install_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    """Drive the pip path of TestgenInstallAction end-to-end (steps run)."""
    action = TestgenInstallAction()
    args_mock.prod = "tg"
    args_mock.action = "install"
    args_mock.install_mode = INSTALL_MODE_PIP
    # Bypass mode resolution: pretend check_requirements already ran.
    action._resolved_mode = INSTALL_MODE_PIP
    action.steps = action.pip_steps
    # uv "found" on PATH so UvBootstrapStep doesn't try to download.
    # resolve_testgen_path is mocked to skip the on-disk existence check.
    # start_testgen_app is mocked so the test doesn't actually spawn a
    # subprocess and block waiting for a port.
    with (
        patch("tests.installer.shutil.which", return_value="/usr/local/bin/uv"),
        patch(
            "tests.installer.resolve_testgen_path",
            return_value="/Users/test/.local/bin/testgen",
        ),
        patch("tests.installer.start_testgen_app"),
        patch.object(action, "execute", new=partial(action.execute, args_mock)),
    ):
        yield action


@pytest.fixture
def install_action(action_cls, args_mock, tmp_data_folder):
    """A bare TestgenInstallAction for testing the mode-resolution layer."""
    action = TestgenInstallAction()
    args_mock.prod = "tg"
    args_mock.action = "install"
    args_mock.install_mode = None
    # Replace the action_cls-shared analytics mock with an instance-level one
    # whose additional_properties is a real dict so tests can assert on it.
    action.analytics = MagicMock()
    action.analytics.additional_properties = {}
    return action


@pytest.mark.integration
def test_tg_pip_install_happy_path(pip_install_action, start_cmd_mock, tmp_data_folder):
    pip_install_action.execute()

    expected_constraint = f"dataops-testgen[standalone]>={TESTGEN_MAJOR_VERSION},<{int(TESTGEN_MAJOR_VERSION) + 1}"

    start_cmd_mock.assert_has_calls(
        [
            call(
                "/usr/local/bin/uv",
                "tool",
                "install",
                "--force",
                "--python",
                TESTGEN_PYTHON_VERSION,
                expected_constraint,
                raise_on_non_zero=True,
                env=None,
            ),
            call(
                "/usr/local/bin/uv",
                "tool",
                "update-shell",
                raise_on_non_zero=False,
                env=None,
            ),
        ],
        any_order=True,
    )

    cred_path = Path(tmp_data_folder) / "dk-tg-credentials.txt"
    assert cred_path.exists()
    content = cred_path.read_text()
    assert "Username: admin" in content
    assert "Password:" in content
    assert "http://localhost:8501" in content

    marker_path = Path(tmp_data_folder) / "dk-tg-install.json"
    assert marker_path.exists()
    data = json.loads(marker_path.read_text())
    assert data["install_mode"] == INSTALL_MODE_PIP
    assert "created_on" in data
    assert "last_updated_on" in data


@pytest.mark.integration
def test_tg_pip_install_threads_user_ports_through_standalone_setup(
    pip_install_action, args_mock, start_cmd_mock, tmp_data_folder
):
    from tests.installer import TESTGEN_LOG_FILE_PATH

    args_mock.port = 9000
    args_mock.api_port = 9530
    args_mock.ssl_cert_file = "/etc/ssl/certs/tg.crt"
    args_mock.ssl_key_file = "/etc/ssl/private/tg.key"

    pip_install_action.execute()

    setup_call = next(c for c in start_cmd_mock.call_args_list if "standalone-setup" in c.args)
    assert setup_call.kwargs["env"] == {
        "TG_UI_PORT": "9000",
        "TG_API_PORT": "9530",
        "TESTGEN_LOG_FILE_PATH": str(TESTGEN_LOG_FILE_PATH),
        "SSL_CERT_FILE": "/etc/ssl/certs/tg.crt",
        "SSL_KEY_FILE": "/etc/ssl/private/tg.key",
    }

    cred_path = Path(tmp_data_folder) / "dk-tg-credentials.txt"
    content = cred_path.read_text()

    assert "User Interface: https://localhost:9000" in content
    assert "API & MCP:      https://localhost:9530" in content


@pytest.mark.integration
def test_tg_pip_install_auto_starts_app(pip_install_action, args_mock):
    """After install completes, the app is auto-started so user has a single-command experience."""
    from tests.installer import start_testgen_app as patched_start

    pip_install_action.execute()

    # The fixture patches tests.installer.start_testgen_app — confirm it was called.
    assert patched_start.call_count == 1
    assert patched_start.call_args.args[0] is pip_install_action
    assert patched_start.call_args.args[1] is args_mock


@pytest.mark.integration
def test_tg_pip_install_auto_runs_demo(pip_install_action, start_cmd_mock):
    """A successful pip install also generates demo data so users see something on first launch."""
    pip_install_action.execute()

    start_cmd_mock.assert_any_call(
        "/Users/test/.local/bin/testgen",
        "quick-start",
        raise_on_non_zero=True,
        env=None,
    )


@pytest.mark.integration
def test_tg_pip_install_no_demo_flag_skips_quick_start(pip_install_action, args_mock, start_cmd_mock):
    """--no-demo opts out of the auto-demo step."""
    args_mock.generate_demo = False

    pip_install_action.execute()

    for invocation in start_cmd_mock.call_args_list:
        assert "quick-start" not in invocation.args, f"quick-start should be skipped, got: {invocation}"


@pytest.mark.integration
def test_tg_pip_install_password_redacted_in_logs(pip_install_action, start_cmd_mock):
    """The autogenerated admin password must not appear in the logged cmd_str."""
    pip_install_action.execute()

    # The standalone-setup call should pass redact=(<password>,) so cmd_str gets
    # censored. Verify by inspecting the call kwargs.
    setup_call = next(c for c in start_cmd_mock.call_args_list if "standalone-setup" in c.args)
    redact = setup_call.kwargs.get("redact")
    assert redact and len(redact) == 1
    password = redact[0]
    assert isinstance(password, str) and len(password) >= 8

    # The actual --password argument is still present on the command line — it
    # only gets censored at log/filename time inside start_cmd.
    assert "--password" in setup_call.args
    pw_idx = setup_call.args.index("--password")
    assert setup_call.args[pw_idx + 1] == password


@pytest.mark.integration
@pytest.mark.parametrize("user_input", ["", "p", "pip", "P"])
def test_auto_mode_picks_pip_when_docker_unavailable(install_action, args_mock, console_msg_mock, user_input):
    """Docker probe fails → user accepts the recommended pip default (or types pip explicitly) → resolve to pip."""
    with (
        patch("tests.installer.Requirement.check_availability", return_value=False),
        patch("builtins.input", return_value=user_input),
    ):
        install_action._resolve_install_mode(args_mock)

    assert install_action._resolved_mode == INSTALL_MODE_PIP
    assert install_action.analytics.additional_properties["install_mode"] == INSTALL_MODE_PIP


@pytest.mark.integration
def test_auto_mode_displays_prereq_status_when_docker_unavailable(install_action, args_mock, console_msg_mock):
    """Docker probe fails → the prereq display lists each requirement with a marker and (for failures) a fix hint."""
    # Only the first prereq passes — exercises the mixed pass/fail rendering.
    def selective_check(req_self, *_, **__):
        return req_self.key == "DOCKER"

    with (
        patch("tests.installer.Requirement.check_availability", autospec=True, side_effect=selective_check),
        patch("builtins.input", return_value="p"),
    ):
        install_action._resolve_install_mode(args_mock)

    console_msg_mock.assert_any_msg_contains("two installation modes")
    console_msg_mock.assert_any_msg_contains("Prerequisites:")
    console_msg_mock.assert_any_msg_contains("(✓) Docker installed")
    console_msg_mock.assert_any_msg_contains("(X) Docker engine running")


@pytest.mark.integration
def test_auto_mode_pip_only_prompt_when_docker_unavailable(install_action, args_mock, console_msg_mock):
    """When Docker prereqs fail, the prompt only offers pip and tells the user how to retry with Docker."""
    with (
        patch("tests.installer.Requirement.check_availability", return_value=False),
        patch("builtins.input", return_value="") as input_mock,
    ):
        install_action._resolve_install_mode(args_mock)

    assert install_action._resolved_mode == INSTALL_MODE_PIP
    # The prompt itself shouldn't offer [d] anymore in this branch.
    prompt = input_mock.call_args.args[0]
    assert "[d]" not in prompt
    assert "[p]" in prompt
    console_msg_mock.assert_any_msg_contains("To install with Docker, fix the prerequisites and run the install again.")


@pytest.mark.integration
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, EOFError])
def test_auto_mode_aborts_on_user_interrupt_when_docker_unavailable(install_action, args_mock, interrupt):
    """Docker probe fails → user interrupts the prompt → abort."""
    with (
        patch("tests.installer.Requirement.check_availability", return_value=False),
        patch("builtins.input", side_effect=interrupt),
        pytest.raises(AbortAction),
    ):
        install_action._resolve_install_mode(args_mock)


@pytest.mark.integration
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, EOFError])
def test_auto_mode_prompt_aborts_on_user_interrupt(install_action, args_mock, interrupt):
    with (
        patch("tests.installer.Requirement.check_availability", return_value=True),
        patch("builtins.input", side_effect=interrupt),
        pytest.raises(AbortAction),
    ):
        install_action._resolve_install_mode(args_mock)


@pytest.mark.integration
@pytest.mark.parametrize(
    "user_input,expected",
    [("", INSTALL_MODE_DOCKER), ("d", INSTALL_MODE_DOCKER), ("p", INSTALL_MODE_PIP), ("pip", INSTALL_MODE_PIP)],
)
def test_auto_mode_prompts_when_docker_available_tty(install_action, args_mock, user_input, expected):
    with (
        patch("tests.installer.Requirement.check_availability", return_value=True),
        patch("builtins.input", return_value=user_input),
    ):
        install_action._resolve_install_mode(args_mock)

    assert install_action._resolved_mode == expected


@pytest.mark.integration
def test_dispatcher_routes_to_pip_with_flag(install_action, args_mock):
    args_mock.install_mode = INSTALL_MODE_PIP

    install_action._resolve_install_mode(args_mock)

    assert install_action._resolved_mode == INSTALL_MODE_PIP
    assert install_action.steps == TestgenInstallAction.pip_steps
    assert install_action.analytics.additional_properties["install_mode"] == INSTALL_MODE_PIP


@pytest.mark.integration
def test_dispatcher_routes_to_docker_with_flag(install_action, args_mock):
    args_mock.install_mode = INSTALL_MODE_DOCKER

    install_action._resolve_install_mode(args_mock)

    assert install_action._resolved_mode == INSTALL_MODE_DOCKER
    assert install_action.steps == TestgenInstallAction.docker_steps
    assert install_action.analytics.additional_properties["install_mode"] == INSTALL_MODE_DOCKER


@pytest.mark.integration
@pytest.mark.parametrize("existing", [INSTALL_MODE_DOCKER, INSTALL_MODE_PIP])
def test_dispatcher_aborts_on_existing_install(install_action, args_mock, tmp_data_folder, existing, console_msg_mock):
    InstallMarker(Path(tmp_data_folder), "tg").write(existing)

    with pytest.raises(AbortAction):
        install_action._resolve_install_mode(args_mock)

    console_msg_mock.assert_any_msg_contains("tg upgrade")
    console_msg_mock.assert_any_msg_contains("tg delete")
    console_msg_mock.assert_any_msg_contains(existing)
