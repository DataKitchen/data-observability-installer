import csv
import json
import requests
import time
from datetime import datetime, timedelta
from pathlib import Path
from random import randint
from typing import Any, Dict, List
from dataclasses import dataclass

from events_ingestion_client import (
    Configuration,
    EventsApi,
    ApiClient,
    RunStatusApiSchema,
    MessageLogEventApiSchema,
    MetricLogApiSchema,
    TestOutcomesApiSchema
)
from events_ingestion_client.rest import ApiException


@dataclass
class Config:
    api_host: str
    api_key: str
    project_id: str
    cloud_provider: str

    events_api_url: str
    obs_api_url: str
    agent_api_url: str

    api_headers: Dict
    events_api_client: EventsApi

    def __init__(self):
        with open(CONFIG_FILE, "r") as file:
            json_config = json.load(file)
            
        self.api_host = json_config['api_host']
        self.events_api_url = f"{self.api_host}/events/v1"
        self.obs_api_url = f"{self.api_host}/observability/v1"
        self.agent_api_url = f"{self.api_host}/agent/v1"
        
        self.api_key = json_config["api_key"]
        self.project_id = json_config["project_id"]
        self.cloud_provider = json_config.get("cloud_provider", DEFAULT_CLOUD_PROVIDER)

        self.api_headers = {
            "Accept": "application/json",
            "ServiceAccountAuthenticationKey": self.api_key,
        }

        events_api_config = Configuration()
        events_api_config.host = self.api_host
        events_api_config.api_key["ServiceAccountAuthenticationKey"] = self.api_key
        self.events_api_client = EventsApi(ApiClient(events_api_config))


DEFAULT_SERVER_NAME = "Testing Server 41245"
CONFIG_FILE = "demo-config.json"
DEFAULT_CLOUD_PROVIDER = "azure"

DASHBOARD_JOURNEY = "Dashboard and Model Production"
NIGHTLY_EXPORTS_JOURNEY = "Nightly Exports"
DAILY_DATA_LOAD_JOURNEY = "Daily Data Load"

DASHBOARD_COMPONENTS = {
    "aws": [
        ("AWS_Lambda_Datakitchen_Obs_Demo", "aws_lambda"),
        ("Python_Customer_Segmentation", "aws_sagemaker"),
        ("Query_Delta_Table_Notebook", "databricks"),
        ("Tableau_Dashboard", "tableau"),
        ("Databricks_Personal_Compute_Cluster", "databricks"),
    ],
    "azure": [
        ("ADF_Datakitchen_Obs_Demo", "data_factory"),
        ("Python_Customer_Segmentation", "azure_ml"),
        ("Query_Delta_Table_Notebook", "databricks"),
        ("Power_BI_Dashboard", "power_bi"),
        ("Databricks_Personal_Compute_Cluster", "databricks"),
    ],
}
NIGHTLY_EXPORTS_COMPONENTS = {
    "aws": [
        ("AWS_Lambda_Prepare_and_Export_Data", "aws_lambda"),
        ("AWS_Glue_Job_14", "aws_glue"),
        ("Daily_Summary_Table", "databricks")
    ],
    "azure": [
        ("Azure_Functions_Prepare_and_Export_Data", "azure_functions"),
        ("Azure_Data_Factory_Job_14", "data_factory"),
        ("Daily_Summary_Table", "databricks")
    ],
}
DAILY_DATA_LOAD_COMPONENTS = {
    "aws": [
        ("AWS_Managed_Workflows_Data_Loader", "airflow"),
        ("D_Product", "redshift"),
        ("D_Order", "redshift"),
        ("F_PT_TRTMT_Summary", "redshift"),
        ("D_Customer", "redshift")
    ],
    "azure": [
        ("Azure_Airflow_Data_Loader", "airflow"),
        ("D_Product", "mssql"),
        ("D_Order", "mssql"),
        ("F_PT_TRTMT_Summary", "mssql"),
        ("D_Customer", "mssql")
    ],
}


def divide_chunks(l, n):
    # looping till length l
    for i in range(0, len(l), n):
        yield l[i:i + n]


#  0 4 * * *
def daily_cron(minute, hour) -> str:
    return f"{minute} {hour} * * *"


def put_component_schedule(config: Config, component_id: str, schedule_json: dict) -> None:
    if component_id is not None:
        url = f"{config.obs_api_url}/components/{component_id}/schedules"
        try:
            req = requests.post(url, headers=config.api_headers, json=schedule_json)
            if req.status_code != 201:
                print("Unexpected response code when calling component schedule post via requests: %s\n" % req.content)
        except requests.exceptions.RequestException as e:
            print("Exception when calling component schedule post via requests: %s\n" % e)


def get_component_id(config: Config, component_name: str) -> str:
    component_id = ""
    url = f"{config.obs_api_url}/projects/{config.project_id}/components?search={component_name}"
    response = requests.get(url=url, headers=config.api_headers).json()
    if response["entities"]:
        component_id = response["entities"][0].get("id")
    return component_id


def delete_component(config: Config, component_name: str) -> None:
    component_id = get_component_id(config, component_name)

    if not component_id:
        return

    url = f"{config.obs_api_url}/components/{component_id}"
    response = requests.delete(url=url, headers=config.api_headers)
    response.raise_for_status()
    time.sleep(1)


def create_batch_pipeline_component(
        config: Config,
        pipeline_name: str,
        component_tool: str = None
) -> str:
    url = f"{config.obs_api_url}/projects/{config.project_id}/batch-pipelines"
    json_body = {
        "type": "BATCH_PIPELINE",
        "name": pipeline_name,
        "key": pipeline_name,
        "tool": component_tool
    }
    response = requests.post(url=url, headers=config.api_headers, json=json_body).json()
    time.sleep(1)
    return response.get("id", "")


def create_server_component(
        config: Config,
        server_name: str,
        component_tool: str = None
) -> str:
    url = f"{config.obs_api_url}/projects/{config.project_id}/servers"
    json_body = {
        "type": "SERVER",
        "name": server_name,
        "key": server_name,
        "tool": component_tool
    }
    response = requests.post(url=url, headers=config.api_headers, json=json_body).json()
    time.sleep(1)
    return response.get("id", "")


def create_dataset_component(
        config: Config,
        dataset_name: str,
        component_tool: str = None
) -> str:
    url = f"{config.obs_api_url}/projects/{config.project_id}/datasets"
    json_body = {
        "type": "DATASET",
        "name": dataset_name,
        "key": dataset_name,
        "tool": component_tool
    }
    response = requests.post(url=url, headers=config.api_headers, json=json_body).json()
    time.sleep(1)
    return response.get("id", "")


def get_journey_id(config: Config, journey_name: str) -> str:
    journey_id = ""
    url = f"{config.obs_api_url}/projects/{config.project_id}/journeys?search={journey_name}"
    response = requests.get(url=url, headers=config.api_headers).json()
    if response["entities"]:
        journey_id = response["entities"][0].get("id")
    return journey_id


def delete_journey(config: Config, journey_name: str) -> None:
    journey_id = get_journey_id(config, journey_name)

    if not journey_id:
        return

    url = f"{config.obs_api_url}/journeys/{journey_id}"
    response = requests.delete(url=url, headers=config.api_headers)
    response.raise_for_status()
    time.sleep(1)


