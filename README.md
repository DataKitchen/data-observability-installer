# DataKitchen Data Observability Installer 
![apache 2.0 license Badge](https://img.shields.io/badge/License%20-%20Apache%202.0%20-%20blue) 
![PRs Badge](https://img.shields.io/badge/PRs%20-%20Welcome%20-%20green) 
[![Documentation](https://img.shields.io/badge/docs-On%20datakitchen.io-06A04A?style=flat)](https://docs.datakitchen.io/articles/#!open-source-data-observability/data-observability-overview) 
[![Static Badge](https://img.shields.io/badge/Slack-Join%20Discussion-blue?style=flat&logo=slack)](https://data-observability-slack.datakitchen.io/join)

*<p style="text-align: center;">Data breaks. Servers break. Your toolchain breaks. Ensure your data team is the first to know and the first to solve with visibility across and down your data estate. Save time with simple, fast data quality test generation and execution. Trust your data, tools, and systems from end to end.</p>*

This repo contains the installer and quickstart setup for the DataKitchen Open Source Data Observability product suite.
* [**DataOps Data Quality TestGen**](https://docs.datakitchen.io/articles/dataops-testgen-help/dataops-testgen-help) is a data quality verification tool that does five main tasks: (1) data profiling, (2) new dataset screening and hygiene review, (3) algorithmic generation of data quality validation tests, (4) ongoing production testing of new data refreshes and (5) continuous periodic monitoring of datasets for anomalies.
* [**DataOps Observability**](https://docs.datakitchen.io/articles/dataops-observability-help/dataops-observability-help) monitors every tool used in the data journey, from source to customer value, across all  environments, tools, teams, datasets, and databases, enabling immediate detection, localization, and understanding of problems.


[![DataKitchen Open Source Data Observability](https://datakitchen.io/wp-content/uploads/2024/04/both-products.png)](https://datakitchen.storylane.io/share/byag8vimd5tn)
[Interactive Product Tour](https://datakitchen.storylane.io/share/byag8vimd5tn)

## Features

What does DataKitchen's  Open Source Data Observability do?  It helps you understand and <b>find data issues in new data</b>. 
<p align="center">
<img alt="DatKitchen Open Source Data Observability Features - New Data" src="https://datakitchen.io/wp-content/uploads/2024/06/Quick-over-view.png" width="70%" >
</p>
It constantly <b>watches your data for data quality anomalies</b> and alerts you of problems.
<br></br>
<p align="center">
<img alt="DatKitchen Open Source Data Observability Features - Data Ingestion and Polling" src="https://datakitchen.io/wp-content/uploads/2024/06/Quick-over-view-1.png" width="70%" >
</p>
It monitors <b>multi-tool, multi-data set, multi-hop data analytic production</b> processes.  
<br></br>
<p align="center">
<img alt="DatKitchen Open Source Data Observability Features - Data Production" src="https://datakitchen.io/wp-content/uploads/2024/06/Quick-over-view-2.png" width="70%" >
</p>
And it allows you to <b>make fast, safe development changes</b>.
<br></br>
<p align="center">
<img alt="DatKitchen Open Source Data Observability Features - Development CI-CD" src="https://datakitchen.io/wp-content/uploads/2024/06/Quick-over-view-4.png" width="70%" >
</p>


## Prerequisites

### Minimum system requirements

- 2 CPUs
- 8 GB memory
- 20 GB disk space

### Install the required software

#### Requirements for TestGen & Observability

| Software                | Tested Versions               | Command to check version                |
|-------------------------|-------------------------|-------------------------------|
| [Python](https://www.python.org/downloads/) <br/>- Most Linux and macOS systems have Python pre-installed. <br/>- On Windows machines, you will need to download and install it.        | 3.9, 3.10, 3.11, 3.12                | `python3 --version`                |
| [Docker](https://docs.docker.com/get-docker/) <br/>[Docker Compose](https://docs.docker.com/compose/install/)         | 26.1, 27.5, 28.1 <br/> 2.34, 2.35, 2.36        | `docker -v` <br/> `docker compose version`         |

#### Additional Requirements for Observability only

| Software                | Tested Versions               | Command to check version                |
|-------------------------|-------------------------|-------------------------------|
| [Minikube](https://minikube.sigs.k8s.io/docs/start/)         | 1.33, 1.34, 1.35                | `minikube version`                |
| [Helm](https://helm.sh/docs/intro/install/)            | 3.15, 3.16, 3.17        | `helm version`         |

### Download the installer

On Unix-based operating systems, use the following command to download it to the current directory. We recommend creating a new, empty directory.

```shell
curl -o dk-installer.py 'https://raw.githubusercontent.com/DataKitchen/data-observability-installer/main/dk-installer.py'
```

* Alternatively, you can manually download the [`dk-installer.py`](https://github.com/DataKitchen/data-observability-installer/blob/main/dk-installer.py) file from this repo.
* All commands listed below should be run from the folder containing this file.
* For usage help and command options, run `python3 dk-installer.py --help` or `python3 dk-installer.py <command> --help`.

On Windows operating systems, you can also download the executable file [`dk-installer.exe`](https://github.com/DataKitchen/data-observability-installer/releases/download/latest/dk-installer.exe) and run it by double-clicking the file.

## Quickstart Guide

The [Data Observability quickstart](https://docs.datakitchen.io/articles/open-source-data-observability/data-observability-overview) walks you through Dataops Observability and TestGen capabilities to demonstrate how our products cover critical use cases for data and analytic teams.

Before going through the quickstart, complete the prequisites above and then the following steps to install the two products and setup the demo data. For any of the commands, you can view additional options by appending `--help` at the end.

### Install the TestGen application

The installation downloads the latest Docker images for TestGen and deploys a new Docker Compose application. The process may take 5~10 minutes depending on your machine and network connection.

```shell
python3 dk-installer.py tg install
```
The `--port` option may be used to set a custom localhost port for the application (default: 8501).

To enable SSL for HTTPS support, use the `--ssl-cert-file` and `--ssl-key-file` options to specify local file paths to your SSL certificate and key files.

Once the installation completes, verify that you can login to the UI with the URL and credentials provided in the output.

### Install the Observability application

The installation downloads the latest Helm charts and Docker images for Observability and deploys the application on a new minikube cluster. The process may take 5~30 minutes depending on your machine and network connection. 
```shell
python3 dk-installer.py obs install
```
#### Bind HTTP ports to host machine

This step is required to access the application when using Docker driver on Mac or Windows. It may also be useful for installations on remote machines to access the UI from a local browser.

```shell
python3 dk-installer.py obs expose
```
The `--port` option may be used to set a custom localhost port for the application (default: 8082).

Verify that you can login to the UI with the URL and credentials provided in the output. Leave this process running, and continue the next steps on another terminal window.

### Run the TestGen demo setup

The `demo-config.json` file generated by the Observability installation must be present in the folder.

```shell
python3 dk-installer.py tg run-demo --export
```
In the TestGen UI, you will see that new data profiling and test results have been generated. Additionally, in the Observavility UI, you will see that new test outcome events have been received.

### Run the Observability demo setup

The `demo-config.json` file generated by the Observability installation must be present in the folder.

```shell
python3 dk-installer.py obs run-demo
```
In the Observability UI, you will see that new journeys and events have been generated.

### Run the Agent Heartbeat demo setup

The `demo-config.json` file generated by the Observability installation must be present in the folder.

```shell
python3 dk-installer.py obs run-heartbeat-demo
```
In the Observability UI, you will see that new agents have been generated on the Integrations page.

Leave this process running, and continue with the [quickstart guide](https://docs.datakitchen.io/articles/open-source-data-observability/data-observability-overview) to tour the applications.

## Product Documentation

[DataOps TestGen](https://docs.datakitchen.io/articles/dataops-testgen-help/dataops-testgen-help)

[DataOps Observability](https://docs.datakitchen.io/articles/dataops-observability-help/dataops-observability-help)

## Useful Commands

### DataOps TestGen

The [docker compose CLI](https://docs.docker.com/compose/reference/) can be used to operate the installed TestGen application. All commands must be run in the same folder that contains the `docker-compose.yaml` file generated by the installation.

Access the _testgen_ CLI: `docker compose exec engine bash` (use `exit` to return to the regular terminal)

Stop the app: `docker compose down`

Restart the app: `docker compose up`

Upgrade the app to latest version: `python3 dk-installer.py tg upgrade`

### DataOps Observability

The [minikube](https://minikube.sigs.k8s.io/docs/commands/) and [kubectl](https://kubernetes.io/docs/reference/kubectl/) command line tools can be used to operate the Observability application.

Inspect the pods: `kubectl get pods`

Get pod logs: `kubectl logs <POD ID>`

Stop the app: `minikube stop`

Restart the app: `minikube start`

## Remove Demo Data 

After completing the quickstart, you can remove the demo data from the applications with the following steps.

### Stop the Agent Heartbeat demo

Stop the process that is running the Agent Heartbeat demo using `Ctrl + C`.

*Note*: Currently, the agents generated by the heartbeat demo are not cleaned up.

### Remove TestGen & Observability demo data

The `demo-config.json` file generated by the Observability installation must be present in the folder.

```shell
python3 dk-installer.py tg delete-demo
python3 dk-installer.py obs delete-demo
```

## Uninstall Applications

### Uninstall TestGen
```shell
python3 dk-installer.py tg delete
```

### Uninstall Observability
```shell
python3 dk-installer.py obs delete
```

## Use Cases for Data Observability

**Data Analytics Use Case**|**When Does it Happen**|**Data Observability Challenge**|**Key Data Observability Product Feature**|**Key Benefit**
:-----:|:-----:|:-----:|:-----:|:-----:
[**Patch (or pushback)**](https://datakitchen.io/the-five-use-cases-in-data-observability-part-1/): New data analysis and cleansing|Before New Data Sources Are Added To Production|Evaluate new data, find data hygiene issues, and communicate with your data providers.|DataOps TestGen's data profiling of 51 data characteristics, then 27 data hygiene detector suggestions; UI to review and disposition|Save time, lower errors, improve data quality
[**Poll**](https://datakitchen.io/the-five-use-cases-in-data-observability-part-2): Updates to existing data sources; Data ingestion monitoring|Continually|Find anomalies in data updates and notify the proper party in the right place.|DataOps TestGen's auto-generation of data anomaly tests: freshness, schema, volume, and data drift checks.  DataOps Observability Data Journeys, overview UI, and notification rules and limits|Find problem data quickly, save time, lower errors
[**Production**](https://datakitchen.io/the-five-use-cases-in-data-observability-part-3):  Monitoring of multi-tool, multi-data sets, multi-hop, data analytic production processes.|During The Production Cycle|Find data, SLA, and toolchain problems, local quickly, and notify quickly.|DataOps TestGen's auto-generation of 32 data quality validation tests based on data profiling. 2 custom test types. Fast in database SQL execution (no data copies). DataOps Observability's end-to-end Data Journeys are digital twins that represent your entire process and allow you to find, alert, and fix quickly.|Stop embarrassing customer errors, gain customer data trust, lower errors, improve team productivity
[**Push**](https://datakitchen.io/the-five-use-cases-in-data-observability-part-4): Development Unit, Regression Tests, and Impact Assessment.|During The Development Process|Find problems in data or tools in development to validate code/configuration changes.|The combination of DataOps Observability and DataOps TestGen can be run in your development environment against test data to provide functional, unit, and regression tests.|Improve the speed and lower the risk of changes to production, less wasted time, improve productivity
[**Parallel**](https://datakitchen.io/the-five-use-cases-in-data-observability-part-5): Checking data accuracy during Data Migration projects: "Does It Match'?|During a Data Migration Process|Checking two data similar data sets or processes so they produce the same results.|DataOps TestGen can find errors between migrated data sets by comparing source and target data quality tests. DataOps Observability can monitor legacy tools and migrated cloud tools at the same time.|Lower risk of data errors, improve project delivery time



## Community

### Getting Started Guide
We recommend you review the [Data Observability Overview Demo](https://docs.datakitchen.io/articles/open-source-data-observability/data-observability-overview).

### Support
For support requests, [join the Data Observability Slack](https://data-observability-slack.datakitchen.io/join) and ask post on #support channel.

### Connect
Talk and Learn with other data practitioners who are building with DataKitchen. Share knowledge, get help, and contribute to our open-source project. 

Join our community here:

* 🌟 [Star us on GitHub](https://github.com/DataKitchen/data-observability-installer)

* 🐦 [Follow us on Twitter](https://twitter.com/i/flow/login?redirect_after_login=%2Fdatakitchen_io)

* 🕴️ [Follow us on LinkedIn](https://www.linkedin.com/company/datakitchen)

* 📺 [Get Free Data Observability and Data Quality Testing Certificationn](https://info.datakitchen.io/webinar-2024-05-video-form-data-observability-and-data-quality-testing-certification-series)

* 📺 [Get Free DataOps Fundamentals Certification](https://info.datakitchen.io/training-certification-dataops-fundamentals)

* 📚 [Read our blog posts](https://datakitchen.io/blog/)

* 👋 [Join us on Slack](https://data-observability-slack.datakitchen.io/join)

* 🗃 [Sign The DataOps Manifesto](https://DataOpsManifesto.org)

* 🗃 [Sign The Data Journey Manifesto](https://DataJourneyManifesto.org)


### Contributing
For details on contributing or running the project for development, check out our [contributing guide](https://github.com/DataKitchen/data-observability-installer/blob/main/CONTRIBUTING.md).

### License
DataKitchen DataOps Observability is Apache 2.0 licensed.
