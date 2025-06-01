from pathlib import Path
from ssl import SSLContext

import pytest
from unittest.mock import patch, ANY
from .installer import AnalyticsWrapper


@pytest.fixture
def urlopen_mock():
    with patch("urllib.request.urlopen") as mock:
        yield mock


@pytest.fixture
def analytics_wrapper(action, args_mock):
    yield AnalyticsWrapper(action, args_mock)


@pytest.fixture
def instance_id_mock(analytics_wrapper):
    with patch.object(analytics_wrapper, "get_instance_id", return_value="test-instance-id") as mock:
        yield mock


@pytest.mark.integration
def test_send_on_exit(analytics_wrapper, action, urlopen_mock, instance_id_mock):
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
def test_exception_handling(analytics_wrapper, action, urlopen_mock, instance_id_mock):
    urlopen_mock.side_effect = RuntimeError
    with analytics_wrapper:
        pass

    urlopen_mock.assert_called_once()


@pytest.mark.integration
def test_event_data(analytics_wrapper, action, urlopen_mock, instance_id_mock):
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
