# SmartButtons für Home Assistant

Integration für SmartButtons-Panels: 10 LCD-Tasten (SA6432, 64×32 px) an einem ESP32-S3.
Home Assistant findet die Panels selbst, verwaltet ihre Seiten und schaltet, was die Tasten auslösen.

- Panels erscheinen per mDNS unter Einstellungen → Geräte & Dienste.
- Seiten-Editor mit Pixel-Vorschau in der Seitenleiste „SmartButtons“ (nur Admins), Entitäten aus HA zur Auswahl.
- Jedes Panel ist mit genau einem HA gekoppelt und darf nur die Entitäten seiner Seiten schalten.
- Ein Passwort schützt Web-UI und OTA aller Panels.
- Jeder Tastendruck kommt als Event `smartbuttons_button_pressed` für eigene Automationen.

Voraussetzung: Panel-Firmware ab v0.6.0, Home Assistant ab 2025.1.

## Installation

1. Ordner `custom_components/smartbuttons/` nach `<HA-config>/custom_components/smartbuttons/` kopieren.
2. Home Assistant neu starten.
3. Gefundene Panels unter Einstellungen → Geräte & Dienste hinzufügen.

HACS unterstützt nur Repositories auf GitHub, dieses liegt auf GitLab.

## Panel zurücksetzen

SW6 und SW10 beim Einschalten 5 Sekunden halten. Das löscht Kopplung und Passwort.

Entwickelt wird die Integration zusammen mit der Firmware in einem eigenen Repo. Dieses hier
enthält nur die veröffentlichte Integration.
