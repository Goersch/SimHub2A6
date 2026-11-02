# Testbericht – 2. November 2026

## Nachprüfung: Com-Konsolenfenster schließen

**33.300 Prüfungen bestehen im vollständigen C#-Lauf.** Die Bewegungs- und
Shutdown-Telegramme sind in den Python-Vergleichen unverändert bytegleich.

Zusätzlich wurden die tatsächlich gebaute Com-EXE und ihre fensterlose
Kommunikationsinstanz in getrennten Prozessen geprüft:

- Der normale wartende `--shutdown`-Aufruf und die Abweisung einer zweiten
  Instanz bestehen weiterhin.
- Ein abrupt beendeter Konsolenprozess löst über die geschlossene anonyme Pipe
  den geordneten Shutdown des Kommunikationsprozesses aus.
- Ein eigens erzeugtes, verborgenes Windows-Konsolenfenster erhält ein echtes
  `WM_CLOSE`. Die Konsoleninstanz endet; die Kommunikationsinstanz beendet das
  Absenken nach mehr als fünf Sekunden weiterhin vollständig.
- Die Protokollreihenfolge wird geprüft: Abschaltanforderung, Hubachsen
  abgesenkt, alle Modbus-Ports geschlossen, `SHUTDOWN_COMPLETE`, Prozessende.
- Der UDP-Port lässt sich nach dem Abschalten erneut exklusiv binden.

Die Tests liefen ausschließlich mit simulierten Antrieben. Die erfolgreiche
Absenkung wurde über die simulierten Antriebsantworten geprüft; es wurden keine
realen Achsen bewegt. Die Anwendung enthält weiterhin nur zwei EXEs; die
Konsolen- und Kommunikationsinstanz verwenden dieselbe Com-EXE.

Die Fensterprüfung erzeugt ihr eigenes Konsolenfenster mit `CREATE_NEW_CONSOLE`
und `SW_HIDE`, sodass kein Benutzerfenster geschlossen wird. Vor jedem
Steuerzugriff prüfen die Tests PID, isoliertes Datenverzeichnis und
Simulationsstatus der eigenen Instanz.

## Nachprüfung: HMI-Anordnung und Senderdiagramm

Die Tabellenköpfe und Werte sind zentriert. Die Knöpfe für Licht, Wartung/Analyse
und Leveling werden in festen Spalten untereinander angeordnet. Die y-Achse des
Senderdiagramms bleibt bei 0–100 ms, die x-Achse bei einem festen Zeitfenster bis
0, ohne den bisherigen Achsentitel `s`. Die Messpunkte werden während einer
Aktualisierung gemeinsam ausgetauscht; der neueste Punkt bleibt bei x = 0.

**374 gezielte Prüfungen bestehen:** 65 Statusprüfungen und 309 UI-/Chartprüfungen.
Geprüft wurden normale und minimale Fenstergröße, vollständig sichtbare Knöpfe,
deren Reihenfolge, zentrierte Tabellen sowie feste Achsengrenzen bei leerer,
kurzer und langer Historie sowie Messwerten oberhalb der Anzeigeskala. Die
gerenderten Hauptfenster wurden visuell geprüft.

Die HMI-EXE wurde gegen die bereits verwendete Shared-DLL gebaut und ersetzt.
Com-EXE, Shared-DLL und der laufende Com-Prozess blieben unverändert.

## Nachprüfung: Python-Verzeichnisstruktur

Die Python-Version wurde einschließlich `LIB`, `tools`, `analysis`, `tests`,
Abhängigkeiten und Toolkonfiguration nach `Python/` verschoben. Die gemeinsamen
Verzeichnisse `INI`, `LOG` und `SimHubData` bleiben im Hauptverzeichnis. Die
Python-Pfadauflösung, Paketimporte, CI und Referenzskripte wurden angepasst.

