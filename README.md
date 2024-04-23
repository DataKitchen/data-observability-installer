# DataKitchen Data Observability Installer 
![apache 2.0 license Badge](https://img.shields.io/badge/License%20-%20Apache%202.0%20-%20blue) 
![PRs Badge](https://img.shields.io/badge/PRs%20-%20Welcome%20-%20green) 
[![Documentation](https://img.shields.io/badge/docs-On%20datakitchen.io-06A04A?style=flat)](https://docs.datakitchen.io/articles/#!open-source-data-observability/data-observability-overview) 
[![Static Badge](https://img.shields.io/badge/Slack-Join%20Discussion-blue?style=flat&logo=slack)](https://data-observability.slack.com)

*<p style="text-align: center;">Data breaks. Servers break. Your toolchain breaks. Ensure your data team is the first to know and the first to solve with visibility across and down your data estate. Save time with simple, fast data quality test generation and execution. Trust your data, tools, and systems end to end.</p>*

This repo contains the installer and quickstart setup for the DataKitchen Open Source Data Observability product suite (released April 2024).
* [**DataOps TestGen**](https://docs.datakitchen.io/articles/dataops-testgen-help/dataops-testgen-help) is a data quality verification tool that does five main tasks: (1) data profiling, (2) new dataset screening and hygiene review, (3) algorithmic generation of data quality validation tests, (4) ongoing production testing of new data refreshes and (5) continuous periodic monitoring of datasets for anomalies [(GitHub)](https://github.com/DataKitchen/dataops-testgen).
* [**DataOps Observability**](https://docs.datakitchen.io/articles/dataops-observability-help/dataops-observability-help) monitors every tool used in the journey of data from data source to customer value, from any team development environment into production, across every tool, team, data set, environment, and project so that problems are detected, localized, and understood immediately [(GitHub)](https://github.com/DataKitchen/dataops-observability).

![DatKitchen Open Source Data Observability](https://datakitchen.io/wp-content/uploads/2024/04/both-products.png)

For background on why we build this product check out the articles on ['why we open sourced'](https://datakitchen.io/why-we-open-sourced-our-data-observability-products/), [manifesto](https://datajourneymanifesto.org/), [free book](https://datakitchen.io/the-dataops-cookbook/), and [top data observability and DataOps articles](https://datakitchen.io/datakitchen-resource-guide-to-data-journeys-data-observability-dataops/).

## Prerequisites

### Install the required software

| Software                | Tested Versions               | Command to check version                |
|-------------------------|-------------------------|-------------------------------|
| **Requirements for TestGen & Observability**
| [Python](https://www.python.org/downloads/) <br/>- Most Linux and macOS systems have Python pre-installed. <br/>- On Windows machines, you will need to download and install it.        | 3.9, 3.10, 3.11, 3.12                | `python3 --version`                |
| [Docker](https://docs.docker.com/get-docker/) <br/>[Docker Compose](https://docs.docker.com/compose/install/) (pre-installed with Docker Desktop)           | 25.0.3 <br/> 2.24.6        | `docker -v` <br/> `docker compose version`         |
|  **Additional Requirements for Observability** |
| [Minikube](https://minikube.sigs.k8s.io/docs/start/)         | 1.32.0                | `minikube version`                |
| [Helm](https://helm.sh/docs/intro/install/)            | 3.13.3, 3.14.3        | `helm version`         |
| Minikube Driver <br/>- macOS on Intel chip: [HyperKit](https://minikube.sigs.k8s.io/docs/drivers/hyperkit/) <br/>- Other operating systems: [Docker](https://minikube.sigs.k8s.io/docs/drivers/docker/) | <br/>0.20210107 <br/> 25.0.3             | <br/>`hyperkit -v` <br/>`docker -v`         |

### Download the installer

On Unix-based operating systems, use the following command to download it to the current directory. We recommend creating a new, empty directory.

```shell
curl -o dk-installer.py 'https://raw.githubusercontent.com/DataKitchen/data-observability-installer/main/dk-installer.py'
```

* Alternatively, you can manually download the [`dk-installer.py`](https://github.com/DataKitchen/data-observability-installer/blob/main/dk-installer.py) file from this repo.
* All commands listed below should be run from the folder containing this file.
* For usage help and command options, run `python3 dk-installer.py --help` or `python3 dk-installer.py <command> --help`.

### Temporary Prerequisites

Until we make the images public, Docker credentials with access to the `datakitchen` namespace have to be configured on your machine.

* Ask the development team for credentials.
* On Docker Desktop, login with the credentials.
* On the terminal, set these environment variables.
  * `export DOCKER_USERNAME=<username>`
  * `export DOCKER_PASSWORD=<password>`

## Quickstart Guide

The [Data Observability quickstart](https://docs.datakitchen.io/articles/open-source-data-observability/data-observability-overview) walks you through Dataops Observability and TestGen capabilities to demonstrate how our products cover critical use cases for data and analytic teams.

Before going through the quickstart, complete the prequisites above and then the following steps to install the two products and setup the demo data. For any of the commands, you can view additional options by appending `--help` at the end.

### Install the TestGen application

The installation downloads the latest Docker images for TestGen and deploys a new Docker Compose application. The process may take 5~10 minutes depending on your machine and network connection. 

```shell
python3 dk-installer.py tg install
```
Once it completes, verify that you can login to the UI with the URL and credentials provided in the output.

### Install the Observability application

The installation downloads the latest Helm charts and Docker images for Observability and deploys the application on a new minikube cluster. The process may take 5~30 minutes depending on your machine and network connection. 
```shell
python3 dk-installer.py obs install
```

For *Windows* and *macOS running M-series (ARM) chip* only: The Docker driver is not allowed to expose the HTTP ports to the host machine, so the following command has to be run after the install to access the application. Leave this process running, and continue the next steps on another terminal window.

```shell
python3 dk-installer.py obs expose
```
Verify that you can login to the UI with the URL and credentials provided in the output.

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
**Patch (or pushback)**: New data analysis and cleansing|Before New Data Sources Are Added To Production|Evaluate new data, find data hygiene issues, and communicate with your data providers.|DataOps TestGen's data profiling of 51 data characteristics, then 27 data hygiene detector suggestions; UI to review and disposition|Save time, lower errors, improve data quality
**Poll**: Updates to existing data sources; Data ingestion monitoring|Continually|Find anomalies in data updates and notify the proper party in the right place.|DataOps TestGen's auto-generation of data anomaly tests: freshness, schema, volume, and data drift checks.  DataOps Observability Data Journeys, overview UI, and notification rules and limits|Find problem data quickly, save time, lower errors
**Production**:  Monitoring of multi-tool, multi-data sets, multi-hop, data analytic production processes.|During The Production Cycle|Find data, SLA, and toolchain problems, local quickly, and notify quickly.|DataOps TestGen's auto-generation of 32 data quality validation tests based on data profiling. 2 custom test types. Fast in database SQL execution (no data copies). DataOps Observability's end-to-end Data Journeys are digital twins that represent your entire process and allow you to find, alert, and fix quickly.|Stop embarrassing customer errors, gain customer data trust, lower errors, improve team productvity
**Push**: Development Unit, Regression Tests, and Impact Assessment.|During The Development Process|Find problems in data or tools in development to validate code/configuration changes.|The combination of DataOps Observability and DataOps TestGen can be run in your development environment against test data to provide functional, unit, and regression tests.|Improve the speed and lower the risk of changes to production, less wasted time, improve productivity
**Parallel**: Checking data accuracy during Data Migration projects: "Does It Match'?|During a Data Migration Process|Checking two data similar data sets or processes so they produce the same results.|DataOps TestGen can find errors between migrated data sets by comparing source and target data quality tests. DataOps Observability can monitor legacy tools and migrated cloud tools at the same time.|Lower risk of data errors, improve project delivery time

## Community

### Getting Started Guide
We recommend you start by going through the [Data Observability Overview Demo](https://docs.datakitchen.io/articles/open-source-data-observability/data-observability-overview).

### Connect
Talk and Learn with other data practitioners who are building with DataKitchen. Share knowledge, get help, and contribute to our open-source project. 

Join our community here:

* 🌟 [Star us on GitHub](https://github.com/DataKitchen/data-observability-installer)

* 🐦 [Follow us on Twitter](https://twitter.com/i/flow/login?redirect_after_login=%2Fdatakitchen_io)

* 🕴️ [Follow us on LinkedIn](https://www.linkedin.com/company/datakitchen)

* 📺 [Get Free DataOps Fundamentals Certification](https://info.datakitchen.io/training-certification-dataops-fundamentals)

* 📚 [Read our blog posts](https://datakitchen.io/blog/)

* 👋 [Join us on Slack](https://data-observability.slack.com)

* 🗃 [Sign The DataOps Manifesto](https://DataOpsManifesto.org)

* 🗃 [Sign The Data Journey Manifesto](https://DataJourneyManifesto.org)


### Contributing
For details on contributing or running the project for development, check out our contributing guide (coming soon!).

### License
DataKitchen DataOps Observability is Apache 2.0 licensed.
