# Enigma2 Recording Manager

Kleines Webinterface, das über die OpenWebif-API deiner Enigma2-Box (Vu+ Uno 4K /
OpenATV) läuft und Aufnahmen anhand von Titel-Mustern automatisch nach einer
festgelegten Anzahl Tage löscht — ideal als Ergänzung zu AutoTimer-Liste.

## Seiten

- **Aufnahmen** – alle abgeschlossenen Aufnahmen auf der Festplatte, inkl. Anzeige,
  welches Muster greift und wann gelöscht wird. Manuelles Löschen ist ebenfalls möglich.
- **Timer** – kommende / laufende Timer (inkl. der von AutoTimer-Liste erzeugten).
- **Muster** – Verwaltung der Lösch-Regeln, z. B. "Newstime" → nach 3 Tagen löschen.
  Optional als Regex. Nur Aufnahmen mit passendem Muster werden je gelöscht.
- **Log** – Protokoll aller automatischen und manuellen Löschungen.
- **Einstellungen** – Prüfintervall des Hintergrund-Jobs (Standard 6h), manueller
  Sofort-Check, Verbindungsstatus zur Box.

## Start

```bash
cd recording-manager
docker compose up -d --build
```

Danach unter `http://<docker-host>:8080` erreichbar.

Standardmäßig zeigt `docker-compose.yml` auf `192.168.0.5:80`. Anpassen über
Umgebungsvariablen in `docker-compose.yml`:

- `ENIGMA_HOST`, `ENIGMA_PORT` – Adresse der Box
- `ENIGMA_USER`, `ENIGMA_PASS` – falls im OpenWebif ein Login aktiviert ist
- `CHECK_INTERVAL_HOURS` – Standard-Prüfintervall (später auch in der UI änderbar)
- `TZ` – Zeitzone für die Anzeige
- `MANAGER_USER`, `MANAGER_PASS` – optionaler Basic-Auth-Schutz für dieses
  Webinterface selbst (empfehlenswert, da hier gelöscht werden kann)

Die SQLite-Datenbank (Muster, Einstellungen, Log) liegt persistent im Ordner
`./data`.

## Funktionsweise des Hintergrund-Jobs

Alle *N* Stunden (einstellbar) wird die Aufnahmeliste der Box geladen. Für jede
Aufnahme wird geprüft, ob der Titel einem aktiven Muster entspricht. Trifft eines
zu und ist `Aufnahmedatum + Tage <= jetzt`, wird die Aufnahme über die
OpenWebif-API (`/api/moviedelete`) gelöscht und im Log vermerkt. Aufnahmen ohne
passendes Muster werden nie automatisch gelöscht.
