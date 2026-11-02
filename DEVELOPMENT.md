# Development checks

The runtime is now C# with two executable projects in
[`CSharp/SimHub2SimRig.sln`](CSharp/SimHub2SimRig.sln). Build and run the native
regression tests on Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\CSharp\build.ps1 -Test
python .\CSharp\Tests\run_python_tests.py
```

See [CSharp/README.md](CSharp/README.md) for the process boundary, launch options,
test coverage and package build. Python remains the wire-format oracle and
legacy regression baseline; the C# applications never invoke Python.

Install optional development tools:

```powershell
python -m pip install -r Python/requirements.txt -r Python/requirements-dev.txt
```

Run the checks from this directory:

```powershell
python -m unittest discover -s Python/tests -t .
python -m pytest Python/tests
python -m ruff check --config Python/pyproject.toml Python CSharp/Tests
python -m pyright --project Python/pyproject.toml
```

The `unittest` suite has no third-party dependency. Pytest, Ruff and Pyright
are optional local quality tools configured through `Python/pyproject.toml`.

Python-only source and tools live in `Python/`, with `LIB`, `tools`, `analysis`
and `tests` retained below it. `INI`, `LOG` and `SimHubData` remain shared at the
repository root; `Python/LIB/paths.py` resolves them independently of the current
working directory. Start the Python application from the root with
`python -m Python.SimHub2SimRig`.

Runtime settings are stored in `INI/SimHub2SimRig.ini`. The application loads and
validates this file when the package starts. Restart the application after
changing serial ports, axis parameters, timing values or UI settings.