def create_data_journey(config: Config, journey_name: str) -> str:
    url = f"{config.obs_api_url}/projects/{config.project_id}/journeys"
    json_body = {
        "name": journey_name,
    }
    response = requests.post(url=url, headers=config.api_headers, json=json_body).json()
    time.sleep(1)
    return response.get("id", "")


def create_journey_rule(
        config: Config,
        journey_id: str,
        json_body: Dict[Any, Any]
) -> None:
    url = f"{config.obs_api_url}/journeys/{journey_id}/rules"
    response = requests.post(url=url, headers=config.api_headers, json=json_body)
    response.raise_for_status()
    time.sleep(.5)


# put_journey
def link_components_in_journey(
        config: Config,
        journey_id: str,
        left_component_id: str = None,
        right_component_id: str = None
) -> None:
    url = f"{config.obs_api_url}/journeys/{journey_id}/dag"
    if left_component_id and right_component_id:
        json_body = {
            "left": left_component_id,
            "right": right_component_id
        }
    elif left_component_id and (right_component_id is None):
        json_body = {
            "left": left_component_id
        }
    elif right_component_id and (left_component_id is None):
        json_body = {
            "right": right_component_id
        }
    else:
        json_body = []
    response = requests.put(url=url, headers=config.api_headers, json=json_body)
    response.raise_for_status()
    time.sleep(.5)


def create_journey_instance_condition(
        config: Config,
        journey_id: str,
        json_body: Dict[Any, Any]
) -> None:
    url = f"{config.obs_api_url}/journeys/{journey_id}/instance-conditions"
    response = requests.post(url=url, headers=config.api_headers, json=json_body)
    response.raise_for_status()
    time.sleep(.5)


def set_component_heartbeat(
        config: Config,
        agent_name: str,
        component_tool: str,
        version: str,

) -> None:
    url = f"{config.agent_api_url}/heartbeat"
    json_body = {
        "key": agent_name,
        "tool": component_tool,
        "version": version,
        "latest_event_timestamp": datetime.now().astimezone().isoformat()
    }
    try:
        req = requests.post(url, headers=config.api_headers, json=json_body)
        if req.status_code != 201 and req.status_code != 204:
            print("Unexpected response code when calling set_component_heartbeat post via requests: %s\n" % req.content)
    except requests.exceptions.RequestException as e:
        print("Exception when calling set_component_heartbeat post via requests: %s\n" % e)
    time.sleep(.5)


def begin_pipeline_component(
        config: Config,
        pipeline_key: str,
        run_key: str,
        pipeline_tool: str,
        my_datetime: datetime,
        a_start: int,
        random_minutes: int,
) -> None:
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": "RUNNING",
        "component_tool": pipeline_tool,
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) - timedelta(minutes=random_minutes)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "log_level": "WARNING",
        "message": "some log message goes here",
        "component_tool": pipeline_tool,
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) - timedelta(minutes=random_minutes)
        ).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**event_data))
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "metric_key": "disk space",
        "metric_value": "500.9",
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) - timedelta(minutes=random_minutes)
        ).isoformat()
    }
    config.events_api_client.post_metric_log(MetricLogApiSchema(**event_data))


def do_task_a(
        config: Config,
        pipeline_key: str,
        run_key: str,
        my_datetime: datetime,
        a_start: int,
        a_end: int,
        random_minutes: int,
) -> None:
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": "RUNNING",
        "task_name": "Data_Load_" + str(a_start),
        "task_key": "Data_Load_" + str(a_start),
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) - timedelta(minutes=random_minutes)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": "COMPLETED",
        "task_name": "Data_Load_" + str(a_start),
        "task_key": "Data_Load_" + str(a_start),
        "event_timestamp": (
                my_datetime - timedelta(hours=a_end) - timedelta(minutes=random_minutes)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))


def do_task_b(
        config: Config,
        pipeline_key: str,
        run_key: str,
        my_datetime: datetime,
        b_start: int,
        b_end: int,
        fail: bool,
) -> None:
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": "RUNNING",
        "task_name": "Data_Transform_" + str(b_start),
        "task_key": "Data_Transform_" + str(b_start),
        "event_timestamp": (my_datetime - timedelta(hours=b_start)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))

    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": "FAILED" if fail else "COMPLETED",
        "task_name": "Data_Transform_" + str(b_start),
        "task_key": "Data_Transform_" + str(b_start),
        "event_timestamp": (my_datetime - timedelta(hours=b_end)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))


def do_task_c(
        config: Config,
        pipeline_key: str,
        run_key: str,
        my_datetime: datetime,
        c_start: int,
        c_end: int,
        fail: bool,
) -> None:
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": "RUNNING",
        "task_name": "Data_Segment_" + str(c_start),
        "task_key": "Data_Segment_" + str(c_start),
        "event_timestamp": (my_datetime - timedelta(hours=c_start)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))

    test_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "task_name": "Data_Segment_" + str(c_start),
        "task_key": "Data_Segment_" + str(c_start),
        "event_timestamp": (my_datetime - timedelta(hours=c_start)).isoformat(),
        "test_outcomes": [
            {
                "metric_value": "20.4",
                "max_threshold": "27.8",
                "min_threshold": "13.8",
                "name": "Max summary value threshold test",
                "status": "PASSED",
                "description": "Max summary value threshold test"
            },
            {
                "metric_value": "354",
                "max_threshold": "278",
                "min_threshold": "138",
                "name": "Row Count Test",
                "status": "FAILED" if fail else "PASSED",
                "description": "Row Count Test"
            }
        ]
    }
    config.events_api_client.post_test_outcomes(TestOutcomesApiSchema(**test_data))
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": "COMPLETED",
        "task_name": "Data_Segment_" + str(c_start),
        "task_key": "Data_Segment_" + str(c_start),
        "event_timestamp": (my_datetime - timedelta(hours=c_end)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))


def finish_pipeline_component(
        config: Config,
        pipeline_key: str,
        run_key: str,
        pipeline_tool: str,
        my_datetime: datetime,
        c_end: int,
        fail: bool,
) -> None:
    if "aws_glue" in pipeline_tool:
        external_url = "https://drive.google.com/file/d/1ENlMYQq1vnT_OTj9kcLrXmEN-qX09_la/view"
    elif "aws_lambda" in pipeline_tool:
        external_url = "https://drive.google.com/file/d/1B0q_aKpTTfGU7Eh9E4EUYkXbiPuaM-rj/view"
    elif "data_factory" in pipeline_tool:
        external_url = "https://drive.google.com/file/d/1jZtx0MnPMSP1Zpn_7JSp3hOh9Osg2QwO/view"
    elif "azure_functions" in pipeline_tool:
        external_url = "https://drive.google.com/file/d/1OupJwVgY5QBPCrTtBsaki7G3w9ZYlUwF/view"
    else:
        external_url = None
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key, "status": "FAILED" if fail else "COMPLETED",
        "component_tool": pipeline_tool,
        "external_url": external_url,
        "event_timestamp": (my_datetime - timedelta(hours=c_end)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))


