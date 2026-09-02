# omarchy-pebble-index

Receive [Pebble Index](https://help.repebble.com/en/articles/15724406-index-advanced-features-mcp-webhook) webhooks on Omarchy and turn each transcription into one desktop action: `omarchy agent` by default, or a note, reminder, calendar event, or Herdr when those match.

The HTTP receiver is a `systemd --user` unit. It does **not** run inside `omarchy-shell`. The bar widget is a QML inbox.

Plugin id: `io.github.goooooooooody.omarchy-pebble-index`

## Requirements

- Omarchy with the Quattro plugin API
- Python 3 (stdlib only)
- Tailscale
- GNU `date`, `systemd --user`
- Optional: Caldir / Omarchy Google Calendar Clock for dated events
- Optional: an OpenAI-compatible HTTP endpoint if you switch the classifier off `rules`

## Install

```bash
omarchy plugin add https://github.com/Goooooooooody/omarchy-pebble-index.git --enable
```

Open the bar icon and click **Start receiver**. That writes `~/.config/omarchy-pebble-index/config.toml` (0600), copies `.env.example` to `.env` if missing, creates `~/Notes/inbox`, and enables the user unit. The panel then shows the Tailscale URL and bearer token to paste into CoreApp.

`omarchy plugin add` only installs the widget. Omarchy does not run plugin install hooks. The receiver is a `systemd --user` unit on purpose, so the widget starts it.

From a checkout you can do the same work in a terminal: `./setup` or `pebble-index setup`. Neither reads OMP or other credential stores.

Menu **Remove plugin** does not stop the unit. Use **Stop receiver** in the panel, or `./uninstall`. The unit also refuses to start if the plugin directory is gone.

If `omarchy plugin add` says this id is already used by `io.github.goody.omarchy-pebble-index`, that is a leftover folder from the id rename. Remove it (`omarchy plugin remove io.github.goody.omarchy-pebble-index --yes`) and add again. On a machine that already has this checkout, skip the GitHub clone and run `./setup` so you keep the working tree.

## CoreApp webhook

Shown in the widget after Start receiver. Same values from `pebble-index webhook`:

```text
URL:    http://<tailscale-ipv4>:8787/webhook
Header: Authorization
Value:  Bearer <token from the widget>
Send:   Transcription
```

The receiver binds only to a Tailscale CGNAT address (`100.64.0.0/10`). It refuses to start if Tailscale is down or the address is not in that range. It never binds `0.0.0.0`.

`GET /health` is unauthenticated and returns only `{status, online}`. `GET /status` and `POST /webhook` require the bearer token.

Any device that has the bearer token can dispatch actions. Treat the token like a password. Tailscale membership is not authentication.

## Classifier

`classifier` in config is exactly one of `rules`, `local`, or `cloud`. There is no hidden fallback.

- **rules** (default): `herd`/`herdr` at the start → Herdr; `note …` → inbox markdown; `in N minutes/hours` → reminder; spoken dates and times → calendar; otherwise `omarchy agent`. Relative delays longer than 24 hours, or `in N days/weeks`, become calendar events.
- **local / cloud**: OpenAI-compatible `POST /v1/chat/completions`. If the endpoint is unset or errors, dispatch fails and you get a notification.

API keys stay out of `config.toml`. Put `OPENROUTER_API_KEY` in `~/.config/omarchy-pebble-index/.env` (0600). The user unit loads that file via `EnvironmentFile`. Example cloud settings (OMP's GLM Flash):

```toml
classifier = "cloud"

[cloud]
base_url = "https://openrouter.ai/api"
api_key = ""
model = "z-ai/glm-5.3-flash"
```

## Sinks

| Transcript | Action |
|---|---|
| `in 20 minutes …` | `omarchy reminder 20 "…"` |
| `tomorrow 3pm …` | `calendar-create` or `caldir new` (1h). If neither exists, a note. |
| starts with `note` | `~/Notes/inbox/YYYY-MM-DD-HHMMSS-<id8>.md` |
| starts with `herd` / `herdr` | `herdr agent prompt default …` |
| everything else | `omarchy agent prompt …` |

`agent_enabled` ships **on**. Set it `false` to fall back to notes. Voice transcripts are untrusted input.

Add a community sink by dropping a TOML file — see [ACTIONS.md](ACTIONS.md). `pebble-index actions` lists what is loaded. Shipped example (`log`) stays off until you set `[actions.log] enabled = true`.

Any other Omarchy plugin can ship `pebble-index/*.toml`. Those commands run as your user after a valid webhook. Treat third-party Index actions like shell plugins: only install plugins you trust.

Desktop voice (VoxType overlay) is a sibling plugin: [omarchy-pebble-voice](https://github.com/Goooooooooody/omarchy-pebble-voice). It calls `pebble-index capture` and can attach a focused-window screenshot for “what is this” / “how does this work”.

The receiver unit may write to `~/Notes` and the pebble-index config/state/data dirs. If an action needs another path, add a systemd drop-in:

```ini
# ~/.config/systemd/user/omarchy-pebble-index.service.d/writepaths.conf
[Service]
ReadWritePaths=%h/Lists
```

Then `systemctl --user daemon-reload && systemctl --user restart omarchy-pebble-index.service`.

## Data access

| Path | Mode | Contents |
|---|---|---|
| `~/.config/omarchy-pebble-index/config.toml` | 0600 | token, classifier, agent switch |
| `~/.config/omarchy-pebble-index/.env` | 0600 | `OPENROUTER_API_KEY` (never commit) |
| `~/.config/omarchy-pebble-index/actions/` | 0700 | your extra action TOML + scripts |
| `~/.local/share/omarchy-pebble-index/inbox.db` | 0600 | full transcripts + dispatch history |
| `~/.local/state/omarchy-pebble-index/state.json` | 0600 | widget snapshot |
| `~/Notes/inbox/*.md` | 0600 | captured notes |

Journald logs do not include transcript bodies. Enabling `cloud` sends transcripts to the configured third-party URL.

Duplicates (same client + recordedAt + transcription) return `{"status":"duplicate"}` and do not dispatch again. Inbox **Re-run** is `--force` and creates another action on purpose.

## Widget

Left-click the bar icon for the inbox. First open: **Start receiver**, then copy the CoreApp URL and token. Open a note, show reminders, or toggle the calendar plugin. **Re-run** re-dispatches and will create another reminder/event/note. **Stop receiver** leaves notes and config.

## License

MIT. Hosted at [github.com/Goooooooooody/omarchy-pebble-index](https://github.com/Goooooooooody/omarchy-pebble-index).
