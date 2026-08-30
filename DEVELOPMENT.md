# Development checks

Install optional development tools:

```powershell
python -m pip install -r requirements-dev.txt
```

Run the checks from this directory:

```powershell
python -m unittest discover -s tests -t .
python -m pytest
ruff check .
pyright
```

The `unittest` suite has no third-party dependency. Pytest, Ruff and Pyright
are optional local quality tools configured through `pyproject.toml`.

Runtime settings are stored in `INI/SimHub2SimRig.ini`. The application loads and
validates this file when the package starts. Restart the application after
changing serial ports, axis parameters, timing values or UI settings.
