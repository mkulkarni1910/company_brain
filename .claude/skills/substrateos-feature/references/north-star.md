# SubstrateOS — Product North Star (the pitch)

Source: `~/Desktop/MicrosoftHackthon/SubstrateOS - Company Brain - Master Deck.pdf`
(Mangesh Kulkarni · Lokesh Bhoyar). This is the product we're building toward —
align every feature to it, and keep `mockups/architecture.html` consistent with it.

## One line
**"Building AI agents is becoming easy. Trusting them with real work is the hard
part."** SubstrateOS — *The Company Brain* — turns the know-how in people's heads
into **playbooks (skills) the AI runs** — fast to build, and safe: your rules, a
person approving anything risky, every action logged.

## The problem
How a company actually gets work done (refund handled, discount approved, outage
fixed) isn't written down — it lives in people's heads and across a dozen tools
(Slack, Salesforce, tickets, email). There are no guardrails, so no one dares let
AI act. AI can't do work it can't see, can't follow, and can't be trusted to do.

## What makes us different — climb the ladder
Most AI stops at **Find → Plan**. SubstrateOS climbs to **Do → Stay in control**:

1. **Find** — look up the answer
2. **Plan** — work out the steps
3. **Do** — take the actual action ← *today's tools stop before here*
4. **Stay in control** — approve risky moves · log everything ← *our edge*

"Doing the work is where the value is. Keeping it safe is what lets you allow it."

## The playbook shape (one shape for every task)
**When** (something happens) → **Check** (the rules) → **Stop** (if risky, a human
signs off) → **Do it** (take the action) → **Record** (log everything).
Write it once — the AI runs it. (Refund / Discount / Outage all share this shape.)

## The engine (under the hood)
One engine runs every playbook, step by step:
**Who's asking → Gather facts → Check rules → Ask a human (if risky) → Do it → Log it.**
It always knows who's asking. What it knows: customers, orders, people, history —
pulled into one place. What it can do: ask a person to approve · act in your tools
· log every step.

## Surfaces — where it starts (it always knows who's asking)
The engine is reachable from every surface, each carrying identity:
**Slack · Teams · Web app · Other AI tools · API.** New surfaces should plug into
the same engine + identity, never fork the logic.

## Built on Microsoft (nothing new for IT to vet)
| Job | Microsoft service |
|-----|-------------------|
| Who can do what | Microsoft Entra ID |
| Find the right info | Azure AI Search |
| Remember people & history | Cosmos DB |
| Live company data | Microsoft Graph |
| Runs & scales | Azure Container Apps |
| Watches everything | Azure Monitor |

Plugs into **Microsoft Copilot Studio & Foundry**. AI model: **Gemini today,
Azure OpenAI–ready.**

## Why safe (Microsoft's own controls)
- **Known identity** — everyone, and every AI, signs in through Microsoft.
- **Human approval** — anything risky goes to a person in Teams first.
- **A full record** — every action logged: who, what, which rule, when.
- **Stops if unsure** — missing info or a broken rule → it stops, never guesses.

## Governance at scale
One small platform team sets the rails (the engine, the rules everyone follows,
the catalog of playbooks, the links to tools). Each team **owns** its playbooks
with one accountable name. The **Admin Console** lists every playbook · owner ·
status (live/draft), approvals waiting, full history, and a kill switch.
**Built & kept current:** AI drafts → owner reviews → a human approves → goes live
(versioned like code, roll back anytime) → watched & updated. Risk sets the bar —
risky playbooks need tighter approval; routine ones stay light and fast.

## Vision
**Start** (one safe playbook, live) → **Grow** (many playbooks, one place) →
**Company brain** (the system every AI-driven company runs on). "Every company
will need a company brain. We're building the one that works across all your tools
— and stays safe with Microsoft."

## How this maps to the repo (pitch → code)
- Playbooks / skills → `app/skills/` (SkillStore, SkillRouter) + `app/workflows/`
  (RefundEngine, RefundFlow, RunStore) — the When→Check→Stop→Do→Record engine.
- "Gather facts" → retrieval/people/activity pillars + live_fetch (Graph).
- "Ask a human" → approval step (Teams) in the workflow engine.
- "Log it" → RunStore + metrics + OpenTelemetry audit trail.
- Surfaces → `app/bots/` (Slack/Teams), `app/mcp/` (other AI tools), `app/api/context`
  + PAT tokens (API), and the Next.js web app (Web).
- Admin Console → Admin Panel `web/app/admin/` (skills, sources, surfaces, permissions).
