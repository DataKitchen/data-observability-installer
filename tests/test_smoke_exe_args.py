"""The exe smoke test drives the installer through its CLI, and only runs on a merge to
main. Parsing its argv against the real parser here turns a 25-minute round trip into a
2-second one -- the first run of that job failed because ``--no-analytics`` is a top-level
flag that cannot follow the product name.
"""

import pytest

from .e2e.smoke_exe import DELETE_ARGS, INSTALL_ARGS
from .installer import get_installer_instance


@pytest.mark.unit
def test_smoke_install_args_are_accepted():
    args = get_installer_instance().parser.parse_args(list(INSTALL_ARGS))

    assert args.prod == "tg"
    assert args.install_mode == "pip"
    # The demo step is skipped on purpose: it is `required = False`, so it could not gate
    # the release anyway, and it is the longest part of the install.
    assert args.generate_demo is False


@pytest.mark.unit
def test_smoke_delete_args_are_accepted():
    args = get_installer_instance().parser.parse_args(list(DELETE_ARGS))

    assert args.prod == "tg"
    # Nothing is kept: the smoke test asserts the uninstall left nothing behind.
    assert args.keep_data is False
    assert args.keep_images is False
