import re
from pathlib import Path
from ssl import SSLContext

import pytest
from unittest.mock import patch, ANY
from .installer import (
    AbortAction,
    AbortReason,
    AnalyticsWrapper,
    CommandFailed,
    InstallerError,
    get_command_name,
)


@pytest.fixture
def urlopen_mock():
    with patch("urllib.request.urlopen") as mock:
        yield mock


@pytest.fixture
def analytics_wrapper(action, args_mock):
    action.registered_prod = args_mock.prod
    yield AnalyticsWrapper(action, args_mock)


@pytest.fixture
def sending():
    """Lift the guard that keeps the installer from sending events while pytest runs."""
    with patch("tests.installer.running_a_test", return_value=False):
        yield


@pytest.fixture
def instance_id_mock(analytics_wrapper):
    with patch.object(analytics_wrapper, "get_instance_id", return_value="test-instance-id") as mock:
        yield mock


@pytest.mark.integration
def test_send_on_exit(analytics_wrapper, action, urlopen_mock, instance_id_mock, sending):
    with analytics_wrapper:
        urlopen_mock.assert_not_called()
        assert hasattr(action, "analytics")

    urlopen_mock.assert_called_once()
    req_call = urlopen_mock.call_args_list[0]
    assert req_call.args[0].full_url == "https://api.mixpanel.com/track?ip=1"
    assert req_call.args[0].method == "POST"
    assert isinstance(req_call.kwargs["context"], SSLContext)
    assert req_call.kwargs["timeout"] == 3


@pytest.mark.integration
def test_exception_handling(analytics_wrapper, action, urlopen_mock, instance_id_mock, sending):
    urlopen_mock.side_effect = RuntimeError
    with analytics_wrapper:
        pass

    urlopen_mock.assert_called_once()


@pytest.mark.integration
def test_event_data(analytics_wrapper, action, urlopen_mock, instance_id_mock, sending):
    with patch.object(analytics_wrapper, "send_mp_request") as mp_req_mock:
        with analytics_wrapper:
            analytics_wrapper.additional_properties["ap"] = "additional"

    mp_req_mock.assert_called_once_with(
        "track?ip=1",
        {
            "event": "test_prod-test",
            "properties": {
                "token": ANY,
                "prod": "test_prod",
                "action": "test",
                "ap": "additional",
                "elapsed": ANY,
                "os_version": ANY,
                "os_arch": ANY,
                "$os": ANY,
                "python_info": ANY,
                "installer_version": ANY,
                "is_exe": False,
                "distinct_id": ANY,
                "instance_id": ANY,
            },
        },
    )


@pytest.mark.integration
def test_get_instance_id(analytics_wrapper, tmp_logs_folder):
    Path(tmp_logs_folder, "instance.txt").write_text("some-instance-id")
    instance_id = analytics_wrapper.get_instance_id()
    assert instance_id == "some-instance-id"


@pytest.mark.integration
def test_get_instance_id_create(analytics_wrapper, tmp_logs_folder):
    instance_id = analytics_wrapper.get_instance_id()
    assert len(instance_id) == 16
    assert Path(tmp_logs_folder, "instance.txt").exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    "instance_id,expected_hash", (("inst-1", "5f0c3843f9c1f38d"), ("inst-other", "1f59818ab9e8ce51"))
)
def test_hash_is_unique_per_instance(instance_id, expected_hash, analytics_wrapper, instance_id_mock):
    instance_id_mock.return_value = instance_id

    assert analytics_wrapper._hash_value("anything") == expected_hash
    assert analytics_wrapper._hash_value(b"anything") == expected_hash


@pytest.mark.integration
def test_no_event_under_pytest(analytics_wrapper, urlopen_mock, instance_id_mock):
    with analytics_wrapper:
        pass

    urlopen_mock.assert_not_called()


@pytest.mark.integration
def test_no_event_for_an_unregistered_command(analytics_wrapper, action, urlopen_mock, instance_id_mock, sending):
    action.registered_prod = None
    with analytics_wrapper:
        pass

    urlopen_mock.assert_not_called()


@pytest.mark.integration
def test_failed_event_carries_no_message_text(analytics_wrapper, instance_id_mock, sending):
    cmd = ("C:\\Users\\Jane Doe\\.local\\bin\\testgen.exe", "standalone-setup", "--password", "s3cret")
    failure = CommandFailed(3, " ".join(cmd), 1, get_command_name(cmd))
    with patch.object(analytics_wrapper, "send_mp_event") as send_mock:
        with pytest.raises(InstallerError):
            with analytics_wrapper:
                raise InstallerError("Failed step: TestgenStandaloneSetupStep") from failure

    event_name, properties = send_mock.call_args.args
    assert event_name == "failed"
    assert properties["error_types"] == ["InstallerError", "CommandFailed"]
    assert properties["failed_cmd"] == "testgen standalone-setup"
    assert properties["cmd_exit_code"] == 1
    assert "error" not in properties
    assert not any("Jane" in str(value) or "s3cret" in str(value) for value in properties.values())


@pytest.mark.integration
def test_abort_reason_comes_from_the_chain(analytics_wrapper, instance_id_mock, sending):
    """Most sites raise their AbortAction while handling a CommandFailed, and steps wrap it again."""
    with patch.object(analytics_wrapper, "send_mp_event") as send_mock:
        with pytest.raises(AbortAction):
            with analytics_wrapper:
                try:
                    try:
                        raise CommandFailed(1, "docker compose ls", 1, "docker compose")
                    except CommandFailed:
                        raise AbortAction(reason=AbortReason.DELETE_FAILED)
                except AbortAction as e:
                    raise AbortAction("Step pre-execute aborted") from e

    event_name, properties = send_mock.call_args.args
    assert event_name == "aborted"
    assert properties["abort_reason"] == AbortReason.DELETE_FAILED
    assert properties["failed_cmd"] == "docker compose"


@pytest.mark.unit
def test_every_abort_has_a_reason():
    """The step wrappers re-raise an AbortAction whose cause carries the reason."""
    source = Path(__file__).with_name("installer.py").read_text()
    without_reason = [
        line.strip()
        for line in source.splitlines()
        if re.match(r"\s*raise AbortAction\b(?!\(reason=)", line) and "pre-execute aborted" not in line
    ]
    assert not without_reason


@pytest.mark.integration
def test_send_success_sends_once(analytics_wrapper, instance_id_mock, sending):
    """Pip mode reports success when the app is ready, then keeps running until the user stops it."""
    with patch.object(analytics_wrapper, "send_mp_event") as send_mock:
        with pytest.raises(AbortAction):
            with analytics_wrapper:
                analytics_wrapper.send_success()
                raise AbortAction(reason=AbortReason.INTERRUPTED)

    send_mock.assert_called_once()
    assert send_mock.call_args.args[0] == "test_prod-test"


@pytest.mark.unit
@pytest.mark.parametrize(
    "cmd,expected",
    (
        (("/Users/jane/.local/bin/uv", "tool", "upgrade", "--no-cache", "dataops-testgen"), "uv tool upgrade"),
        (("docker", "compose", "-f", "/home/jane/docker-compose.yml", "up", "--wait"), "docker compose"),
        (("C:\\Users\\Jane Doe\\bin\\testgen.exe", "quick-start"), "testgen quick-start"),
        (("docker", "manifest", "inspect", "datakitchen/dataops-testgen:v5"), "docker manifest inspect"),
    ),
)
def test_command_name_holds_no_paths_or_arguments(cmd, expected):
    assert get_command_name(cmd) == expected
