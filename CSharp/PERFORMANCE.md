# Com-Performanceprüfung – 5. November 2026

Die Prüfung vergleicht den bisherigen C#-Sender mit der optimierten Version.
Es wurden ausschließlich simulierte Busse und getrennte Testverzeichnisse
verwendet; reale RS-485-Adapter und Antriebe wurden nicht angesprochen.

## Befunde und Änderungen

- Der 10-ms-Sender und die Aufzeichnung warteten über
  `ManualResetEventSlim.Wait(TimeSpan)`. Sie verwenden jetzt jeweils einen
  wiederverwendbaren hochauflösenden Windows-Timer, den Shutdown sofort
  unterbrechen kann. Es gibt keine Änderung der systemweiten Timerauflösung.
  [Microsoft dokumentiert den hochauflösenden Timer ab Windows 10 1803](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createwaitabletimerexw).
  Auf älteren Systemen wird auf einen normalen Waitable Timer zurückgefallen.
  Auch [Python verwendet unter Windows für `time.sleep` einen Waitable Timer](https://docs.python.org/3/library/time.html#time.sleep).
- Serielle Antwortabfragen und RTU-Pausen liefen bisher für die vollständigen
  1 beziehungsweise 1,75 ms in einer aktiven Warteschleife. Ein Timer übernimmt
  jetzt den Großteil der Pause; nur die letzten höchstens 0,5 ms werden bei
  Bedarf aktiv abgewartet. Eine reine Timerpause war im Versuch etwa 0,3–0,4 ms
  länger als angefordert. Die kurze Feinwartephase erhält die Busgeschwindigkeit
  bei wesentlich weniger CPU-Zeit. Die konfigurierte Mindestpause wird nicht
  verkürzt.
- Die Hintergrundtelemetrie las während eines Spiels je Statusprüfung fünf
  Register: Fehlergrund, ServoStatus und drei Homing-Register. Jetzt bleiben
  Fehlergrund und ServoStatus aktiv; die drei Homing-Abfragen entfallen während
  des Spiels. Die separate Hub-Überwachung im Sender und angeforderte
  Istpositionen bleiben aktiv. Nach Spielende werden Homing und Lastwerte wieder
  abgefragt. Anders als Python zeigt C# weiterhin den ServoStatus aller Achsen.
- CSV-Berechnung, Formatierung und Dateizugriffe lagen unter `Recorder.Gate`.
  Dieselbe Sperre benötigen Sender und Statusabfragen. Jetzt wird nur ein
  konsistenter Zustandsabzug unter dieser Sperre erstellt; die restliche Arbeit
  verwendet eine getrennte Schreibsperre. Ein gezielt blockierter Schreibzugriff
  blockiert dadurch weder neue Bewegungsziele noch Istpositionsupdates.
- CSV-Zeilen werden gepuffert statt nach jeder Zeile ausgeschrieben. Header,
  periodischer Flush nach einer Sekunde und geordnetes Beenden schreiben den
  Puffer aus. Bei einem Prozessabbruch können noch gepufferte Daten fehlen.
  Das Format mit 58 Spalten bleibt erhalten.

## Vergleichsmessung

`PerformanceTests.cs` verwendet drei virtuelle Busse entsprechend der INI.
Jede Übertragung berücksichtigt Telegrammlänge, 115200 Baud, die konfigurierte
RTU-Pause und zusätzlich 1 ms Antwortlaufzeit. Gemessen wird nach einer Sekunde
Aufwärmzeit jeweils sechs Sekunden lang. Bewegte Ziele wechseln zwischen zwei
Positionen; im Aufzeichnungsfall laufen die echte CSV-Erzeugung und der
Spielmodus mit. Die übrigen Szenarien laufen ohne Spielmodus.

Lokaler Vergleich, jeweils **vorher → nachher**:

| Szenario | Mittelwert (ms) | P99 (ms) | Maximum (ms) | CPU-Zeit in 6 s (s) |
| --- | ---: | ---: | ---: | ---: |
| Stillstand | 10,690 → 10,004 | 18,685 → 12,787 | 23,274 → 18,374 | 0,141 → 0,016 |
| Bewegte Ziele ohne Spielmodus | 16,695 → 15,987 | 26,332 → 26,342 | 28,789 → 29,574 | 1,609 → 1,484 |
| Bewegte Ziele mit Spielmodus und CSV | 15,801 → 15,783 | 25,200 → 24,583 | 28,270 → 26,040 | 1,406 → 0,844 |

Der Mittelwert bei Bewegung und Aufzeichnung ist praktisch unverändert; die
Buslaufzeiten begrenzen ihn weiterhin. Der Leerlauftakt ist genauer, und im
Aufzeichnungsfall sinkt die Prozess-CPU-Zeit in diesem Versuch um rund 40 %.
Einzelne Maximalwerte schwanken weiterhin. In allen sechs Messfenstern gab es
keine Intervalle über 50 ms.

Die separat gemessenen seriellen Wartefunktionen benötigen für 500 Pausen:

| Angeforderte Pause | Mittlere Dauer vorher → nachher | CPU-Zeit vorher → nachher |
| --- | ---: | ---: |
| 1,00 ms | 1,000 → 1,204 ms | 0,484 → 0,031 s |
| 1,75 ms | 1,750 → 1,753 ms | 0,875 → 0,078 s |

Die RTU-Pause bleibt im Mittel nahezu gleich lang; die CPU-Zeit der
Wartefunktion sinkt um über 90 %. Die Antwortabfrage kann je nach Aufwachlatenz
etwas später erfolgen. Diese Messung testet die Wartefunktion, nicht einen
realen seriellen Datenaustausch.

Die Messwerte sind ein kurzer lokaler Vergleich, keine garantierten
Echtzeitgrenzen. Der Test bildet weder USB-Treiberlatenzen noch echte
Antriebsantworten, Kommunikationsfehler, Spielelast oder parallele HMI-Abfragen
ab. Die gemeldeten 100-ms-Spitzen wurden in diesem Modell bereits vor der
Änderung nicht reproduziert. Ihre Beseitigung an der Hardware ist deshalb
noch nicht nachgewiesen.

## Reproduzieren und Regressionen prüfen

Zunächst den dokumentierten vollständigen Test-Build ausführen:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\CSharp\build.ps1 -Test
```

Der vollständige Lauf prüft zusätzlich Timer-Abbruch, das Einhalten der
RTU-Pause, blockierte CSV-Schreibzugriffe sowie die Telemetrie während und nach
einem Spiel. Diese Prüfungen lassen sich nach dem Bauen separat aufrufen:

```powershell
.\CSharp\Tests\bin\SimHub2SimRig.Tests.exe . --performance-checks
```

Die Messung liefert Mittelwert, P95, P99, Maximum, Anzahl der Intervalle über
50 ms und Prozess-CPU-Zeit. Außerdem vergleicht sie die bisherige aktive
Warteschleife mit der neuen seriellen Wartefunktion für jeweils 500 Pausen:

```powershell
.\CSharp\Tests\bin\SimHub2SimRig.Tests.exe . --performance-only --performance-report CSharp\Tests\work\performance.json
```

Der vollständige C#-Lauf bestand mit 33.604 Prüfungen. Nach der ergänzten
Feinwartephase und Telemetrieprüfung bestanden außerdem alle 24 gezielten
Performance-Regressionen. Initialisierung, Shutdown, Bewegungs- und
Leveling-Telegramme bleiben in den vorhandenen Python-Vergleichen bytegleich.
