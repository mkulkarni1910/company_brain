# Identity-Aware Order Lookup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A known requester's identity (from the user directory) flows into retrieval and prompts, so "my order" resolves to *their* order — and customers can never surface another customer's order.

**Architecture:** `DirectoryUser` is threaded as an optional `requester` through the Slack generic Q&A path (`bots.py` → `kernel.answer`) and the refund engine. A pure `order_scope` module is the code-level own-orders gate (prompt rule is the backstop). The customer support-card gets pre-filled by a read-only engine run.

**Tech Stack:** FastAPI · Python 3.12 · uv · pytest (+pytest-asyncio) · existing fakes patterns.

**Spec:** `docs/superpowers/specs/2026-06-07-identity-aware-orders-design.md` — read first.
**Branch:** `feat/identity-aware-orders` (exists, spec committed).
**Run tests from `substrateos-api/`** with `uv run pytest <files> -q`. NEVER run the bare `tests/` directory without `-m "not integration"` (live tests hang).

**Plan addition discovered during planning (now also in spec):** the answer cache is keyed `(user, query)` and the Slack path always uses the shared bot user — a cached identity-scoped answer would leak between requesters. **`requester is not None` ⇒ skip the cache entirely** (read and write), same mechanism as history-carrying requests.

**File map:**

| Action | Path | Responsibility |
|---|---|---|
| Create | `substrateos-api/app/retrieval/order_scope.py` | own-orders gate (pure functions) |
| Modify | `substrateos-api/app/generation/prompts.py` | `requester_note_for` + note injection |
| Modify | `substrateos-api/app/orchestrator/kernel.py` | thread `requester`, scope, cache-skip |
| Modify | `substrateos-api/app/workflows/engine.py` | requester-aware retrieval + prompt |
| Modify | `substrateos-api/app/workflows/flow.py` | customer-path engine lookup |
| Modify | `substrateos-api/app/bots/refund_cards.py` | facts on the customer card |
| Modify | `substrateos-api/app/api/bots.py` | resolve requester on the generic path |
| Modify | `substrateos-api/scripts/seed_refund_demo.py` | Priya's real email |
| Modify | `docs/superpowers/specs/2026-06-07-identity-aware-orders-design.md` | cache-skip decision |
| Tests | `tests/test_order_scope.py` (new), `tests/test_prompts.py`, `tests/test_orchestrator_requester.py` (new), `tests/test_refund_engine_requester.py` (new), `tests/test_refund_flow.py`, `tests/test_bots_api.py` | |

