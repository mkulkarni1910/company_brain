"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { getMe, initials, Me } from "@/lib/api";
import type { SkillCreate } from "@/lib/skillsApi";
import {
  draftSkill, submitSkill, resubmitSkill, withdrawSubmission, getMySubmissions,
  Submission,
} from "@/lib/studioApi";
import "./studio.css";

/* ── icons (ported from the mockup) ── */
const SparkIcon = (
  <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 3v4M3 5h4M13 3l2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5L13 3z" />
  </svg>
);
const ChecklistIcon = (
  <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
  </svg>
);
const DraftBtnIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 3v4M3 5h4M6 17v4M4 19h4M13 3l2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5L13 3z" />
  </svg>
);
const ArrowRight = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12h13M13 6l6 6-6 6" />
  </svg>
);
const ArrowRightBold = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12h13M13 6l6 6-6 6" />
  </svg>
);
const SparkSmall = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 3v4M3 5h4M13 3l2.5 6.5L22 12l-6.5 2.5L13 21l-2.5-6.5L4 12l6.5-2.5L13 3z" />
  </svg>
);
const CheckIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5 12l5 5L20 6" />
  </svg>
);
const LockIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="4" y="11" width="16" height="9" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </svg>
);
const ShieldIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3 5 6v5c0 4 3 6.7 7 8 4-1.3 7-4 7-8V6l-7-3Z" />
  </svg>
);
const BackIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M19 12H5" /><path d="m12 19-7-7 7-7" />
  </svg>
);

/* ── form state (mirrors SkillCreate; AI-drafted, fully editable) ── */
type FormState = {
  name: string; slug: string; description: string; team: string;
  steps: string[]; data_feeds: string[]; system_prompt: string;
};

const EMPTY_FORM: FormState = {
  name: "", slug: "", description: "", team: "Finance",
  steps: [], data_feeds: [], system_prompt: "",
};

function toSlug(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
function sanitizeSlug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9-]+/g, "-");
}

function draftToForm(d: SkillCreate): FormState {
  return {
    name: d.name ?? "", slug: d.slug ?? "", description: d.description ?? "",
    team: d.team ?? "Finance", steps: d.steps ?? [], data_feeds: d.data_feeds ?? [],
    system_prompt: d.system_prompt ?? "",
  };
}
function formToSkill(f: FormState): SkillCreate {
  return {
    name: f.name, slug: f.slug, description: f.description, team: f.team,
    steps: f.steps, data_feeds: f.data_feeds, system_prompt: f.system_prompt,
  };
}

/* ── access-restricted screen (mirrors the admin denied screen) ── */
function AccessRestricted({ me }: { me: Me | null }) {
  return (
    <div className="st-app">
      <div className="st-denied">
        <div className="st-denied-card">
          <div className="st-glyph" />
          <h2>Access restricted</h2>
          <p className="why">The Skill Studio is limited to members of the{" "}
            <b>Finance&nbsp;SME</b> group in Microsoft&nbsp;Entra&nbsp;ID.</p>
          <span className="st-denied-group">{ShieldIcon}Entra ID · Finance SME group</span>
          {me && (
            <div className="st-denied-id">
              <div className="st-avatar">{initials(me.display_name)}</div>
              <div className="nm">{me.display_name}<span>{me.email}</span></div>
            </div>
          )}
          <p className="st-denied-hint">You&apos;re signed in, but your account isn&apos;t in the group.
            Ask your administrator to add you, then reload this page.</p>
          <Link className="st-denied-back" href="/">{BackIcon}Back to chat</Link>
        </div>
      </div>
    </div>
  );
}

