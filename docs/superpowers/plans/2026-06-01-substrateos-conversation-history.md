# Persistent Conversation History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** Persist Ask sessions as multi-turn conversations in Cosmos Gremlin; History lists conversations and re-opens the full saved thread.

**Architecture:** `/query` appends a turn (question + grounded answer) to a Cosmos `conversation` vertex **only when the request carries a `conversation_id`** (the Ask chat sends one; the search AI-Overview call does not). New `GET /conversations` + `GET /conversations/{id}`. Frontend Ask view owns a `conversationId`; History becomes a conversation list that replays a saved thread.

**Tech Stack:** FastAPI/Pydantic, gremlinpython (Cosmos Gremlin), Next.js/React/TS. Conventions: backend root `brain-api/`; `uv run pytest`; `uv run ruff check`; stay on `main`; commit trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Non-integration tests inject fakes (no real Azure). Debug header: `x-debug-bypass-auth: t-test,u-x,t-test:everyone`.

**Reference (existing code):** `app/people/graph_client.py` shows the gremlinpython `client.Client(endpoint,"g",username=f"/dbs/{db}/colls/{graph}",password=key,message_serializer=GraphSONSerializersV2d0())` construction + `asyncio.to_thread` submit + the Cosmos `coalesce(unfold(), addV(...))` upsert idiom + `valueMap` returning single-element lists. `Answer{text, citations:list[Citation], query_id, debug}`; `Citation{doc_id, chunk_id, source_url, title, snippet}`; `QueryRequest{query, session_id, k, include_debug}`.

---

## Task 1: Conversation domain models

**Files:** Create `brain-api/app/domain/conversation.py`; Test `brain-api/tests/test_conversation_models.py`

- [ ] **Step 1: failing test**
```python
# tests/test_conversation_models.py
from datetime import UTC, datetime

from app.domain.conversation import Conversation, ConversationSummary, ConversationTurn
from app.domain.query import Answer, Citation


def test_models_construct() -> None:
    turn = ConversationTurn(
        query="pto?",
        answer=Answer(text="20 days", citations=[Citation(
            doc_id="d1", chunk_id="d1#0", source_url="http://x", title="PTO", snippet="...")], query_id=""),
        ts=datetime(2026, 6, 1, tzinfo=UTC))
    conv = Conversation(id="c1", title="pto?", updated_at=datetime(2026, 6, 1, tzinfo=UTC), turns=[turn])
    summ = ConversationSummary(id="c1", title="pto?", updated_at=datetime(2026, 6, 1, tzinfo=UTC), turn_count=1)
    assert conv.turns[0].answer.text == "20 days"
    assert conv.turns[0].answer.citations[0].doc_id == "d1"
    assert summ.turn_count == 1
```
- [ ] **Step 2:** run → fails.
- [ ] **Step 3: implement**
```python
# app/domain/conversation.py
from datetime import datetime

from pydantic import BaseModel

from app.domain.query import Answer


class ConversationTurn(BaseModel):
    query: str
    answer: Answer
    ts: datetime


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: datetime
    turn_count: int


class Conversation(BaseModel):
    id: str
    title: str
    created_at: datetime | None = None
    updated_at: datetime
    turns: list[ConversationTurn]
```
- [ ] **Step 4:** run → pass.
- [ ] **Step 5:** `uv run ruff check app/domain/conversation.py tests/test_conversation_models.py` + commit `feat(history): conversation domain models`.

---

## Task 2: ConversationStore (Cosmos Gremlin)

**Files:** Create `brain-api/app/conversations/__init__.py` (empty), `brain-api/app/conversations/store.py`; Modify `brain-api/app/config.py`; Test `brain-api/tests/test_conversation_store.py`

- [ ] **Step 1:** add to `app/config.py` next to the other cosmos fields:
```python
    cosmos_gremlin_conversations_graph: str = "conversations"
```