**92 Python-Tests und 14 Subtests bestehen** sowohl aus dem Hauptverzeichnis
als auch aus `Python/`. Ruff und Pyright sind ebenfalls erfolgreich. Die
direkten Skript-Bootstraps wurden ohne Aufruf der Hardwareinitialisierung
geprüft. Die vollständige neu erzeugte C#-Vergleichsreferenz ist nach dem Umzug
bytegleich zur vorherigen Datei (SHA-256):

```text
E1AB16878CBF5FFDCEA4AFB6A195327B5DD6089CF522257A78D7CEEA6F7D1130
```

Es wurden weder die C#-Laufzeitdateien noch die gemeinsame Konfiguration oder
Aufzeichnungen für die Umstrukturierung ersetzt.

## Nachprüfung: falsche Anzeige „Error 5“

Die HMI-Anzeige von `U40.43` wurde an die bestehende Python-Zuordnung angepasst.
Die Python-Referenzdaten enthalten jetzt zusätzlich die Zustände `-1`, `0` bis
`6` sowie `65535`. Die Korrektur betrifft ausschließlich HMI-Code; die laufende
Com-Implementierung, Modbus-Lese-/Schreibbefehle und IPC-Verträge wurden dafür
nicht geändert.

Die korrigierte HMI-EXE wurde gegen die exakte bereits vorhandene Shared-DLL
gebaut und separat geprüft:

- **65 Statusprüfungen bestanden:** vollständige Python-Zuordnung bei den
  ServoStatus-Werten 0, 1 und 2, unbekannte Zustände und Vorrang tatsächlicher
  Servo-Fehler bei ServoStatus 3.
- **255 UI-Prüfungen bestanden:** bei `U40.43 = 5` zeigen alle sieben Achsen `OK`
  ohne rote Fehlerfarbe. ServoStatus 3 bleibt auch bei Grundwert 5 rot als
  `Drive fault` sichtbar. Rendering und Mindestgrößen wurden erneut geprüft.

Die geprüfte HMI-EXE wurde nach dem Schließen des HMI nach `CSharp/bin/`
übernommen. Es wurde dafür ausschließlich `SimHub2SimRigHMI.exe` ersetzt.

Der folgende vollständige Regressionstest-Bericht dokumentiert den vorherigen
Grundstand der Portierung; die vorstehende gezielte Nachprüfung ergänzt ihn.

Geprüft unter Windows x64 mit dem lokal vorhandenen .NET-Framework-C#-Compiler
14.8.10001.0, Ziel .NET Framework 4.8 / C# 5. Der Build verwendet
`/warnaserror+` und benötigt keine externen C#-Pakete.

## Ergebnis

**33.138 C#-Prüfungen, 13 Testgruppen, keine fehlgeschlagene Gruppe.**

| Testgruppe | Prüfungen | Ergebnis |
| --- | ---: | --- |
| Python-Referenz: RTU FC03/FC06/FC10 und CRC | 12.000 | bestanden |
| Python-Referenz: Einheiten und Zielpositionen | 7.175 | bestanden |
| Python-Referenz: 112 Bewegungsabläufe, alle Achsen, beide Wortreihenfolgen | 2.396 | bestanden |
| Python-Referenz: vollständige Initialisierung und Abschaltung | 1.418 | bestanden |
| Python-Referenz: Leveling-Korrekturen und Rücknahme | 6.428 | bestanden |
| Python-Referenz: Bewegungsdynamik und Bodenmodell | 2.400 | bestanden |
| CRC-Wiederholungen, Fehlerantworten, signed U32 und Float32 | 9 | bestanden |
| Drei-Bus-Barriere und unterdrückter Trigger nach Schreibfehler | 11 | bestanden |
| CSV, Kalibrierung, Vorschau und Playback-Umrechnung | 940 | bestanden |
| UDP, IPC, Wartung, Homing, Leveling, Schmierung und Playback | 113 | bestanden |
| Tatsächlich gestartete Com-EXE, zweite Instanz, wartender Shutdown-Client | 7 | bestanden |
| Trennung der HMI-Assembly von der Com-Implementierung | 2 | bestanden |
| WinForms-Rendering und vollständig sichtbare Buttons bei Mindestgröße | 239 | bestanden |

