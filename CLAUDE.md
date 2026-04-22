# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

This repo ships **one artifact**: `dk-installer.py` — a single-file, stdlib-only Python script that end users download and run to install/upgrade/start/demo the open-source DataKitchen products (TestGen and DataOps Observability) locally. TestGen supports both Docker Compose and pip-via-uv install modes; Observability is Docker-only. On Windows it is also packaged as `dk-installer.exe` via PyInstaller (see `.github/workflows/release_exe.yml`).

The `demo/` directory is a separate deliverable: it is built into the `datakitchen/data-observability-demo` Docker image that `dk-installer.py` pulls at runtime to generate demo data. It is **not** imported by the installer.

## Common commands

```bash
pip install .[dev,test]                 # install ruff + pytest (project has no runtime deps)

ruff check --show-fixes                 # lint (CI-enforced)
ruff format --check --diff              # format check (CI-enforced)
ruff format                             # apply formatting

pytest                                  # run full test suite
pytest tests/test_tg_install.py         # single file
pytest tests/test_action.py::test_name  # single test
pytest -m unit                          # only unit-marked tests
pytest -m integration                   # only integration-marked tests
pytest --cov --cov-report=term-missing  # with coverage (matches CI)

python3 dk-installer.py --help          # see installer CLI
python3 dk-installer.py tg install      # run an action locally during dev
```

Building the Windows `.exe` happens automatically on every push to `main` (`release_exe.yml` → PyInstaller → GitHub Release tagged `latest`). For local builds on Windows, see `docs/build_windows_installer.md`.

## Architecture: how `dk-installer.py` is organized

The installer is a ~2300-line single file intentionally using only the Python stdlib — users run it without installing any packages. Do not introduce third-party runtime dependencies.

Core abstractions (all in `dk-installer.py`):

- **`Installer`** — top-level argparse wrapper. `get_installer_instance()` at the bottom of the file registers the two products (`obs`, `tg`) and their actions. Each product sets compose-file defaults (`compose_file_name`, `compose_project_name`) that flow into actions via argparse `set_defaults`.
- **`Action`** — base class for one CLI subcommand (e.g., `tg install`). Owns session-scoped concerns: creates a timestamped log folder under `.dk-installer/` (or `%LOCALAPPDATA%/DataKitchenApps/` on Windows), configures logging, zips logs on exit, wraps execution in `AnalyticsWrapper`, enforces `requirements` (list of `Requirement` objects that shell out to check `docker`, `docker compose`, etc.), and provides `run_cmd` / `run_cmd_retries` — always use these rather than raw `subprocess` so output is captured per-command into the session zip.
- **`MultiStepAction`** — `Action` subclass that declares a `steps: list[type[Step]]`. Each `Step` has `pre_execute` (run for all steps before any executes — validation phase) then `execute` (the actual work). On any step failure, remaining steps are skipped and `on_action_fail` runs in reverse order; on success, `on_action_success` runs in reverse order. **Most install/upgrade actions are `MultiStepAction`s** — when adding a new install phase, write a new `Step` class and add it to the list.
- **`Step`** — unit of work inside a `MultiStepAction`. Steps share state via `action.ctx` (a dict on the parent action). Raising `SkipStep` from `execute` marks it SKIPPED; raising any other exception marks it FAILED and aborts the action if `required = True`.
- **`ComposeActionMixin` / `ComposeDeleteAction` / `ComposePullImagesStep` / `ComposeStartStep` / `CreateComposeFileStepBase`** — shared building blocks for both products. `Obs*` and `TestGen*` classes specialize these.
- **`AnalyticsWrapper`** — sends anonymous Mixpanel events for each action (disabled with `--no-analytics` or `DK_INSTALLER_ANALYTICS=no`). Instance ID is persisted to `.dk-installer/instance.txt`. Don't log PII here.
- **`Console`** (global `CONSOLE`) — all user-facing output goes through this; don't use bare `print` for user messages (the menu code and `collect_user_input` are the exceptions).
- **`Menu`** / `show_menu` — only used when the frozen Windows `.exe` is launched with no arguments (double-click). Not part of the CLI flow on Unix.

The action registry in `get_installer_instance()` is the authoritative list of user-facing commands — to add a new command, add an `Action` subclass there.

### TestGen install modes

TestGen has two install modes: `docker` (Compose) and `pip` (uv-managed venv with embedded Postgres). Mode is recorded at install time in a JSON marker file (`dk-tg-install.json`) so `tg upgrade` / `tg delete` / `tg start` / `tg run-demo` / `tg delete-demo` know which path to take.

