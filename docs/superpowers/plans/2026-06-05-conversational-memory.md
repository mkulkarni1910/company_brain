# Conversational Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed prior conversation turns back to the LLM so follow-up questions ("what was my name?") work across web chat, Slack, and Teams.

**Architecture:** A thin `ConversationMemory` service wraps the existing Cosmos-backed `ConversationStore` (load last 6 turns trimmed to 800 chars / record new turn, both best-effort). The orchestrator and prompt builder accept history as plain data — no new dependencies. Each surface derives a conversation id (web: client UUID; Slack: `slack:{channel}:{thread_ts}`; Teams: `teams:{conversation.id}`) and wires load-before / record-after around `orchestrator.answer`. When history is non-empty the answer cache is skipped entirely (keyed by `(user, query)` only — would leak across conversations).

**Tech Stack:** FastAPI, Pydantic v2, Cosmos Gremlin (existing `ConversationStore`), pytest + TestClient with `app.dependency_overrides`.

**Spec:** `docs/superpowers/specs/2026-06-05-conversational-memory-design.md`

**Working directory:** `substrateos-api/` (all paths below relative to it). Run tests with `.venv/bin/python -m pytest`.

---

### Task 1: `ConversationMemory` service

**Files:**
- Create: `app/conversations/memory.py`
- Test: `tests/test_conversation_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_conversation_memory.py
from datetime import UTC, datetime

from app.conversations.memory import MAX_ANSWER_CHARS, MAX_HISTORY_TURNS, ConversationMemory
from app.domain.conversation import Conversation, ConversationTurn
from app.domain.identity import User
from app.domain.query import Answer

_USER = User(user_id="u1", tenant_id="t1", email="u@x", display_name="U", group_ids=set())


def _turn(i: int, answer_text: str = "a") -> ConversationTurn:
    return ConversationTurn(
        query=f"q{i}",
        answer=Answer(text=answer_text, citations=[], query_id=f"id{i}"),
        ts=datetime.now(UTC),
    )


def _conv(turns: list[ConversationTurn]) -> Conversation:
    return Conversation(id="c1", title="t", updated_at=datetime.now(UTC), turns=turns)


class _Store:
    def __init__(self, conv=None, err=False):
        self.conv, self.err = conv, err
        self.appended = []

    async def get(self, *, user, conversation_id):
        if self.err:
            raise RuntimeError("boom")
        return self.conv

    async def append(self, *, user, conversation_id, query, answer):
        self.appended.append((conversation_id, query, answer.text))


async def test_load_returns_last_n_turns() -> None:
    turns = [_turn(i) for i in range(10)]
    mem = ConversationMemory(_Store(conv=_conv(turns)))
    out = await mem.load_history(user=_USER, conversation_id="c1")
    assert len(out) == MAX_HISTORY_TURNS
    assert out[0].query == "q4" and out[-1].query == "q9"


async def test_load_trims_long_answers() -> None:
    long_text = "x" * (MAX_ANSWER_CHARS + 500)
    mem = ConversationMemory(_Store(conv=_conv([_turn(1, long_text)])))
    out = await mem.load_history(user=_USER, conversation_id="c1")
    assert len(out[0].answer.text) == MAX_ANSWER_CHARS + 1  # +1 for the ellipsis
    assert out[0].answer.text.endswith("…")


async def test_load_empty_when_no_store() -> None:
    mem = ConversationMemory(None)
    assert await mem.load_history(user=_USER, conversation_id="c1") == []


async def test_load_empty_when_no_conversation_id() -> None:
    mem = ConversationMemory(_Store(conv=_conv([_turn(1)])))
    assert await mem.load_history(user=_USER, conversation_id=None) == []


async def test_load_empty_when_conversation_missing() -> None:
    mem = ConversationMemory(_Store(conv=None))
    assert await mem.load_history(user=_USER, conversation_id="c1") == []


async def test_load_empty_on_store_error() -> None:
    mem = ConversationMemory(_Store(err=True))
    assert await mem.load_history(user=_USER, conversation_id="c1") == []


async def test_record_delegates_to_append() -> None:
    store = _Store()
    mem = ConversationMemory(store)
    ans = Answer(text="hello", citations=[], query_id="x")
    await mem.record(user=_USER, conversation_id="c1", query="hi", answer=ans)
    assert store.appended == [("c1", "hi", "hello")]


async def test_record_noop_when_no_store() -> None:
    mem = ConversationMemory(None)
    ans = Answer(text="hello", citations=[], query_id="x")
    await mem.record(user=_USER, conversation_id="c1", query="hi", answer=ans)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_conversation_memory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.conversations.memory'`