Shared test fixture used across tasks (define locally in each new test file; it's 8 lines):

```python
from app.domain.directory import DirectoryUser

_PRIYA = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                       display_name="Priya Sharma", role="customer")
_TOM = DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                     manager_email="diana@x", groups=["Support Agent"], role="agent")
```

Order-record content fixtures (mirror the seed format exactly — `- **Customer:** Name (email)`):

```python
_PRIYA_ORDER = ("# Order #48213\n\n- **Customer:** Priya Sharma (priya@x)\n"
                "- **Order total:** $1,200.00\n")
_MARCUS_ORDER = ("# Order #48190\n\n- **Customer:** Marcus Lee (marcus.lee@example.com)\n"
                 "- **Order total:** $89.00\n")
_ORPHAN_ORDER = "# Order #99999\n\n- **Customer:** Unknown Person\n- **Order total:** $10\n"
_POLICY = "# Refund Policy\n\nAuto-approve refunds up to $500 within 30 days.\n"
```

---

### Task 1: `order_scope` module

**Files:**
- Create: `substrateos-api/app/retrieval/order_scope.py`
- Test: `substrateos-api/tests/test_order_scope.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_order_scope.py`:

```python
"""Own-orders gate: customers only ever see their own order records."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.directory import DirectoryUser
from app.domain.query import Candidate
from app.retrieval.order_scope import (
    is_order_chunk,
    order_customer_email,
    scope_order_chunks,
)

_PRIYA = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                       display_name="Priya Sharma", role="customer")
_TOM = DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                     manager_email="diana@x", groups=["Support Agent"], role="agent")

_PRIYA_ORDER = ("# Order #48213\n\n- **Customer:** Priya Sharma (priya@x)\n"
                "- **Order total:** $1,200.00\n")
_MARCUS_ORDER = ("# Order #48190\n\n- **Customer:** Marcus Lee (marcus.lee@example.com)\n"
                 "- **Order total:** $89.00\n")
_ORPHAN_ORDER = "# Order #99999\n\n- **Customer:** Unknown Person\n- **Order total:** $10\n"
_POLICY = "# Refund Policy\n\nAuto-approve refunds up to $500 within 30 days.\n"


def _cand(content: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(chunk=Chunk(
        chunk_id=f"c#{hash(content) & 0xffff}", doc_id="d", tenant_id="t-test",
        source="uploaded", source_url="local://d", title="t", content=content,
        acl_principals=["t-test:everyone"], created_at=now, modified_at=now,
        chunk_index=0,
    ))


def test_is_order_chunk_detection():
    assert is_order_chunk(_PRIYA_ORDER) is True
    assert is_order_chunk(_ORPHAN_ORDER) is True
    assert is_order_chunk(_POLICY) is False


def test_order_customer_email_extraction():
    assert order_customer_email(_PRIYA_ORDER) == "priya@x"
    assert order_customer_email(_MARCUS_ORDER) == "marcus.lee@example.com"
    assert order_customer_email(_ORPHAN_ORDER) is None
    assert order_customer_email(_POLICY) is None


def test_customer_keeps_own_order_and_policy_drops_others():
    cands = [_cand(_PRIYA_ORDER), _cand(_MARCUS_ORDER), _cand(_POLICY)]
    out = scope_order_chunks(cands, _PRIYA)
    contents = [c.chunk.content for c in out]
    assert _PRIYA_ORDER in contents and _POLICY in contents
    assert _MARCUS_ORDER not in contents


def test_fail_closed_on_unparseable_order_for_customer():
    out = scope_order_chunks([_cand(_ORPHAN_ORDER)], _PRIYA)
    assert out == []


def test_staff_and_anonymous_pass_through():
    cands = [_cand(_PRIYA_ORDER), _cand(_MARCUS_ORDER), _cand(_ORPHAN_ORDER)]
    assert len(scope_order_chunks(cands, _TOM)) == 3
    assert len(scope_order_chunks(cands, None)) == 3


def test_email_match_is_case_insensitive():
    upper = _PRIYA_ORDER.replace("(priya@x)", "(PRIYA@X)")
    out = scope_order_chunks([_cand(upper)], _PRIYA)
    assert len(out) == 1
```

- [ ] **Step 2:** Run `uv run pytest tests/test_order_scope.py -q` — expect `ModuleNotFoundError: app.retrieval.order_scope`.

- [ ] **Step 3: Implement** — `app/retrieval/order_scope.py`:

```python
"""Own-orders gate for customer requesters.

Order records in the corpus carry a `Customer: Name (email)` line. When the
requester's directory role is "customer", any order-record chunk whose
embedded email doesn't match theirs is dropped before it can reach the
prompt — the prompt rule (generation/prompts.requester_note_for) is the
backstop, this filter is the gate. Fail closed: an order-looking chunk with
no parseable customer email is dropped for customers. Staff (agent/manager)
and anonymous requests pass through untouched.
"""

from __future__ import annotations

import re

from app.domain.directory import DirectoryUser
from app.domain.query import Candidate

_ORDER_ID_RE = re.compile(r"Order\s*#\d+", re.IGNORECASE)
_CUSTOMER_EMAIL_RE = re.compile(r"Customer:[^\n(]*\(([^()\s]+@[^()\s]+)\)", re.IGNORECASE)


def is_order_chunk(content: str) -> bool:
    """An order record mentions an order id AND has a Customer: line."""
    return bool(_ORDER_ID_RE.search(content)) and "customer:" in content.lower()


def order_customer_email(content: str) -> str | None:
    m = _CUSTOMER_EMAIL_RE.search(content)
    return m.group(1).lower() if m else None


def scope_order_chunks(
    candidates: list[Candidate], requester: DirectoryUser | None
) -> list[Candidate]:
    if requester is None or requester.role != "customer":
        return list(candidates)
    email = (requester.email or "").lower()
    return [
        c for c in candidates
        if not is_order_chunk(c.chunk.content)
        or order_customer_email(c.chunk.content) == email
    ]
```

- [ ] **Step 4:** `uv run pytest tests/test_order_scope.py -q` — 6 passed.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/retrieval/order_scope.py substrateos-api/tests/test_order_scope.py
git commit -m "feat(retrieval): own-orders gate for customer requesters"
```

---

### Task 2: Requester note in the answer prompt

**Files:**
- Modify: `substrateos-api/app/generation/prompts.py`
- Test: `substrateos-api/tests/test_prompts.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_prompts.py`:

```python
def test_requester_note_for_customer_and_staff() -> None:
    from app.domain.directory import DirectoryUser
    from app.generation.prompts import requester_note_for

    priya = DirectoryUser(email="priya@x", display_name="Priya Sharma", role="customer")
    note = requester_note_for(priya)
    assert "Priya Sharma" in note and "priya@x" in note and "customer" in note
    assert "only discuss their own orders" in note

    tom = DirectoryUser(email="tom@x", display_name="Tom Reyes", role="agent")
    note = requester_note_for(tom)
    assert "Tom Reyes" in note and "agent" in note
    assert "any customer" in note
    assert "only discuss" not in note


def test_requester_note_injected_into_system_message() -> None:
    msgs = build_grounded_messages(
        query="q", candidates=[], skill_prompt="SKILL RULES",
        requester_note="Requester: Priya (priya@x), role: customer.",
    )
    system = msgs[0]["content"]
    assert "SKILL RULES" in system
    assert "Requester: Priya (priya@x)" in system
    # note comes after the skill prompt, before nothing else breaks
    assert system.index("SKILL RULES") < system.index("Requester: Priya")


def test_no_requester_note_leaves_system_unchanged() -> None:
    msgs = build_grounded_messages(query="q", candidates=[])
    assert "Requester:" not in msgs[0]["content"]
```

- [ ] **Step 2:** `uv run pytest tests/test_prompts.py -q` — new tests FAIL (ImportError / TypeError).

- [ ] **Step 3: Implement** — in `app/generation/prompts.py`:

Add import at top (with the other `app.domain` imports):

```python
from app.domain.directory import DirectoryUser
```

Add after `SYSTEM_PROMPT`:

```python
def requester_note_for(requester: DirectoryUser) -> str:
    """Identity block injected into the system prompt for known requesters.
    The own-orders rule here is the BACKSTOP — the enforced gate is
    retrieval.order_scope.scope_order_chunks."""
    who = requester.display_name or requester.email
    base = f"Requester: {who} ({requester.email}), role: {requester.role}."
    if requester.role == "customer":
        return base + (
            " 'My order' or 'my refund' refers to orders belonging to that email. "
            "Never reveal another customer's order details to them; if asked, say "
            "you can only discuss their own orders."
        )
    return base + (
        " 'My …' refers to them; they may ask about any customer's order."
    )
```

Change `build_grounded_messages` signature and system assembly:

```python
def build_grounded_messages(
    *,
    query: str,
    candidates: list[Candidate],
    skill_prompt: str | None = None,
    history: list[ConversationTurn] | None = None,
    requester_note: str | None = None,
) -> list[dict[str, str]]:
    system = SYSTEM_PROMPT
    if skill_prompt:
        system = f"{skill_prompt}\n\n{system}"
    if requester_note:
        system = f"{system}\n\n{requester_note}"
```

Order matters: skill prompt prefixes the base system prompt, the requester note is appended last — the test asserts `SKILL RULES` appears before `Requester:`. Keep exactly this order.

- [ ] **Step 4:** `uv run pytest tests/test_prompts.py -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/generation/prompts.py substrateos-api/tests/test_prompts.py
git commit -m "feat(generation): requester identity note in the grounded prompt"
```

---

### Task 3: Kernel — thread `requester`, scope candidates, skip cache

**Files:**
- Modify: `substrateos-api/app/orchestrator/kernel.py` (`answer` ~line 157, `_answer` ~line 174)
- Modify: `docs/superpowers/specs/2026-06-07-identity-aware-orders-design.md` (cache decision)
- Test: `substrateos-api/tests/test_orchestrator_requester.py` (new)

- [ ] **Step 1: Write the failing tests** — `tests/test_orchestrator_requester.py` (fake harness mirrors `tests/test_orchestrator_history.py`):

```python
"""Unit tests: requester identity threads into the prompt, customer candidates
are own-orders scoped, and requester-carrying requests bypass the answer cache
(it is keyed (user, query) with the shared bot user — caching would leak
identity-scoped answers across requesters)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.chunk import Chunk
from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.query import Candidate, QueryRequest
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.orchestrator.planner import QueryPlan
from app.ranking.personalized_ranker import PersonalizedRanker

_USER = User(user_id="bot", tenant_id="t-test", email="bot@x",
             display_name="Bot", group_ids={"t-test:everyone"})

_PRIYA = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                       display_name="Priya Sharma", role="customer")
_TOM = DirectoryUser(email="tom@x", slack_id="U_TOM", display_name="Tom Reyes",
                     role="agent")

_PRIYA_ORDER = ("# Order #48213\n\n- **Customer:** Priya Sharma (priya@x)\n"
                "- **Order total:** $1,200.00\n")
_MARCUS_ORDER = ("# Order #48190\n\n- **Customer:** Marcus Lee (marcus.lee@example.com)\n"
                 "- **Order total:** $89.00\n")
_POLICY = "# Refund Policy\n\nAuto-approve refunds up to $500 within 30 days.\n"


def _candidate(doc_id: str, content: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        chunk=Chunk(
            chunk_id=f"{doc_id}#chunk-0", doc_id=doc_id, tenant_id="t-test",
            source="uploaded", source_url=f"local://{doc_id}", title=doc_id,
            content=content, acl_principals=["t-test:everyone"],
            created_at=now, modified_at=now, chunk_index=0,
        ),
        raw_scores={"content_rrf": 0.9},
    )


class _FakeRetriever:
    async def retrieve(self, *, query, user, k, timer=None):
        return [_candidate("order-48213", _PRIYA_ORDER),
                _candidate("order-48190", _MARCUS_ORDER),
                _candidate("refund-policy", _POLICY)]


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
    async def fetch(self, *, query, user, user_token=None):
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


async def test_requester_note_reaches_system_prompt() -> None:
    orch, _, llm = _build()
    await orch.answer(QueryRequest(query="help with my order"), user=_USER,
                      requester=_PRIYA)
    system = llm.messages[0]["content"]
    assert "Priya Sharma" in system and "priya@x" in system
    assert "only discuss their own orders" in system


async def test_customer_candidates_are_own_order_scoped() -> None:
    orch, _, llm = _build()
    await orch.answer(QueryRequest(query="help with my order"), user=_USER,
                      requester=_PRIYA)
    context = llm.messages[-1]["content"]
    assert "48213" in context          # her order present
    assert "Marcus Lee" not in context  # other customer's order filtered out
    assert "Refund Policy" in context   # non-order docs untouched


async def test_staff_requester_sees_all_orders() -> None:
    orch, _, llm = _build()
    await orch.answer(QueryRequest(query="orders status"), user=_USER,
                      requester=_TOM)
    context = llm.messages[-1]["content"]
    assert "48213" in context and "Marcus Lee" in context


async def test_cache_skipped_when_requester_present() -> None:
    orch, cache, _ = _build()
    await orch.answer(QueryRequest(query="q"), user=_USER, requester=_PRIYA)
    assert cache.get_calls == [] and cache.set_calls == []


async def test_no_requester_keeps_anonymous_behavior() -> None:
    orch, cache, llm = _build()
    await orch.answer(QueryRequest(query="q"), user=_USER)
    assert "Requester:" not in llm.messages[0]["content"]
    assert len(cache.get_calls) == 1 and len(cache.set_calls) == 1
```

- [ ] **Step 2:** `uv run pytest tests/test_orchestrator_requester.py -q` — FAIL (`unexpected keyword argument 'requester'`).

- [ ] **Step 3: Implement** — in `app/orchestrator/kernel.py`:

Add imports (top of file, with the other `app.` imports):

```python
from app.domain.directory import DirectoryUser
from app.retrieval.order_scope import scope_order_chunks
```

(`requester_note_for` is imported from `app.generation.prompts` — add it to the existing `from app.generation.prompts import …` line.)

`answer()` signature + passthrough:

```python
    async def answer(
        self, request: QueryRequest, *, user: User, user_token: str | None = None,
        skill_context: ResolvedSkill | None = None,
        history: list[ConversationTurn] | None = None,
        requester: DirectoryUser | None = None,
    ) -> Answer:
        query_id = str(uuid.uuid4())
        timer = StageTimer(query_id=query_id)
        t0 = time.perf_counter()
        try:
            return await self._answer(
                request, user=user, user_token=user_token, timer=timer,
                query_id=query_id, skill_context=skill_context, history=history,
                requester=requester,
            )
        finally:
            total_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.info("query timing %s total=%sms", timer.summary(), total_ms)
```

`_answer()` — add the parameter and three changes:

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
        requester: DirectoryUser | None = None,
    ) -> Answer:
```

(1) Cache guard (replace the existing `use_cache` line; identity-scoped answers
must not be served from or written to the shared (user, query) key):

```python
        use_cache = (not request.include_debug and not history
                     and requester is None)
```

(2) After `candidates = [r.candidate for r in ranked]` (line ~207), scope:

```python
        candidates = scope_order_chunks(candidates, requester)
        if not candidates:
            return Answer(
                text="I don't have information about that.",
                citations=[],
                query_id=query_id,
            )
```

(3) Message build gains the note:

```python
        messages = build_grounded_messages(
            query=request.query, candidates=candidates[:5],
            skill_prompt=skill_context.system_prompt if skill_context else None,
            history=history,
            requester_note=requester_note_for(requester) if requester else None,
        )
```

- [ ] **Step 4:** `uv run pytest tests/test_orchestrator_requester.py tests/test_orchestrator_history.py tests/test_orchestrator_degradation.py tests/test_orchestrator_livefetch.py -q` — all pass.

- [ ] **Step 5: Amend the spec** — in `docs/superpowers/specs/2026-06-07-identity-aware-orders-design.md`, under the `### Changed: app/orchestrator/kernel.py` bullet list, add:

```markdown
- Skips the answer cache (read AND write) when a requester is present —
  the cache key is (user, query) with the shared bot user, so cached
  identity-scoped answers would leak across requesters.
```

- [ ] **Step 6: Commit**

```bash
git add substrateos-api/app/orchestrator/kernel.py substrateos-api/tests/test_orchestrator_requester.py docs/superpowers/specs/2026-06-07-identity-aware-orders-design.md
git commit -m "feat(orchestrator): requester-aware answers — note, own-orders scope, cache skip"
```

---

### Task 4: Refund engine — requester-aware retrieval + prompt

**Files:**
- Modify: `substrateos-api/app/workflows/engine.py`
- Test: `substrateos-api/tests/test_refund_engine_requester.py` (new)

- [ ] **Step 1: Write the failing tests** — `tests/test_refund_engine_requester.py`:

```python
"""RefundEngine with a requester: query augmented, order hits scoped, prompt
tells the LLM who is asking."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.domain.chunk import Chunk
from app.domain.directory import DirectoryUser
from app.domain.identity import User
from app.domain.query import Candidate
from app.workflows.engine import RefundEngine

_PRIYA = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                       display_name="Priya Sharma", role="customer")

