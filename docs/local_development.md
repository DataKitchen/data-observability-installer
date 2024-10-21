# Local Environment Setup

This document describes how to set up your local environment for dk-installer development.

### Prerequisites

- [Python 3](https://www.python.org/downloads/)
- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)


### Install dependencies

Install the Python dependencies in editable mode.
```shell
# On Linux
pip install -e .[dev]

# On Mac
pip install -e .'[dev]'

#On Windows
pip install -e .'[dev]'
```


### Generate Executable File

In python's terminal
```
pyinstaller dk-installer.py --onefile
```
This will create a ```dk-installer.spec``` file, ```dist``` and ```build``` folders. Inside ```dist``` it will be the ```dk-installer.exe```.