def send_pipeline_events(
        config: Config,
        component_name: str,
        component_tool: str,
        backdate_hours: int,
        length_hours: int,
        fail: bool
) -> None:
    run_key = f"{component_name}:{str(backdate_hours)}:{str(length_hours)}"
    my_datetime = datetime.now().astimezone()
    a_start = backdate_hours + length_hours
    a_end = a_start - int((backdate_hours + length_hours) / 2)
    b_start = a_end
    b_end = backdate_hours - length_hours
    c_start = a_end
    c_end = b_end
    random_minutes = randint(1, 59)
    try:
        begin_pipeline_component(config, component_name, run_key, component_tool, my_datetime, a_start, random_minutes)
        do_task_a(config, component_name, run_key, my_datetime, a_start, a_end, random_minutes)
        do_task_b(config, component_name, run_key, my_datetime, b_start, b_end, fail)
        do_task_c(config, component_name, run_key, my_datetime, c_start, c_end, fail)
        finish_pipeline_component(config, component_name, run_key, component_tool, my_datetime, c_end, fail)
    except ApiException as e:
        print(f"Exception when calling send events:\n{e}")


def send_dataset_metrics(
        config: Config,
        metric_data: dict
) -> None:
    try:
        config.events_api_client.post_metric_log(MetricLogApiSchema(**metric_data))
    except ApiException as e:
        print(f"Exception when calling event metrics-log post via requests:\n{e}")


def send_dataset_tests(
        config: Config,
        test_data: dict
) -> None:
    try:
        config.events_api_client.post_test_outcomes(TestOutcomesApiSchema(**test_data))
    except ApiException as e:
        print(f"Exception when calling event test-outcomes post via requests:\n{e}")


def send_dataset_events(
        config: Config,
        dataset_key: str,
        dataset_tool: str,
        backdate_hours: int,
        post_start_interval_mins: int,
        fail: bool
) -> None:
    my_datetime = datetime.now().astimezone()

    metric_data = {
        "dataset_key": dataset_key,
        "dataset_name": dataset_key,
        "metric_key": "DATA_TABLE Row count",
        "metric_value": 50345,
        "component_tool": dataset_tool,
        "event_timestamp": (
                my_datetime - timedelta(hours=backdate_hours) + timedelta(minutes=post_start_interval_mins)
        ).isoformat()
    }
    test_data = {
        "dataset_key": dataset_key,
        "dataset_name": dataset_key,
        "event_timestamp": (
                my_datetime - timedelta(hours=backdate_hours) + timedelta(minutes=post_start_interval_mins)
        ).isoformat(),
        "test_outcomes": [
            {
                "metric_value": "20.4",
                "max_threshold": "27.8",
                "min_threshold": "13.8",
                "name": "Count Check Table DATA_TABLE",
                "status": "PASSED",
                "description": "Row Count Test."
            },
            {
                "metric_value": "35.4",
                "name": "Column FOO Check Table ",
                "status": "FAILED" if fail else "PASSED",
                "description": "Column Check."
            },
            {
                "metric_value": "27.8",
                "name": "Column BAR Check Table ",
                "status": "FAILED" if fail else "PASSED",
                "description": "Column Check."
            }
        ]
    }
    send_dataset_metrics(config, metric_data)
    send_dataset_tests(config, test_data)


def create_nightly_export_data_journey(
        config: Config,
        backdate_hours: int,
        length_hours: int,
        failure: bool,
        create_journey: bool
) -> None:
    journey_components_dict = NIGHTLY_EXPORTS_COMPONENTS
    create_component_list = journey_components_dict.get(config.cloud_provider)
    serverless_name, serverless_tool = create_component_list[0]
    batch_pipeline_name, batch_pipeline_tool = create_component_list[1]
    dataset_name, dataset_tool = create_component_list[2]

    if create_journey is True:
        delete_journey(config, NIGHTLY_EXPORTS_JOURNEY)
        journey_id = create_data_journey(config, NIGHTLY_EXPORTS_JOURNEY)

        components = {}

        # Populate required components

        for name, tool in create_component_list:
            component_id = ""
            delete_component(config, name)
            if ("job_14" in name.lower()) or ("prepare_and_export_data" in name.lower()):
                component_id = create_batch_pipeline_component(config, name, component_tool=tool)
            elif "daily_summary_table" in name.lower():
                component_id = create_dataset_component(config, name, component_tool=tool)
            components[name] = component_id

        # Link Components
        batch_pipeline_component_id = components[batch_pipeline_name]
        dataset_component_id = components[dataset_name]
        serverless_component_id = components[serverless_name]

        link_components_in_journey(
            config,
            journey_id,
            left_component_id=batch_pipeline_component_id,
            right_component_id=dataset_component_id
        )

        link_components_in_journey(
            config,
            journey_id,
            left_component_id=batch_pipeline_component_id,
            right_component_id=serverless_component_id
        )

        # daily schedule
        schedule_json = {
            "expectation": "BATCH_PIPELINE_START_TIME",
            "schedule": daily_cron(0, 23),
            "margin": "600",
            "description": "Daily, at 11 pm New York Time",
            "timezone": "America/New_York",
        }
        put_component_schedule(config, batch_pipeline_component_id, schedule_json)

        create_journey_instance_condition(
            config,
            journey_id,
            json_body={
                "action": "START",
                "batch_pipeline": batch_pipeline_component_id
            }
        )
        create_journey_instance_condition(
            config,
            journey_id,
            json_body={
                "action": "END",
                "batch_pipeline": serverless_component_id
            }
        )

    send_pipeline_events(
        config=config,
        component_name=batch_pipeline_name,
        component_tool=batch_pipeline_tool,
        backdate_hours=backdate_hours + 1,
        length_hours=length_hours + 1,
        fail=False
    )
    send_dataset_events(
        config=config,
        dataset_key=dataset_name,
        dataset_tool=dataset_tool,
        backdate_hours=backdate_hours + 1,
        post_start_interval_mins=30,
        fail=failure
    )
    send_pipeline_events(
        config=config,
        component_name=serverless_name,
        component_tool=serverless_tool,
        backdate_hours=backdate_hours,
        length_hours=length_hours,
        fail=failure
    )


def add_pipeline_task_status(
        config: Config,
        task_name: str,
        task_key: str,
        url: str,
        a_start: int,
        my_datetime: datetime,
        pipeline_key: str,
        tool: str,
        run_key: str,
        status: str,
        time_duration_from_start: int
) -> None:
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": status,
        "task_name": task_name,
        "task_key": task_key,
        "external_url": url,
        "component_tool": tool,
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=time_duration_from_start)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))