- [ ] **Step 2: failing test** (inject a fake gremlin client via the constructor; mirror `test_resolve_people`):
```python
# tests/test_conversation_store.py
import json

import pytest

from app.conversations.store import ConversationStore
from app.domain.identity import User
from app.domain.query import Answer, Citation


class FakeResult:
    def __init__(self, rows): self._rows = rows
    def all(self):
        class _F:
            def __init__(s, r): s._r = r
            def result(s): return s._r
        return _F(self._rows)


class FakeGremlin:
    """Records submits; returns queued responses (one per submit call)."""
    def __init__(self, responses): self._responses = list(responses); self.calls = []
    def submit(self, query, bindings=None):
        self.calls.append((query, bindings or {}))
        return FakeResult(self._responses.pop(0) if self._responses else [])
    def close(self): pass


def _store(responses):
    return ConversationStore(gremlin_client=FakeGremlin(responses))


def _user():
    return User(user_id="u1", tenant_id="t1", email="", display_name="U", group_ids=set())


def _answer():
    return Answer(text="20 days", citations=[Citation(
        doc_id="d1", chunk_id="d1#0", source_url="http://x", title="PTO", snippet="..")], query_id="q1")


@pytest.mark.asyncio
async def test_append_creates_and_caps(monkeypatch) -> None:
    # First submit (read existing) returns empty; second is the upsert.
    g = FakeGremlin([[], []])
    store = ConversationStore(gremlin_client=g)
    await store.append(user=_user(), conversation_id="c1", query="how much pto?", answer=_answer())
    upsert_query, b = g.calls[1]
    assert "coalesce(unfold()" in upsert_query and "addV('conversation')" in upsert_query
    turns = json.loads(b["tj"])
    assert turns[-1]["q"] == "how much pto?"
    assert turns[-1]["a"]["text"] == "20 days"
    assert turns[-1]["a"]["citations"][0]["doc_id"] == "d1"
    assert b["tc"] == 1 and b["title"] == "how much pto?"


@pytest.mark.asyncio
async def test_append_degrades_on_error() -> None:
    class Boom:
        def submit(self, *a, **k): raise RuntimeError("cosmos down")
        def close(self): pass
    store = ConversationStore(gremlin_client=Boom())
    await store.append(user=_user(), conversation_id="c1", query="x", answer=_answer())  # must not raise


@pytest.mark.asyncio
async def test_list_parses_and_orders() -> None:
    rows = [{"id": "c2", "title": "newer", "updated_at": "2026-06-01T10:00:00+00:00", "turn_count": 2},
            {"id": "c1", "title": "older", "updated_at": "2026-05-31T10:00:00+00:00", "turn_count": 1}]
    out = await _store([rows]).list(user=_user())
    assert [s.id for s in out] == ["c2", "c1"]
    assert out[0].turn_count == 2


@pytest.mark.asyncio
async def test_get_parses_turns() -> None:
    tj = json.dumps([{"q": "pto?", "a": {"text": "20 days", "citations": [
        {"doc_id": "d1", "chunk_id": "d1#0", "source_url": "http://x", "title": "PTO", "snippet": ".."}]},
        "ts": "2026-06-01T10:00:00+00:00"}])
    vm = [{"conv_id": ["c1"], "title": ["pto?"], "created_at": ["2026-06-01T09:00:00+00:00"],
           "updated_at": ["2026-06-01T10:00:00+00:00"], "turns_json": [tj]}]
    conv = await _store([vm]).get(user=_user(), conversation_id="c1")
    assert conv is not None
    assert conv.id == "c1" and conv.title == "pto?"
    assert conv.turns[0].answer.text == "20 days"
    assert conv.turns[0].answer.citations[0].doc_id == "d1"


@pytest.mark.asyncio
async def test_get_missing_returns_none() -> None:
    assert await _store([[]]).get(user=_user(), conversation_id="nope") is None
```

- [ ] **Step 3:** run → fails (ModuleNotFoundError).