The five `Testgen*Action` classes that span both modes follow a unified pattern:
- `_per_invocation_attrs` includes `_resolved_mode` (and `steps` / `intro_text` for the `MultiStepAction`-based ones) so menu re-runs start clean.
- `check_requirements` resolves mode once via `_resolve_install_mode`, then calls `super().check_requirements`.
- `_resolve_install_mode` reads the marker (or runs auto-detect for `install`), sets `self._resolved_mode`, optionally records `analytics["install_mode"]`. Install/upgrade/start/run-demo abort when no install exists; delete and delete-demo are idempotent (return rather than raise).
- `get_requirements` reads `self._resolved_mode` — Docker reqs only when in Docker mode.
- `execute` branches on `self._resolved_mode`. For `MultiStepAction` subclasses, `self.steps` is also swapped at resolution time using class-level `pip_steps` / `docker_steps`.

The pip path bootstraps a pinned `uv` from the astral-sh GitHub release if one isn't already on PATH (see "Bumping uv" below), then runs `uv tool install` to put `dataops-testgen` in a managed venv. After install, the app is auto-started via `start_testgen_app` (foreground until Ctrl+C); `tg start` brings it up again later. TestGen reads its config from `~/.testgen/config.env` — port, SSL, and `TESTGEN_LOG_FILE_PATH` are all written there at standalone-setup time.

### Data locations at runtime

- Unix: installer writes the compose file, credentials file, and `demo-config.json` next to `dk-installer.py`; logs go to `./.dk-installer/<action>-<timestamp>.zip`.
- Windows: data and logs go to `%LOCALAPPDATA%/DataKitchenApps/`.

### Demo container

The `demo/` tree is built into a separate image (`datakitchen/data-observability-demo:latest`) via `demo/deploy/build-image`. `DemoContainerAction` in `dk-installer.py` pulls this image and mounts `demo-config.json` into it. Changes to `demo/*.py` don't affect the installer until that image is rebuilt and pushed.

### Bumping uv

The pip install path bootstraps a known version of `uv` from the astral-sh GitHub release. Two top-level constants govern this:

- `UV_VERSION` — the pinned version (e.g., `"0.11.7"`).
- `UV_ASSETS` — a `(platform.system(), platform.machine()) → (asset_name, sha256)` map. Six entries: Linux x86_64/aarch64, Darwin x86_64/arm64, Windows AMD64/ARM64.

To bump:

1. Update `UV_VERSION`.
2. Pull the matching `dist-manifest.json` from `https://github.com/astral-sh/uv/releases/download/<version>/dist-manifest.json` and refresh the SHA256 for each of the 6 assets in `UV_ASSETS`. Each release also publishes a `<asset>.sha256` file you can `curl` directly if you'd rather pin one at a time.
3. Sanity-check: `pytest tests/test_uv_bootstrap.py`. The bootstrap step exercises hash verification and the asset-not-supported path.

Do not skip the hash refresh — TLS verification is intentionally relaxed for the GitHub download (corp-proxy support), and the SHA256 pin is the security guarantee.

## Testing conventions

- `tests/installer.py` is a **symlink to `../dk-installer.py`** — tests import installer internals as `from tests.installer import ...`. Don't replace this with a copy.
- Heavy use of `unittest.mock.patch` to stub `subprocess` / `start_cmd` / `run_cmd`. The key fixtures live in `tests/conftest.py` — `action_cls` patches class-level attributes on `Action` so tests can instantiate actions without a real session folder, and `args_mock` provides a fully-populated `argparse.Namespace`.
- Tests are marked `@pytest.mark.unit` or `@pytest.mark.integration`. CI runs everything; use the markers locally to scope a run.

## Style

- Line length 120, double quotes, ruff-enforced (`pyproject.toml` restricts ruff's `include` to `dk-installer.py` only — the `demo/` and `tests/` trees are deliberately not linted by this project's ruff config).
- Pre-commit hooks run ruff on commit (`.pre-commit-config.yaml`). Install once with `pre-commit install`.
- Target Python is 3.9 (CI uses 3.9); avoid 3.10+ syntax like `match` statements or `X | Y` type unions in new code — the file uses `typing.Union` / `typing.Optional` deliberately for this reason.

## CI

`.github/workflows/pull_request.yml` runs ruff + pytest (with coverage comment) on every PR against `main`. `release_exe.yml` publishes the Windows `.exe` on every push to `main` by force-moving the `latest` tag and recreating the release — keep this in mind before merging, since each merge replaces the public download.