def startup_pipeline_component(
        config: Config,
        pipeline_key: str,
        run_key: str,
        tool: str,
        a_start: int,
        my_datetime: datetime,
) -> None:
    if "AWS" in pipeline_key:
        url = "https://drive.google.com/file/d/142CBxC9rbshCdC8UHPrET9AdbYCUFtWR/view"
    elif "Azure" in pipeline_key:
        url = "https://drive.google.com/file/d/1HFneEBpC_HnLGiPbY1rjTTIGr9US31No/view"
    else:
        url = None
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": "RUNNING",
        "external_url": url,
        "component_tool": tool,
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "log_level": "INFO",
        "message": "load_instance.py:635 INFO - DAG Run starting",
        "external_url": url,
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=10)
        ).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**event_data))
    add_pipeline_task_status(
        config, "Create_Schema_If_Not_Exists", "Create_Schema_If_Not_Exists", url, a_start,
        my_datetime, pipeline_key, tool, run_key, "RUNNING", 12)
    add_pipeline_task_status(config, "Load_Raw_Data", "Load_Raw_Data", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "RUNNING", 15)
    add_pipeline_task_status(config, "Cleanse_Raw_Data", "Cleanse_Raw_Data", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "RUNNING", 18)
    add_pipeline_task_status(config, "Create_Customer_Dimension", "Create_Customer_Dimension", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "RUNNING", 23)
    add_pipeline_task_status(config, "Create_Order_Dimension", "Create_Order_Dimension", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "RUNNING", 23)
    add_pipeline_task_status(config, "Create_Product_Dimension", "Create_Product_Dimension", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "RUNNING", 23)
    add_pipeline_task_status(config, "Dimensionalize_Superstore_and_Create_Facts",
                             "Dimensionalize_Superstore_and_Create_Facts", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "RUNNING", 29)
    add_pipeline_task_status(config, "Call_DataKitchen_DataOps_Automation_Tests",
                             "Call_DataKitchen_DataOps_Automation_Tests", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "RUNNING", 32)


def send_test_outcomes_to_dataset_component(
        config: Config,
        a_start: int,
        dataset1_name: str,
        dataset2_name: str,
        dataset3_name: str,
        dataset4_name: str,
        my_datetime: datetime,
        test_data: List[Any],
        component_integrations: dict
) -> None:
    do_new = True
    secs = 3
    send_tests_to_dataset(config, a_start, divide_chunks(test_data, 100), component_integrations, dataset1_name, do_new,
                          my_datetime, secs, True)

    secs = 5
    send_tests_to_dataset(config, a_start, divide_chunks(test_data, 100), component_integrations, dataset2_name, do_new,
                          my_datetime, secs, False)

    secs = 7
    send_tests_to_dataset(config, a_start, divide_chunks(test_data, 100), component_integrations, dataset3_name, do_new,
                          my_datetime, secs, False)

    secs = 9
    send_tests_to_dataset(config, a_start, divide_chunks(test_data, 100), component_integrations, dataset4_name, do_new,
                          my_datetime, secs, False)


def send_tests_to_dataset(
        config: Config,
        a_start: int,
        chunked_list,
        component_integrations: dict,
        dataset_name: str,
        do_new: bool,
        my_datetime: datetime,
        secs: int,
        send_metrics: bool,
) -> None:
    for chunk in chunked_list:
        outcomes = []
        for test in chunk:
            if test[1] in dataset_name:
                if do_new is False:
                    result = test[7].strip("\"")
                    outcomes.append(
                        {
                            "name": f"DataKitchen DataOps TestGen ({test[3]}) of table: {test[1]} in column: {test[2]} with results {result}",
                            "status": "PASSED" if test[5] == "Pass" else "WARNING" if test[
                                                                                          5] == "Warning" else "FAILED",
                            "description": test[4]
                        }
                    )
                else:
                    test_counts = []
                    tr = test[7].strip("\"").split(", ")
                    for t in tr:
                        if len(t.split("=")) > 0:
                            test_counts.append({"name": t.split("=")[0].strip(), "value": t.split("=")[1].strip()})
                        else:
                            print("send_tests_to_dataset test result format: \n")
                    outcomes.append(
                        {
                            "name": test[4],
                            "result": test[7].strip("\""),
                            "status": "PASSED" if test[5] == "Pass" else "WARNING" if test[
                                                                                          5] == "Warning" else "FAILED",
                            "description": test[4],
                            "integrations": {
                                "testgen": {
                                    "table": test[1],
                                    "test_suite": "default-suite-1",
                                    "version": 1,
                                    "columns": [test[2]],
                                    "test_parameters": test_counts
                                }
                            }
                        }
                    )
        td = {
            "dataset_key": dataset_name,
            "dataset_name": dataset_name,
            "event_timestamp": (
                    my_datetime - timedelta(hours=a_start) + timedelta(minutes=21) + timedelta(seconds=secs)
            ).isoformat(),
            "test_outcomes": outcomes
        }
        if do_new is True:
            td["component_integrations"] = component_integrations
            for o in outcomes:
                for tc in o["integrations"]["testgen"]["test_parameters"]:
                    for t in tc:
                        if "Threshold_Value" in t:
                            td["max_threshold"] = t["Threshold_Value"]
                        if "Baseline_Value" in t:
                            td["metric_value"] = t["Baseline_Value"]
        if send_metrics is True:
            md = {
                "dataset_key": dataset_name,
                "dataset_name": dataset_name,
                "metric_key": dataset_name + " Row count",
                "metric_value": randint(50000, 100000),
                "event_timestamp": (
                        my_datetime - timedelta(hours=a_start) + timedelta(minutes=22) + timedelta(seconds=secs)
                ).isoformat()
            }
            send_dataset_metrics(config, md)
        if outcomes:
            send_dataset_tests(config, td)
        time.sleep(1)


def shutdown_pipeline_component(
        config: Config,
        pipeline_key: str,
        run_key: str,
        tool: str,
        a_start: int,
        my_datetime: datetime,
):
    if "AWS" in pipeline_key:
        url = "https://drive.google.com/file/d/142CBxC9rbshCdC8UHPrET9AdbYCUFtWR/view"
    elif "Azure" in pipeline_key:
        url = "https://drive.google.com/file/d/1HFneEBpC_HnLGiPbY1rjTTIGr9US31No/view"
    else:
        url = None
    add_pipeline_task_status(config, "Create_Schema_If_Not_Exists", "Create_Schema_If_Not_Exists", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "COMPLETED", 15)
    add_pipeline_task_status(config, "Load_Raw_Data", "Load_Raw_Data", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "COMPLETED", 18)
    add_pipeline_task_status(config, "Cleanse_Raw_Data", "Cleanse_Raw_Data", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "COMPLETED", 22)
    add_pipeline_task_status(config, "Create_Customer_Dimension", "Create_Customer_Dimension", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "COMPLETED", 26)
    add_pipeline_task_status(config, "Create_Order_Dimension", "Create_Order_Dimension", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "COMPLETED", 25)
    add_pipeline_task_status(config, "Create_Product_Dimension", "Create_Product_Dimension", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "COMPLETED", 28)
    add_pipeline_task_status(config, "Dimensionalize_Superstore_and_Create_Facts",
                             "Dimensionalize_Superstore_and_Create_Facts", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "COMPLETED", 32)
    add_pipeline_task_status(config, "Call_DataKitchen_DataOps_Automation_Tests",
                             "Call_DataKitchen_DataOps_Automation_Tests", url, a_start,
                             my_datetime, pipeline_key, tool, run_key, "COMPLETED", 35)

    event_data = {
        "pipeline_key": pipeline_key,
        "run_key": run_key,
        "status": "COMPLETED",
        "external_url": url,
        "component_tool": tool,
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=38)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))