_PRIYA_ORDER = ("# Order #48213\n\n- **Customer:** Priya Sharma (priya@x)\n"
                "- **Order total:** $1,200.00\n")
_MARCUS_ORDER = ("# Order #48190\n\n- **Customer:** Marcus Lee (marcus.lee@example.com)\n"
                 "- **Order total:** $89.00\n")
_POLICY = "# Refund Policy\n\nAuto-approve refunds up to $500 within 30 days.\n"

_DECISION_JSON = json.dumps({
    "found": True, "order_id": "48213", "customer": "Priya Sharma",
    "amount_usd": 1200, "order_age_days": 45, "policy_limit_usd": 500,
    "policy_limit_days": 30, "auto_approve": False, "reasoning": "over limit",
})


def _user() -> User:
    return User(user_id="bot", tenant_id="t-test", email="bot@x",
                display_name="Bot", group_ids={"t-test:everyone"})


def _cand(doc_id: str, content: str) -> Candidate:
    now = datetime.now(UTC)
    return Candidate(chunk=Chunk(
        chunk_id=f"{doc_id}#0", doc_id=doc_id, tenant_id="t-test",
        source="uploaded", source_url=f"local://{doc_id}", title=doc_id,
        content=content, acl_principals=["t-test:everyone"],
        created_at=now, modified_at=now, chunk_index=0,
    ))


