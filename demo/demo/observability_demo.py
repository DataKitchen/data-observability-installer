from demo_helper import *


def run_obs_demo(config: Config) -> None:
    print("Adding journey for Nightly Exports (7 hours ago)..")
    create_nightly_export_data_journey(
        config,
        backdate_hours=7,
        length_hours=2,
        create_journey=True,
        failure=False
    )
    print("Adding journey for Nightly Exports (31 hours ago)..")
    create_nightly_export_data_journey(
        config,
        backdate_hours=31,
        length_hours=2,
        create_journey=False,
        failure=True
    )
    print("Adding journey for Nightly Exports (55 hours ago)..")
    create_nightly_export_data_journey(
        config,
        backdate_hours=55,
        length_hours=5,
        create_journey=False,
        failure=True
    )
    
    print("Adding journey for Daily Data Load..")
    create_daily_data_journey(
        config,
        backdate_hours=2,
        length_hours=1
    )

    print("Adding journey for Dashboard and Model Production..")
    create_dashboard_model_journey(
        config,
    )

def delete_obs_demo(config: Config) -> None:
    journey_list = [
        (NIGHTLY_EXPORTS_JOURNEY, NIGHTLY_EXPORTS_COMPONENTS),
        (DAILY_DATA_LOAD_JOURNEY, DAILY_DATA_LOAD_COMPONENTS),
        (DASHBOARD_JOURNEY, DASHBOARD_COMPONENTS),
    ]
    for journey_name, journey_component_data in journey_list:
        print(f"Deleting journey and components for {journey_name}..")
        delete_journey(config, journey_name)

        for _cloud, component_list in journey_component_data.items():
            for component_name, _tool in component_list:
                delete_component(config, component_name)
    