def read_csv(csv_file):
    data = []
    with open(csv_file, "r") as f:
        for row in csv.reader(f, quotechar="\"", delimiter=",", quoting=csv.QUOTE_ALL, skipinitialspace=True):
            data.append(row)
    return data


def send_ingest_journey_events(
        config: Config,
        pipeline_key: str,
        tool: str,
        dataset_1_name: str,
        dataset_2_name: str,
        dataset_3_name: str,
        dataset_4_name: str,
        backdate_hours: int,
        length_hours: int
) -> None:
    run_key = f"{pipeline_key}:{str(backdate_hours)}:{str(length_hours)}"
    my_datetime = datetime.now().astimezone()
    a_start = backdate_hours + length_hours
    component_integrations = {
        "integrations": {
            "testgen": {
                "database_name": "production",
                "schema": "test_gen",
                "tables": {
                    "include_pattern": "%",
                    "include_list": ["D_Customer", "D_Order", "D_Product", "F_PT_TRTMT_Summary"]
                },
                "connection_name": "default",
                "version": 1,
                "table_group_configuration": {
                    "group_id": "0ea85e17-acbe-47fe-8394-9970725ad37d",
                    "project_code": "DEFAULT",
                    "uses_sampling": False
                }
            }
        }
    }

    file_path = Path(__file__).parent / Path("input_data/data_tests.csv")
    test_data = read_csv(file_path)

    try:
        startup_pipeline_component(config, pipeline_key, run_key, tool, a_start, my_datetime)
        send_test_outcomes_to_dataset_component(config, a_start, dataset_1_name, dataset_2_name,
                                                dataset_3_name, dataset_4_name, my_datetime, test_data,
                                                component_integrations)
        shutdown_pipeline_component(config, pipeline_key, run_key, tool, a_start, my_datetime)
    except ApiException as e:
        print(f"Exception when calling send_ingest_journey_events:\n{e}")


def create_daily_data_journey(
        config: Config,
        backdate_hours: int,
        length_hours: int
) -> None:
    journey_components_dict = DAILY_DATA_LOAD_COMPONENTS
    create_component_list = journey_components_dict.get(config.cloud_provider)

    delete_journey(config, DAILY_DATA_LOAD_JOURNEY)
    journey_id = create_data_journey(config, DAILY_DATA_LOAD_JOURNEY)

    components = {}

    # Populate required components
    for name, tool in create_component_list:
        component_id = ""
        delete_component(config, name)
        if "data_loader" in name.lower():
            component_id = create_batch_pipeline_component(config, name, component_tool=tool)
        else:
            component_id = create_dataset_component(config, name, component_tool=tool)
        components[name] = component_id

    # Link Components
    batch_pipeline_name, batch_pipeline_tool = create_component_list[0]
    dataset_1_name, dataset_1_tool = create_component_list[1]
    dataset_2_name, dataset_2_tool = create_component_list[2]
    dataset_3_name, dataset_3_tool = create_component_list[3]
    dataset_4_name, dataset_4_tool = create_component_list[4]

    batch_pipeline_component_id = components[batch_pipeline_name]
    dataset_1_id = components[dataset_1_name]
    dataset_2_id = components[dataset_2_name]
    dataset_3_id = components[dataset_3_name]
    dataset_4_id = components[dataset_4_name]

    for dataset_id in [dataset_1_id, dataset_2_id, dataset_3_id, dataset_4_id]:
        link_components_in_journey(
            config,
            journey_id,
            left_component_id=batch_pipeline_component_id,
            right_component_id=dataset_id
        )

    # Create journey start and end conditions
    create_journey_instance_condition(
        config,
        journey_id,
        json_body={
            "action": "START",
            "batch_pipeline": batch_pipeline_component_id
        }
    )
    create_journey_instance_condition(
        config,
        journey_id,
        json_body={
            "action": "END",
            "batch_pipeline": batch_pipeline_component_id
        }
    )

    # daily schedule
    schedule_json = {
        "expectation": "BATCH_PIPELINE_START_TIME",
        "schedule": daily_cron(0, 22),
        "margin": "600",
        "description": "Daily, at 10 pm New York Time",
        "timezone": "America/New_York",
    }
    put_component_schedule(config, batch_pipeline_component_id, schedule_json)

    # Add events
    send_ingest_journey_events(
        config,
        batch_pipeline_name,
        batch_pipeline_tool,
        dataset_1_name,
        dataset_2_name,
        dataset_3_name,
        dataset_4_name,
        backdate_hours,
        length_hours
    )


def run_demo_pipeline_component(
        config: Config,
        demo_pipeline_name: str,
        demo_pipeline_tool: str,
        my_datetime: datetime,
        a_start: int,
        test_data: List[Any],
) -> None:
    run_key = f"{demo_pipeline_name}_{str(int(round(my_datetime.timestamp())))}"

    if "aws_lambda" in demo_pipeline_tool:
        external_url = "https://drive.google.com/file/d/1B0q_aKpTTfGU7Eh9E4EUYkXbiPuaM-rj/view"
    elif "data_factory" in demo_pipeline_tool:
        external_url = "https://drive.google.com/file/d/1jZtx0MnPMSP1Zpn_7JSp3hOh9Osg2QwO/view"
    else:
        external_url = None
    event_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "status": "RUNNING",
        "component_tool": demo_pipeline_tool,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    # step 1: Running blob_to_blob
    event_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "status": "RUNNING",
        "task_name": "blob_to_blob",
        "task_key": "blob_to_blob",
        "component_tool": demo_pipeline_tool,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=2)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    message_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "log_level": "INFO",
        "task_name": "blob_to_blob",
        "task_key": "blob_to_blob",
        "component_tool": demo_pipeline_tool,
        "message": "ADF: blob_to_blob ....",
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=4)
        ).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**message_data))
    metric_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "task_name": "blob_to_blob",
        "task_key": "blob_to_blob",
        "component_tool": demo_pipeline_tool,
        "metric_key": "filesRead",
        "metric_value": 3,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=5)
        ).isoformat()
    }
    config.events_api_client.post_metric_log(MetricLogApiSchema(**metric_data))
    metric_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "task_name": "blob_to_blob",
        "task_key": "blob_to_blob",
        "component_tool": demo_pipeline_tool,
        "metric_key": "dataRead",
        "metric_value": 12333905,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=5)
        ).isoformat()
    }
    config.events_api_client.post_metric_log(MetricLogApiSchema(**metric_data))
    metric_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "task_name": "blob_to_blob",
        "task_key": "blob_to_blob",
        "component_tool": demo_pipeline_tool,
        "metric_key": "filesWritten",
        "metric_value": 3,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=14)
        ).isoformat()
    }
    config.events_api_client.post_metric_log(MetricLogApiSchema(**metric_data))
    metric_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "task_name": "blob_to_blob",
        "task_key": "blob_to_blob",
        "component_tool": demo_pipeline_tool,
        "metric_key": "dataWritten",
        "metric_value": 12333905,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=14)
        ).isoformat()
    }
    config.events_api_client.post_metric_log(MetricLogApiSchema(**metric_data))
    event_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "external_url": external_url,
        "task_name": "blob_to_blob",
        "task_key": "blob_to_blob",
        "component_tool": demo_pipeline_tool,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=15)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    # step 2: Running calculate_order_summary
    event_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "status": "RUNNING",
        "task_name": "calculate_order_summary",
        "task_key": "calculate_order_summary",
        "component_tool": demo_pipeline_tool,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=16)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    message_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "log_level": "INFO",
        "task_name": "calculate_order_summary",
        "task_key": "calculate_order_summary",
        "component_tool": demo_pipeline_tool,
        "message": "ADF: calculate_order_summary ....",
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=17)
        ).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**message_data))
    outcomes = []
    for test in test_data:
        outcomes.append({
            "name": test[1],
            "status": "PASSED" if test[0] == "PASSED" else "WARNING" if test[0] == "WARNING" else "FAILED",
            "description": test[2]
        })
    test_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "task_name": "calculate_order_summary",
        "task_key": "calculate_order_summary",
        "external_url": "https://cloud.datakitchen.io/#/orders/im/IM_Demo_GCP_Production/runs/00a5e63e-9ff7-11ec-b2e8-02bb825df102",
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=22)).isoformat(),
        "test_outcomes": outcomes
    }
    config.events_api_client.post_test_outcomes(TestOutcomesApiSchema(**test_data))
    event_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "external_url": external_url,
        "task_name": "calculate_order_summary",
        "task_key": "calculate_order_summary",
        "component_tool": demo_pipeline_tool,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=28)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    event_data = {
        "pipeline_key": demo_pipeline_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "external_url": external_url,
        "component_tool": demo_pipeline_tool,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=30)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    time.sleep(2)


