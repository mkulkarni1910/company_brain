# Org Skills — Design Spec

**Date:** 2026-06-05  
**Feature:** Org Skills (Capability 02 of the Active Intelligence Extension)  
**Status:** Approved for implementation

---

## Overview

Org Skills packages how *this company* does a recurring task — distilled from real workflows, stored in a central registry, and executable by any permitted employee. Skills are invoked in two ways:

1. **Explicitly** — user types `/skill-slug followed by their query` in the chat interface.
2. **Automatically** — the query pipeline detects that the user's query matches a skill and applies it without any slash command.

Running a skill injects that skill's `system_prompt` into the query context before the main LLM call, steering the response toward the company's proven approach rather than a generic answer.

---

## Data Model

New Cosmos DB container: `skills`. Partition key: `/id`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string (UUID) | yes | Primary key |
| `slug` | string | yes | URL-safe, lowercase, hyphenated. Used for `/slug` invocation. Must be unique. Example: `seo-research` |
| `name` | string | yes | Display name shown in catalog. Example: `SEO Research` |
| `description` | string | yes | 1–2 sentences. Shown in catalog AND fed to the skill router LLM to decide relevance |
| `team` | string | yes | Free-text department label: `Engineering`, `Product`, `HR`, `Marketing`, `Business / Ops` |
| `run_scope` | `"org"` \| `"team"` | yes | `"org"` = all authenticated users; `"team"` = reserved for future team-scoped ACL enforcement |
| `enabled` | boolean | yes | Disabled skills are invisible to users and excluded from skill routing |
| `steps` | `list[str]` | yes | Human-readable ordered steps shown in the catalog modal. Not executed programmatically — informational only |
| `data_feeds` | `list[str]` | yes | Data sources the skill reads, shown in catalog modal. Informational only at v1 |
| `system_prompt` | string | yes | Full instruction text injected into query context when this skill is active |
| `retrieval_config` | `object \| null` | no | Reserved for v2 custom retrieval targeting. Stored but ignored at v1 |
| `rating` | float | yes (default: 0.0) | Rolling average of user ratings (1–5) |
| `rating_count` | int | yes (default: 0) | Number of ratings submitted. Used as denominator in rolling average |
| `run_count` | int | yes (default: 0) | Incremented by the query pipeline each time this skill is applied |
| `created_at` | ISO datetime | yes | Set on creation |
| `updated_at` | ISO datetime | yes | Updated on every PATCH |

---

## API

### User-facing endpoints (auth-gated, Azure Easy Auth)

**`GET /skills`**  
Returns all enabled skills. At v1, `run_scope` is not enforced beyond `enabled` — all authenticated users see all enabled skills. Returns array of skill documents minus `system_prompt` (never exposed to clients).

**`POST /skills/{id}/run`**  
Logs a skill execution. Increments `run_count` by 1. Returns `204 No Content`. This endpoint exists for external callers (non-web surfaces). The web query pipeline increments `run_count` directly server-side — the frontend does not call this endpoint.

**`POST /skills/{id}/rate`**  
Body: `{"rating": 1-5}`. Updates rolling average: `new_rating = (current_rating * rating_count + submitted) / (rating_count + 1)`, then increments `rating_count`. Returns updated skill summary.

### Admin endpoints (admin-key-gated, same guard as existing `/admin/*` routes)

**`GET /admin/skills`**  
Returns all skills regardless of `enabled`. Includes all fields except `system_prompt` is included here (admin needs to edit it).

**`POST /admin/skills`**  
Create a skill. Body: all fields except `id`, `rating`, `run_count`, `created_at`, `updated_at` (server-generated). Validates `slug` uniqueness. Returns created document.

**`PATCH /admin/skills/{id}`**  
Partial update. Any subset of: `name`, `description`, `team`, `run_scope`, `enabled`, `steps`, `data_feeds`, `system_prompt`, `retrieval_config`. Always updates `updated_at`. Returns updated document.

**`DELETE /admin/skills/{id}`**  
Hard delete. Returns `204 No Content`.

---

## Query Integration

Two touch points added to the existing query pipeline in `app/api/query.py`, executed before the main orchestrator call.

### Touch point 1 — Explicit `/slug` invocation

If the incoming query text matches the pattern `/[a-z0-9-]+(\s|$)`:

1. Extract the slug from the prefix.
2. Look up the skill in Cosmos by `slug` where `enabled = true`.
3. If found: strip the `/slug` prefix from the query text, set `active_skill = skill`.
4. If not found: pass through unchanged (user may have mistyped — don't error).

### Touch point 2 — Automatic skill routing

If no explicit skill was found AND there are enabled skills in the registry:

1. Fetch the skill catalog from Redis cache (`skills:catalog` key, TTL 5 minutes). On cache miss, query Cosmos for all `enabled = true` skills and cache the `[{slug, name, description}]` list.
2. Send a fast structured LLM call:

```
System: You are a skill router. Given a user query and a catalog of skills,
        return the slug of the most relevant skill if one clearly applies,
        or null if none applies with high confidence. Be conservative — only
        return a skill if the match is strong. Respond only with valid JSON:
        {"skill": "<slug>" | null}

Catalog: [{slug, name, description}, ...]

User query: <query>
```

3. Parse the JSON response. If `skill` is non-null, load the full skill from Cosmos, set `active_skill = skill`.
4. If the LLM call fails or returns malformed JSON, log the error and proceed without a skill (fail open — never block the main query).

### Skill injection

If `active_skill` is set:

- Prepend `active_skill.system_prompt` to the query's system context, separated by `\n\n`.
- Add `skill_used: {id, slug, name}` to the query response payload.
- Increment `active_skill.run_count` directly in Cosmos (async, fire-and-forget — never blocks the response).

If no skill is active, query proceeds unchanged and `skill_used` is `null` in the response.

---

## Frontend

### New page: `/skills` (user-facing catalog)

New entry in the left rail nav: "Skills" with a wrench SVG icon (consistent in size and style with the existing nav icons), positioned below History/Discover.

**Layout:**
- Page header: section label ("Org Skills"), title, one-line description.
- Team filter row: horizontal chip row — "All", then one chip per distinct `team` value. Active chip highlighted with `--amber` accent.
- Skill grid: 3 columns (desktop), 2 (tablet), 1 (mobile). Each card:
  - Team chip (color-coded by team, matching prototype: green for Engineering, terracotta for Product, purple for HR, amber for Marketing, blue for Business/Ops)
  - Star rating + score (top-right)
  - Skill name (Fraunces font)
  - Description (2–3 lines, truncated)
  - Footer: scope badge, run count
  - Hover: slight lift + border darkens
- Clicking a card opens a modal:
  - Header: name + close button
  - "What this skill does" — full description
  - "Steps it runs" — numbered list
  - "Data it reads" — pill chips
  - Footer: "Run skill" primary button + "Close" ghost button
- "Run skill" action: navigates to `/?prefill=/slug` (the main chat route). The Chat component reads the `prefill` query param on mount, sets the input value to `/slug `, and focuses the input so the user can type their query and send.

**Data:** populated from `GET /skills`. Empty state if no skills are enabled.

### New admin page: `/admin/skills`

New tab in the admin layout alongside Sources / Surfaces / Permissions.

**Layout:**
- Header: "Org Skills", description, "Add skill" button (primary).
- Skills table: columns — Name, Team, Scope, Rating, Runs, Enabled (toggle), Actions (Edit | Delete).
- Enabled toggle: inline PATCH on change, optimistic update with rollback on error.
- Add / Edit: opens a full modal form with fields:
  - Name (text), Slug (text, auto-derived from name but editable, validated for uniqueness on blur)
  - Description (textarea, 2 rows)
  - Team (text or select from existing values)
  - Run scope (radio: Org-wide / Team-only)
  - Steps (dynamic list — add/remove rows)
  - Data feeds (dynamic list — add/remove rows)
  - System prompt (textarea, 6 rows, monospace font)
  - Enabled (checkbox)
- Delete: confirmation dialog before hard delete.

### Chat input integration

- When the user types `/` as the first character in the chat input, fetch `GET /skills` (or use cached result) and show an autocomplete dropdown: skill name + description, filtered by what they type after `/`.
- Selecting a suggestion completes the slug and positions the cursor after the space ready for the query text.
- When a query response includes `skill_used`, render a small pill badge below the user's message bubble: `▶ via {skill.name}`.

---

## Error Handling & Edge Cases

- **Skill router call fails:** log error, proceed with normal query. Never block the user.
- **Slug not found on explicit invocation:** pass query through unchanged, no error shown.
- **Disabled skill invoked explicitly:** treat as not found (same as above).
- **`run_count` / `rating` update fails:** log and swallow — these are metrics, not blocking.
- **Two skills equally matched by router:** the router is instructed to be conservative and return null on ambiguity. Only one skill can be active per query.
- **Admin slug collision:** `POST /admin/skills` returns `409 Conflict` with a clear message.

---

## What Is Reused vs New

| Reused as-is | New work |
|---|---|
| Cosmos DB (new container only) | `app/skills/` domain module + store |
| Redis cache (new key `skills:catalog`) | `app/api/skills.py` router |
| Azure Easy Auth + admin key guard | Query pipeline skill routing logic |
| Existing query/orchestrator pipeline | Frontend `/skills` catalog page |
| Admin layout + CSS patterns | Frontend `/admin/skills` management page |
| Chat input component | Chat autocomplete + `skill_used` badge |

---

## Out of Scope (v1)

- Team-scoped ACL enforcement for `run_scope: "team"` — stored but not enforced.
- Auto-discovery of skill candidates from activity patterns (mentioned in design rationale — future).
- Skill versioning or approval workflow — admin edits take effect immediately.
- `retrieval_config` — field stored, ignored.
- Skill ratings displayed back to the user who rated (one-way fire-and-forget).
