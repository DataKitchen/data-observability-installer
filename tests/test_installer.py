import pytest

from .installer import Installer, Action, AbortAction, InstallerError


@pytest.fixture
def installer(action_cls, execute_with_log_mock):
    class ActionOne(Action):
        args_cmd = "one"

        def get_parser(self, sub_parsers):
            parser = super().get_parser(sub_parsers)
            parser.add_argument("--int-arg", type=int, action="store", default=4)
            return parser

    installer = Installer()
    installer.add_product(
        "product",
        [
            ActionOne(),
            type("ActionTwo", (Action,), {"args_cmd": "two"})(),
        ],
    )
    yield installer


@pytest.mark.integration
@pytest.mark.parametrize("arg_val,arg_expected", ((None, 4), (25, 25)))
def test_calls_action(arg_val, arg_expected, installer, execute_with_log_mock):
    ret = installer.run(["product", "one"] + ([] if arg_val is None else [f"--int-arg={arg_val}"]))

    assert ret == 0
    execute_with_log_mock.assert_called_once()
    args = execute_with_log_mock.call_args_list[0].args[0]
    assert args.prod == "product"
    assert args.int_arg == arg_expected


@pytest.mark.integration
@pytest.mark.parametrize(
    "args,expected_in_err",
    (
        (["other", "one"], "invalid choice: 'other'"),
        (["product", "three"], "invalid choice: 'three'"),
        (["product", "one", "no-no-no"], "unrecognized arguments: no-no-no"),
        (["product", "one", "--int-arg=x"], "invalid int value: 'x'"),
    ),
    ids=("invalid product", "invalid action", "invalid argument", "invalid value"),
)
def test_invalid_args(args, expected_in_err, installer, capfd):
    with pytest.raises(SystemExit) as exc_info:
        installer.run(args)

    assert expected_in_err in capfd.readouterr().err
    assert exc_info.value.args == (2,)


@pytest.mark.integration
@pytest.mark.parametrize(
    "exc_class,expected_code",
    ((AbortAction, 1), (InstallerError, 2)),
)
def test_return_codes(exc_class, expected_code, installer, execute_with_log_mock):
    execute_with_log_mock.side_effect = exc_class
    ret = installer.run(["product", "two"])

    assert ret == expected_code
    execute_with_log_mock.assert_called_once()