def run_server_component(
        config: Config,
        server_name: str,
        server_tool: str,
        my_datetime: datetime,
        a_start: int,
) -> None:
    if "databricks" in server_tool:
        external_url = "https://drive.google.com/file/d/1vQqiauTvgY-tiIAFk8__wL_OcXqg-bLe/view"
    else:
        external_url = None
    event_timestamp = (
            my_datetime - timedelta(hours=a_start) + timedelta(minutes=10)
    ).isoformat()
    message_data = {
        "server_name": server_name,
        "server_key": server_name,
        "log_level": "INFO",
        "message": "RUNNING: Cluster is running",
        "component_tool": server_tool,
        "external_url": external_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": event_timestamp
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**message_data))

    event_timestamp = (
            my_datetime - timedelta(hours=a_start) + timedelta(minutes=15)
    ).isoformat()
    metric_data = {
        "server_name": server_name,
        "server_key": server_name,
        "metric_value": 27,
        "metric_key": "Cluster Capacity Percentage",
        "component_tool": server_tool,
        "external_url": external_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": event_timestamp
    }
    config.events_api_client.post_metric_log(MetricLogApiSchema(**metric_data))


def run_query_job_component(
        config: Config,
        db_job_name: str,
        db_job_tool: str,
        my_datetime: datetime,
        a_start: int,
) -> None:
    run_key = f"{db_job_name}_{str(int(round(my_datetime.timestamp())))}"
    external_task_url = "https://drive.google.com/file/d/1tfkMgszgCznUfw5kLVjTZT31NRp7W8Ri/view"
    external_workflow_url = "https://drive.google.com/file/d/1ic2iucKONqVLKvlbrifJUfsNC5qx2svJ/view"
    event_data = {
        "pipeline_key": db_job_name,
        "run_key": run_key,
        "status": "RUNNING",
        "component_tool": db_job_tool,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=31)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))

    # step 1: Running query_delta_table
    event_data = {
        "pipeline_key": db_job_name,
        "run_key": run_key,
        "status": "RUNNING",
        "component_tool": db_job_tool,
        "external_url": external_task_url,
        "task_name": "query_delta_table",
        "task_key": "query_delta_table",
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=32)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    message_data = {
        "pipeline_key": db_job_name,
        "run_key": run_key,
        "log_level": "INFO",
        "task_name": "query_delta_table",
        "task_key": "query_delta_table",
        "component_tool": db_job_tool,
        "external_url": external_task_url,
        "message": "model.ipynb:15 INFO - Model starting",
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=33)
        ).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**message_data))
    event_data = {
        "pipeline_key": db_job_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "component_tool": db_job_tool,
        "task_name": "query_delta_table",
        "task_key": "query_delta_table",
        "external_url": external_task_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=35)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    # step 2: Running calculate_order_summary
    event_data = {
        "pipeline_key": db_job_name,
        "run_key": run_key,
        "status": "RUNNING",
        "component_tool": db_job_tool,
        "task_name": "calculate_order_summary",
        "task_key": "calculate_order_summary",
        "external_url": external_task_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=36)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    message_data = {
        "pipeline_key": db_job_name,
        "run_key": run_key,
        "log_level": "INFO",
        "task_name": "calculate_order_summary",
        "task_key": "calculate_order_summary",
        "external_url": external_task_url,
        "message": "run_model.py:15 INFO - Model starting",
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=37)
        ).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**message_data))
    event_data = {
        "pipeline_key": db_job_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "component_tool": db_job_tool,
        "task_name": "calculate_order_summary",
        "task_key": "calculate_order_summary",
        "external_url": external_task_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=36)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    event_data = {
        "pipeline_key": db_job_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "component_tool": db_job_tool,
        "task_name": "query_delta_table_again",
        "task_key": "query_delta_table_again",
        "external_url": external_task_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=39)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    event_data = {
        "pipeline_key": db_job_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "component_tool": db_job_tool,
        "external_url": external_workflow_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=40)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    time.sleep(2)


