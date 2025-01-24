# Build Windows Installer

This document describes how to build dk-installer as a Windows executable file.

### Prerequisites

- Windows operating system
- [Python 3](https://www.python.org/downloads/)

### Set up virtual environment

From the root of your local repository, create and activate a virtual environment.

```powershell
py -3.12 -m venv venv
venv\Scripts\activate
```

### Install dependencies

Install the Python dependencies in editable mode.

```powershell
pip install -e ".[dev]"
```

### Generate Executable File

Run PyInstaller to generate the package.
```powershell
pyinstaller dk-installer.py --onefile
```

This will create a `dk-installer.spec` file, `dist` and `build` folders. 

The executable can be found in `dist/dk-installer.exe`.
