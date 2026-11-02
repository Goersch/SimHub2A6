# Python-Version

Hier liegen der Python-Quellcode, seine Tests, Analyseprogramme, Werkzeuge und
Abhängigkeitsdateien. Die Unterverzeichnisse `LIB`, `tools`, `analysis` und
`tests` bleiben erhalten.

Die gemeinsam verwendeten Dateien liegen weiterhin eine Ebene höher:

```text
SimHub2SimRig/
  INI/                 gemeinsame Konfiguration und Kalibrierung
  LOG/                 Protokolle beider Versionen
  SimHubData/          gemeinsame CSV-Aufzeichnungen
  CSharp/              C#-Version und Vergleichstests
  Python/
    LIB/
    tools/
    analysis/
    tests/
    SimHub2SimRig.py
    ShutDown.py
    requirements.txt
    requirements-dev.txt
    pyproject.toml
```

Aus dem Hauptverzeichnis des Repositorys installieren und starten:

```powershell
python -m pip install -r Python/requirements.txt
python -m Python.SimHub2SimRig
```

Der Start initialisiert die realen Antriebe wie bisher. Python und Com können
nicht gleichzeitig dieselben seriellen Ports und denselben UDP-Port verwenden.

Ein sauberes Abschalten der Python-Version anfordern:

```powershell
python -m Python.ShutDown
```

Die direkten Skriptaufrufe funktionieren ebenfalls:

```powershell
python Python/SimHub2SimRig.py
python Python/ShutDown.py
```

Entwicklungswerkzeuge und Tests aus dem Hauptverzeichnis:

```powershell
python -m pip install -r Python/requirements.txt -r Python/requirements-dev.txt
python -m pytest Python/tests
python -m unittest discover -s Python/tests -t .
python -m ruff check --config Python/pyproject.toml Python CSharp/Tests
python -m pyright --project Python/pyproject.toml
```

Die C#-Vergleichstests unter `CSharp/Tests` verwenden diese Python-Version als
Referenz. Sie bleiben beim C#-Projekt und laden die Module aus `Python/`.

Werkzeuge und Analyseprogramme werden über ihre neuen Pfade aufgerufen, etwa
`python Python/tools/update_simhub_motion_config.py`. Die Analyseprogramme lesen
weiterhin die gemeinsamen CSV-Dateien aus `SimHubData/` im Hauptverzeichnis.