class _Retriever:
    def __init__(self):
        self.queries: list[str] = []

    async def retrieve(self, *, query, user, k, timer=None):
        self.queries.append(query)
        if "policy" in query.lower():
            return [_cand("refund-policy", _POLICY)]
        return [_cand("order-48213", _PRIYA_ORDER), _cand("order-48190", _MARCUS_ORDER)]


class _LLM:
    def __init__(self):
        self.messages = None

    async def complete(self, *, messages, temperature, max_tokens):
        self.messages = messages
        return _DECISION_JSON


@pytest.mark.asyncio
async def test_requester_augments_query_and_prompt_and_scopes_orders():
    retriever, llm = _Retriever(), _LLM()
    engine = RefundEngine(retriever=retriever, llm=llm)
    decision = await engine.evaluate("I want a refund for my order",
                                     user=_user(), requester=_PRIYA)
    assert decision.found is True
    # order-retrieval query carries the requester's name and email
    assert "Priya Sharma" in retriever.queries[0] and "priya@x" in retriever.queries[0]
    user_msg = llm.messages[-1]["content"]
    # identity line present, own order in context, other customer's order scoped out
    assert "Requester: Priya Sharma (priya@x)" in user_msg
    assert "48213" in user_msg
    assert "Marcus Lee" not in user_msg