- [ ] **Step 3: Write the implementation**

```python
# app/conversations/memory.py
from __future__ import annotations

import logging

from app.conversations.store import ConversationStore
from app.domain.conversation import ConversationTurn
from app.domain.identity import User
from app.domain.query import Answer

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 6
MAX_ANSWER_CHARS = 800


class ConversationMemory:
    """Loads recent conversation turns for prompt context and records new turns.
    Best-effort: load failures degrade to a stateless answer; record failures
    are already swallowed by ConversationStore.append."""

    def __init__(self, store: ConversationStore | None) -> None:
        self._store = store

    async def load_history(
        self, *, user: User, conversation_id: str | None
    ) -> list[ConversationTurn]:
        if self._store is None or not conversation_id:
            return []
        try:
            conv = await self._store.get(user=user, conversation_id=conversation_id)
        except Exception as e:  # noqa: BLE001 - memory must never break the answer path
            logger.warning("memory load failed (cid=%s): %s", conversation_id, e)
            return []
        if conv is None:
            return []
        return [_trim(t) for t in conv.turns[-MAX_HISTORY_TURNS:]]

    async def record(
        self, *, user: User, conversation_id: str, query: str, answer: Answer
    ) -> None:
        if self._store is None:
            return
        await self._store.append(
            user=user, conversation_id=conversation_id, query=query, answer=answer
        )


def _trim(turn: ConversationTurn) -> ConversationTurn:
    text = turn.answer.text
    if len(text) <= MAX_ANSWER_CHARS:
        return turn
    return turn.model_copy(
        update={"answer": turn.answer.model_copy(update={"text": text[:MAX_ANSWER_CHARS] + "…"})}
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_conversation_memory.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add app/conversations/memory.py tests/test_conversation_memory.py
git commit -m "feat(conversations): ConversationMemory — load trimmed history, record turns"
```

---

### Task 2: History injection in `build_grounded_messages`

**Files:**
- Modify: `app/generation/prompts.py` (SYSTEM_PROMPT at lines 7–12, `build_grounded_messages` at lines 15–30)
- Test: `tests/test_prompts.py` (append new tests; do not change existing ones beyond what Step 4 shows)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_prompts.py`)

The file already has `from datetime import UTC, datetime` and `from app.domain.query import Candidate`; extend the latter to `from app.domain.query import Answer, Candidate` and add `from app.domain.conversation import ConversationTurn`. Then append:

```python
# --- conversational history ---

def _history_turn(q: str, a: str) -> ConversationTurn:
    return ConversationTurn(
        query=q,
        answer=Answer(text=a, citations=[], query_id="h1"),
        ts=datetime.now(UTC),
    )


def test_history_renders_as_alternating_messages() -> None:
    history = [_history_turn("my name is Tom", "Nice to meet you, Tom.")]
    msgs = build_grounded_messages(query="what was my name?", candidates=[], history=history)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "my name is Tom"
    assert msgs[2]["content"] == "Nice to meet you, Tom."
    assert msgs[3]["content"].startswith("QUESTION: what was my name?")


def test_no_history_keeps_two_message_shape() -> None:
    msgs = build_grounded_messages(query="q", candidates=[])
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_system_prompt_allows_conversation_facts() -> None:
    msgs = build_grounded_messages(query="q", candidates=[], history=[])
    assert "conversation" in msgs[0]["content"].lower()


def test_skill_prompt_still_prepended_with_history() -> None:
    history = [_history_turn("a", "b")]
    msgs = build_grounded_messages(
        query="q", candidates=[], skill_prompt="SKILL RULES", history=history
    )
    assert msgs[0]["content"].startswith("SKILL RULES")
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv/bin/python -m pytest tests/test_prompts.py -v`
Expected: new tests FAIL (`unexpected keyword argument 'history'` / role-shape assertion); existing tests PASS

- [ ] **Step 3: Implement** (replace SYSTEM_PROMPT and `build_grounded_messages` in `app/generation/prompts.py`)

```python
from app.domain.conversation import ConversationTurn

