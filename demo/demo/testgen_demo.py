from demo_helper import *

JOURNEY_NAME: str = "Database Watcher"
COMPONENT_NAME: str = "default"
COMPONENT_KEY: str =  "0ea85e17-acbe-47fe-8394-9970725ad37d"


def run_tg_demo(config: Config) -> None:
    print(f"Adding journey for {JOURNEY_NAME}..")
    delete_journey(config, JOURNEY_NAME)
    delete_component(config, COMPONENT_NAME)

    component_id = create_dataset_component(config, COMPONENT_KEY)
    journey_id = create_data_journey(config, JOURNEY_NAME)
    link_components_in_journey(
        config,
        journey_id,
        left_component_id=None,
        right_component_id=component_id,
    )


def delete_tg_demo(config: Config) -> None:
    print(f"Deleting journey {JOURNEY_NAME}..")
    delete_journey(config, JOURNEY_NAME)
    print(f"Deleting component {COMPONENT_NAME}..")
    delete_component(config, COMPONENT_NAME)
