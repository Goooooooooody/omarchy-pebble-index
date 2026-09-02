# omarchy-pebble-index

Receive [Pebble Index](https://help.repebble.com/en/articles/15724406-index-advanced-features-mcp-webhook) webhooks on Omarchy and turn each transcription into one desktop action: a markdown note, an `omarchy reminder`, a Caldir calendar event, or (opt-in) an agent prompt.

The HTTP receiver is a `systemd --user` unit. It does **not** run inside `omarchy-shell`. The bar widget is a QML inbox.

Plugin id: `io.github.goody.omarchy-pebble-index`

## Requirements

- Omarchy with the Quattro plugin API
- Python 3 (stdlib only)
- Tailscale
- `jq`, GNU `date`, `systemd --user`
- Optional: Caldir / Omarchy Google Calendar Clock for dated events
- Optional: an OpenAI-compatible HTTP endpoint if you switch the classifier off `rules`

## Install

From a checkout:

```bash
./setup
omarchy plugin add /path/to/omarchy-pebble-index --enable
```

`./setup` links this repo into `~/.config/omarchy/plugins/`, writes `~/.config/omarchy-pebble-index/config.toml` (mode 0600), copies `.env.example` to `~/.config/omarchy-pebble-index/.env` if that file is missing, creates `~/Notes/inbox`, and enables the user unit. It does not read OMP or other credential stores.

`omarchy plugin add` alone does **not** start the daemon. Always run `./setup`. Removing the plugin from the Omarchy menu also leaves the unit running; use `./uninstall`.

## CoreApp webhook

```text
URL:    http://<tailscale-ipv4>:8787/webhook
Header: Authorization
Value:  Bearer <token from setup>
Send:   Transcription
```

The receiver binds only to a Tailscale CGNAT address (`100.64.0.0/10`). It refuses to start if Tailscale is down or the address is not in that range. It never binds `0.0.0.0`.

`GET /health` is unauthenticated and returns only `{status, online}`. `GET /status` and `POST /webhook` require the bearer token.

Any device that has the bearer token can dispatch actions. Treat the token like a password. Tailscale membership is not authentication.

## Classifier

`classifier` in config is exactly one of `rules`, `local`, or `cloud`. There is no hidden fallback.

- **rules** (default): wake phrase at the start of the transcript → agent (if enabled); `in N minutes/hours` → reminder; GNU `date`-parseable clock/date → calendar; otherwise a note. Relative delays longer than 24 hours become calendar events.
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
| everything else | `~/Notes/inbox/YYYY-MM-DD-HHMMSS-<id8>.md` |
| starts with `herd` / `herdr` / `agent` / `go do` | `omarchy agent prompt …` **only if** `agent_enabled = true` |

The agent sink ships **off**. Voice transcripts are untrusted input.

Add a community sink by dropping a TOML file — see [ACTIONS.md](ACTIONS.md). `pebble-index actions` lists what is loaded. Shipped example (`log`) stays off until you set `[actions.log] enabled = true`.

Any other Omarchy plugin can ship `pebble-index/*.toml`. Those commands run as your user after a valid webhook. Treat third-party Index actions like shell plugins: only install plugins you trust.

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

Left-click the bar icon for the inbox. Open a note, show reminders, or toggle the calendar plugin. **Re-run** re-dispatches and will create another reminder/event/note.

## License

MIT. Publish from a personal GitHub account that matches the plugin id host (`goody`), not an employer-managed `gh` login.