Die beibehaltene Python-Regression besteht ebenfalls vollständig:
**90 Tests und 6 Subtests bestanden**. `ruff check . tests` meldet keine Fehler;
`pyright .` meldet 0 Fehler und 0 Warnungen. `git diff --check` meldet keine
Whitespace-Fehler.

Die veraltete Python-Test-Erwartung für die Abschaltwartezeiten wurde an die
bereits bestehenden Aufrufe `[2.0, 1]` angepasst. `Python/LIB/playback.py` erhielt
ausschließlich eine Formatierung zur Behebung bestehender Ruff-Beanstandungen.
Die Python-Modbus- und Bewegungsimplementierung wurde nicht geändert.

## Aussage des Telegrammvergleichs

Die Referenzdaten werden bei jedem Testlauf direkt durch Ausführen der
Python-Funktionen erzeugt. Es werden die vollständigen Bytefolgen einschließlich
Slave-ID, Funktionscode, Registeradresse, Werte, Wortreihenfolge und CRC
verglichen. Bei mehrteiligen Abläufen wird die Reihenfolge je COM-Bus geprüft.
Damit werden auch zusammengefasste Parameterwrites, unveränderte Zielwerte,
Homing-Flanken und gemeinsame Broadcast-Trigger erfasst.

In allen geprüften Referenzabläufen besteht **Bytegleichheit**. Die bewusste
Korrektur der Achszuordnung bei einer eingeschränkten Playback-Auswahl wird
separat geprüft; das fehlerhafte Python-Verhalten wird dort nicht nachgebildet.

Der Integrationstest prüft außerdem Pause während einer langen Sample-Wartezeit,
Fortsetzen ohne Zeitsprung, Seek, erneutes Playback, natürliche Beendigung,
Zentrieren, das Wiederöffnen bestehender Dialogsitzungen und das Sperren alter
SimHub-Ziele nach der Wartung. Fenster wurden als PNG gerendert, visuell geprüft
und auf abgeschnittene Buttons geprüft.

## Laufzeitdateien

Das Paket `dist/SimHub2SimRig-20261102-162457/` enthält genau zwei
Anwendungs-EXEs. Die kopierten Dateien stimmen per SHA-256 mit den getesteten
Build-Dateien überein:

```text
SimHub2SimRigCom.exe
656608D3FF6D8F9DC2C1D450A3D303532AC1FC1994E17789026DC98017E49468

SimHub2SimRigHMI.exe
9AB3287D864492C6FABC9058688F9E2244BD94D0A7640FC1533A788A3016DE35

SimHub2SimRig.Shared.dll
6C4A6522A8FA54B21F439697A90BA61B2638EFBEFBD688239CEB8C9E933FFAA0
```

## Grenzen und Wiederholung

Alle Antriebstests liefen mit simulierten Gegenstellen. **Keine realen
RS-485-Ports oder Antriebe wurden angesprochen.** Bus-Timing, reale
Antriebsantworten, Verkabelung und physische Bewegungen wurden deshalb nicht
gemessen. Die Licht-URL wurde nicht aufgerufen. Die Tests liefern keine Aussage
über den Zustand der angeschlossenen Hardware.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\CSharp\build.ps1 -Test
python .\CSharp\Tests\run_python_tests.py
python -m ruff check --config Python/pyproject.toml Python CSharp/Tests
python -m pyright --project Python/pyproject.toml
```

Der automatisch erzeugte Detailbericht liegt nach dem Testlauf in
`Tests/test-results.json`; PNGs liegen in `Tests/screenshots/`. Beide sind ebenso
wie isolierte Testdaten und Build-Dateien von Git ausgeschlossen.