@pytest.mark.asyncio
async def test_no_requester_is_unchanged():
    retriever, llm = _Retriever(), _LLM()
    engine = RefundEngine(retriever=retriever, llm=llm)
    await engine.evaluate("refund order 48190", user=_user())
    assert retriever.queries[0] == "refund order 48190"
    user_msg = llm.messages[-1]["content"]
    assert "Requester:" not in user_msg
    assert "Marcus Lee" in user_msg  # staff/anonymous: nothing scoped out
```

- [ ] **Step 2:** `uv run pytest tests/test_refund_engine_requester.py -q` — FAIL (`unexpected keyword argument 'requester'`).

- [ ] **Step 3: Implement** — in `app/workflows/engine.py`:

Add imports:

```python
from app.domain.directory import DirectoryUser
from app.retrieval.order_scope import scope_order_chunks
```

Replace `evaluate` with:

```python
    async def evaluate(self, text: str, *, user: User,
                       requester: DirectoryUser | None = None) -> RefundDecision:
        timer = StageTimer()
        order_query = text
        if requester is not None:
            who = f"{requester.display_name or ''} {requester.email or ''}".strip()
            order_query = f"{text} customer {who}"
        order_hits = await self._retriever.retrieve(
            query=order_query, user=user, k=6, timer=timer
        )
        order_hits = scope_order_chunks(list(order_hits), requester)
        policy_hits = await self._retriever.retrieve(
            query=_POLICY_QUERY, user=user, k=4, timer=timer
        )
        seen: set[str] = set()
        parts: list[str] = []
        for cand in [*order_hits, *policy_hits]:
            ch = cand.chunk
            if ch.chunk_id in seen:
                continue
            seen.add(ch.chunk_id)
            parts.append(f"[{ch.title}]\n{ch.content}")
        context = "\n\n".join(parts[:8]) or "(no documents found)"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        requester_line = ""
        if requester is not None:
            requester_line = (
                f"Requester: {requester.display_name or requester.email} "
                f"({requester.email}), role {requester.role} — "
                "'my order' refers to them.\n"
            )
        messages = [
            {"role": "system", "content": DECISION_PROMPT},
            {"role": "user", "content": (
                f"Today's date: {today}\n{requester_line}\n"
                f"Context documents:\n{context}\n\n"
                f"Refund request: {text}"
            )},
        ]
        raw = await self._llm.complete(messages=messages, temperature=0.0, max_tokens=500)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("Refund engine: no JSON in LLM reply: %r", raw[:200])
            raise RefundEngineError("no JSON in LLM reply")
        try:
            return RefundDecision.model_validate(json.loads(match.group(0)))
        except Exception as e:  # noqa: BLE001
            logger.warning("Refund engine: unparseable decision: %s", e)
            raise RefundEngineError(str(e)) from e
