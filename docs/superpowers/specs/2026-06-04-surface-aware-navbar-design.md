# Surface-Aware Navbar Design

**Date:** 2026-06-04  
**Status:** Approved

## Problem

The web chat topbar hardcodes five surface chips (Web, Teams, Slack, API, MCP). Teams and Slack are "coming soon" stubs that add noise. API and MCP chips appear unconditionally, ignoring whether the admin has enabled them in Surfaces settings. If the admin disables the Web surface, users still see the full chat UI.

## Changes

### Backend — public surfaces endpoint

Add `GET /surfaces` to the non-admin router (user bearer auth, not admin key). Returns `[{name: str, enabled: bool}]` — just enough for the frontend to gate visibility. No write access.

File: `substrateos-api/app/api/query.py` (or a new thin `surfaces.py` route).

### Frontend — surface-gated topbar

**`web/lib/api.ts`**
- Add `getSurfaces(): Promise<{name: string; enabled: boolean}[]>` using `authedFetch`.

**`web/components/Chat.tsx`**
- On mount, call `getSurfaces()` and store result in state (`surfaceMap`).
- While loading, default all surfaces to enabled (fail-open, no flash).
- Remove `Teams` and `Slack` chips from topbar permanently.
- Remove `Teams` and `Slack` from the `SURFACE_META` map and `Surface` union type.
- Remove the "coming soon" modal branch (`soon` logic in `ConnectModal`).
- Render `API` chip only when `surfaceMap.api === true`.
- Render `MCP` chip only when `surfaceMap.mcp === true`.
- If `surfaceMap.web === false`, render a full-screen blocked page ("Web app disabled by your admin") instead of the chat UI.

## Data Flow

```
Chat mounts
  → getSurfaces() → GET /surfaces (user auth)
  → [{name:"web",enabled:true}, {name:"api",enabled:false}, ...]
  → surfaceMap state
  → topbar renders: Web chip always, API/MCP chips conditional
  → if web disabled: blocked page shown instead
```

## Constraints

- Fail-open: if the `/surfaces` fetch errors, show all chips (don't break the app).
- No admin key needed for regular users.
- Teams/Slack removed entirely from frontend — no conditional logic, no modal, no type.
