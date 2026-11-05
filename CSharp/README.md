# SimHub2SimRig in C#

Die Anwendung besteht aus zwei unabhängig laufenden Windows-Programmen:

| Programm | Zuständigkeit |
| --- | --- |
| `SimHub2SimRigHMI.exe` | Hauptdialog, Achsenanzeige, Senderdiagramm, Wartungsdialog, Analyse mit Zoom und Signalauswahl, Dateiauswahl, Playback-Bedienung und direkter HTTP-Aufruf für das Raumlicht |
| `SimHub2SimRigCom.exe` | Serielle Busse, Modbus RTU, UDP-Empfang, Initialisierung, Homing, Bewegungsplanung, Antriebsstatus, Istpositionsabfrage, Leveling, Lastkalibrierung, Schmierung, Betriebsstunden, Simulation, CSV-Aufzeichnung, CSV-Auswertung und Playback einschließlich Zeitsteuerung |

`SimHub2SimRig.Shared.dll` enthält ausschließlich Konfiguration, Nachrichtenverträge und den IPC-Client. Das HMI referenziert die Com-Implementierung nicht. Es hat keinen Zugriff auf die Antriebssteuerung. Auch Diagrammvorschauen liest und reduziert Com aus den Aufzeichnungsdateien. Beim Playback überträgt das HMI den **Dateinamen**, die Achsenauswahl und die Befehle Play/Pause/Stop/Seek; es überträgt keine Bewegungsdaten und steuert keinen Playback-Takt.

Die Python-Referenz liegt mit ihren Bibliotheken, Werkzeugen und Tests unter
`Python/`. Die Vergleichsskripte in `CSharp/Tests` laden sie von dort. Die von
beiden Versionen genutzten Verzeichnisse `INI`, `LOG` und `SimHubData` bleiben
im Hauptverzeichnis des Repositorys.

## Bauen und starten

Voraussetzung: Windows x64 mit **.NET Framework 4.8**. Für den normalen Betrieb werden weder Python noch NuGet-Pakete benötigt. Der mit Windows/.NET Framework vorhandene C#-Compiler reicht für den Release-Skript-Build aus. Die Solution verwendet SDK-Projekte mit dem Ziel `net48`; für die Projektunterstützung in VS Code und für `dotnet build` wird zusätzlich ein .NET-SDK benötigt. In einem aktuellen Visual Studio lässt sie sich mit dem .NET-Framework-4.8-Targeting-Pack öffnen.

