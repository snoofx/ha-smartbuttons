# SmartButtons für Home Assistant

Integration für SmartButtons-Panels: 10 LCD-Tasten (SA6432, 64×32 px) an einem ESP32-S3.
Home Assistant findet die Panels selbst, verwaltet ihre Seiten und schaltet, was die Tasten auslösen.

- Panels erscheinen per mDNS unter Einstellungen → Geräte & Dienste.
- Seitenleiste „SmartButtons“ (nur Admins): Seiten-Editor mit Pixel-Vorschau, Suche nach Entitäten
  und Anzeigenamen, Panels umbenennen.
- Konfigurationen: eine Belegung für mehrere Panels, Vorlagen kopieren und auf andere Panels ausrollen.
- Jedes Panel ist mit genau einem HA gekoppelt und darf nur die Entitäten seiner Seiten schalten.
- Ein Passwort schützt Web-UI und OTA aller Panels.
- Jeder Tastendruck kommt als Event `smartbuttons_button_pressed` für eigene Automationen.
- Buttons pro Panel: „Identifizieren“ (Panel zeigt, wo es hängt) und „Aufwecken“ (Bildschirmschoner
  aus, z. B. per Bewegungsmelder). Beide ab Panel-Firmware 0.9.2.
- Protokoll zwischen HA und Panel, mit Versionen: [PROTOCOL.md](PROTOCOL.md).

Voraussetzung: Panel-Firmware ab v0.6.0, Home Assistant ab 2025.1.

## Installation

**Mit HACS:** HACS → Integrationen → ⋮ → Benutzerdefinierte Repositories →
`https://github.com/snoofx/ha-smartbuttons`, Typ „Integration“. Dann „SmartButtons“ installieren und
Home Assistant neu starten.

**Ohne HACS:** Ordner `custom_components/smartbuttons/` nach `<HA-config>/custom_components/smartbuttons/`
kopieren und Home Assistant neu starten.

Danach erscheinen gefundene Panels unter Einstellungen → Geräte & Dienste.

## Panel zurücksetzen

Am Panel SW10 auf START lang drücken (Debug-Screen), dann SW6 „RESET“ lang. Oder SW6 und SW10 während
des Starts 5 Sekunden halten. Das löscht Kopplung und Passwort, WLAN und Belegung bleiben.

Entwickelt wird die Integration zusammen mit der Firmware in einem eigenen Repo. Dieses hier
enthält nur die veröffentlichte Integration.