- [ ] **Step 4: implement**
```python
# app/conversations/__init__.py
```
```python
# app/conversations/store.py
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from gremlin_python.driver import client, serializer

from app.config import get_settings
from app.domain.conversation import Conversation, ConversationSummary, ConversationTurn
from app.domain.identity import User
from app.domain.query import Answer, Citation

logger = logging.getLogger(__name__)
_MAX_TURNS = 50


def _one(vm: dict, key: str):
    v = vm.get(key)
    return v[0] if isinstance(v, list) else v


class ConversationStore:
    """Persistent per-user conversation history in Cosmos Gremlin (label `conversation`,
    partition key tenant_id). All methods are best-effort: a Cosmos failure never breaks
    /query (append) and read paths degrade to []/None."""

    def __init__(self, gremlin_client: Any | None = None) -> None:
        if gremlin_client is not None:
            self._client = gremlin_client
        else:
            s = get_settings()
            if not s.cosmos_gremlin_endpoint or not s.cosmos_gremlin_key:
                raise RuntimeError("Cosmos Gremlin settings are not configured")
            self._client = client.Client(
                s.cosmos_gremlin_endpoint,
                "g",
                username=f"/dbs/{s.cosmos_gremlin_database}/colls/{s.cosmos_gremlin_conversations_graph}",
                password=s.cosmos_gremlin_key,
                message_serializer=serializer.GraphSONSerializersV2d0(),
            )

    async def _submit(self, query: str, bindings: dict[str, Any] | None = None) -> list[Any]:
        def _run() -> list[Any]:
            return self._client.submit(query, bindings or {}).all().result()
        return await asyncio.to_thread(_run)

    async def aclose(self) -> None:
        try:
            await asyncio.to_thread(self._client.close)
        except Exception:  # noqa: BLE001 - close is best-effort
            pass

    async def append(self, *, user: User, conversation_id: str, query: str, answer: Answer) -> None:
        now = datetime.now(UTC).isoformat()
        turn = {
            "q": query,
            "a": {"text": answer.text, "citations": [c.model_dump() for c in answer.citations]},
            "ts": now,
        }
        try:
            rows = await self._submit(
                "g.V().has('conversation','conv_id', cid).has('tenant_id', tid).has('user_id', uid)"
                ".valueMap('turns_json')",
                {"cid": conversation_id, "tid": user.tenant_id, "uid": user.user_id},
            )
            existing: list = []
            if rows:
                raw = _one(rows[0], "turns_json")
                if raw:
                    existing = json.loads(raw)
            turns = (existing + [turn])[-_MAX_TURNS:]
            await self._submit(
                "g.V().has('conversation','conv_id', cid).has('tenant_id', tid).fold()"
                ".coalesce(unfold(),"
                " addV('conversation').property('conv_id', cid).property('tenant_id', tid)"
                "  .property('user_id', uid).property('title', title).property('created_at', now))"
                ".property('updated_at', now).property('turn_count', tc).property('turns_json', tj)",
                {"cid": conversation_id, "tid": user.tenant_id, "uid": user.user_id,
                 "title": (query[:80] or "Untitled"), "now": now,
                 "tc": len(turns), "tj": json.dumps(turns)},
            )
        except Exception as e:  # noqa: BLE001 - best-effort; never break /query
            logger.warning("conversation append failed (cid=%s): %s", conversation_id, e)

    async def list(self, *, user: User, limit: int = 100) -> list[ConversationSummary]:
        try:
            rows = await self._submit(
                "g.V().has('conversation','tenant_id', tid).has('user_id', uid)"
                ".order().by('updated_at', decr).limit(lim)"
                ".project('id','title','updated_at','turn_count')"
                ".by('conv_id').by('title').by('updated_at').by('turn_count')",
                {"tid": user.tenant_id, "uid": user.user_id, "lim": limit},
            )
        except Exception as e:  # noqa: BLE001 - degrade to empty
            logger.warning("conversation list failed: %s", e)
            return []
        out: list[ConversationSummary] = []
        for r in rows:
            try:
                out.append(ConversationSummary(
                    id=r["id"], title=r["title"], updated_at=r["updated_at"],
                    turn_count=int(r["turn_count"])))
            except Exception:  # noqa: BLE001 - skip malformed
                continue
        return out

    async def get(self, *, user: User, conversation_id: str) -> Conversation | None:
        try:
            rows = await self._submit(
                "g.V().has('conversation','conv_id', cid).has('tenant_id', tid).has('user_id', uid)"
                ".valueMap('conv_id','title','created_at','updated_at','turns_json')",
                {"cid": conversation_id, "tid": user.tenant_id, "uid": user.user_id},
            )
        except Exception as e:  # noqa: BLE001 - degrade to None
            logger.warning("conversation get failed (cid=%s): %s", conversation_id, e)
            return None
        if not rows:
            return None
        vm = rows[0]
        raw = _one(vm, "turns_json")
        turns: list[ConversationTurn] = []
        for t in (json.loads(raw) if raw else []):
            a = t.get("a", {})
            turns.append(ConversationTurn(
                query=t.get("q", ""),
                answer=Answer(
                    text=a.get("text", ""),
                    citations=[Citation(**c) for c in a.get("citations", [])],
                    query_id=""),
                ts=t.get("ts")))
        return Conversation(
            id=_one(vm, "conv_id"), title=_one(vm, "title"),
            created_at=_one(vm, "created_at"), updated_at=_one(vm, "updated_at"), turns=turns)
```
Note: `order().by('updated_at', decr)` uses the Cosmos Gremlin descending token `decr`. If the live cluster rejects it, fall back to `desc` (verify in Task 7).

