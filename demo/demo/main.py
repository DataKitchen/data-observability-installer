import signal

from demo_helper import Config
from observability_demo import run_obs_demo, delete_obs_demo
from heartbeat_demo import run_heartbeat_demo
from testgen_demo import run_tg_demo, delete_tg_demo
from argparse import ArgumentParser


def init_parser() -> ArgumentParser:
    parser = ArgumentParser(description="This is a tool to create a demo of DataOps Observability & TestGen.")
    subparsers = parser.add_subparsers(title="subcommands", required=True)

    commands = (
        ("obs-run-demo", "Run the Observability demo", run_obs_demo),
        ("obs-delete-demo", "Delete data created by the Observability demo", delete_obs_demo),
        ("obs-heartbeat-demo", "Run the Observability Heartbeat demo", run_heartbeat_demo),
        ("tg-run-demo", "Run the TestGen demo", run_tg_demo),
        ("tg-delete-demo", "Delete data created by the TestGen demo", delete_tg_demo),
    )

    for cmd, desc, func in commands:
        sub_parser = subparsers.add_parser(cmd, description=desc)
        sub_parser.set_defaults(func=func)

    return parser


def init_signal_handler():
    def _keyboard_interrupt(_signum, _frame):
        raise KeyboardInterrupt

    # Docker sends SIGTERM on Ctrl-C, so we raise a KeyboardInterrupt
    signal.signal(signal.SIGTERM, _keyboard_interrupt)


def main():
    init_signal_handler()
    args = init_parser().parse_args()
    config = Config()
    args.func(config)


if __name__ == "__main__":
    main()
