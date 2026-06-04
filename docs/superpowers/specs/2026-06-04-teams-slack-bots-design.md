# Teams & Slack Bot Integration — Design Spec
_2026-06-04_

## Overview

Add real bot handlers for Microsoft Teams and Slack so that users can @-mention SubStrateOS in either platform and get grounded answers via the existing query pipeline. The "Install to Teams" and "Install to Slack" buttons on the admin Surfaces page become functional: they show a guided setup modal with a manifest download (Teams) or step-by-step instructions (Slack), and flip to an "active" state once credentials are configured.

Bot credentials are managed via env vars only (no admin UI). No OAuth install flow is built — the admin registers the apps manually in Azure / Slack and pastes credentials into the server environment.

---

## Architecture

### New backend module: `app/bots/`

| File | Responsibility |
|---|---|
| `app/bots/teams.py` | Parse Bot Framework activity, verify JWT, build Adaptive Card reply |
| `app/bots/slack.py` | Verify Slack HMAC, handle url_verification, parse app_mention, post reply |
| `app/bots/manifest.py` | Generate Teams app manifest.zip in memory |
| `app/api/bots.py` | FastAPI router wiring all four endpoints |

### New API endpoints

```
POST /bot/teams
  Auth:    Bot Framework JWT in Authorization header (verified against Microsoft OIDC)
  Body:    Bot Framework Activity JSON
  Returns: 200 Bot Framework reply activity (Adaptive Card) | 401 | 503

POST /bot/slack
  Auth:    X-Slack-Signature + X-Slack-Request-Timestamp HMAC headers
  Body:    Slack Event payload JSON
  Returns: 200 { challenge } for url_verification | 200 {} for events (reply sent async) | 403 | 503

GET /admin/bot/status
  Auth:    x-admin-key header
  Returns: { teams: { configured: bool, app_id: str | null }, slack: { configured: bool } }

GET /admin/bot/teams/manifest
  Auth:    x-admin-key header
  Returns: application/zip — substrateos-teams.zip | 404 if TEAMS_BOT_APP_ID not set
```

### New config fields (`app/config.py` `Settings`)

```python
teams_bot_app_id: str | None = None        # TEAMS_BOT_APP_ID
teams_bot_app_password: str | None = None  # TEAMS_BOT_APP_PASSWORD
slack_bot_token: str | None = None         # SLACK_BOT_TOKEN
slack_signing_secret: str | None = None    # SLACK_SIGNING_SECRET
```

### Frontend (`web/app/admin/surfaces/page.tsx`)

- On mount: call `getBotStatus()` alongside existing `getSurfaces()`.
- Pass bot status into each `SurfaceCard` to drive three UI states (see Install Flow section).
- New `adminApi.ts` exports: `getBotStatus(): Promise<BotStatus>`, `downloadTeamsManifest(): void`.

---

## Bot Message Flow

### Teams

