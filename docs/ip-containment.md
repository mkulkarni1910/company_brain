# SubstrateOS — Your IP, Contained

> **Status: design rationale.** This captures a critical design decision for the
> platform — *capture without leakage*. Some controls are shipped; the headline
> one (no-train enterprise model posture) is **design intent, not yet baked in**.
> Each control below carries an honest status. See [Status today](#status-today).

Preserving company know-how in the AI era. Internal reference for why
containment is load-bearing — not a feature, the precondition for the whole
thesis.

---

## 1. The paradox (start here)

In the human-capital era, the **tacitness of your know-how *was* the moat.** A
competitor couldn't copy how your best people work because nobody could fully
articulate it — it lived in heads, dispersed across hundreds of people, sticky
and embodied. Leakage happened, but through *attrition*: one person leaves, takes
a fragment, and even that fragment degrades because it was never the whole
picture. Slow, partial, self-limiting.

The AI era inverts this. To make know-how **runnable**, you have to make the
implicit explicit — write it into Skills, steps, rules, context. The moment it's
legible to a machine, it's legible to anyone: concentrated in one library, fully
articulated, copyable in a single action. **The act that creates the value
destroys the natural protection** — and the downside is *amplified*, because a
competitor who gets your codified playbook gets the finished article, not a hazy
sense of how you operate.

So the real question isn't "can we capture our know-how?" It's: **can we capture
it without it escaping into a model or vendor we don't control?** Capture without
containment just hands someone your advantage in a cleaner format than they could
ever have assembled themselves.

**The trade:** you give up *moat-by-tacitness* and rebuild it as
*moat-by-containment* — which only holds if the containment is **real and
architectural, not a checkbox.**

## 2. The threat model has changed

| | Human-capital era | AI / agentic era |
|---|---|---|
| **Where IP lives** | In heads | Codified in systems, prompts, models |
| **Nature** | Tacit, dispersed, sticky, non-fungible | Explicit, concentrated, fungible, copyable |
| **Leakage mode** | By attrition — slow, partial | By extraction / copy — fast, potentially total |
| **Protected by** | Retention, NDAs, culture | Tenant isolation, grounding-not-training, identity + audit |

## 3. New leakage vectors (that didn't exist when it was in heads)

- **Vendor absorption** *(the big one)* — know-how sent to a third-party model
  that logs / retains / trains becomes part of *someone else's* model and can
  surface for competitors. You taught the vendor's model how your company works.
- **Concentration / wholesale copy** — codified IP is one library, not 10,000
  heads. A single misconfigured share, rogue export, or breach copies the *entire
  operational brain* at once.
- **Model memorization / cross-tenant bleed** — knowledge baked into a shared or
  fine-tuned model can be extracted by prompting or leak across tenants.
- **Inference by aggregation** — even without raw data, an external agent that
  observes enough of your runs can *infer* your playbook from patterns, outputs,
  and telemetry.
- **Exhaust leakage** — agent traces, logs, and observability data shipped to
  external tools quietly carry process IP out the side door.

## 4. How SubstrateOS contains it — the four controls

1. **Stays in your tenant.** The Skills/library live in *your* Azure/Entra
   boundary — your Cosmos DB, your storage — not a vendor cloud or a shared model.
2. **Grounding, not training** *(the technical crux)*. Know-how is *retrieved as
   context at inference time*, never baked into model weights that leave your
   control. The model is a reasoning engine over your grounded data; the vendor
   never absorbs your playbooks. **What makes this real** is an enterprise model
   deployment with **no-train + data-residency** guarantees — the difference
   between "using a model" and "feeding your IP into one."
3. **Identity-scoped access (Entra + ACLs).** Even internally, the know-how isn't
   a free-for-all; who and what can read each Skill is governed at query time,
   limiting lateral movement.
4. **Append-only audit.** Every access and run is logged, so leakage becomes
   *detectable* instead of invisible. You can't audit who accessed an expert's
   intuition; you *can* audit who touched a Skill.

> **The governance frame does double duty.** The guardrails / identity / audit
> band isn't only "safe to **run**" (don't take the wrong action) — it's equally
> "safe to **hold**" (don't let the know-how escape). Same frame, two jobs — and
> the second is the one a CISO actually loses sleep over.

## 5. Status today

Honest mapping of each control to what's actually implemented, so this reads as
an engineering reference and not a brochure.

| Control | Status | Where it stands |
|---|---|---|
| **Stays in your tenant** | 🟡 Partial | Library & connector data persist in **Cosmos DB** inside the Azure/Entra tenant (`app/connectors/cosmos_store.py`). But model **inference egresses to Google's Gemini API** (see next row), and run/audit state is currently Redis-backed. "Fully in-tenant" requires closing both. |
| **Grounding, not training** | 🔴 Design intent | The *grounding* half is real: it's RAG — know-how is injected as context, nothing is fine-tuned into weights. The *vendor-containment* half is **not yet true**: answers run on **Gemini 2.5 Pro via the Google AI Studio API** (`app/generation/gemini.py`), a third-party endpoint whose data terms are **not** an enterprise no-train + data-residency deployment. **This is the headline gap.** Target: Azure OpenAI (or Vertex enterprise) with contractual no-train + residency. The deck's "Gemini today, Azure OpenAI–ready" *is* this gap. |
| **Identity-scoped access (Entra + ACLs)** | 🟢 Shipped | Entra SSO on every surface; query-time ACL filter (`app/acl/enforcement.py` → `build_acl_filter`, applied in `app/retrieval/`); Skill Studio gated to an Entra SME group, fail-closed. |
| **Append-only audit** | 🟢 Shipped *(hardening pending)* | Immutable, identity-stamped event log per run (`app/audit/log.py` — "Events are immutable"), surfaced as the governance receipt `GET /runs/{id}`. Hardening target: tamper-evident / WORM-grade store with retention, rather than Redis with in-process degradation. |

## 6. Honest tensions (don't get caught flat-footed)

- **Usability vs. secrecy.** Legibility to machines *is* copyability. You can't
  make know-how maximally runnable and maximally secret at once; you push the
  frontier — maximum usefulness with maximum containment — but the honest claim
  is **"contained, governed, auditable, never vendor-absorbed," not
  "unleakable."** A determined insider with access can always exfiltrate; no
  architecture fixes that.
- **You're creating a high-value target.** Concentrating the IP into one library
  is itself a risk — which is precisely why first-class security (encryption,
  isolation, audit) and Microsoft's enterprise substrate are **load-bearing, not
  nice-to-have.**
- **The vendor guarantee is a dependency, not magic.** "Grounding not training"
  is only as strong as the deployment's data terms. Name the Azure/no-train
  posture explicitly; don't wave at it. (Today that posture is *aspirational* —
  see [Status](#status-today).)

## 7. Why it matters

1. **Kills the security objection** that quietly ends most enterprise AI deals —
   the CISO's "where does our data and our process go?" Containment is the real,
   architectural answer.
2. **It's the moat argument** for the CEO/board — and the precondition for
   everything else. Compounding only makes the library a moat *if it stays
   contained*; a library that leaks stops appreciating the moment a competitor has
   a copy. Containment is what makes "the library is the moat" actually **true**.

## 8. One-liners

> "In the old world your know-how walked out one person at a time. In the AI world
> it can be copied all at once. SubstrateOS makes your know-how runnable without
> making it leakable."

> "The moment you write your know-how down for AI, it becomes copyable — so we
> capture it to stay yours: in your tenant, used as grounding not training, never
> absorbed by a model or a vendor."

---

*Related: the governance receipt and approval/audit machinery
([`app/audit/`](../substrateos-api/app/audit/), [`app/approvals/`](../substrateos-api/app/approvals/),
[`app/policy/`](../substrateos-api/app/policy/)) are the "safe to run" half of the
same frame this doc reads as "safe to hold."*