def run_python_component(
        config: Config,
        python_pipeline_name: str,
        python_pipeline_tool: str,
        my_datetime: datetime,
        a_start: int,
) -> None:
    run_key = "PY5-42349-fnw398" + "-" + str(int(round(my_datetime.timestamp())))

    if "aws_sagemaker" in python_pipeline_tool:
        external_url = "https://drive.google.com/file/d/1ze4htJO6WlY_i6Uxka5rwR5Y1KI6N6Mj/view"
    elif "azure_ml" in python_pipeline_tool:
        external_url = "https://drive.google.com/file/d/1EKVtcxPqJe26UfnCHloFmuL3JU-qU4kZ/view"
    else:
        external_url = None
    event_data = {
        "pipeline_key": python_pipeline_name,
        "run_key": run_key,
        "component_tool": python_pipeline_tool,
        "status": "RUNNING",
        "external_url": external_url,
        "payload_keys": ["sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=31)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    # step 1: Running Forecast Model
    event_data = {
        "pipeline_key": python_pipeline_name,
        "run_key": run_key,
        "component_tool": python_pipeline_tool,
        "external_url": external_url,
        "status": "RUNNING",
        "task_name": "Running Forecast Model",
        "task_key": "Running_Forecast_Model",
        "payload_keys": ["sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=32)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    message_data = {
        "pipeline_key": python_pipeline_name,
        "run_key": run_key,
        "component_tool": python_pipeline_tool,
        "log_level": "INFO",
        "task_name": "Running Forecast Model",
        "task_key": "Running_Forecast_Model",
        "external_url": external_url,
        "message": "run_model.py:15 INFO - Model starting",
        "payload_keys": ["sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=33)
        ).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**message_data))
    event_data = {
        "pipeline_key": python_pipeline_name,
        "run_key": run_key,
        "component_tool": python_pipeline_tool,
        "status": "COMPLETED",
        "task_name": "Running Forecast Model",
        "task_key": "Running_Forecast_Model",
        "external_url": external_url,
        "payload_keys": ["sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=41)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    # step 2: Running Tests in DataKitchen
    event_data = {
        "pipeline_key": python_pipeline_name,
        "run_key": run_key,
        "component_tool": python_pipeline_tool,
        "status": "RUNNING",
        "task_name": "Test_ML_Model",
        "task_key": "Test_ML_Model",
        "payload_keys": ["sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=42)
        ).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    message_data = {
        "pipeline_key": python_pipeline_name,
        "run_key": run_key,
        "component_tool": python_pipeline_tool,
        "log_level": "INFO",
        "task_name": "Test_ML_Model",
        "task_key": "Test_ML_Model",
        "message": "running tests in DataKitchen Automation",
        "payload_keys": ["sales_business_customer"],
        "event_timestamp": (
                my_datetime - timedelta(hours=a_start) + timedelta(minutes=42)
        ).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**message_data))
    test_data = {
        "pipeline_key": python_pipeline_name,
        "run_key": run_key,
        "component_tool": python_pipeline_tool,
        "task_name": "Test_ML_Model",
        "task_key": "Test_ML_Model",
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=43)).isoformat(),
        "external_url": "https://cloud.datakitchen.io/#/orders/im/IM_Demo_GCP_Production/runs/a3c65a72-9fe5-11ec-9428-26b71968b605",
        "payload_keys": ["sales_business_customer"],
        "test_outcomes": [
            {
                "metric_value": "3290523",
                "min_threshold": "1000000",
                "name": "Validate_ML_Model_File_Size",
                "status": "PASSED",
                "description": "Check the input model file size"
            },
            {
                "metric_value": "813547",
                "min_threshold": "100000",
                "name": "Validate_Test_Data_File_Size",
                "status": "PASSED",
                "description": "Check the input data file size"
            },
            {
                "metric_value": "53.1608407481397",
                "max_threshold": "60",
                "name": "Validate_RMSE",
                "status": "PASSED",
                "description": "Evaluate the Root Means Squared Error of the Model Forecast Prediction"
            },
        ]
    }
    config.events_api_client.post_test_outcomes(TestOutcomesApiSchema(**test_data))
    event_data = {
        "pipeline_key": python_pipeline_name,
        "run_key": run_key,
        "component_tool": python_pipeline_tool,
        "status": "COMPLETED",
        "task_name": "Test_ML_Model",
        "task_key": "Test_ML_Model",
        "external_url": "https://cloud.datakitchen.io/#/orders/im/IM_Demo_GCP_Production/runs/a3c65a72-9fe5-11ec-9428-26b71968b605",
        "payload_keys": ["sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=44)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    event_data = {
        "pipeline_key": python_pipeline_name,
        "run_key": run_key,
        "component_tool": python_pipeline_tool,
        "status": "COMPLETED",
        "payload_keys": ["sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=45)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    time.sleep(1)


def run_bi_component(
        config: Config,
        bi_pipeline_name: str,
        bi_pipeline_tool: str,
        my_datetime: datetime,
        a_start: int,
) -> None:
    run_key = "T42394-458324-46" + "-" + str(int(round(my_datetime.timestamp())))
    if "power_bi" in bi_pipeline_tool:
        external_url = "https://drive.google.com/file/d/173yweFZNQhNUycBPQAJCnlsaUFCovIIj/view"
    elif "tableau" in bi_pipeline_tool:
        external_url = "https://drive.google.com/file/d/1aXO54XPyK1-dhleV_e4ojfwrnDjFSc7v/view"
    else:
        external_url = "https://drive.google.com/file/d/1aXO54XPyK1-dhleV_e4ojfwrnDjFSc7v/view"
    event_data = {
        "pipeline_key": bi_pipeline_name,
        "run_key": run_key,
        "status": "RUNNING",
        "external_url": external_url,
        "component_tool": bi_pipeline_tool,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=46)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    # step 1: Export from API
    event_data = {
        "pipeline_key": bi_pipeline_name,
        "run_key": run_key,
        "status": "RUNNING",
        "task_name": "API Interaction",
        "task_key": "API-Interaction",
        "external_url": external_url,
        "component_tool": bi_pipeline_tool,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=47)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    message_data = {
        "pipeline_key": bi_pipeline_name,
        "run_key": run_key,
        "log_level": "INFO",
        "task_name": "API Interaction",
        "task_key": "API-Interaction",
        "component_tool": bi_pipeline_tool,
        "external_url": external_url,
        "message": "INFO: Running API Export ...",
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=50)).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**message_data))

    event_data = {
        "pipeline_key": bi_pipeline_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "task_name": "API Interaction",
        "task_key": "API-Interaction",
        "component_tool": bi_pipeline_tool,
        "external_url": external_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=53)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    # step 2: Run Test in DataKitchen
    event_data = {
        "pipeline_key": bi_pipeline_name,
        "run_key": run_key,
        "status": "RUNNING",
        "task_name": f"Test_{bi_pipeline_tool.capitalize()}",
        "task_key": f"Test_{bi_pipeline_tool.capitalize()}",
        "component_tool": bi_pipeline_tool,
        "external_url": external_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=54)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    message_data = {
        "pipeline_key": bi_pipeline_name,
        "run_key": run_key,
        "log_level": "INFO",
        "task_name": f"Test_{bi_pipeline_tool.capitalize()}",
        "task_key": f"Test_{bi_pipeline_tool.capitalize()}",
        "component_tool": bi_pipeline_tool,
        "message": "running tests in DataKitchen Automation",
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=55)).isoformat()
    }
    config.events_api_client.post_message_log(MessageLogEventApiSchema(**message_data))
    test_data = {
        "pipeline_key": bi_pipeline_name,
        "run_key": run_key,
        "task_name": f"Test_{bi_pipeline_tool.capitalize()}",
        "task_key": f"Test_{bi_pipeline_tool.capitalize()}",
        "external_url": "https://cloud.datakitchen.io/#/orders/im/IM_Demo_GCP_Production/runs/a72c299e-9fe5-11ec-af6a-32a3999d1b3e",
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=56)).isoformat(),
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "test_outcomes": [
            {
                "metric_value": "739445.57",
                "max_threshold": "739445.57",
                "name": "test_output_value",
                "status": "PASSED",
                "description": "Evaluate the test output parameter for a power_bi Notebook Test"
            }
        ]
    }
    config.events_api_client.post_test_outcomes(TestOutcomesApiSchema(**test_data))
    event_data = {
        "pipeline_key": bi_pipeline_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "task_name": f"Test_{bi_pipeline_tool.capitalize()}",
        "task_key": f"Test_{bi_pipeline_tool.capitalize()}",
        "component_tool": bi_pipeline_tool,
        "external_url": "https://cloud.datakitchen.io/#/orders/im/IM_Demo_GCP_Production/runs/a72c299e-9fe5-11ec-af6a-32a3999d1b3e",
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=58)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    event_data = {
        "pipeline_key": bi_pipeline_name,
        "run_key": run_key,
        "status": "COMPLETED",
        "component_tool": bi_pipeline_tool,
        "external_url": external_url,
        "payload_keys": ["marketing_business_customer", "sales_business_customer"],
        "event_timestamp": (my_datetime - timedelta(hours=a_start) + timedelta(minutes=60)).isoformat()
    }
    config.events_api_client.post_run_status(RunStatusApiSchema(**event_data))
    time.sleep(1)