SYSTEM_PROMPT = (
    "You answer questions from the provided CONTEXT and the conversation so far. "
    "Cite every factual claim drawn from CONTEXT with bracketed indices like [1] [2]. "
    "Facts the user stated earlier in this conversation (such as their name or "
    "preferences) may be used without citations. "
    "If neither the context nor the conversation contains the answer, say "
    "'I don't have information about that.' Do not invent facts or sources."
)


def build_grounded_messages(
    *,
    query: str,
    candidates: list[Candidate],
    skill_prompt: str | None = None,
    history: list[ConversationTurn] | None = None,
) -> list[dict[str, str]]:
    system = SYSTEM_PROMPT
    if skill_prompt:
        system = f"{skill_prompt}\n\n{system}"
    blocks: list[str] = []
    for i, c in enumerate(candidates, start=1):
        blocks.append(
            f"[{i}] {c.chunk.title} — {c.chunk.source_url}\n{c.chunk.content}"
        )
    user = f"QUESTION: {query}\n\nCONTEXT:\n" + "\n\n".join(blocks)
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for t in history or []:
        messages.append({"role": "user", "content": t.query})
        messages.append({"role": "assistant", "content": t.answer.text})
    messages.append({"role": "user", "content": user})
    return messages
```

- [ ] **Step 4: Run all prompt tests**

Run: `.venv/bin/python -m pytest tests/test_prompts.py -v`
Expected: all PASS. The three pre-existing tests assert message shape and candidate content, not SYSTEM_PROMPT wording, so they must pass untouched — if one fails, the implementation broke the history-less shape; fix the implementation, not the test.

- [ ] **Step 5: Commit**

```bash
git add app/generation/prompts.py tests/test_prompts.py
git commit -m "feat(generation): inject conversation history into grounded messages"
```

---

### Task 3: Orchestrator `history` param + cache skip

**Files:**
- Modify: `app/orchestrator/kernel.py` (`answer` at line 156, `_answer` at line 172; cache get at 182–189, cache set at 233–236)
- Test: Create `tests/test_orchestrator_history.py` (unit test with fakes, modeled on `tests/test_orchestrator_degradation.py` — `tests/test_orchestrator.py` is integration-marked and unsuitable)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_orchestrator_history.py
"""Unit tests: history is threaded into the LLM messages, and history-carrying
requests bypass the (user, query)-keyed answer cache in both directions."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.conversation import ConversationTurn
from app.domain.identity import User
from app.domain.query import Answer, Candidate, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.orchestrator.planner import QueryPlan
from app.ranking.personalized_ranker import PersonalizedRanker

_USER = User(user_id="u-x", tenant_id="t-test", email="u@x",
             display_name="U", group_ids={"t-test:everyone"})


def _candidate(doc_id: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#chunk-0", doc_id=doc_id, tenant_id="t-test",
            source="uploaded", source_url=f"local://{doc_id}", title=doc_id,
            content="hello world", acl_principals=["t-test:everyone"],
            created_at=now, modified_at=now, chunk_index=0,
        ),
        raw_scores={"content_rrf": 0.9},
    )


def _history_turn(q: str, a: str) -> ConversationTurn:
    return ConversationTurn(
        query=q, answer=Answer(text=a, citations=[], query_id="h"), ts=datetime.now(UTC)
    )


class _FakeRetriever:
    async def retrieve(self, *, query, user, k, timer=None):
        return [_candidate("up:doc")]


class _FakeACLStore:
    async def recheck(self, *, candidates, user):
        return list(candidates)


class _ZeroSignal:
    async def score(self, *, user, doc_ids):
        return {}


class _RecordingCache:
    def __init__(self):
        self.get_calls, self.set_calls = [], []

    async def get_json(self, key):
        self.get_calls.append(key)
        return None

    async def set_json(self, key, value, ttl_seconds):
        self.set_calls.append(key)


class _RecordingLLM:
    def __init__(self):
        self.messages = None

    async def complete(self, *, messages, temperature, max_tokens):
        self.messages = messages
        return "answer text"


class _FakeLiveFetcher:
    async def fetch(self, *, query, user):
        return []


class _FakePlanner:
    async def plan(self, query):
        return QueryPlan(needs_retrieval=True, needs_live_fetch=False,
                         entities=[], rewrite=query)


def _build() -> tuple[SemanticKernelOrchestrator, _RecordingCache, _RecordingLLM]:
    cache, llm = _RecordingCache(), _RecordingLLM()
    orch = SemanticKernelOrchestrator(
        retriever=_FakeRetriever(), llm=llm, cache=cache, acl_store=_FakeACLStore(),
        proximity=_ZeroSignal(),
        ranker=PersonalizedRanker(weight_content=0.7, weight_people=0.3),
        activity=_ZeroSignal(), live_fetcher=_FakeLiveFetcher(), planner=_FakePlanner(),
    )
    return orch, cache, llm


async def test_history_rendered_into_llm_messages() -> None:
    orch, _, llm = _build()
    history = [_history_turn("my name is Tom", "Hi Tom")]
    await orch.answer(QueryRequest(query="what was my name?"), user=_USER, history=history)
    roles = [m["role"] for m in llm.messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert llm.messages[1]["content"] == "my name is Tom"
    assert llm.messages[2]["content"] == "Hi Tom"


async def test_cache_skipped_when_history_present() -> None:
    orch, cache, _ = _build()
    history = [_history_turn("a", "b")]
    await orch.answer(QueryRequest(query="q"), user=_USER, history=history)
    assert cache.get_calls == [] and cache.set_calls == []


async def test_cache_used_when_no_history() -> None:
    orch, cache, _ = _build()
    await orch.answer(QueryRequest(query="q"), user=_USER)
    assert len(cache.get_calls) == 1 and len(cache.set_calls) == 1
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator_history.py -v`
Expected: FAIL with `unexpected keyword argument 'history'`

