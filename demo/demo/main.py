from demo_helper import Config
from observability_demo import run_obs_demo, delete_obs_demo 
from heartbeat_demo import run_heartbeat_demo
from testgen_demo import run_tg_demo, delete_tg_demo 
from argparse import ArgumentParser


def init_args() -> ArgumentParser:
    parser = ArgumentParser(
        description="""
            This is a tool to create a demo of DataOps Observability & TestGen.
            """,
    )
    subparsers = parser.add_subparsers(title="subcommands")

    obs_run_parser = subparsers.add_parser(
        "obs-run-demo",
        description="Run the Observability demo",
    )
    obs_run_parser.set_defaults(action="obs-run-demo")
    
    obs_delete_parser = subparsers.add_parser(
        "obs-delete-demo",
        description="Delete data created by the Observability demo",
    )
    obs_delete_parser.set_defaults(action="obs-delete-demo")

    obs_heartbeat_parser = subparsers.add_parser(
        "obs-heartbeat-demo",
        description="Run the Observability Heartbeat demo",
    )
    obs_heartbeat_parser.set_defaults(action="obs-heartbeat-demo")

    tg_run_parser = subparsers.add_parser(
        "tg-run-demo",
        description="Run the TestGen demo",
    )
    tg_run_parser.set_defaults(action="tg-run-demo")
    
    tg_delete_parser = subparsers.add_parser(
        "tg-delete-demo",
        description="Delete data created by the TestGen demo",
    )
    tg_delete_parser.set_defaults(action="tg-delete-demo")

    return parser.parse_args()

def main():
    args = init_args()
    config = Config()

    if args.action == "obs-run-demo":
        run_obs_demo(config)
    elif args.action == "obs-delete-demo":
        delete_obs_demo(config)
    elif args.action == "obs-heartbeat-demo":
        run_heartbeat_demo(config)
    elif args.action == "tg-run-demo":
        run_tg_demo(config)
    elif args.action == "tg-delete-demo":
        delete_tg_demo(config)
    else:
        print(f"Command [{args.action}] not recognized.")


if __name__ == "__main__":
    main()