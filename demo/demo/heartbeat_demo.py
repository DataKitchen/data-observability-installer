from demo_helper import *


def run_heartbeat_demo(config: Config) -> None:
    tool_list = set(["airflow"])

    journey_component_list = [
        NIGHTLY_EXPORTS_COMPONENTS,
        DAILY_DATA_LOAD_COMPONENTS,
        DASHBOARD_COMPONENTS,
    ]
    for journey_component_data in journey_component_list:
        component_list = journey_component_data.get(config.cloud_provider)
        component_tools = [tool for _name, tool in component_list]
        tool_list.update(component_tools)

    print(f"Agents: {'_Agent, '.join(tool_list)}_Agent")
    print("Sending heartbeats every 25 seconds..")

    print("")
    print("Keep this process running to continue heartbeat")
    print("Press Ctrl + C to stop")

    send_heartbeat = True
    while send_heartbeat:
        try:
          for tool in tool_list:
              set_component_heartbeat(config, f"{tool}_Agent", tool, "0.1")
          time.sleep(25)
        except KeyboardInterrupt:
          send_heartbeat = False
      