- [ ] **Step 3: Implement in `app/orchestrator/kernel.py`**

Add the import:

```python
from app.domain.conversation import ConversationTurn
```

`answer` (line 156) — add the kwarg and pass it through:

```python
    async def answer(
        self, request: QueryRequest, *, user: User, user_token: str | None = None,
        skill_context: ResolvedSkill | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> Answer:
        query_id = str(uuid.uuid4())
        timer = StageTimer(query_id=query_id)
        t0 = time.perf_counter()
        try:
            return await self._answer(
                request, user=user, user_token=user_token, timer=timer,
                query_id=query_id, skill_context=skill_context, history=history,
            )
        finally:
            total_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.info("query timing %s total=%sms", timer.summary(), total_ms)
```

`_answer` (line 172) — add the kwarg; gate BOTH cache get and cache set on `not history`; pass history to the prompt builder:

```python
    async def _answer(
        self,
        request: QueryRequest,
        *,
        user: User,
        user_token: str | None,
        timer: StageTimer,
        query_id: str,
        skill_context: ResolvedSkill | None = None,
        history: list[ConversationTurn] | None = None,
    ) -> Answer:
        key = _cache_key(user, request.query)
        # Skip the cache for debug requests (cached answers carry debug=None) and
        # for history-carrying requests (answers depend on the conversation and
        # the key is (user, query) only — serving or storing them would leak
        # across conversations).
        use_cache = not request.include_debug and not history
        if use_cache:
            async with timer.stage("cache_get"):
                cached = await self._cache.get_json(key)
            if cached:
                return Answer.model_validate({**cached, "query_id": query_id})
```

…and at the bottom of `_answer`, wrap the existing cache write (lines 233–236):

```python
        if use_cache:
            cache_blob = answer.model_dump()
            cache_blob.pop("query_id", None)
            cache_blob.pop("debug", None)
            await self._cache.set_json(key, cache_blob, ttl_seconds=600)

        return answer
```

The `build_grounded_messages` call (line 202) becomes:

```python
        messages = build_grounded_messages(
            query=request.query, candidates=candidates[:5],
            skill_prompt=skill_context.system_prompt if skill_context else None,
            history=history,
        )
```

- [ ] **Step 4: Run the orchestrator test files**

Run: `.venv/bin/python -m pytest tests/test_orchestrator_history.py tests/test_orchestrator_degradation.py tests/test_orchestrator_livefetch.py -v`
Expected: all PASS (`tests/test_orchestrator.py` is `@pytest.mark.integration` — excluded from unit runs)

- [ ] **Step 5: Commit**

```bash
git add app/orchestrator/kernel.py tests/test_orchestrator_history.py
git commit -m "feat(orchestrator): accept conversation history; skip answer cache when present"
```

---