```

- [ ] **Step 4:** `uv run pytest tests/test_refund_engine_requester.py tests/test_refund_engine.py -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/workflows/engine.py substrateos-api/tests/test_refund_engine_requester.py
git commit -m "feat(refund): engine knows the requester — query bias, scoping, prompt line"
```

---

### Task 5: Customer path — pre-filled support card

**Files:**
- Modify: `substrateos-api/app/bots/refund_cards.py` (`customer_request_blocks`)
- Modify: `substrateos-api/app/workflows/flow.py` (`handle_request` customer branch + `_route_to_support`)
- Test: `substrateos-api/tests/test_refund_cards.py` (append), `substrateos-api/tests/test_refund_flow.py` (modify)

- [ ] **Step 1: Card test first** — append to `tests/test_refund_cards.py`:

```python
def test_customer_request_blocks_with_decision_facts():
    from app.bots.refund_cards import customer_request_blocks
    from app.domain.workflow import RefundDecision

    d = RefundDecision(found=True, order_id="48213", customer="Priya Sharma",
                       amount_usd=1200, order_age_days=45, policy_limit_usd=500,
                       policy_limit_days=30, auto_approve=False,
                       reasoning="Over the limit.")
    card = customer_request_blocks(
        request_text="I want a refund", customer_name="Priya Sharma",
        run_id="RB-1", decision=d,
    )
    body = str(card["attachments"])
    assert "#48213" in body and "$1,200" in body and "45 days" in body
    assert "over the auto-approve limit" in body

    bare = customer_request_blocks(
        request_text="I want a refund", customer_name="Priya Sharma", run_id="RB-1",
    )
    assert "#48213" not in str(bare["attachments"])
```

- [ ] **Step 2:** `uv run pytest tests/test_refund_cards.py -q` — new test FAILS (`unexpected keyword argument 'decision'`).

- [ ] **Step 3: Implement the card** — in `app/bots/refund_cards.py`, replace `customer_request_blocks` with:

```python
def customer_request_blocks(*, request_text: str, customer_name: str, run_id: str,
                            decision: RefundDecision | None = None) -> dict:
    """Channel card for a customer's refund ask — needs a support agent to pick
    it up and run the playbook themselves (customers can't trigger refunds).
    When the engine pre-fetched their order, the facts ride along."""
    inner: list[dict] = []
    if decision is not None and decision.found:
        inner.append(_facts_fields(decision))
        inner.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": (f"Order fetched automatically for the requester — "
                     f"{'within' if decision.auto_approve else 'over'} "
                     "the auto-approve limit.")}]})
    inner.extend([
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*From*\n{customer_name}"},
            {"type": "mrkdwn", "text": f"*Request*\n{request_text[:500]}"},
        ]},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": "Customers can't trigger refunds directly — an agent should "
                    "pick this up and run it."}]},
    ])
    return {
        "blocks": [{"type": "section", "text": {"type": "mrkdwn",
            "text": f":wave: *Customer refund request* — needs a support agent · run {run_id}"}}],
        "attachments": [_bar(_AMBER, inner)],
    }