Aus dem Repository-Verzeichnis:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\CSharp\build.ps1
```

Die Anwendungsdateien liegen anschließend in `CSharp/bin/`. Dort gehören beide EXEs, beide `.exe.config`-Dateien und die gemeinsame DLL zusammen.

### Visual Studio Code

Im Repository `SimHub2SimRig.code-workspace` öffnen. Nach dem Wechsel der
C#-Einstellungen einmal **Developer: Reload Window** ausführen. Unter
**Ausführen und Debuggen** die Konfiguration **SimHub2SimRigHMI** auswählen und
mit **F5** starten. **Strg+Umschalt+B** baut beide Anwendungen samt gemeinsamer
Bibliothek. Die Aufgabe **SimHub2SimRigHMI: Start without debugger** startet das
HMI auch ohne Debugger; sie ist über **Terminal → Aufgabe ausführen** erreichbar.

Der Workspace lädt die vollständige Solution über die C#-Erweiterung und
deaktiviert die automatische Behandlung einzelner `.cs`-Dateien als Programme.
`Program.cs` ist Teil des HMI-Projekts und kein eigenständiges Programm. Ein
Aufruf wie `dotnet build CSharp/HMI/Program.cs` lässt die WinForms-Referenzen,
`Shared` und die übrigen Dialogklassen aus. Der Befehl
`csdevkit.debug.noDebugFileLaunch` gehört zum Einzeldatei-Start und wird von den
bereitgestellten Startkonfigurationen nicht benötigt.

Für Debug-Builds benötigt der Skript-Build zusätzlich ein .NET-SDK. Er verwendet
dessen Roslyn-Compiler für portable PDB-Dateien und kompiliert weiterhin gegen
.NET Framework 4.8. Der Release-Build bleibt ohne SDK möglich. Bei einem normalen
`dotnet build CSharp/SimHub2SimRig.sln` werden ohne separates 4.8-Targeting-Pack
die installierten Framework-Assemblies verwendet; ein vorhandenes Targeting-Pack
hat Vorrang.

Die SDK-Projekte sorgen dafür, dass der C#-Sprachserver das MSBuild des
.NET-SDKs verwendet. Alte Projekte mit `ToolsVersion="15.0"` konnten stattdessen
die installierten Visual-Studio-2017-BuildTools auswählen und beim Laden mit
`GetReferenceNearestTargetFrameworkTask` oder einem nicht erreichbaren
MSBuild-Kindprozess scheitern. Nach dieser Projektumstellung einmal
**Developer: Reload Window** ausführen. Die Anwendungen bleiben bei .NET
Framework 4.8; zusätzliche NuGet-Pakete werden nicht eingebunden.

Die VS-Code-Konfiguration folgt den Hinweisen zur
[C#-Erweiterung für Framework-Projekte](https://code.visualstudio.com/docs/csharp/cs-dev-kit-faq#project-system)
und zum [Desktop-CLR-Debugger mit portablen PDBs](https://github.com/dotnet/vscode-csharp/blob/main/docs/debugger/Desktop-.NET-Framework.md).

Com zuerst starten; in einem zweiten Terminal das HMI starten:

```powershell
.\CSharp\bin\SimHub2SimRigCom.exe
```

```powershell
.\CSharp\bin\SimHub2SimRigHMI.exe
```

Com und HMI können unabhängig gestartet werden. Ist Com beim Start des HMI
nicht erreichbar, erscheint **START** über dem INIT-Button. Ein Klick startet
Com und blendet START aus. Sobald eine erfolgreiche IPC-Statusabfrage die
Verbindung bestätigt, fordert das HMI automatisch einmal **INIT** an; eine
feste Wartezeit gibt es nicht. Ohne Klick wird Com nicht gestartet. Ist Com
bereits erreichbar, bleibt der bisherige manuelle INIT-Ablauf verfügbar.
Com wartet nach dem Start auf einen ausdrücklichen **INIT**-Auftrag aus
dem HMI. Vorher werden keine seriellen Ports geöffnet und keine Antriebe
initialisiert. Das HMI zeigt einen großen **INIT**-Button; alle anderen
Bedienfunktionen einschließlich Licht und Moduswahl sind gesperrt. Ist Com
nicht erreichbar, ist INIT gesperrt und START verfügbar.

Nach einem Klick auf **INIT** öffnet Com die Ports und führt den bisherigen
Homing-/Positionierablauf aus. Währenddessen zeigt das HMI den Fortschritt und
sperrt auch den INIT-Button. Nach erfolgreichem Abschluss erscheint die normale
Achsentabelle und die Bedienung wird freigegeben. Schlägt die Initialisierung
fehl, bleiben die übrigen Funktionen gesperrt und INIT kann erneut angefordert
werden. Die INI wird beim Programmstart geladen; Änderungen an Ports,
Geometrie oder Bewegungsparametern erfordern einen Neustart.

Bis zum erfolgreichen Abschluss der Initialisierung verwirft Com alle
SimHub-Telegramme, auch `START`, `POSITIONS`, `END` und `SHUTDOWN`. Sie setzen
weder Verbindungs- noch Aufzeichnungszustand und werden nicht später
nachgeholt. Das Beenden von Com über Konsole oder IPC bleibt möglich.

Kann Com einen seriellen Port nicht öffnen (zum Beispiel weil er belegt oder
nicht verfügbar ist), wird derselbe Port alle **10 Sekunden** erneut geöffnet,
bis der Zugriff gelingt oder Com beendet wird. Konsole, Log und HMI-Status
zeigen dabei zum Beispiel `COM Error (COM6) - I try again in 10 seconds` und
die Fehlerursache. Erst wenn alle Ports geöffnet sind, beginnt die
Antriebsinitialisierung. Das Beenden von Com unterbricht die Wartezeit sofort.

Beide Programme suchen `INI/SimHub2SimRig.ini` ausgehend vom EXE-Verzeichnis in den übergeordneten Verzeichnissen. Alternativ lässt sich das Datenverzeichnis explizit angeben:

```powershell
.\CSharp\bin\SimHub2SimRigCom.exe --root H:\SimHub2SimRig
.\CSharp\bin\SimHub2SimRigHMI.exe --root H:\SimHub2SimRig
```

Im aktiven Modus laufen Hub-Statusüberwachung und Sender-Zeitmessung auch ohne
geöffnetes HMI weiter. Das erneute Öffnen des HMI übernimmt die vorhandene
Messhistorie und den aktuellen Betriebszustand von Com.

Die Statusausgabe steht am unteren Rand des HMI über die gesamte Fensterbreite
und zeigt die letzten drei Meldungen als Tabelle mit den Spalten **Datum**,
**Uhrzeit**, **PROZESS** und **Message**. In der Prozessspalte stehen `COM` oder
`HMI`; Datum und Uhrzeit werden separat vom Meldungstext dargestellt.
Unveränderte
Statusabfragen erzeugen keine doppelten Einträge. Die letzten drei
Com-Ausgaben werden ebenfalls über IPC übertragen und nach einem HMI-Neustart
wieder angezeigt.

Ein Doppelklick auf die Statusleiste öffnet die **Meldungen seit HMI-Start**.
Dieser Dialog zeigt alle während der aktuellen HMI-Sitzung eingegangenen
Meldungen mit Zeitstempel und Herkunft. Neue Meldungen werden auch im offenen
Dialog ergänzt; vertikale und horizontale Scrollleisten erlauben das Lesen
älterer beziehungsweise langer Ausgaben. Beim Zurückscrollen bleibt die
Leseposition erhalten. Mit einem Neustart des HMI beginnt eine neue Historie.

Com protokolliert `HMI connected`, `HMI disconnected` und bei einer angenommenen
Initialisierungsanforderung `INIT request received by HMI`. Das HMI kennzeichnet
seine Statusabfragen mit einer Sitzungs-ID; nach fünf Sekunden ohne Kontakt
wird die Trennung gemeldet. Diese Erkennung dient der Statusausgabe und
beeinflusst weder Initialisierung noch laufende Vorgänge.

Nach einmal erfolgreichem INIT ist kein geöffnetes HMI mehr erforderlich.
Das Schließen oder der Verlust des HMI ändert den Zustand von Com nicht:
SimHub-Verarbeitung, Bewegung, Aufzeichnung, Statusabfragen sowie laufendes
Leveling, Schmierung oder Playback laufen unverändert weiter. INIT gilt bis zum
Beenden des Com-Prozesses. Ein neu gestartetes HMI übernimmt diesen Zustand;
Wartungs- und Analysefenster können zur Bedienung laufender Vorgänge erneut
geöffnet werden. Das ausdrückliche Schließen eines einzelnen Wartungs- oder
Analysefensters behält seinen bisherigen Abschlussablauf.

Ein vollständiges Abschalten erfolgt über das Schließen des Com-Fensters,
Strg+C im Com-Terminal oder:

```powershell
.\CSharp\bin\SimHub2SimRigCom.exe --shutdown
```

Dieser Befehl wartet auf die vollständige Abschaltsequenz und liefert bei einem Fehler einen Exitcode ungleich null. Das bestehende UDP-Protokoll mit `SHUTDOWN_ACCEPTED`, `SHUTDOWN_COMPLETE` und `SHUTDOWN_ERROR` bleibt zusätzlich erhalten.

### Com-Fenster schließen

Die Com-EXE startet einen sichtbaren Konsolenprozess und einen fensterlosen
Kommunikationsprozess **aus derselben EXE**. Nur der Kommunikationsprozess
besitzt die Antriebsverbindungen. Es bleibt bei zwei Anwendungs-EXEs; im
Task-Manager können während des Betriebs zwei Com-Prozesse erscheinen.

Das Konsolenfenster und der Kommunikationsprozess sind über eine geerbte
anonyme Pipe gekoppelt. Beim Schließen des Fensters fordert der Close-Handler
das Abschalten an. Endet der Konsolenprozess ohne diesen Handler, erkennt der
Kommunikationsprozess die geschlossene Pipe ebenfalls. Dadurch kann er die
bisherige Abschaltsequenz auch nach dem Verschwinden des Fensters beenden:

1. Laufende Vorgänge stoppen und Aufzeichnung schließen.
2. Zentrieren und die Hubachsen absenken.
3. Servos deaktivieren und alle Modbus-Verbindungen schließen.
4. Den Kommunikationsprozess beenden.

Während des Absenkens bleiben die Ports noch belegt und eine neue Com-Instanz
wird abgewiesen. Der Fortschritt steht in `LOG/simhub2simrig-com.log`; ein
erfolgreicher Abschluss endet mit `SHUTDOWN_COMPLETE`. Das HMI zeigt während
der Abschaltung deren aktuellen Schritt und sperrt neue Bewegungsaufträge.
Der Konsolenprozess gibt die PID des Kommunikationsprozesses aus; zum Debuggen
der Bewegungslogik lässt sich ein Debugger an diese PID anhängen.

Dieses Verhalten behandelt das Schließen beziehungsweise den Verlust des
Konsolenfensters. Stromausfall, das erzwungene Beenden des eigentlichen
Kommunikationsprozesses und ein sofortiger Windows-Neustart können keine
vollständige Software-Abschaltsequenz garantieren. Ein geordneter Shutdown wird
weiterhin auch mit `--shutdown` angefordert.

Hintergrund zur kurzen Windows-Frist für Konsolen-Close-Handler:
[Microsoft: HandlerRoutine](https://learn.microsoft.com/en-us/windows/console/handlerroutine).

## Simulation

```powershell
.\CSharp\bin\SimHub2SimRigCom.exe --simulate --root <separates-Testverzeichnis>
.\CSharp\bin\SimHub2SimRigHMI.exe --root <separates-Testverzeichnis>
```

In das Testverzeichnis vorher `INI/SimHub2SimRig.ini` und `INI/Language.ini` kopieren. `--simulate` konstruiert keine seriellen Ports. Die simulierten Antriebe verarbeiten echte RTU-Bytefolgen; Com, UDP, HMI, Aufzeichnung und Playback laufen ansonsten unverändert. Das HMI kennzeichnet diesen Betrieb mit `SIMULATION`. Die Antriebssimulation quittiert Bewegungen unmittelbar; die separat aufgezeichnete Trapezsimulation entspricht der Python-Berechnung.

Simulation schreibt ebenfalls JSON-Zustand und CSV-Dateien und wendet die Aufbewahrungsfrist an. Ein eigenes Testverzeichnis hält diese Daten vom produktiven Zustand getrennt. Pro Windows-Benutzer darf genau eine Com-Instanz laufen, einschließlich Simulation.

## Vorhandene Dateien

Die bestehenden Formate werden weiterverwendet:

- `INI/SimHub2SimRig.ini`: Ports, Achsenparameter, Geometrie, Zeitvorgaben und UI-Einstellungen.
- `INI/Language.ini`: vorhandene Dialogtexte.
- `INI/leveling_offsets.json`, `INI/grease_data.json`, `INI/simrig_load_values.json`: bestehender persistenter Zustand; Schreiben erfolgt über eine temporäre Datei mit atomarem Austausch.
- `SimHubData/*.csv`: bisherige und neue Aufzeichnungsformate; neue Dateien enthalten dieselben 58 Spalten einschließlich Istpositions-Zeitstempel, Alter und Bodenlasten.
- `INI/room_light.ini`: private Lichtkonfiguration; alternativ `[room_light]` in der zentralen INI. Der Platzhalter `{status}` wird durch `0` oder `1` ersetzt. Nur das HMI ruft die URL auf.

Die Spalte **State** wertet `U40.43` wie die Python-Version aus: `0` und `5`
werden als `OK` angezeigt, `1` als `Servo off`, `2` als `Ready / stopped`,
`3` als `Start locked` und `4` als `Positioning locked`. Das IPC-Feld `Error`
behält aus Kompatibilitätsgründen seinen bisherigen Namen; sein Wert ist keine
pauschale Alarmnummer. Der separate ServoStatus aus `U41.0A` wird ebenfalls
berücksichtigt: ServoStatus `3` bleibt als tatsächlicher Antriebsfehler sichtbar,
auch wenn `U40.43` gleichzeitig `0` oder `5` enthält.

Während eines laufenden SimHub-Spiels setzt Com die zyklischen Load-Abfragen
für alle sieben Antriebe aus; das HMI zeigt die Load-Felder dann leer an.
Antriebsstatus und im Modus `actual_positions` die Istpositionen werden weiter
abgefragt. Nach `END` oder einem SimHub-Verbindungsabbruch beginnen die
Load-Abfragen wieder. Wie in Python ist dafür der Spielzustand maßgeblich;
eine bestehende, aber inaktive SimHub-Verbindung unterdrückt die Abfragen nicht.

Eine reine HMI-Aktualisierung lässt sich bei laufendem Com separat bauen. Das
HMI vorher schließen:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\CSharp\build.ps1 -HmiOnly
```

Damit werden weder Com noch dessen gemeinsame DLL ersetzt. Ein abweichendes
Buildverzeichnis ist über `-OutputDirectory <Verzeichnis>` möglich.

Der Sender und die Aufzeichnung verwenden wiederverwendbare, hochauflösende
Windows-Timer mit sofortiger Shutdown-Unterbrechung. Auch die seriellen
Zwischenpausen und Antwortabfragen warten über einen Timer mit einer höchstens
0,5 ms langen Feinwartephase. Die konfigurierten RTU-Pausen bleiben erhalten.
Während eines Spiels
entfallen zusätzlich die drei Homing-Abfragen je Statusprüfung; Fehlerstatus,
ServoStatus, Hub-Interlock und angeforderte Istpositionen werden weiter geprüft.
Außerhalb des Spiels werden Homing und Lastwerte wieder aktualisiert.

Die CSV-Aufzeichnung kopiert den Bewegungszustand kurz unter einer Sperre und
führt Berechnung, Formatierung und Dateizugriffe anschließend außerhalb dieser
Sperre aus. Gepufferte Zeilen werden spätestens beim nächsten Aufzeichnungstakt
nach einer Sekunde sowie bei END und geordnetem Shutdown ausgeschrieben. Bei
einem Prozessabbruch können noch gepufferte Daten fehlen. Messverfahren und
reproduzierbare Performance-Prüfungen stehen in [PERFORMANCE.md](PERFORMANCE.md).

Die Tabellenköpfe und Werte im Hauptfenster sind zentriert. Die Bedienknöpfe
stehen in festen Spalten: Leveling Start/Stop/Lastwerte speichern, Licht
Ein/Aus sowie Maintenance/Analysis. Das Senderdiagramm zeigt 0–100 ms. Sein
x-Bereich bleibt fest bei `-trigger_chart_window_s` bis `0` (neuester Messwert),
mit gleichbleibender Teilung und ohne Achsentitel unterhalb des Diagramms.

Beispiel für eine lokale Lichtkonfiguration:

```ini
[room_light]
uri = http://127.0.0.1/light?status={status}
request_timeout_s = 5.0
```

Bei Playback müssen die vier Hubachsen gemeinsam ausgewählt werden. Einzelne Achsen 1–3 bleiben separat auswählbar. Die C#-Version behebt die bisherige Zuordnung ausgewählter Playback-Achsen: Eine Auswahl von Achse 3 wird tatsächlich an Achse 3 gesendet. Auch eine Pause innerhalb eines langen Abstands zwischen zwei Samples hält die Ausgabe an.

## Prozesskommunikation und Interlocks

Die versionierte lokale Named Pipe `SimHub2SimRig.v1.<Windows-SID>` ist durch eine ACL auf den aktuellen Windows-Benutzer beschränkt. Nachrichten bestehen aus JSON mit Zeilenabschluss. Com prüft Betriebszustand, Achsengruppen, Dateinamen, Wertebereiche und konkurrierende Vorgänge selbst; die Sperren hängen nicht vom Zustand der HMI-Buttons ab. Das IPC-Protokoll bietet keine beliebigen Registerschreibbefehle.

Ein Bus-Worker serialisiert jeden COM-Port. Unicast-Schreibaufträge verschiedener Busse können parallel laufen. Vor einem Broadcast werden alle bereits eingereihten Transfers abgeschlossen und deren Fehler geprüft. Erst danach werden beide Triggerflanken auf alle Busse verteilt. Wortreihenfolge wird wie zuvor einmal pro Verbindungssitzung über `0x0A06` ermittelt. CRC-Wiederholungen und die bestehende Toleranz für fehlende Schreibantworten bleiben erhalten.

Das Leveling portiert den aktiven Python-Ablauf `leveling4`/`level2`: Medianmessung, feste Referenzachse, dreistufige Korrektur, Filter, Integralanteil, Offsetbegrenzung, unabhängige Verifikation sowie Rücknahme schlechterer Korrekturen. Die früheren, im Programm nicht aufgerufenen alternativen Leveling-Experimente verbleiben in der Python-Referenz.

## Tests

```powershell
python -m pip install -r Python/requirements.txt -r Python/requirements-dev.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\CSharp\build.ps1 -Test
python .\CSharp\Tests\run_python_tests.py
```

`-Test` erzeugt seine Referenzen bei jedem Lauf direkt mit der unveränderten Python-Kommunikations-/Bewegungsimplementierung. Es prüft:

- 12.000 deterministische RTU-Testvektoren für FC03, FC06 und FC10, einschließlich CRC.
- 7.049 Einheitenumrechnungen und 126 Zielpositionsfälle.
- 112 Bewegungsabläufe mit beiden Wortreihenfolgen und allen sieben Achsen.
- Vollständige Initialisierung und Abschaltung sowie Leveling-Korrekturen einschließlich Rücknahme; Vergleich der Telegramme pro physischem Bus in ihrer Reihenfolge.
- Simulationsdynamik, Bodenabweichungen, Lastkalibrierung, CSV-Kompatibilität und begrenzte Diagrammvorschauen.
- CRC-Fehler, Wiederholungen, Fehlerantworten, Wartungssperren und Broadcast-Barrieren.
- UDP und Named Pipes, Wartungsbewegungen, Homing, Schmierung, Aufzeichnung, Playback, Pause, Seek und Wiederherstellung der Planner.
- Start und Abschaltung der tatsächlich gebauten Com-EXE und Abweisung einer zweiten Instanz.
- Assembly-Trennung und gerenderte WinForms-Fenster, einschließlich Mindestgröße.

Der Testlauf öffnet ausschließlich simulierte Antriebe. Testdaten liegen unter `CSharp/Tests/work/`, Renderbilder unter `CSharp/Tests/screenshots/`, der maschinenlesbare Ergebnisbericht in `CSharp/Tests/test-results.json`. Testprogramme werden getrennt unter `CSharp/Tests/bin/` gebaut. Die Python-Dateien werden für diese Regressionen als Referenz behalten und gehören nicht zum Laufzeitpaket.

Eine zusätzliche Kontrolle an realen RS-485-Adaptern und Antrieben wurde bei der Umsetzung nicht ausgeführt. Bytegleichheit in den geprüften Softwareabläufen ersetzt keine Messung von Bus-Timing oder realen Antriebsantworten.

## Laufzeitpaket erstellen

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\CSharp\package.ps1
```

Das Skript erstellt unter `CSharp/dist/` ein neues Verzeichnis mit genau zwei Anwendungs-EXEs, gemeinsamer DLL, Laufzeitkonfiguration und INI-Dateien. Es ersetzt keine vorhandene Installation. Private Lichtkonfiguration und vorhandene Kalibrierungs-/Wartungsdateien werden nicht automatisch in ein neues Paket kopiert; bei Übernahme einer bestehenden Installation deren lokale Dateien beibehalten.