### Task 4: Web `/query` wiring

**Files:**
- Modify: `app/deps.py` (add `get_conversation_memory`)
- Modify: `app/api/query.py` (load history before answer at line 56; replace inline append at lines 80–83)
- Modify: `tests/test_query_conversation_logging.py` (fakes gain `history` kwarg + `get`)
- Test: same file, new test

- [ ] **Step 1: Add the dependency to `app/deps.py`**

```python
from app.conversations.memory import ConversationMemory


def get_conversation_memory(request: Request) -> "ConversationMemory":
    return ConversationMemory(getattr(request.app.state, "conversation_store", None))
```

- [ ] **Step 2: Update fakes + write the failing test in `tests/test_query_conversation_logging.py`**

Replace the whole file with:

```python
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.deps import get_conversation_store, get_orchestrator
from app.domain.conversation import Conversation, ConversationTurn
from app.domain.query import Answer
from app.main import app

_HDR = {"x-debug-bypass-auth": "t-test,u-x,t-test:everyone"}


class _Orch:
    def __init__(self): self.seen_history = None
    async def answer(self, body, *, user, user_token=None, skill_context=None, history=None):
        self.seen_history = history
        return Answer(text="x", citations=[], query_id="q1")


class _Store:
    def __init__(self, conv=None):
        self.appended = []
        self.conv = conv
    async def get(self, *, user, conversation_id):
        return self.conv
    async def append(self, *, user, conversation_id, query, answer):
        self.appended.append((conversation_id, query))


def _run(payload, conv=None):
    store = _Store(conv=conv)
    orch = _Orch()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_conversation_store] = lambda: store
    try:
        with TestClient(app) as client:
            # get_conversation_memory builds from app.state; point state at the fake
            app.state.conversation_store = store
            resp = client.post("/query", json=payload, headers=_HDR)
        return resp, store, orch
    finally:
        app.dependency_overrides.clear()
        if hasattr(app.state, "conversation_store"):
            del app.state.conversation_store


def test_logs_when_conversation_id_present() -> None:
    resp, store, _ = _run({"query": "pto?", "conversation_id": "c1"})
    assert resp.status_code == 200
    assert store.appended == [("c1", "pto?")]


def test_no_log_without_conversation_id() -> None:
    resp, store, _ = _run({"query": "pto?"})
    assert resp.status_code == 200
    assert store.appended == []


def test_history_loaded_and_passed_to_orchestrator() -> None:
    turn = ConversationTurn(
        query="my name is Tom",
        answer=Answer(text="Hi Tom", citations=[], query_id="h"),
        ts=datetime.now(UTC),
    )
    conv = Conversation(id="c1", title="t", updated_at=datetime.now(UTC), turns=[turn])
    resp, _, orch = _run({"query": "what was my name?", "conversation_id": "c1"}, conv=conv)
    assert resp.status_code == 200
    assert orch.seen_history is not None and orch.seen_history[0].query == "my name is Tom"


def test_no_history_without_conversation_id() -> None:
    resp, _, orch = _run({"query": "pto?"})
    assert resp.status_code == 200
    assert orch.seen_history == []
```

- [ ] **Step 3: Run to verify the new tests fail**

Run: `.venv/bin/python -m pytest tests/test_query_conversation_logging.py -v`
Expected: `test_history_loaded_and_passed_to_orchestrator` FAILS (orchestrator never receives history)

- [ ] **Step 4: Wire `app/api/query.py`**

Change the import line 10 to include the new dep:

```python
from app.deps import (
    get_conversation_memory,
    get_conversation_store,
    get_orchestrator,
    get_skill_router_svc,
    get_skill_store,
    get_token_store,
)
```

Add the dependency param next to `conversation_store` (line 22):

```python
    memory=Depends(get_conversation_memory),
```

Load history just before the orchestrator call (before line 56) and pass it:

```python
    history = await memory.load_history(user=user, conversation_id=body.conversation_id)

    answer = await orchestrator.answer(
        effective_body, user=user, user_token=tok, skill_context=skill_ctx, history=history
    )
```

Replace the append block (lines 80–83) with the memory service:

```python
    if body.conversation_id:
        await memory.record(
            user=user, conversation_id=body.conversation_id, query=body.query, answer=answer
        )
```