1. User @-mentions or DMs the bot in Teams.
2. Azure Bot Service POSTs a `message` activity to `/bot/teams` with a Bearer JWT.
3. Handler fetches Microsoft's OIDC JWKS (cached) and verifies the JWT — audience must match `TEAMS_BOT_APP_ID`. Returns 401 on failure.
4. Extracts `activity.text` (strips `<at>BotName</at>` XML prefix), `activity.from.aadObjectId`, `activity.channelData.tenant.id`, `activity.serviceUrl`, `activity.conversation.id`, `activity.id`.
5. Constructs a `User` via `_apply_pilot_tenant` — same path as Easy Auth, so ACL scoping is identical to the web app.
6. Calls `orchestrator.query(query, user)` — same pipeline as `POST /query`.
7. Returns the Adaptive Card **synchronously in the 200 response body** (Bot Framework supports this; Teams allows up to ~15 s which is within the query pipeline's typical latency). No separate POST back to `serviceUrl` required. Adaptive Card contains: answer text (markdown rendered) + up to 5 citation links as `Action.OpenUrl` buttons.
9. On query error: replies with "Sorry, I couldn't find an answer right now. Try rephrasing your question." — no internal error details exposed.

### Slack

1. User @-mentions bot in a channel or DM (`app_mention` or `message.im` event).
2. Slack POSTs to `/bot/slack`.
3. Handler verifies HMAC: `HMAC-SHA256(SLACK_SIGNING_SECRET, "v0:" + timestamp + ":" + raw_body)` must match `X-Slack-Signature`. Rejects 403 if invalid or if `X-Slack-Request-Timestamp` is >5 min old (replay protection).
4. `url_verification` challenge: returns `{ challenge }` synchronously — no auth check needed (Slack's initial handshake).
5. For `app_mention` / `message.im`: returns 200 immediately, then processes async.
6. Extracts `event.text` (strips `<@BOTID>` mention), `event.user`, `event.channel`, `event.thread_ts` (if in thread, replies in thread).
7. Constructs a `User` via `_apply_pilot_tenant` — Slack has no AAD identity so all bot users share the tenant-wide `everyone` ACL principal.
8. Calls `orchestrator.query(query, user)`.
9. POSTs reply to `chat.postMessage` using `SLACK_BOT_TOKEN` — formatted as Slack blocks: header section + answer text + source links as context block.
10. On query error: posts friendly error message to channel.

---

## Install Flow UI

### Teams card — three states

| Condition | UI |
|---|---|
| `configured=false` | "Install to Teams" button → opens setup modal |
| `configured=true, installed=false` | "Download manifest.zip" button on card + note to complete Teams Admin upload |
| `configured=true, installed=true` | Green dot · "Active in Microsoft Teams" |

**Setup modal content:**
- Step 1: Azure Portal → Create Azure Bot resource. Set messaging endpoint: `https://<api-url>/bot/teams`
- Step 2: Copy App ID + App Password → set `TEAMS_BOT_APP_ID` and `TEAMS_BOT_APP_PASSWORD` in server env → restart API
- Step 3: "Download manifest.zip" button (calls `GET /admin/bot/teams/manifest`) → upload zip in Teams Admin Center → Apps → Manage apps → Upload an app
- Step 4: Bot is live — this card will show active on next page load

### Slack card — two states

| Condition | UI |
|---|---|
| `configured=false` | "Install to Slack" button → opens setup modal |
| `configured=true` | Green dot · "Active in Slack" |

**Setup modal content:**
- Step 1: api.slack.com/apps → Create new app → From scratch → name it "SubStrateOS"
- Step 2: OAuth & Permissions → add bot scopes: `app_mentions:read`, `chat:write`, `im:read`, `im:write`
- Step 3: Event Subscriptions → enable → Request URL: `https://<api-url>/bot/slack` → subscribe to `app_mention` and `message.im`
- Step 4: Install to workspace → copy Bot User OAuth Token and Signing Secret
- Step 5: Set `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET` in server env → restart API → card shows active

### Auto-heal on load

When `configured=true` but `installed=false`, the surfaces page automatically calls `PATCH /admin/surfaces/{name}` with `{ installed: true, workspace_name: "Microsoft Teams" | "Slack" }` on mount. This means the installed state self-heals after a server restart without any admin action.

---

## Teams Manifest

Generated in memory by `app/bots/manifest.py`, served as a zip from `GET /admin/bot/teams/manifest`.

**manifest.json fields:**
```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
  "manifestVersion": "1.17",
  "id": "<TEAMS_BOT_APP_ID>",
  "name": { "short": "SubStrateOS", "full": "SubStrateOS Intelligence Layer" },
  "description": { "short": "Ask your company knowledge base", "full": "SubStrateOS is your company intelligence layer. @-mention it in any channel or chat to get grounded answers drawn from SharePoint, Teams, and connected sources — scoped to what you can see." },
  "bots": [{ "botId": "<TEAMS_BOT_APP_ID>", "scopes": ["personal", "team", "groupchat"] }],
  "validDomains": ["<api-host-from-config>"]
}
```

Zip contains: `manifest.json` + `color.png` (192×192, SVG-to-PNG or placeholder) + `outline.png` (32×32).
Icons are embedded as base64 in `manifest.py` — no external asset loading needed.

---

## Error Handling & Security

| Scenario | Behaviour |
|---|---|
| Teams JWT invalid/missing | 401, no query runs |
| Teams env vars missing at startup | Router registers, returns 503 with log warning |
| Slack HMAC invalid | 403 |
| Slack timestamp >5 min | 403 |
| Slack url_verification | 200 + challenge (no HMAC check — Slack can't sign this yet) |
| Slack env vars missing | 503 |
| Query pipeline error (both bots) | Friendly error reply to user, no internal details |
| Malformed Teams activity (missing fields) | 200, no reply (drop silently — Teams retries on 4xx/5xx) |
| manifest download, app_id not set | 404 `{ "detail": "Teams bot not configured" }` |

The `/bot/teams` and `/bot/slack` endpoints are **not** behind the admin key — they are called by Microsoft/Slack infrastructure, not by admins. Security is via JWT verification (Teams) and HMAC verification (Slack).

---

## Testing

### Unit tests (`tests/test_bots.py`)
- JWT verification: valid token passes, tampered token rejected, wrong audience rejected
- Slack HMAC: valid signature passes, tampered body rejected, expired timestamp rejected
- Activity text stripping: `<at>SubStrateOS</at> what is the PTO policy?` → `what is the PTO policy?`
- Mention stripping: `<@U123> what is the PTO policy?` → `what is the PTO policy?`
- Manifest zip: contains `manifest.json`, `color.png`, `outline.png`; manifest JSON has correct `id` and `botId`

### Integration tests (`tests/test_bots_api.py`)
- `POST /bot/teams` with mock activity + mock orchestrator → Adaptive Card in response with `type: "message"`
- `POST /bot/slack` url_verification → `{ challenge: "..." }` in response
- `POST /bot/slack` app_mention with invalid HMAC → 403
- `GET /admin/bot/status` with vars set → `configured: true`; without → `configured: false`
- `GET /admin/bot/teams/manifest` with app_id set → zip download; without → 404

No end-to-end bot tests — Teams and Slack are external systems; the HTTP boundary is the test surface.

---

## Files Changed

**New:**
- `substrateos-api/app/bots/__init__.py`
- `substrateos-api/app/bots/teams.py`
- `substrateos-api/app/bots/slack.py`
- `substrateos-api/app/bots/manifest.py`
- `substrateos-api/app/api/bots.py`
- `substrateos-api/tests/test_bots.py`
- `substrateos-api/tests/test_bots_api.py`

**Modified:**
- `substrateos-api/app/config.py` — 4 new optional settings
- `substrateos-api/app/main.py` — register bots router
- `substrateos-api/app/api/admin.py` — extend `SurfacePatch` model to include optional `installed: bool | None` and `workspace_name: str | None`; update `patch_surface` handler to write them when present
- `web/app/admin/surfaces/page.tsx` — bot status fetch, modal, three-state cards
- `web/lib/adminApi.ts` — `getBotStatus`, `downloadTeamsManifest`; extend `patchSurface` to pass `installed` + `workspace_name`