- [ ] **Step 5:** run → pass (5 passed). Ruff. Commit `feat(history): ConversationStore (Cosmos Gremlin, best-effort)`.

---

## Task 3: Wire store + log conversations on /query

**Files:** Modify `brain-api/app/deps.py`, `brain-api/app/main.py`, `brain-api/app/domain/query.py`, `brain-api/app/api/query.py`; Test `brain-api/tests/test_query_conversation_logging.py`

- [ ] **Step 1:** `app/domain/query.py` — add to `QueryRequest`:
```python
    conversation_id: str | None = None
```

- [ ] **Step 2:** `app/deps.py` — add `from app.conversations.store import ConversationStore` at top and append:
```python
def get_conversation_store(request: Request) -> "ConversationStore | None":
    return getattr(request.app.state, "conversation_store", None)
```

- [ ] **Step 3:** `app/main.py` — add `from app.conversations.store import ConversationStore`; in lifespan after `app.state.history_store = HistoryStore()` add:
```python
    app.state.conversation_store = ConversationStore()
```
In `finally:` add (before `app.state.history_store.aclose()` or near it):
```python
        await app.state.conversation_store.aclose()
```

- [ ] **Step 4:** `app/api/query.py` — replace the HistoryStore write with the conversation append. New body:
```python
from app.api._auth_resolve import resolve_user
from app.deps import get_conversation_store, get_orchestrator
from app.domain.query import Answer, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator

router = APIRouter(tags=["query"])


@router.post("/query", response_model=Answer)
async def query(
    body: QueryRequest,
    orchestrator: SemanticKernelOrchestrator = Depends(get_orchestrator),
    conversation_store=Depends(get_conversation_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Answer:
    user = await resolve_user(
        easy_auth=x_ms_client_principal,
        authorization=authorization,
        debug_header=x_debug_bypass_auth,
    )
    tok = (
        authorization.split(" ", 1)[1]
        if authorization and authorization.lower().startswith("bearer ")
        else None
    )
    answer = await orchestrator.answer(body, user=user, user_token=tok)
    # Persist the turn only when the client supplies a conversation_id — the Ask chat
    # does; the search AI-Overview call does not, so overviews aren't logged.
    if body.conversation_id and conversation_store is not None:
        await conversation_store.append(
            user=user, conversation_id=body.conversation_id, query=body.query, answer=answer)
    return answer
```
(Remove the now-unused `get_history_store`/`HistoryStore` imports.)