export default function StudioPage() {
  const [loaded, setLoaded] = useState(false);
  const [me, setMe] = useState<Me | null>(null);

  const [view, setView] = useState<"author" | "subs">("author");

  // author state
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<FormState | null>(null);
  const [justDrafted, setJustDrafted] = useState(false); // shows the ribbon + glow
  const [drafting, setDrafting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingRunId, setEditingRunId] = useState<string | null>(null);

  // submissions
  const [subs, setSubs] = useState<Submission[]>([]);
  const [subsErr, setSubsErr] = useState(false);

  // withdraw/remove confirm modal
  const [confirmWithdraw, setConfirmWithdraw] = useState<{ runId: string; name: string; verb: "Withdraw" | "Remove" } | null>(null);

  const loadSubs = () =>
    getMySubmissions()
      .then((data) => { setSubs(data); setSubsErr(false); })
      .catch(() => { setSubsErr(true); });

  useEffect(() => {
    getMe().then((m) => { setMe(m); setLoaded(true); });
  }, []);

  useEffect(() => {
    if (loaded && me && (me.is_sme || me.is_admin)) loadSubs();
  }, [loaded, me]);

  if (!loaded) return null;
  if (!me || (!me.is_sme && !me.is_admin)) return <AccessRestricted me={me} />;

  /* ── author actions ── */
  const handleDraft = async () => {
    if (!text.trim() || drafting) return;
    setDrafting(true); setError(null);
    try {
      const d = await draftSkill(text);
      setDraft(draftToForm(d));
      setJustDrafted(true);
    } catch (e) {
      // Manual fallback: empty editable form so the SME can still author by hand.
      setError(`Couldn't draft automatically (${(e as Error).message}). Fill the form in manually below.`);
      setDraft(EMPTY_FORM);
      setJustDrafted(false);
    } finally {
      setDrafting(false);
    }
  };

  const setField = (k: keyof FormState, v: unknown) =>
    setDraft((p) => (p ? { ...p, [k]: v } : p));

  const handleNameChange = (name: string) =>
    setDraft((p) => (p ? { ...p, name, slug: p.slug || toSlug(name) } : p));

  const startOver = () => {
    setText(""); setDraft(null); setJustDrafted(false);
    setError(null); setEditingRunId(null);
  };

  const handleSubmit = async () => {
    if (!draft || submitting) return;
    setSubmitting(true); setError(null);
    try {
      const skill = formToSkill(draft);
      if (editingRunId) await resubmitSkill(editingRunId, skill, text);
      else await submitSkill(skill, text);
      startOver();
      setView("subs");
      await loadSubs();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleEdit = (s: Submission) => {
    setText(s.source_text ?? "");
    setDraft(s.skill ? draftToForm(s.skill) : EMPTY_FORM);
    setJustDrafted(false);
    setEditingRunId(s.run_id);
    setError(null);
    setView("author");
  };

  const handleRemove = (s: Submission, verb: "Withdraw" | "Remove") => {
    setConfirmWithdraw({ runId: s.run_id, name: s.name, verb });
  };

  const handleConfirmWithdraw = async () => {
    if (!confirmWithdraw) return;
    const { runId } = confirmWithdraw;
    setConfirmWithdraw(null);
    try { await withdrawSubmission(runId); await loadSubs(); }
    catch (e) { alert((e as Error).message); }
  };

  /* ── steps + feeds list editors ── */
  const setStep = (i: number, v: string) =>
    setDraft((p) => (p ? { ...p, steps: p.steps.map((s, j) => (j === i ? v : s)) } : p));
  const addStep = () => setDraft((p) => (p ? { ...p, steps: [...p.steps, ""] } : p));
  const removeStep = (i: number) =>
    setDraft((p) => (p ? { ...p, steps: p.steps.filter((_, j) => j !== i) } : p));

  const setFeed = (i: number, v: string) =>
    setDraft((p) => (p ? { ...p, data_feeds: p.data_feeds.map((f, j) => (j === i ? v : f)) } : p));
  const addFeed = () => setDraft((p) => (p ? { ...p, data_feeds: [...p.data_feeds, ""] } : p));
  const removeFeed = (i: number) =>
    setDraft((p) => (p ? { ...p, data_feeds: p.data_feeds.filter((_, j) => j !== i) } : p));

  const submitLabel = editingRunId ? "Resubmit for approval" : "Submit for approval";

  return (
    <div className="st-app">
      {/* ── left rail ── */}
      <aside className="st-rail">
        <div className="st-brand">
          <div className="st-glyph" />
          <div><h1>Substrate<span>OS</span></h1><div className="st-sub">Skill Studio</div></div>
        </div>
        <div>
          <h2>Skill Studio</h2>
          <nav className="st-nav">
            <button type="button" className={view === "author" ? "active" : ""}
              aria-current={view === "author" ? "page" : undefined}
              onClick={() => setView("author")}>
              {SparkIcon}Author
            </button>
            <button type="button" className={view === "subs" ? "active" : ""}
              aria-current={view === "subs" ? "page" : undefined}
              onClick={() => setView("subs")}>
              {ChecklistIcon}My submissions
              {subs.length > 0 && <span className="st-ncount">{subs.length}</span>}
            </button>
          </nav>
        </div>
        <div className="st-foot">
          <div className="st-avatar">{initials(me.display_name)}</div>
          <div className="st-who">{me.display_name}<span>{me.title ?? "Finance SME"}</span></div>
        </div>
      </aside>

      {/* ── main ── */}
      <main className="st-main"><div className="st-wrap">
        <div className="st-pg-head">
          <div className="st-eyebrow">Finance · author a skill</div>
          <h2>Skill Studio</h2>
          <p>Turn what you know into a governed skill — no code.</p>
        </div>

        {view === "author" ? (
          <div className="st-panel">
            {/* progress rail */}
            <div className="st-steps-rail">
              <div className="st-s on"><span className="st-n">1</span>Describe</div>
              <span className="st-ar" />
              <div className={`st-s${draft ? " on" : ""}`}><span className="st-n">2</span>Review draft</div>
              <span className="st-ar" />
              <div className="st-s"><span className="st-n">3</span>Admin approves</div>
            </div>

            <div className="st-author-grid">
              {/* pane 1 — describe */}
              <section className="st-card st-pane-describe">
                <div className="st-card-head">
                  <div className="st-card-num">1</div>
                  <div className="st-ct">
                    <h3>Describe it</h3>
                    <p>Write the rule the way you&apos;d explain it to a new teammate.</p>
                  </div>
                </div>
                <textarea className="st-describe" value={text} onChange={(e) => setText(e.target.value)}
                  placeholder="e.g. Refunds under $500 and 30 days are auto-approved. Anything bigger needs my sign-off…" />
                <div className="st-describe-foot">
                  <button className="st-btn-primary" onClick={handleDraft} disabled={drafting || !text.trim()}>
                    {DraftBtnIcon}{drafting ? "Drafting…" : "Draft with AI"}
                  </button>
                </div>
                <span className="st-drafts-affordance">
                  {SparkSmall}AI drafts the form on the right{ArrowRight}
                </span>
                <p className="st-hint" style={{ marginTop: 12 }}>
                  Drafted in plain English — you review everything before it&apos;s submitted.
                </p>
              </section>

              {/* connector */}
              <div className="st-connector" aria-hidden="true">
                <div className="st-line-top" />
                <div className="st-arrow">{ArrowRightBold}</div>
                <div className="st-line-bot" />
              </div>

              {/* pane 2 — review */}
              <section className={`st-card st-pane-review${justDrafted ? " drafted" : ""}`}>
                <div className="st-card-head">
                  <div className="st-card-num">2</div>
                  <div className="st-ct">
                    <h3>Review the draft</h3>
                    <p>The AI filled this in from your description. Edit anything that&apos;s not quite right.</p>
                  </div>
                </div>

                {justDrafted && (
                  <div className="st-draft-ribbon">
                    <span className="st-ai-badge" style={{ flex: "none", padding: "4px 10px" }}>
                      {SparkSmall}Just drafted
                    </span>
                    <span className="st-txt">Generated from your description on the left.{" "}
                      <b>Nothing is live yet</b> — review, edit, then submit.</span>
                  </div>
                )}

                {error && <div className="st-error">{error}</div>}

                {draft ? (
                  <>
                    <div className="st-grid2">
                      <div className="st-field">
                        <label>Name</label>
                        <input value={draft.name} onChange={(e) => handleNameChange(e.target.value)} />
                      </div>
                      <div className="st-field">
                        <label>Slug</label>
                        <input className="mono" value={draft.slug}
                          onChange={(e) => setField("slug", sanitizeSlug(e.target.value))} />
                      </div>
                    </div>

                    <div className="st-field">
                      <label>Description</label>
                      <textarea rows={2} value={draft.description}
                        onChange={(e) => setField("description", e.target.value)} />
                    </div>

                    <div className="st-field">
                      <label>Team</label>
                      <input value={draft.team} onChange={(e) => setField("team", e.target.value)} />
                    </div>

                    <div className="st-field">
                      <label>Steps — When → Check → Stop → Do → Record</label>
                      {draft.steps.map((s, i) => (
                        <div className="st-step-row" key={i}>
                          <span className="st-step-drag">⠿</span>
                          <input value={s} onChange={(e) => setStep(i, e.target.value)} />
                          <button className="st-step-remove" type="button"
                            onClick={() => removeStep(i)} aria-label="Remove step">✕</button>
                        </div>
                      ))}
                      <button className="st-list-add" type="button" onClick={addStep}>
                        <span className="plus">+</span>Add step
                      </button>
                    </div>

                    <div className="st-field">
                      <label>Data feeds</label>
                      <div className="st-feeds">
                        {draft.data_feeds.map((feed, i) => (
                          <span className="st-feed-chip" key={i}>
                            <span className="dot" />
                            <input
                              className="st-feed-input"
                              value={feed}
                              onChange={(e) => setFeed(i, e.target.value)}
                              aria-label={`Data feed ${i + 1}`}
                            />
                            <button className="x" type="button" onClick={() => removeFeed(i)}
                              aria-label="Remove feed">×</button>
                          </span>
                        ))}
                        <button className="st-feed-add" type="button" onClick={addFeed}>
                          + Add feed
                        </button>
                      </div>
                    </div>

                    <div className="st-field">
                      <label>System prompt</label>
                      <textarea className="sysprompt" rows={6} value={draft.system_prompt}
                        onChange={(e) => setField("system_prompt", e.target.value)} />
                    </div>

                    <div className="st-review-foot">
                      <button className="st-btn-ghost" onClick={startOver} disabled={submitting}>Start over</button>
                      <button className="st-btn-primary" onClick={handleSubmit} disabled={submitting}>
                        {CheckIcon}{submitting ? "Submitting…" : submitLabel}
                      </button>
                      <span className="st-gate">{LockIcon}An admin reviews before this goes live.</span>
                    </div>
                  </>
                ) : (
                  <p className="st-hint">Write your rule on the left, then{" "}
                    <b>Draft with AI</b> — the form appears here for you to review.</p>
                )}
              </section>
            </div>
          </div>
        ) : (
          /* ── My submissions ── */
          <div className="st-panel">
            <section className="st-card">
              <div className="st-card-head">
                <div className="st-card-num">3</div>
                <div className="st-ct">
                  <h3>My submissions</h3>
                  <p>Skills you&apos;ve sent for approval, and where each one stands.</p>
                </div>
              </div>

              {subsErr ? (
                <div className="st-empty st-subs-err">
                  Couldn&apos;t load your submissions — check the API and refresh.
                </div>
              ) : subs.length === 0 ? (
                <div className="st-empty">
                  No submissions yet — head to <b>Author</b> to draft your first skill.
                </div>
              ) : (
                <>
                  <div className="st-sub-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th style={{ width: 230 }}>Skill</th>
                          <th style={{ width: 160 }}>Status</th>
                          <th style={{ width: 100 }}>Submitted</th>
                          <th>Notes</th>
                          <th style={{ width: 172 }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {subs.map((s) => (
                          <SubmissionRow key={s.run_id} s={s}
                            onEdit={() => handleEdit(s)} onRemove={handleRemove} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="st-sub-footnote"><span className="gl">↩</span> Editing reopens the
                    authoring panes (Cards 1–2) pre-filled — the submit button becomes{" "}
                    <b>Resubmit for approval</b>.</p>
                </>
              )}
            </section>
          </div>
        )}
      </div></main>

      {/* ── Withdraw / Remove confirm modal ── */}
      {confirmWithdraw && (
        <div className="st-modal-backdrop" onClick={() => setConfirmWithdraw(null)}>
          <div className="st-modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>{confirmWithdraw.verb} submission?</h3>
            <p>It stays in the audit log, visible to admins in Runs.</p>
            <div className="st-modal-foot">
              <button className="st-btn-ghost" onClick={() => setConfirmWithdraw(null)}>Cancel</button>
              <button className="st-btn-danger" onClick={handleConfirmWithdraw}>
                {confirmWithdraw.verb}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// The backend run statuses (skill_publish flow) → the three user-facing states the
// mockup designs for. "pending_approval" is the only editable/withdrawable state.
type SubState = "pending" | "live" | "rejected";
function subState(status: string): SubState {
  if (status === "approved" || status === "completed" || status === "live") return "live";
  if (status === "rejected") return "rejected";
  return "pending"; // pending_approval (and any in-flight state)
}

function SubmissionRow({ s, onEdit, onRemove }: {
  s: Submission; onEdit: () => void; onRemove: (s: Submission, verb: "Withdraw" | "Remove") => void;
}) {
  const when = new Date(s.created_at).toLocaleDateString();
  const state = subState(s.status);
  return (
    <tr>
      <td>
        <span className="st-sub-name">{s.name}</span>
        <span className="st-sub-slug">{s.slug}</span>
      </td>
      <td>
        {state === "pending" && (
          <span className="st-badge st-b-pending"><span className="d" />Pending approval</span>
        )}
        {state === "live" && (
          <span className="st-badge st-b-live"><span className="d" />Live</span>
        )}
        {state === "rejected" && (
          <span className="st-badge st-b-rejected"><span className="d" />Rejected</span>
        )}
      </td>
      <td><span className="st-sub-when">{when}</span></td>
      <td>
        {state === "rejected" ? (
          <div className="st-reject-note">
            <span className="lbl">Admin</span>
            <div>{s.rejection_note ?? "Resubmit with the requested changes."}</div>
          </div>
        ) : state === "live" ? (
          <span className="st-note-muted">Approved · running org-wide.</span>
        ) : (
          <span className="st-note-muted">Waiting on an admin in the approval queue.</span>
        )}
      </td>
      <td>
        {state === "pending" && (
          <>
            <div className="st-sub-actions">
              <button className="st-sk-btn" onClick={onEdit}
                title="Reopens Cards 1–2 pre-filled — the submit button becomes Resubmit for approval.">Edit</button>
              <button className="st-sk-btn del" onClick={() => onRemove(s, "Withdraw")}>Withdraw</button>
            </div>
            <span className="st-act-caption">Withdraw cancels the request — kept in the audit log,
              visible to admins in Runs.</span>
          </>
        )}
        {state === "rejected" && (
          <>
            <div className="st-sub-actions">
              <button className="st-sk-btn primary" onClick={onEdit}
                title="Reopens Cards 1–2 pre-filled — the submit button becomes Resubmit for approval.">Edit &amp; resubmit</button>
              <button className="st-sk-btn del" onClick={() => onRemove(s, "Remove")}>Remove</button>
            </div>
            <span className="st-act-caption">Remove dismisses it — kept in the audit log,
              visible to admins in Runs.</span>
          </>
        )}
        {state === "live" && (
          <span className="st-managed-hint">{LockIcon}Managed in Org Skills</span>
        )}
      </td>
    </tr>
  );
}
