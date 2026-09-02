# Adding an action

One transcript becomes one action. To add a new sink, drop a TOML file. You do not edit `classify.py` or `dispatch.py`.

## Where to put it

Later paths win on the same `id`.

| Path | Who |
|---|---|
| `actions/*.toml` in this plugin | shipped examples |
| `~/.config/omarchy/plugins/<other-plugin>/pebble-index/*.toml` | other Omarchy plugins |
| `~/.config/omarchy-pebble-index/actions/*.toml` | you |

Enable or disable without moving files:

```toml
# ~/.config/omarchy-pebble-index/config.toml
[actions.log]
enabled = true
```

Restart the receiver after changes: `systemctl --user restart omarchy-pebble-index.service`

Check what loaded: `pebble-index actions`

## Smallest working file

```toml
id = "shop"
label = "Shopping"
description = "Add an item to the shopping list. Starts with shop or shopping."
wake = ["shop", "shopping"]
command = ["{dir}/shop.sh", "{title}"]
```

Put `shop.sh` next to the TOML. `{dir}` is that folder. The script receives argv, not a shell string.

```bash
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$1" >> "${HOME}/Notes/shopping.md"
echo "${HOME}/Notes/shopping.md"
```

`chmod +x shop.sh`. If the script prints a path, the inbox **Open** button will open it when it ends in `.md`.

## Matching

Rules classifier, in order:

1. **wake** — phrase at the start (`shop oat milk`)
2. built-in **relative** reminder (`in 20 minutes`)
3. built-in **dateish** calendar (`tomorrow 3pm`)
4. **regex** — `match = "regex"` plus `pattern`
5. **note**

The model classifier sees every enabled action and its `description`. Write that line so a model can choose it.

Wake phrases need a word after them. `shop` alone is still a note.

## Command placeholders

`{title}` `{body}` `{prompt}` `{minutes}` `{when}` `{id}` `{text}` `{dir}`

```toml
timeout = 15
background = false   # true = fire-and-forget, like the agent
fallback = "note"    # or "none" to fail the event
require = []         # add "agent_enabled" to honor that config switch
priority = 80        # higher wins among overlapping wakes
fields = ["title", "body"]
```

`id` must be `^[a-z][a-z0-9_-]{0,31}$`. Do not reuse `note`, `reminder`, `calendar`, `agent`, or `herdr` unless you intend to replace them.

## Other plugins

Ship `pebble-index/your-action.toml` beside your plugin `manifest.json`. Index will pick it up on the next receiver restart. No patch to this repo.

Voice transcripts are untrusted input. Quote nothing through a shell; use argv. The receiver runs as your user.

Other Omarchy plugins may ship `pebble-index/*.toml`. Index loads them on the next receiver restart. Only install plugins you trust.

The unit’s `ReadWritePaths` are `~/Notes` plus the pebble-index config/state/data dirs. Scripts that write elsewhere need a systemd drop-in (see the README).