Keep the `get_conversation_store` dependency import only if still referenced; if `conversation_store` is now unused in the route, remove the param and its import. (`tests` still import `get_conversation_store` for overrides of *other* routes — only clean up `query.py`.)

- [ ] **Step 5: Run the affected test files**

Run: `.venv/bin/python -m pytest tests/test_query_conversation_logging.py tests/test_debug_auth_guard.py tests/test_skills_routing.py -v`
`tests/test_debug_auth_guard.py:9` fake gains `history=None` kwarg if it errors; same for any fake orchestrator the run flags.
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/deps.py app/api/query.py tests/test_query_conversation_logging.py tests/test_debug_auth_guard.py
git commit -m "feat(api): /query loads conversation history and records turns via ConversationMemory"
```

---

### Task 5: Slack wiring

**Files:**
- Modify: `app/api/bots.py` (slack_webhook, lines 140–219)
- Test: `tests/test_bots_api.py`

- [ ] **Step 1: Write the failing test**

First update the two fake orchestrators in `tests/test_bots_api.py` to accept the new kwarg:

`tests/test_bots_api.py:27` (`_FakeOrchestrator`):

```python
class _FakeOrchestrator:
    async def answer(self, request, *, user, user_token=None, skill_context=None, history=None):
        return Answer(text="Here is the answer.", citations=[], query_id="q1")
```

`tests/test_bots_api.py:149` (`_ExplodingOrchestrator`):

```python
    async def answer(self, request, *, user, user_token=None, history=None):
        raise AssertionError("orchestrator must not be called for small talk")
```

Then append the new test (module-level `_Memory` so Task 6 reuses it). It follows the exact pattern of `test_slack_webhook_surface_enabled_answers` (env vars + `get_settings.cache_clear()` + signed body via `_slack_sig`; TestClient runs FastAPI background tasks before returning the response):

```python
# ── conversational memory ─────────────────────────────────────────────────────

from app.deps import get_conversation_memory  # noqa: E402


class _Memory:
    def __init__(self):
        self.loaded, self.recorded = [], []

    async def load_history(self, *, user, conversation_id):
        self.loaded.append(conversation_id)
        return []

    async def record(self, *, user, conversation_id, query, answer):
        self.recorded.append((conversation_id, query))