def send_dashboard_model_events(
        config: Config,
        demo_pipeline_name: str,
        demo_pipeline_tool: str,
        db_job_name: str,
        db_job_tool: str,
        python_pipeline_name: str,
        python_pipeline_tool: str,
        bi_pipeline_name: str,
        bi_pipeline_tool: str,
        server_name: str,
        server_tool: str,
        backdate_hours: int
) -> None:
    my_datetime = datetime.now().astimezone()
    a_start = backdate_hours
    file_path = Path(__file__).parent / Path("input_data/demo_pipeline_tests.csv")
    test_data = read_csv(file_path)

    try:
        run_demo_pipeline_component(config, demo_pipeline_name, demo_pipeline_tool, my_datetime, a_start, test_data)
        run_server_component(config, server_name, server_tool, my_datetime, a_start)
        run_query_job_component(config, db_job_name, db_job_tool, my_datetime, a_start)
        run_python_component(config, python_pipeline_name, python_pipeline_tool, my_datetime, a_start)
        run_bi_component(config, bi_pipeline_name, bi_pipeline_tool, my_datetime, a_start)
    except ApiException as e:
        print(f"Exception when calling send_azure_journey_events:\n{e}")


def create_dashboard_model_journey(
        config: Config,
) -> None:
    journey_components_dict = DASHBOARD_COMPONENTS
    create_component_list = journey_components_dict.get(config.cloud_provider)

    delete_journey(config, DASHBOARD_JOURNEY)
    journey_id = create_data_journey(config, DASHBOARD_JOURNEY)

    components = {}

    for name, tool in create_component_list:
        component_id = ""
        if "Personal_Compute_Cluster" in name:
            component_id = create_server_component(config, name, component_tool=tool)
        else:
            component_id = create_batch_pipeline_component(config, name, component_tool=tool)
        components[name] = component_id

    demo_pipeline_name, demo_pipeline_tool = create_component_list[0]
    python_pipeline_name, python_pipeline_tool = create_component_list[1]
    db_job_name, db_job_tool = create_component_list[2]
    bi_pipeline_name, bi_pipeline_tool = create_component_list[3]
    server_name, server_tool = create_component_list[4]

    # Link components
    dk_demo_pipeline_id = components[demo_pipeline_name]
    python_pipeline_id = components[python_pipeline_name]
    db_pipeline_id = components[db_job_name]
    bi_pipeline_id = components[bi_pipeline_name]
    server_id = components[server_name]

    link_components_in_journey(
        config,
        journey_id,
        dk_demo_pipeline_id,
        python_pipeline_id
    )
    link_components_in_journey(
        config,
        journey_id,
        dk_demo_pipeline_id,
        db_pipeline_id
    )
    link_components_in_journey(
        config,
        journey_id,
        python_pipeline_id,
        bi_pipeline_id
    )
    link_components_in_journey(
        config,
        journey_id,
        db_pipeline_id,
        bi_pipeline_id
    )
    link_components_in_journey(
        config,
        journey_id,
        right_component_id=server_id
    )

    create_journey_instance_condition(
        config,
        journey_id,
        json_body={
            "action": "START",
            "batch_pipeline": dk_demo_pipeline_id
        }
    )
    create_journey_instance_condition(
        config,
        journey_id,
        json_body={
            "action": "END",
            "batch_pipeline": bi_pipeline_id
        }
    )

    create_journey_instance_condition(
        config,
        journey_id,
        json_body={
            "action": "END_PAYLOAD",
            "batch_pipeline": bi_pipeline_id
        }
    )
    email_rule_json = {
        "action": "SEND_EMAIL",
        "rule_schema": "simple_v1",
        "action_args": {
            "template": "NotifyTemplate",
            "recipients": ["dk@example.com"]
        },
        "rule_data": {
            "when": "all",
            "conditions": [{"run_state": {"matches": "FAILED"}}]
        }
    }

    jira_webhook_rule_json = {
        "action": "CALL_WEBHOOK",
        "rule_schema": "simple_v1",
        "action_args": {
            "headers": [],
            "payload": {"text": "Relevant details {datapoint.from_table}"},
            "url": "https://hooks.jira.com/rest/webhooks/1.0/webhook/tHisv6jIss9lA9daDummyni8ID"
        },
        "rule_data": {
            "when": "all",
            "conditions": [{"run_state": {"matches": "LATE_START"}}]
        }
    }

    jira_webhook_rule_2_json = {
        "action": "CALL_WEBHOOK",
        "rule_schema": "simple_v1",
        "action_args": {
            "headers": [],
            "payload": {"text": "Relevant details {datapoint.from_table}"},
            "url": "https://hooks.slack.com/services/tHisv6jIs/s9lA9da/Dummyni8ID"
        },
        "rule_data": {
            "when": "all",
            "conditions": [{"run_state": {"matches": "LATE_END"}}]
        }
    }

    metric_rule_json = {
        "action": "SEND_EMAIL",
        "rule_schema": "simple_v1",
        "component": server_id,
        "action_args": {
            "template": "NotifyTemplate",
            "recipients": ["dk@example.com"]
        },
        "rule_data": {
            "conditions": [
                {
                    "metric_log": {
                        "key": "Cluster Capacity Percentage",
                        "operator": "gt",
                        "static_value": 80
                    }
                }
            ],
            "when": "all"
        }
    }

    out_of_order_rule_json = {
        "action": "SEND_EMAIL",
        "rule_schema": "simple_v1",
        "action_args": {
            "template": "NotifyTemplate",
            "recipients": ["dk@example.com"]
        },
        "rule_data": {
            "conditions": [
                {
                    "instance_alert": {
                        "type_matches": ["OUT_OF_SEQUENCE"]
                    }
                }
            ],
            "when": "all"
        }
    }

    for rule_json in [
        email_rule_json,
        jira_webhook_rule_json,
        jira_webhook_rule_2_json,
        metric_rule_json,
        out_of_order_rule_json
    ]:
        create_journey_rule(
            config,
            journey_id,
            json_body=rule_json
        )

    send_dashboard_model_events(
        config,
        demo_pipeline_name,
        demo_pipeline_tool,
        db_job_name,
        db_job_tool,
        python_pipeline_name,
        python_pipeline_tool,
        bi_pipeline_name,
        bi_pipeline_tool,
        server_name,
        server_tool,
        backdate_hours=1
    )