- [ ] **Step 5: test**
```python
# tests/test_query_conversation_logging.py
from fastapi.testclient import TestClient

from app.deps import get_conversation_store, get_orchestrator
from app.domain.query import Answer
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class _Orch:
    async def answer(self, body, *, user, user_token=None):
        return Answer(text="x", citations=[], query_id="q1")


class _Store:
    def __init__(self): self.appended = []
    async def append(self, *, user, conversation_id, query, answer):
        self.appended.append((conversation_id, query))


def _run(payload):
    store = _Store()
    app.dependency_overrides[get_orchestrator] = lambda: _Orch()
    app.dependency_overrides[get_conversation_store] = lambda: store
    try:
        with TestClient(app) as client:
            resp = client.post("/query", json=payload, headers=_HDR)
        return resp, store
    finally:
        app.dependency_overrides.clear()


def test_logs_when_conversation_id_present() -> None:
    resp, store = _run({"query": "pto?", "conversation_id": "c1"})
    assert resp.status_code == 200
    assert store.appended == [("c1", "pto?")]


def test_no_log_without_conversation_id() -> None:
    resp, store = _run({"query": "pto?"})
    assert resp.status_code == 200
    assert store.appended == []
```

- [ ] **Step 6:** `uv run pytest tests/ -q -m "not integration"` (existing query/debug-auth tests still pass), ruff. Commit `feat(history): log conversation turns on /query (gated by conversation_id)`.

---

## Task 4: /conversations endpoints

**Files:** Create `brain-api/app/api/conversations.py`; Modify `brain-api/app/main.py` (include router); Test `brain-api/tests/test_conversations_api.py`

- [ ] **Step 1: failing test**
```python
# tests/test_conversations_api.py
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.deps import get_conversation_store
from app.domain.conversation import Conversation, ConversationSummary, ConversationTurn
from app.domain.query import Answer
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class FakeStore:
    async def list(self, *, user, limit=100):
        return [ConversationSummary(id="c1", title="pto?", updated_at=datetime(2026, 6, 1, tzinfo=UTC), turn_count=1)]
    async def get(self, *, user, conversation_id):
        if conversation_id != "c1":
            return None
        return Conversation(id="c1", title="pto?", updated_at=datetime(2026, 6, 1, tzinfo=UTC),
            turns=[ConversationTurn(query="pto?", answer=Answer(text="20 days", citations=[], query_id=""),
                                    ts=datetime(2026, 6, 1, tzinfo=UTC))])


def test_conversations_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/conversations").status_code == 401


def test_list_conversations() -> None:
    app.dependency_overrides[get_conversation_store] = lambda: FakeStore()
    try:
        with TestClient(app) as client:
            r = client.get("/conversations", headers=_HDR)
        assert r.status_code == 200 and r.json()[0]["id"] == "c1"
    finally:
        app.dependency_overrides.clear()


def test_get_conversation() -> None:
    app.dependency_overrides[get_conversation_store] = lambda: FakeStore()
    try:
        with TestClient(app) as client:
            r = client.get("/conversations/c1", headers=_HDR)
        assert r.status_code == 200
        assert r.json()["turns"][0]["answer"]["text"] == "20 days"
    finally:
        app.dependency_overrides.clear()


def test_get_missing_conversation_404() -> None:
    app.dependency_overrides[get_conversation_store] = lambda: FakeStore()
    try:
        with TestClient(app) as client:
            r = client.get("/conversations/nope", headers=_HDR)
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_list_empty_when_store_unavailable() -> None:
    app.dependency_overrides[get_conversation_store] = lambda: None
    try:
        with TestClient(app) as client:
            r = client.get("/conversations", headers=_HDR)
        assert r.status_code == 200 and r.json() == []
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2:** run → fails (404 routes missing).

- [ ] **Step 3: implement**
```python
# app/api/conversations.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api._auth_resolve import resolve_user
from app.deps import get_conversation_store
from app.domain.conversation import Conversation, ConversationSummary

router = APIRouter(tags=["conversations"])


