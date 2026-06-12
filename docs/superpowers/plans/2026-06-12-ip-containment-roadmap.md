# IP Containment — Roadmap

> **Status: roadmap.** This is a forward-looking plan, not a task-level
> implementation plan. It captures intent, candidate direction, and sequencing
> for the IP-containment controls that are **not yet built**. Items marked
> **🔍 Deep dive needed** are not yet figured out and require their own design
> pass before implementation.
>
> Rationale and threat model: [`../../ip-containment.md`](../../ip-containment.md).

**Goal:** make "capture without leakage" real and architectural. Two of the four
containment controls are already live — identity-scoped access (Entra + ACLs) and
append-only audit. This roadmap covers closing the remaining three so the platform
can honestly claim *"contained, governed, auditable, never vendor-absorbed."*

**Current status (for context):**

| Control | Today | This roadmap |
|---|---|---|
| Identity-scoped access (Entra + ACLs) | 🟢 Shipped (`app/acl/`) | — |
| Append-only audit | 🟢 Shipped (`app/audit/`) | Workstream 3 (harden) |
| Stays in your tenant | 🟡 Partial | Workstream 2 |
| Grounding, not training (no vendor absorption) | 🔴 Not started | Workstream 1 *(load-bearing)* |

---

## Workstream 1 — Grounding, not training (the crux)

**Why it's load-bearing:** this is the control the whole "your IP stays yours"
claim rests on. Until it's closed, the "no vendor absorbs your playbooks" line is
aspirational. It gates the CISO security story and the moat argument.

**Where we are:** answer generation runs on **Gemini 2.5 Pro via the Google AI
Studio API** (`app/generation/gemini.py`). The grounding *pattern* is already
correct — know-how is retrieved as context at inference time, nothing is
fine-tuned into weights — but the *vendor posture* is not: AI Studio's data terms
are not an enterprise no-train + data-residency deployment.

**Direction:** move generation to an enterprise model deployment with
contractual **no-train + data-residency** guarantees — the deck's
"Gemini today, Azure OpenAI–ready." Azure OpenAI is the leading candidate
(consistent with the Microsoft substrate); Vertex enterprise is a fallback.

**🔍 Deep dive needed:**
- Confirm the exact no-train / data-residency contractual terms for the target
  deployment — name them explicitly; don't wave at them.
- Model parity: validate answer quality + plan-step classification against the
  current Gemini baseline before switching (we have eval harness — `eval/run_eval.py`).
- Abstraction: today there's a `GeminiClient`; decide whether to generalize the
  generation client behind an interface so the model is swappable per tenant.
- Cost / latency / quota implications of the target deployment.

**Done when:** generation runs on a deployment whose data terms are named and
no-train, with eval parity to the current baseline.

---

## Workstream 2 — Fully tenant-resident state

**Where we are:** the library and connector data persist in **Cosmos DB** inside
the Azure/Entra tenant (`app/connectors/cosmos_store.py`), but run/audit state is
currently Redis-backed, and model inference egresses to a third-party API (see
Workstream 1).

**Direction:** keep the library, run state, and audit log inside the
Azure/Entra boundary end to end, so there's no path by which the operational
brain leaves the tenant.

**🔍 Deep dive needed:**
- Inventory every egress point (model calls, telemetry/exhaust, any external
  store) and classify each as in-tenant / acceptable / must-close.
- Decide the durable home for run + audit state (stays Redis-in-tenant vs. moves
  to Cosmos) — see Workstream 3, this overlaps.
- Exhaust-leakage review: where do traces/logs/observability go (Azure Monitor
  is in-tenant; confirm nothing ships process IP to external tools).

**Done when:** a documented egress map shows the operational brain (library, runs,
audit, inference) stays within the tenant boundary.

---

## Workstream 3 — Tamper-evident audit

**Where we are:** the AuditLog is append-only and identity-stamped, with immutable
events per run (`app/audit/log.py`), surfaced as the governance receipt
(`GET /runs/{id}`). It's append-only *by discipline* on a Redis-backed store with
in-process degradation — not a tamper-evident / WORM-grade store.

**Direction:** harden the audit log toward a tamper-evident store with retention,
so "leakage becomes detectable" holds up to a CISO's scrutiny, not just ours.

**🔍 Deep dive needed:**
- Choose the durable, append-only-enforced backing store + retention policy.
- Decide whether tamper-evidence is hash-chaining / signing vs. a managed WORM
  store, and what threat it must withstand (insider edit, store compromise).

**Done when:** the audit trail is on a store that enforces immutability +
retention, with a stated tamper-evidence mechanism.

---

## Sequencing & dependencies

1. **Workstream 1 first** — it's load-bearing and unblocks the headline claim.
   The model-client abstraction it introduces is reusable.
2. **Workstream 2** overlaps with 1 (closing inference egress is part of both) and
   with 3 (run/audit state home).
3. **Workstream 3** can proceed in parallel; it's the most self-contained.

**Cross-cutting honest tension** (from the rationale doc): you can't make know-how
maximally runnable *and* maximally secret. The target is "maximum usefulness with
maximum containment," not "unleakable." This roadmap pushes the frontier; it does
not claim to eliminate insider exfiltration.

**Out of scope here:** per-tenant model isolation at scale, and the broader infra
hardening (APIM, OTel, Event Hubs, per-tenant index isolation) tracked separately
under *Infra (needs Entra)* in the README.
