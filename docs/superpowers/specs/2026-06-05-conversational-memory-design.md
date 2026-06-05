# Conversational Memory — Design

**Date:** 2026-06-05
**Status:** Approved design, pending implementation plan

## Problem

`/query` answers every question statelessly. Conversation turns are *recorded*
to Cosmos Gremlin (`conversations` graph) after each answer, but never read
back — so "My name is Tom" followed by "what was my name?" fails. The bot
surfaces (Slack, Teams) are worse: they neither record nor read conversation
history.

Confirmed root cause (2026-06-05): `conversation_id` is used in exactly one
place — the post-answer append in `app/api/query.py`. `kernel.py` and
`prompts.py` have no awareness of prior turns. Additionally, the current
`SYSTEM_PROMPT` instructs the model to answer *strictly* from retrieved
CONTEXT, so injected history would be ignored without a prompt amendment.

## Decisions (from brainstorm)

| Question | Decision |
| --- | --- |
| History scope | **Generation-only.** Retrieval still uses the raw query; no query rewriting. |
| History source | **Server loads from Cosmos** by `conversation_id` (one point-read per query). Clients never send history. |
| History size | **Last 6 turns**, each answer trimmed to **800 chars**. |
| Surfaces | **All:** web `/query`, Slack webhook, Teams webhook. |
| Architecture | **Approach B:** thin `ConversationMemory` service wired at each route; orchestrator stays pure (history passed as data). |

## Design

### 1. `ConversationMemory` service — `app/conversations/memory.py` (new)

Wraps the existing `ConversationStore`. Best-effort like everything else in
this codebase: memory must never break the answer path.

```python
class ConversationMemory:
    def __init__(self, store: ConversationStore | None): ...

    async def load_history(self, *, user: User, conversation_id: str | None)
            -> list[ConversationTurn]:
        # store.get(...); return last 6 turns, answer.text trimmed to 800 chars.
        # [] when store/conversation_id is None, conversation not found, or on error.

    async def record(self, *, user: User, conversation_id: str,
                     query: str, answer: Answer) -> None:
        # Delegates to store.append(...) (already best-effort).
```

Constants: `MAX_HISTORY_TURNS = 6`, `MAX_ANSWER_CHARS = 800`.

### 2. Prompt changes — `app/generation/prompts.py`

`build_grounded_messages(...)` gains `history: list[ConversationTurn] | None = None`.
Prior turns render as real alternating chat messages between the system
message and the final user message:

```
[system]    <amended system prompt (+ optional skill prompt)>
[user]      <turn 1 query>
[assistant] <turn 1 answer text, trimmed>
...
[user]      QUESTION: <current query>\n\nCONTEXT:\n[1] ...
```

`SYSTEM_PROMPT` is amended: answer from the provided CONTEXT **and the
conversation so far**. Bracketed `[n]` citations apply only to CONTEXT facts.
Facts the user stated earlier in the conversation (e.g. their name) may be
used without citation. If neither CONTEXT nor the conversation contains the
answer, say "I don't have information about that."

Works unchanged for both generation backends (Azure OpenAI and Gemini) since
both consume the role-based message list.

### 3. Orchestrator — `app/orchestrator/kernel.py`

`answer()` (and `_answer()`) gain `history: list[ConversationTurn] | None = None`,
passed through to `build_grounded_messages`. No store dependency added; test
fakes stay trivial. All existing fake orchestrators in tests must add the new
kwarg (same churn as `skill_context`, cf. commit cc848aa).

### 4. Per-surface wiring

| Surface | conversation_id | Load | Record |
| --- | --- | --- | --- |
| Web `/query` (`app/api/query.py`) | client UUID from request body (existing) | before `orchestrator.answer` | replaces the existing inline `conversation_store.append` |
| Slack (`app/api/bots.py`) | `slack:{channel}:{thread_ts}` | before answer | after reply posted |
| Teams (`app/api/bots.py`) | `teams:{activity.conversation.id}` | before answer | after reply sent |

Bot turns are stored under whatever user the bot path resolves today
(`_bot_user()` / resolved principal); per-thread memory comes from the
conversation key, not the user key.

`get_conversation_memory` dependency added to `app/deps.py`, built from the
existing `conversation_store` app state.

### 5. Error handling

- `load_history` failure or miss → `[]` → stateless answer (today's behavior).
- `record` failure → logged warning (existing `ConversationStore.append`
  behavior). No new failure modes on the critical path.
- Latency budget: one Cosmos point-read (~10–50 ms) per query that carries a
  `conversation_id` (including fresh conversations, where the read simply
  returns no rows); queries without a `conversation_id` skip the read entirely.

### 6. Testing (TDD per unit)

1. `ConversationMemory`: turn-limit (6), answer trimming (800), `[]` on
   missing store / missing conversation / store error.
2. `build_grounded_messages`: message ordering with history; no history →
   unchanged shape; amended system prompt content; skill prompt still
   prepended.
3. `/query` route: fake store seeded with turns → orchestrator fake receives
   them; answer recorded via memory service.
4. Slack/Teams webhook: correct conversation-id derivation
   (`slack:{channel}:{thread_ts}`, `teams:{conversation.id}`); load + record
   invoked.
5. Update existing fake orchestrators for the new `history` kwarg.

### 7. Rollout

Single PR, ordered commits: memory service → prompts → orchestrator → web
wiring → Slack wiring → Teams wiring. Web works immediately on deploy (client
already sends `conversation_id`). Deploy via the standard `substrateos-deploy`
pipeline. Manual verification: "My name is Tom" → "what was my name?" in web
chat, then in a Slack thread.

## Out of scope

- Retrieval-aware follow-ups (query rewriting) — possible future iteration.
- Cross-surface conversation continuity (web ↔ Slack share no conversation_id).
- Conversation history UI changes (History tab already works).