def test_slack_memory_load_and_record(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    memory = _Memory()
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    app.dependency_overrides[get_connection_store] = lambda: _FakeStore()
    app.dependency_overrides[get_conversation_memory] = lambda: memory
    try:
        body = json.dumps({
            "type": "event_callback",
            "event": {"type": "app_mention", "text": "<@U1> what is PTO?", "user": "u1",
                      "channel": "C1", "ts": "111.222"},
        }).encode()
        ts = str(int(time.time()))
        with patch("app.api.bots.post_slack_reply", new=AsyncMock(return_value=None)):
            with TestClient(app) as client:
                resp = client.post(
                    "/bot/slack", content=body,
                    headers={
                        "content-type": "application/json",
                        "x-slack-signature": _slack_sig(_SLACK_SECRET, ts, body),
                        "x-slack-request-timestamp": ts,
                    },
                )
        assert resp.status_code == 200
        assert memory.loaded == ["slack:C1:111.222"]
        assert memory.recorded == [("slack:C1:111.222", "what is PTO?")]
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bots_api.py -v`
Expected: new test FAILS (memory never called)

- [ ] **Step 3: Wire `app/api/bots.py` slack_webhook**

Add to the deps import block (lines 22–27): `get_conversation_memory`.

Add the dependency param to `slack_webhook` (after line 147):

```python
    memory=Depends(get_conversation_memory),
```

Inside `_reply()`, replace the final try-block (lines 208–216) with:

```python
        try:
            effective = skill_ctx.clean_query if skill_ctx else text
            cid = f"slack:{channel}:{thread_ts}"
            history = await memory.load_history(user=_bot_user(), conversation_id=cid)
            answer = await orchestrator.answer(
                QueryRequest(query=effective), user=_bot_user(),
                skill_context=skill_ctx, history=history,
            )
            await memory.record(
                user=_bot_user(), conversation_id=cid, query=effective, answer=answer
            )
        except Exception:
            logger.exception("Slack bot query failed")
            answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")
        await post_slack_reply(slack_token, channel, thread_ts, answer)
```

(Error answers are deliberately NOT recorded — only successful turns become memory. Smalltalk/disabled/refund paths skip memory, unchanged.)

- [ ] **Step 4: Run the bots tests**

Run: `.venv/bin/python -m pytest tests/test_bots_api.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/bots.py tests/test_bots_api.py
git commit -m "feat(bots): per-thread conversational memory for Slack (slack:{channel}:{thread_ts})"
```

---

### Task 6: Teams wiring

**Files:**
- Modify: `app/api/bots.py` (teams_webhook, lines 66–137)
- Test: `tests/test_bots_api.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_bots_api.py`; reuses Task 5's module-level `_Memory` and follows the exact pattern of `test_teams_webhook_valid`)

```python
def test_teams_memory_load_and_record(monkeypatch):
    monkeypatch.setenv("TEAMS_BOT_APP_ID", _TEAMS_APP_ID)
    monkeypatch.setenv("TEAMS_BOT_APP_PASSWORD", _TEAMS_PASSWORD)
    from app.config import get_settings
    get_settings.cache_clear()
    memory = _Memory()
    app.dependency_overrides[get_orchestrator] = lambda: _FakeOrchestrator()
    app.dependency_overrides[get_conversation_memory] = lambda: memory
    try:
        with patch("app.api.bots.verify_teams_jwt", new=AsyncMock(return_value=True)):
            with patch("app.api.bots.send_teams_activity", new=AsyncMock(return_value=True)):
                with TestClient(app) as client:
                    resp = client.post(
                        "/bot/teams",
                        json={
                            "type": "message",
                            "text": "<at>SubStrateOS</at> what is PTO?",
                            "from": {"id": "u1", "aadObjectId": "aad-u1"},
                            "conversation": {"id": "19:abc"},
                            "id": "act1",
                            "serviceUrl": "https://smba.trafficmanager.net",
                        },
                        headers={"Authorization": "Bearer fake-jwt"},
                    )
        assert resp.status_code == 200
        assert memory.loaded == ["teams:19:abc"]
        assert memory.recorded == [("teams:19:abc", "what is PTO?")]
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_bots_api.py -v`
Expected: new test FAILS

- [ ] **Step 3: Wire `app/api/bots.py` teams_webhook**

Add the dependency param (after line 71):

```python
    memory=Depends(get_conversation_memory),
```

Replace the answer block (lines 125–129) with:

```python
    conv_id = (body.get("conversation") or {}).get("id") or ""
    cid = f"teams:{conv_id}" if conv_id else None
    try:
        history = await memory.load_history(user=_bot_user(), conversation_id=cid)
        answer = await orchestrator.answer(
            QueryRequest(query=text), user=_bot_user(), history=history
        )
        if cid:
            await memory.record(
                user=_bot_user(), conversation_id=cid, query=text, answer=answer
            )
    except Exception:
        logger.exception("Teams bot query failed")
        answer = Answer(text=_ERROR_TEXT, citations=[], query_id="err")
```

- [ ] **Step 4: Run the bots tests**

Run: `.venv/bin/python -m pytest tests/test_bots_api.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/bots.py tests/test_bots_api.py
git commit -m "feat(bots): per-conversation memory for Teams (teams:{conversation.id})"
```

---

### Task 7: Full-suite verification

**Files:** none new.

- [ ] **Step 1: Run the entire test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS. Any remaining fake orchestrator flagged with `unexpected keyword argument 'history'` gets `history=None` added to its signature (known candidates beyond those already fixed: `tests/test_mcp_tools.py:19` — only if the MCP route is later wired; it is NOT wired in this plan and must not fail).

- [ ] **Step 2: Lint/type check** (match repo convention)

Run: `.venv/bin/python -m ruff check app tests` (if ruff is configured in the repo; skip otherwise)
Expected: clean

- [ ] **Step 3: Commit any stragglers**

```bash
git add -A && git commit -m "test: update remaining orchestrator fakes for history kwarg"
```

(Skip if nothing changed.)

---

## Manual verification (post-deploy)

1. Web chat: "My name is Tom" → "what was my name?" — answer must contain "Tom".
2. Web chat: new chat (fresh conversation_id) → "what was my name?" — must NOT know.
3. Slack thread: same two-message exchange in one thread; then verify a different thread has no memory.
4. Verify normal grounded Q&A still cites `[n]` and the cache still serves repeat stateless queries (check `cache_get` in query timing logs).