async def _user(x_ms_client_principal, authorization, x_debug_bypass_auth):
    return await resolve_user(
        easy_auth=x_ms_client_principal, authorization=authorization,
        debug_header=x_debug_bypass_auth)


@router.get("/conversations", response_model=list[ConversationSummary])
async def conversations(
    store=Depends(get_conversation_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> list[ConversationSummary]:
    user = await _user(x_ms_client_principal, authorization, x_debug_bypass_auth)
    if store is None:
        return []
    return await store.list(user=user)


@router.get("/conversations/{conversation_id}", response_model=Conversation)
async def conversation(
    conversation_id: str,
    store=Depends(get_conversation_store),
    authorization: str | None = Header(default=None),
    x_debug_bypass_auth: str | None = Header(default=None),
    x_ms_client_principal: str | None = Header(default=None),
) -> Conversation:
    user = await _user(x_ms_client_principal, authorization, x_debug_bypass_auth)
    conv = None if store is None else await store.get(user=user, conversation_id=conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv
```

- [ ] **Step 4:** `app/main.py` — add `from app.api.conversations import router as conversations_router` and `app.include_router(conversations_router)`.

- [ ] **Step 5:** run → pass (5). Full suite + ruff. Commit `feat(api): GET /conversations + /conversations/{id}`.

---

## Task 5: Frontend API client

**Files:** Modify `web/lib/api.ts`

- [ ] **Step 1:** Change `postQuery` to accept an optional conversation id and send it:
```typescript
export async function postQuery(query: string, conversationId?: string): Promise<{ answer: Answer; latencyMs: number }> {
  const t0 = performance.now();
  const resp = await authedFetch(`${API_BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, include_debug: true, ...(conversationId ? { conversation_id: conversationId } : {}) }),
  });
  if (!resp.ok) throw new Error(`brain-api ${resp.status}: ${await resp.text()}`);
  const answer = (await resp.json()) as Answer;
  return { answer, latencyMs: Math.round(performance.now() - t0) };
}
```
(The SearchView overview call `postQuery(text)` stays one-arg → no conversation_id → not logged.)

- [ ] **Step 2:** Add types + fetchers:
```typescript
export type ConversationTurn = { query: string; answer: Answer; ts: string };
export type ConversationSummary = { id: string; title: string; updated_at: string; turn_count: number };
export type Conversation = { id: string; title: string; created_at: string | null; updated_at: string; turns: ConversationTurn[] };

export async function getConversations(): Promise<ConversationSummary[]> {
  try {
    const resp = await authedFetch(`${API_BASE}/conversations`);
    if (!resp.ok) return [];
    return (await resp.json()) as ConversationSummary[];
  } catch { return []; }
}

export async function getConversation(id: string): Promise<Conversation | null> {
  try {
    const resp = await authedFetch(`${API_BASE}/conversations/${encodeURIComponent(id)}`);
    if (!resp.ok) return null;
    return (await resp.json()) as Conversation;
  } catch { return null; }
}
```
- [ ] **Step 3:** `pnpm typecheck` → clean. Commit `feat(web): conversation api client + postQuery conversation_id`.

---

## Task 6: Frontend — conversationId, New chat, ConversationsView

**Files:** Modify `web/components/Chat.tsx`, `web/app/globals.css`

READ `Chat.tsx` first. It has `Chat` with `turns`/`view` state and `ask(q)`; a `HistoryView` (the old question-list); `Turn = { id; query; answer?; latencyMs?; error?; loading }`; helpers `relTime`, `initials`.

- [ ] **Step 1:** imports — drop `getHistory`/`HistoryEntry`, add `getConversations`, `getConversation`, `Conversation`, `ConversationSummary`:
```typescript
import { postQuery, postFeedback, getConversations, getConversation, logClick, postSearch,
  Answer, Citation, ConversationSummary, SearchResponse } from "@/lib/api";
```

- [ ] **Step 2:** In `Chat`, add a conversation id and a "new chat" helper:
```tsx
const [conversationId, setConversationId] = useState<string>(() => crypto.randomUUID());
function newChat() { setConversationId(crypto.randomUUID()); setTurns([]); setInput(""); setView("ask"); }
```
Change `ask` to send the conversation id: in the `postQuery(query)` call inside `ask`, pass `postQuery(query, conversationId)`.

- [ ] **Step 3:** Add a "New chat" button in the left rail (after the `<nav className="nav">…</nav>` block, before "Connected sources"):
```tsx
<button className="newchat" onClick={newChat}>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 5v14M5 12h14" /></svg>New chat
</button>
```

- [ ] **Step 4:** Replace `HistoryView` with `ConversationsView`:
```tsx
function ConversationsView({ onOpen }: { onOpen: (id: string) => void }) {
  const [items, setItems] = useState<ConversationSummary[] | null>(null);
  useEffect(() => { getConversations().then(setItems); }, []);
  return (
    <main className="main">
      <header className="topbar"><div className="title">History</div></header>
      <div className="scroll">
        <div className="panel-wrap">
          {items === null && <div className="empty-p">Loading…</div>}
          {items?.length === 0 && <div className="empty-p">No conversations yet — ask something to start one.</div>}
          {items?.map((c) => (
            <button className="hist-row" key={c.id} onClick={() => onOpen(c.id)}>
              <span className="hist-q">{c.title}</span>
              <span className="hist-t">{c.turn_count} turn{c.turn_count === 1 ? "" : "s"} · {relTime(c.updated_at)}</span>
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}
```

- [ ] **Step 5:** In the Chat return, render the history branch with the opener that loads + replays a conversation:
```tsx
{view === "history" && <ConversationsView onOpen={async (id) => {
  const conv = await getConversation(id);
  if (!conv) return;
  setConversationId(conv.id);
  setTurns(conv.turns.map((t, i) => ({ id: `${conv.id}:${i}`, query: t.query, answer: t.answer, loading: false })));
  setView("ask");
}} />}
```
(Replace the previous `<HistoryView .../>` branch.)

- [ ] **Step 6:** globals.css — add `.newchat` styling (matches the nav buttons):
```css
  .newchat{display:flex;align-items:center;gap:9px;width:100%;margin-top:10px;background:var(--amber-bg);
    border:1px solid #e6d3a8;border-radius:10px;padding:9px 12px;font:inherit;font-size:13.5px;font-weight:600;
    color:var(--ink);cursor:pointer}
  .newchat:hover{filter:brightness(1.02)}
  .newchat svg{width:15px;height:15px;color:var(--amber)}
```

- [ ] **Step 7:** `pnpm typecheck && pnpm build` → clean (remove any now-unused imports). Commit `feat(web): persistent conversation history (New chat + replay saved threads)`.

---

## Task 7: Provision graph + deploy + verify + tag (controller)

- [ ] **Step 1:** Create the Cosmos graph:
```bash
az cosmosdb gremlin graph create -a cbrain-lokesh-cosmos -g rg-company-brain-dev -d brain \
  -n conversations --partition-key-path /tenant_id
```
- [ ] **Step 2:** Build + push `brain-api:v7` and `substrateos-web:v9`; deploy both.
- [ ] **Step 3:** Verify brain-api `/healthz` 200; anon `GET /conversations` → 401.
- [ ] **Step 4:** Browser: log in → ask 2-3 questions in Ask → click History → see the conversation (title = first question, "N turns") → open it → the full thread (questions + saved answers) replays → "New chat" starts a fresh one. Confirm `decr` ordering works (newest first); if the live Gremlin rejects `decr`, switch to `desc` in `ConversationStore.list` and redeploy.
- [ ] **Step 5:** Tag `conversation-history-v1`.

---

## Notes
- The old `/history` + `HistoryStore` + `getHistory` stay in the repo but are no longer wired/used (dormant).
- All store paths are best-effort; Cosmos being down must never 500 `/query` or block the chat.
- Keep the SubstrateOS aesthetic; reuse the existing `.hist-row`/`.panel-wrap` styles for the conversation list.
