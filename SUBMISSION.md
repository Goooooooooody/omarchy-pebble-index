# Submission

https://github.com/Goooooooooody/omarchy-pebble-index

## Category

Capture / Productivity

## Tags

bar, quickshell, ai

Suggested extra tag: pebble

## Maintainer notes

Runtime dependencies beyond a stock Omarchy install:

- Tailscale (bind address + phone reachability)
- `systemd --user` (receiver unit)
- Python 3 stdlib
- GNU `date` (rules classifier)
- Optional Caldir / Google Calendar Clock plugin for dated events

`omarchy plugin add --enable` is the install. The widget **Start receiver** writes config, enables the user unit, and shows the CoreApp URL and token. `./setup` is the same work from a terminal. Menu removal does not stop the unit; use **Stop receiver** or `./uninstall`. The unit has `ConditionPathExists` on the plugin manifest.

## Security

- Bearer token required. Generated on Start receiver / `pebble-index setup`, stored 0600.
- Listen address is Tailscale CGNAT only; startup is fail-closed.
- Unclassified transcripts open omarchy agent. Herdr only on an explicit herd/herdr wake. `agent_enabled` is the kill switch.
- Transcripts stored in owner-only sqlite; not written to the journal.
- Extra actions are drop-in TOML (see ACTIONS.md); they run as the user.
- Setup does not read OMP or other credential stores.
- `GET /health` is liveness only. Inbox counts require bearer auth.

## Checklist

- [x] `manifest.json` schemaVersion 1, kinds `bar-widget`
- [x] setup / uninstall scripts
- [x] README data-access section
- [x] MIT license
- [x] preview.png
- [x] `omarchy plugin validate` on this checkout
