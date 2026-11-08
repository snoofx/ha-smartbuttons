# Protokoll Home Assistant ↔ Panel

So reden die Integration `smartbuttons` und die Panel-Firmware miteinander, wenn HA das Panel
verwaltet. Quellen: `custom_components/smartbuttons/link.py` (HA) und `src/ha.cpp` / `src/main.cpp`
(Firmware).

**Aktuelle Version: 2.** Änderungen siehe [Versionen](#versionen).

## Verbindung

- **Rollen:** Das Panel ist der WebSocket-Server auf Port **81**, HA verbindet sich als Client:
  `ws://<panel>:81/`.
- **Nachrichten:** Textframes mit genau einem JSON-Objekt. Das Feld `type` bestimmt die Nachricht.
- **Verbindungen:** Pro Panel zählt eine Verbindung. Eine neue ersetzt die alte, z. B. wenn HA neu
  gestartet ist, ohne die alte zu schließen.
- **Heartbeat:**
  - Das Panel pingt alle 15 s und trennt nach 2 verpassten Pongs (je 5 s Wartezeit).
  - HA pingt alle 10 s.
- **Panel startet neu:** Vorher schließt es die Verbindung sauber. HA versucht danach 120 s lang
  jede Sekunde neu zu verbinden. Eine mDNS-Ankündigung des Panels löst sofort einen Versuch aus.
- **Kein TLS:** Der Inhalt ist nicht verschlüsselt. Die Kopplung schützt davor, dass ein fremdes
  Gerät das Panel steuert, nicht davor, dass jemand mitliest.

## Ablauf

```
HA                                   Panel
 |------------ verbinden ------------>|
 |<----------- hello -----------------|  nonce, Modus, Version, Belegung
 |  ungekoppelt:  pair -------------->|  → paired, neues hello
 |  gekoppelt:    auth (HMAC) ------->|  falsch → auth_failed, Panel trennt
 |  Modus local: Ende, nur adopt/unpair möglich
 |  Name anders:  name -------------->|  → renamed, Neustart
 |  rev anders:   config ------------>|  → Neustart, oder neues hello bei gleicher Belegung
 |  password ------------------------>|
 |  welcome ------------------------->|
 |  event (alle Zustände) ----------->|
 |        ... laufender Betrieb ...   |
 |  event (Änderung) ---------------->|
 |<----------- call_service ----------|
 |  result -------------------------->|
 |<----------- press -----------------|
```

## Kopplung

- **Schlüssel:** HA erzeugt pro Panel 32 zufällige Bytes und überträgt sie als 64 Hex-Zeichen mit
  `pair` oder `adopt`. Das Panel speichert den Schlüssel.
- **Nonce:** Jedes `hello` enthält eine neue `nonce` (32 Hex-Zeichen).
- **Beweis:** HA antwortet mit `auth`. Darin steht `hmac = HMAC-SHA256(Schlüssel als Bytes,
  nonce als ASCII)` als Hex. Ist der Beweis richtig, antwortet das Panel nicht. Ist er falsch,
  sendet es `auth_failed` und trennt.
- **Passwort:** Ein Panel mit Passwort (`protected: true`) nimmt `pair` und `adopt` nur mit dem
  passenden `password` an. Sonst wäre Port 81 ein Weg am Passwort der Web-UI vorbei.
- **Neu koppeln:** Ein gekoppeltes Panel behält seinen Schlüssel. Neu koppeln geht nur nach
  `unpair` von HA oder nach einem Reset am Panel (Debug-Screen SW6 lang, oder SW6 und SW10 beim
  Start).

Was das Panel in welchem Zustand annimmt:

| Zustand | angenommen |
|---|---|
| nicht bewiesen, ungekoppelt | `auth`, `pair`, `adopt` (nur im Modus local) |
| nicht bewiesen, gekoppelt | `auth` |
| bewiesen, Modus `local` | `adopt`, `unpair` |
| bewiesen, Modus `ha` | alles außer `pair` und `adopt` |

Alles andere ignoriert das Panel ohne Antwort.

## Nachrichten Panel → HA

### `hello`

Kommt beim Verbinden und nach `pair` sowie nach einer `config` mit gleicher Belegung.

```json
{"type": "hello", "proto": 2, "mode": "ha", "paired": true, "nonce": "9f…(32 hex)",
 "watch": ["light.flur", "climate.bad"],
 "id": "1020ba1555b8", "name": "smartbuttons-1555b8", "version": "v0.9.2+abc1234",
 "rev": "ff8131083e9d", "protected": true, "config": {"buttons": […], "pages": […]}}
```

| Feld | Bedeutung |
|---|---|
| `proto` | Protokollversion. Fehlt sie, ist es Version 1. |
| `mode` | `ha`: HA verwaltet das Panel. `local`: Das Panel verbindet sich selbst mit Token. |
| `paired` | Ein Schlüssel ist gespeichert. |
| `watch` | Entitäten, deren Zustand das Panel braucht (alle Seiten). |
| `id` | MAC-Adresse als 12 Hex-Zeichen, die feste Kennung des Panels. |
| `name` | Hostname, nur `a-z 0-9 -`, höchstens 31 Zeichen. |
| `rev` | Revision der gespeicherten Belegung, leer, wenn noch keine von HA kam. |
| `config` | Gespeicherte Belegung. HA übernimmt sie beim ersten Kontakt, `{}` ohne Belegung. |

### `call_service`

Ein Tastendruck schaltet etwas. Das Format ist dasselbe wie in der HA-WebSocket-API, die `id`
beginnt bei 1000.

```json
{"id": 1003, "type": "call_service", "domain": "light", "service": "toggle",
 "service_data": {}, "target": {"entity_id": "light.flur"}}
```

HA führt den Dienst nur aus, wenn zwei Bedingungen gelten:
- Die Entität steht in der Belegung dieses Panels.
- `domain` ist die Domain der Entität.

Danach antwortet HA mit `result`.

### `press`

Jeder Tastendruck. HA löst daraus das Event `smartbuttons_button_pressed` aus.

```json
{"type": "press", "button": 3, "gesture": "long", "page": "start"}
```

- **`button`:** 1–10, so gezählt, wie das Panel montiert ist.
- **`gesture`:** `single` oder `long`.
- **`page`:** Name der eigenen Seite oder eine der festen Seiten `start`, `debug`, `lamp`, `rgb`,
  `heat`, `locate`, `demo`, `lcd_test`, `light_test`.

### Antworten

| `type` | wann |
|---|---|
| `paired` | Schlüssel aus `pair` gespeichert, ein neues `hello` folgt. |
| `adopted` | `adopt` angenommen, das Panel startet neu. |
| `unpaired` | Schlüssel gelöscht. |
| `config_saved` | `{"reboot": true}`: neue Belegung gespeichert, das Panel startet neu. |
| `config_error` | `{"message": "…"}`: Belegung ungültig oder zu groß, Speichern fehlgeschlagen, Schlüssel oder Passwort falsch. |
| `renamed` | Neuer Name gespeichert, das Panel startet neu. |
| `auth_failed` | Falscher Beweis, das Panel trennt danach. |

## Nachrichten HA → Panel

| `type` | Felder | Wirkung |
|---|---|---|
| `auth` | `hmac` | Beweis der Kopplung, siehe oben. |
| `pair` | `key`, `password` | Ungekoppeltes Panel übernimmt den Schlüssel. |
| `adopt` | `key`, `password` | Lokal verwaltetes, ungekoppeltes Panel: Wechsel in den Modus `ha` mit diesem Schlüssel, dann Neustart. HA prüft den Erfolg am neu gestarteten Panel, weil die Antwort beim Neustart verloren gehen kann. |
| `unpair` | – | Schlüssel löschen, z. B. wenn das Panel in HA entfernt wird. |
| `config` | `rev`, `config` | Belegung (Format: HOMEASSISTANT.md im Firmware-Repo). Bei geänderter Belegung startet das Panel neu, sonst merkt es sich nur `rev` und sendet ein neues `hello`. |
| `name` | `name` | Neuer Hostname, dann Neustart. |
| `password` | `password` | Passwort für Web-UI und OTA, höchstens 32 Zeichen. Leer heißt offen. |
| `welcome` | `ha_version`, `proto` | Ab jetzt laufen Zustände und Dienste. Das Panel gilt als verbunden. |
| `event` | `event.a` | Zustände, siehe unten. |
| `result` | `id`, `success` | Ergebnis eines `call_service`. |
| `locate` | – | Ab Version 2. Locate-Modus: Ping-Pong in der oberen Reihe, unten Name, IP, ID. Eine Taste am Panel beendet ihn, sonst nach 5 Minuten. |
| `wake` | – | Ab Version 2. Bildschirmschoner aus. Die Zeit bis zum nächsten Bildschirmschoner läuft ab jetzt neu. |

### Zustände (`event`)

Das Format ist dasselbe wie bei `subscribe_entities` der HA-WebSocket-API. HA schickt nur die
Attribute, die das Panel anzeigt: `hvac_action`, `current_temperature`, `temperature`,
`min_temp`, `max_temp`, `rgb_color`.

```json
{"type": "event", "event": {"a": {"light.flur": {"s": "on", "a": {"rgb_color": [255, 180, 80]}},
                                  "climate.bad": {"s": "heat", "a": {"temperature": 21}}}}}
```

Eine Entität, die es in HA nicht gibt, kommt als `"s": "unavailable"`. Das Panel versteht auch
Änderungen im Format `"c": {"<entity>": {"+": {…}}}`, die HA im lokalen Modus schickt. Die
Integration selbst sendet immer `a`.

`rev` ist die Revision der Belegung: die ersten 12 Hex-Zeichen des SHA-1 über die Belegung als
kompaktes JSON mit sortierten Schlüsseln. HA berechnet sie, das Panel speichert sie nur.

### Grenzen

- **HA:** Die Belegung ist höchstens 8000 Byte groß (`MAX_LAYOUT_BYTES`).
- **Panel:** Es liest eine Belegung in ein JSON-Dokument mit 12 KiB, Zustandsnachrichten in
  eines mit 2 KiB.

## Außerhalb von Port 81

- **mDNS:** Dienst `_smartbuttons._tcp` auf Port 81. Die TXT-Einträge sind `id`, `name`, `mode`,
  `paired` (`0`/`1`), `version` und ab Version 2 auch `proto`. HA findet darüber neue Panels und
  verbindet sich nach einem Neustart sofort neu.
- **Firmware-Update:** `POST http://<panel>/update` als multipart mit dem Feld `firmware`.
  Bei gesetztem Passwort mit Basic-Auth `admin:<Passwort>`.
- **Web-UI des Panels** (Basic-Auth wie oben):
  - `POST /locate` startet den Locate-Modus.
  - `POST /wake` weckt das Panel auf (ab Firmware 0.9.2).
- **Lokaler Modus:** Das Panel ist selbst Client der HA-WebSocket-API (`/api/websocket`):
  - Anmeldung mit `auth` und Long-Lived Token.
  - Pro Entität ein `subscribe_entities` mit `id` 1…n.
  - `call_service` ab `id` 1000.

## Versionen

Die Version steht in der Firmware in `HA_LINK_PROTOCOL` (`src/ha.h`) und in der Integration in
`PROTOCOL` (`const.py`).

**Regeln:**
- **Wann erhöhen:** bei jeder neuen Nachricht, jedem neuen Pflichtfeld und jeder geänderten
  Bedeutung. Beide Stellen erhöhen und hier einen Eintrag ergänzen.
- **Neue Funktionen:** HA spricht jede ältere Version. Was ein Panel erst ab einer Version kann,
  steht in `FEATURE_PROTOCOL` (`const.py`). Bei älterer Firmware ist der Button nicht verfügbar
  und der Befehl antwortet mit `not_supported`. HA schickt dem Panel nichts, was es nicht kennt.
- **Unbekanntes:** Das Panel ignoriert unbekannte Nachrichten und Felder, HA ebenso.
- **Inkompatible Änderung:** Eine Änderung, die alte Panels nicht verstehen, darf es erst geben,
  wenn HA die alte Form nicht mehr braucht. Dann `MIN_PROTOCOL` einführen und hier vermerken.

| Version | Firmware | Integration | Änderung |
|---|---|---|---|
| 1 | bis 0.9.1 | bis 0.9.1 | Erste Fassung: `hello`, `auth`, `pair`, `adopt`, `unpair`, `config`, `name`, `password`, `welcome`, `event`, `call_service`, `result`, `press`. Kein Feld `proto`. |
| 2 | ab 0.9.2 | ab 0.9.2 | `proto` in `hello`, `welcome` und mDNS. Neu: `locate`, `wake`. Die Firmware gibt jetzt alle Nachrichten der bewiesenen Integration an die Panel-Logik weiter. In 0.9.0 und 0.9.1 blieb `locate` an einer festen Liste hängen und kam nie an. |
