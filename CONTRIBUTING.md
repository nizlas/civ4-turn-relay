# Contributing

Thank you for considering contributions to `civ4-turn-relay`.

## License

By submitting a contribution, you agree that it is licensed under the MIT License (see [`LICENSE`](LICENSE) and [`docs/licensing.md`](docs/licensing.md)).

## What not to contribute

- Do not copy code of unknown or incompatible origin.
- Do not copy source code, text, assets, or implementation details from the existing third-party PBEM manager that inspired this project's workflow.
- Never commit secrets, real save files, user data, or real server details (hosts, usernames, paths, keys, passwords).

## Development setup (Windows PowerShell)

These commands are the **P0 quality gates**. They must pass before closing related work:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy src tests
```

On Linux/macOS, create a Python 3.12 venv and run the same `python -m …` commands against that environment.

## Design expectations

These rules apply once application code exists:

- Functional changes should include tests.
- Domain logic should stay independent of GUI, filesystem, and network adapters.
- State transitions must be explicit and auditable.

There is no Contributor License Agreement (CLA) for this project at this time.