```

- [ ] **Step 4:** `uv run pytest tests/test_refund_cards.py -q` — all pass.

- [ ] **Step 5: Flow tests** — in `tests/test_refund_flow.py`:

REPLACE `test_customer_routes_to_support_channel` with (the engine now RUNS on
the customer path — the old `assert_not_called` flips):

```python
@pytest.mark.asyncio
async def test_customer_routes_to_support_with_prefetched_order(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_REFUND_CHANNEL_ID", "C_SUPPORT")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(decision=_OVER_LIMIT)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="I want a refund for order 48213", channel="D_PRIYA",
                                  thread_ts=None, requester_slack_id="U_PRIYA", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "routed_to_support"
    assert run.decision is not None and run.decision.order_id == "48213"
    # engine ran scoped to the customer
    kwargs = flow._engine.evaluate.await_args.kwargs
    assert kwargs["requester"].email == "priya@x"
    posts = [p for m, p in calls if m == "chat.postMessage"]
    support_post = next(p for p in posts if p["channel"] == "C_SUPPORT")
    assert "48213" in str(support_post)              # facts on the card
    assert any(p["channel"] == "D_PRIYA" for p in posts)
    steps = [e.step for e in await store.list_events(run.id)]
    assert "Order fetched" in steps and "Routed to support" in steps


@pytest.mark.asyncio
async def test_customer_routing_survives_engine_error(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_REFUND_CHANNEL_ID", "C_SUPPORT")
    from app.config import get_settings
    get_settings.cache_clear()
    flow, store = _flow(error=True)
    calls, fake = _slack_recorder()
    with patch("app.workflows.flow.slack_call", new=fake):
        await flow.handle_request(text="refund please", channel="D_PRIYA", thread_ts=None,
                                  requester_slack_id="U_PRIYA", user=_user())
    run = (await store.list_runs())[0]
    assert run.status == "routed_to_support"   # lookup failure never blocks routing
    assert run.decision is None
    posts = [p for m, p in calls if m == "chat.postMessage"]
    assert any(p["channel"] == "C_SUPPORT" for p in posts)  # bare card still posted
    assert "Order fetched" not in [e.step for e in await store.list_events(run.id)]
```

- [ ] **Step 6:** `uv run pytest tests/test_refund_flow.py -q` — the two new/changed tests FAIL.

- [ ] **Step 7: Implement the flow** — in `app/workflows/flow.py`:

The customer branch in `handle_request` passes the directory record and `user`:

```python
        if record.role == "customer":
            await self._route_to_support(token, run, text=text, requester=requester,
                                         record=record, channel=channel,
                                         thread_ts=thread_ts, user=user)
            return
```

Replace `_route_to_support` with:

```python
    async def _route_to_support(self, token: str, run, *, text: str, requester: str,
                                record, channel: str, thread_ts: str | None,
                                user: User) -> None:
        """Customer path: read-only engine lookup pre-fills the hand-off card;
        lookup failure never blocks routing."""
        support_channel = get_settings().slack_refund_channel_id
        if not support_channel:
            run.status = "needs_attention"
            await self._store.save(run)
            await self._store.add_event(
                run.id, step="No support channel",
                detail="SLACK_REFUND_CHANNEL_ID is not configured — customer request not routed",
                actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text="Refunds are handled by our support team — "
                                  "please contact them directly.")
            return
        decision = None
        try:
            decision = await self._engine.evaluate(text, user=user, requester=record)
        except RefundEngineError:
            logger.warning("customer order lookup failed; routing without facts")
        if decision is not None and decision.found:
            run.decision = decision
            await self._store.add_event(
                run.id, step="Order fetched",
                detail=(f"Order #{decision.order_id} · ${decision.amount_usd:,.0f} · "
                        f"age {decision.order_age_days} days — fetched for {requester}"),
                actor="SubstrateOS")
        else:
            decision = None
        posted = await slack_call(token, "chat.postMessage", {
            "channel": support_channel,
            "text": f"Customer refund request from {requester}",
            **customer_request_blocks(request_text=text, customer_name=requester,
                                      run_id=run.id, decision=decision),
        })
        if not posted:
            run.status = "needs_attention"
            await self._store.save(run)
            await self._store.add_event(run.id, step="Routing failed",
                                        detail="Could not post to the refunds channel",
                                        actor="SubstrateOS")
            await self._post(token, channel, thread_ts,
                             text="I couldn't reach the support team — please contact them directly.")
            return
        run.status = "routed_to_support"
        await self._store.save(run)
        await self._store.add_event(
            run.id, step="Routed to support",
            detail=f"Posted to the refunds channel for a support agent ({requester} is a customer)",
            actor="SubstrateOS")
        await self._post(token, channel, thread_ts,
                         text="Refunds are handled by our support team — I've passed your "
                              "request to them and someone will follow up here.")
```

- [ ] **Step 8:** `uv run pytest tests/test_refund_flow.py tests/test_refund_cards.py -q` — all pass (the other flow tests are untouched by this change; if `test_customer_without_channel_config_stops` fails, note the channel-unset branch must run BEFORE the engine lookup — as written above).

- [ ] **Step 9: Commit**

```bash
git add substrateos-api/app/workflows/flow.py substrateos-api/app/bots/refund_cards.py substrateos-api/tests/test_refund_flow.py substrateos-api/tests/test_refund_cards.py
git commit -m "feat(refund): customer hand-off card pre-filled with their own order"
```

---

### Task 6: Slack generic path passes the requester

**Files:**
- Modify: `substrateos-api/app/api/bots.py` (`slack_webhook`)
- Test: `substrateos-api/tests/test_bots_api.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_bots_api.py` (reuses the file's `_slack_sig`, `_FakeStore`, `_FakeAck`, `_FakeRouter`, `_slack_event_body`, `_post_signed_slack` helpers and `_SLACK_*` constants):

```python
# ── POST /bot/slack (requester identity threads into the generic answer) ──────

from app.deps import get_directory_service  # noqa: E402
from app.domain.directory import DirectoryUser  # noqa: E402


class _RecordingOrchestrator:
    def __init__(self):
        self.kwargs = None

    async def answer(self, request, *, user, skill_context=None, history=None,
                     requester=None, user_token=None):
        self.kwargs = {"requester": requester}
        return Answer(text="Here is the answer.", citations=[], query_id="q1")


class _FakeDirectory:
    def __init__(self, record):
        self._record = record

    async def resolve(self, email):
        return self._record if email else None


def test_slack_generic_path_passes_requester(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    priya = DirectoryUser(email="priya@x", slack_id="U_PRIYA",
                          display_name="Priya Sharma", role="customer")
    orch = _RecordingOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_connection_store] = lambda: _FakeStore()
    app.dependency_overrides[get_acknowledger] = lambda: _FakeAck()
    app.dependency_overrides[get_skill_router_svc] = lambda: _FakeRouter(None)
    app.dependency_overrides[get_directory_service] = lambda: _FakeDirectory(priya)

    async def fake_slack_call(token, method, payload):
        if method == "users.info":
            return {"ok": True, "user": {"real_name": "Priya Sharma",
                                         "profile": {"email": "priya@x"}}}
        return {"ok": True}

    try:
        body = _slack_event_body("can you help me with my order", user="U_PRIYA")
        with patch("app.api.bots.slack_call", new=fake_slack_call), \
                patch("app.api.bots.post_slack_reply", new=AsyncMock(return_value=None)):
            with TestClient(app) as client:
                resp = _post_signed_slack(client, body)
        assert resp.status_code == 200
        assert orch.kwargs is not None
        assert orch.kwargs["requester"] is not None
        assert orch.kwargs["requester"].email == "priya@x"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_slack_generic_path_anonymous_when_directory_misses(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", _SLACK_TOKEN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _SLACK_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    orch = _RecordingOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_connection_store] = lambda: _FakeStore()
    app.dependency_overrides[get_acknowledger] = lambda: _FakeAck()
    app.dependency_overrides[get_skill_router_svc] = lambda: _FakeRouter(None)
    app.dependency_overrides[get_directory_service] = lambda: _FakeDirectory(None)

    async def fake_slack_call(token, method, payload):
        if method == "users.info":
            return {"ok": True, "user": {"real_name": "Who",
                                         "profile": {"email": "ghost@x"}}}
        return {"ok": True}

    try:
        body = _slack_event_body("what is the pto policy", user="U_GHOST")
        with patch("app.api.bots.slack_call", new=fake_slack_call), \
                patch("app.api.bots.post_slack_reply", new=AsyncMock(return_value=None)):
            with TestClient(app) as client:
                resp = _post_signed_slack(client, body)
        assert resp.status_code == 200
        assert orch.kwargs == {"requester": None}
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
```

(If `Answer` / `AsyncMock` / `patch` / `get_orchestrator` etc. aren't already imported at the top of this file, they are — verify before adding duplicates.)

- [ ] **Step 2:** `uv run pytest tests/test_bots_api.py -q` — the two new tests FAIL (orchestrator never receives `requester`).

- [ ] **Step 3: Implement** — in `app/api/bots.py`:

Add `get_directory_service` to the existing `from app.deps import (…)` block.

In `slack_webhook`'s signature, add the dependency:

```python
    directory=Depends(get_directory_service),
```

Inside `_reply`, directly BEFORE the final `try:` block (the generic-answer
section, after the three workflow branches), add:

```python
        # Known requester? Their identity scopes and personalizes the answer.
        requester = None
        if directory is not None:
            with contextlib.suppress(Exception):
                _, req_email = await _slack_profile(slack_token, slack_user)
                requester = await directory.resolve(req_email)
```

And pass it to the orchestrator call:

```python
            answer = await orchestrator.answer(
                QueryRequest(query=effective), user=_bot_user(),
                skill_context=skill_ctx, history=history, requester=requester,
            )
```

- [ ] **Step 4:** `uv run pytest tests/test_bots_api.py -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add substrateos-api/app/api/bots.py substrateos-api/tests/test_bots_api.py
git commit -m "feat(bots): Slack generic answers know who is asking"
```

---

### Task 7: Seed email + full verification + docs

**Files:**
- Modify: `substrateos-api/scripts/seed_refund_demo.py:49`
- Modify: `mockups/architecture.html`

- [ ] **Step 1: Seed** — in `scripts/seed_refund_demo.py`, line 49, change:

```python
- **Customer:** Priya Sharma (priya.sharma@example.com)
```

to:

```python
- **Customer:** Priya Sharma (priya@OmkarConsultancy1910.onmicrosoft.com)
```

Marcus Lee (line 61) stays fictional — he demos the staff "any customer" case.

- [ ] **Step 2: Full backend suite**

Run: `uv run pytest tests/ -q -m "not integration" -p no:cacheprovider`
Expected: all pass. Paste the summary line.

- [ ] **Step 3: Architecture doc** — `mockups/architecture.html` (Master Deck palette, reuse existing classes):
- Detailed view, in the directory/identity flow added by the previous feature: extend the `DirectoryService.resolve` description with "feeds requester identity into answers: prompt note + own-orders scoping (`retrieval/order_scope`), cache bypass for personalized answers".
- Refund playbook description: customer hand-off card is "pre-filled with the customer's own order (read-only engine lookup)".
- High-level view, identity pillar sentence: append "answers are identity-scoped — customers only ever see their own orders."
- `open mockups/architecture.html` and eyeball both views.

- [ ] **Step 4: Commit**

```bash
git add substrateos-api/scripts/seed_refund_demo.py mockups/architecture.html
git commit -m "feat(seed)+docs: Priya's real email in order doc; identity-scoped answers in architecture"
```

---

## Post-merge actions (surface in the final report — NOT part of this plan)

1. Deploy `substrateos-api` via the substrateos-deploy skill (explicit approval).
2. Re-run the seed against prod (`scripts/seed_refund_demo.py` — idempotent) so
   order #48213 carries Priya's real email.
3. Demo: Priya asks "can you help me with my order" → answer about #48213 only;
   Priya's refund ask → pre-filled card in #refunds.
