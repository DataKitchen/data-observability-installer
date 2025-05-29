from functools import partial
from pathlib import Path
from unittest.mock import call, patch

import pytest

from tests.installer import ObsInstallAction


@pytest.fixture
def obs_install_action(action_cls, args_mock, tmp_data_folder, start_cmd_mock):
    action = ObsInstallAction()
    args_mock.prod = "obs"
    args_mock.action = "install"
    with patch.object(action, "execute", new=partial(action.execute, args_mock)):
        yield action


@pytest.mark.integration
def test_obs_install(obs_install_action, start_cmd_mock, tmp_data_folder):
    obs_install_action.execute()

    def_call = partial(call, raise_on_non_zero=True, env=None)

    start_cmd_mock.assert_has_calls(
        [
            def_call("minikube", "-p", "dk-observability", "status", "-o", "json", raise_on_non_zero=False),
            def_call("docker", "network", "inspect", "datakitchen-network"),
            def_call(
                "minikube",
                "start",
                "--memory=4096m",
                "--profile=dk-observability",
                "--namespace=datakitchen",
                "--driver=docker",
                "--kubernetes-version=v1.32.0",
                "--network=datakitchen-network",
                "--static-ip=192.168.60.5",
                "--embed-certs",
                "--extra-config=apiserver.service-node-port-range=1-65535",
                "--extra-config=kubelet.allowed-unsafe-sysctls=net.core.somaxconn",
            ),
            def_call(
                "helm",
                "repo",
                "add",
                "datakitchen",
                "https://datakitchen.github.io/dataops-observability/",
                "--force-update",
            ),
            def_call("helm", "repo", "update"),
            def_call(
                "helm",
                "install",
                "dataops-observability-services",
                "datakitchen/dataops-observability-services",
                "--namespace=datakitchen",
                "--create-namespace",
                "--wait",
                "--timeout=10m",
            ),
            def_call(
                "helm",
                "install",
                "dataops-observability-app",
                "datakitchen/dataops-observability-app",
                "--namespace=datakitchen",
                "--create-namespace",
                "--wait",
                "--timeout=10m",
            ),
            def_call(
                "minikube",
                "kubectl",
                "--profile",
                "dk-observability",
                "--",
                "--namespace",
                "datakitchen",
                "exec",
                "-i",
                "deployments/agent-api",
                "--",
                "/dk/bin/cli",
                "init",
                "--demo",
                "--json",
            ),
            def_call("minikube", "profile", "dk-observability"),
        ],
        any_order=True,
    )

    assert Path(tmp_data_folder).joinpath("dk-obs-credentials.txt").stat().st_size > 0
