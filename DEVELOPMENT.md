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

HMI changes require the `hmi-language` agent described in
[`CSharp/Agents/HmiLanguage.md`](CSharp/Agents/HmiLanguage.md), as directed by
`AGENTS.md`. Its project configuration is `.codex/agents/hmi-language.toml`.
Both the script build and the HMI project build run the static language guard;
it can also be run separately:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File CSharp/Tests/check_hmi_language.ps1
```

The agent covers dynamic UI text and backend-message presentation that static
checks cannot fully resolve. Re-run it after further HMI edits. Language.ini is
loaded when the HMI starts, so restart HMI after changing translations. Keep
protocol values and control names/tags independent of displayed wording.

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

Com settings are stored in `INI/SimHub2SimRigCom.ini`; HMI display settings are
stored in `INI/SimHub2SimRigHMI.ini`. Each process loads and validates only its
own configuration at startup. Restart the affected process after changing its
settings. Axis-status polling belongs to `[control]` in the Com INI. HMI receives
position ranges and grease thresholds through Com status messages. The retained
Python reference loads both files.
