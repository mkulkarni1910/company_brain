"use client";
import { useEffect, useRef, useState } from "react";
import {
  adminListSkills, adminCreateSkill, adminUpdateSkill, adminDeleteSkill,
  SkillFull, SkillCreate,
} from "@/lib/skillsApi";
import {
  getSkillSubmissions, decideSkillSubmission, SkillSubmission,
} from "@/lib/adminApi";

type FormState = {
  slug: string; name: string; description: string; team: string;
  run_scope: "org" | "team"; enabled: boolean;
  steps: string[]; data_feeds: string[]; system_prompt: string;
};

const EMPTY_FORM: FormState = {
  slug: "", name: "", description: "", team: "", run_scope: "org",
  enabled: true, steps: [], data_feeds: [], system_prompt: "",
};

function toSlug(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

// Slugs are lowercase with no spaces — sanitize as the user types (keep trailing
// hyphens so they can keep typing), without the leading/trailing strip toSlug does.
function sanitizeSlug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9-]+/g, "-");
}

function StringList({ label, addLabel, items, onChange }: { label: string; addLabel: string; items: string[]; onChange: (v: string[]) => void }) {
  const set = (i: number, v: string) => { const a = [...items]; a[i] = v; onChange(a); };
  const add = () => onChange([...items, ""]);
  const remove = (i: number) => onChange(items.filter((_, j) => j !== i));
  return (
    <div className="skill-form-row">
      <label className="skill-form-label">{label}</label>
      {items.map((v, i) => (
        <div key={i} className="skill-list-row">
          <input value={v} onChange={(e) => set(i, e.target.value)} placeholder="Enter value…" />
          <button className="skill-list-remove" onClick={() => remove(i)} type="button">✕</button>
        </div>
      ))}
      <button className="skill-list-add" onClick={add} type="button"><span className="plus">+</span>{addLabel}</button>
    </div>
  );
}

function SkillForm({ initial, onSave, onClose, saving }: {
  initial: FormState; onSave: (f: FormState) => void; onClose: () => void; saving: boolean;
}) {
  const [f, setF] = useState<FormState>(initial);
  const set = (k: keyof FormState, v: unknown) => setF((p) => ({ ...p, [k]: v }));
  const handleNameChange = (name: string) => {
    setF((p) => ({ ...p, name, slug: p.slug || toSlug(name) }));
  };
  return (
    <div className="skill-form-modal" onClick={onClose}>
      <div className="skill-form-card" onClick={(e) => e.stopPropagation()}>
        <h3>{initial.slug ? "Edit Skill" : "Add Skill"}</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div className="skill-form-row">
            <label className="skill-form-label">Name</label>
            <input className="skill-form-input" value={f.name} onChange={(e) => handleNameChange(e.target.value)} placeholder="SEO Research" />
          </div>
          <div className="skill-form-row">
            <label className="skill-form-label">Slug</label>
            <input className="skill-form-input" value={f.slug} onChange={(e) => set("slug", sanitizeSlug(e.target.value))} placeholder="seo-research" style={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
          </div>
        </div>
        <div className="skill-form-row">
          <label className="skill-form-label">Description</label>
          <textarea className="skill-form-textarea" rows={2} value={f.description} onChange={(e) => set("description", e.target.value)} placeholder="What this skill does in 1–2 sentences." />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
          <div className="skill-form-row">
            <label className="skill-form-label">Team</label>
            <input className="skill-form-input" value={f.team} onChange={(e) => set("team", e.target.value)} placeholder="Engineering" />
          </div>
          <div className="skill-form-row">
            <label className="skill-form-label">Scope</label>
            <select className="skill-form-input" value={f.run_scope} onChange={(e) => set("run_scope", e.target.value as "org" | "team")}>
              <option value="org">Org-wide</option>
              <option value="team">Team-only</option>
            </select>
          </div>
          <div className="skill-form-row" style={{ paddingTop: 22 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13.5 }}>
              <input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} />
              Enabled
            </label>
          </div>
        </div>
        <StringList label="Steps" addLabel="Add step" items={f.steps} onChange={(v) => set("steps", v)} />
        <StringList label="Data Feeds" addLabel="Add feed" items={f.data_feeds} onChange={(v) => set("data_feeds", v)} />
        <div className="skill-form-row">
          <label className="skill-form-label">System Prompt</label>
          <textarea className="skill-form-textarea" rows={6} value={f.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} placeholder="Instructions injected into the query when this skill is active…" />
        </div>
        <div className="skill-form-foot">
          <button className="skill-btn-ghost" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="skill-btn-primary" onClick={() => onSave(f)} disabled={saving}>
            {saving ? "Saving…" : "Save skill"}
          </button>
        </div>
      </div>
    </div>
  );
}

const TEAM_COLORS: Record<string, string> = {
  "Engineering": "t-engineering", "Product": "t-product",
  "HR": "t-hr", "HR / People": "t-hr", "Marketing": "t-marketing",
  "Business / Ops": "t-business", "Business": "t-business",
};
function teamClass(team: string) { return TEAM_COLORS[team] ?? "t-default"; }

// Derive initials from a name like "Deepa Rao" → "DR"
function initials(name: string): string {
  return name.split(" ").map((w) => w[0] ?? "").slice(0, 2).join("").toUpperCase() || "?";
}

// Detect step type from the first word
function stepTagClass(step: string): string {
  const word = step.trim().split(/\s+/)[0]?.toLowerCase() ?? "";
  if (word === "when") return "pt-when";
  if (word === "check") return "pt-check";
  if (word === "stop") return "pt-stop";
  if (word === "do") return "pt-do";
  if (word === "record") return "pt-record";
  return "pt-do";
}

function PendingCard({ sub, onDecide }: { sub: SkillSubmission; onDecide: (runId: string, approve: boolean, note?: string) => void }) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const skill = sub.skill;

  const startReject = () => {
    setRejecting(true);
    // autoFocus via useEffect since the element renders on next tick
  };

  useEffect(() => {
    if (rejecting && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [rejecting]);

  return (
    <div className="pending-card">
      <div className="pending-card-body">
        {/* header: name + slug + byline */}
        <div className="pending-head">
          <div className="pending-ttl">
            <h4 className="pending-name">{sub.name}</h4>
            <span className="pending-slug">/{sub.slug}</span>
            <span className="pending-status-badge">
              <span className="pending-status-dot" />
              Pending approval
            </span>
          </div>
          <div className="pending-by">
            <div className="pending-by-text">
              <span className="pending-by-name">{sub.submitted_by}</span>
              <span className="pending-by-meta">
                {skill?.team ? `${skill.team} SME` : "SME"}
              </span>
            </div>
            <div className="avatar" style={{ width: 28, height: 28, fontSize: 11 }}>
              {initials(sub.submitted_by)}
            </div>
          </div>
        </div>

        {/* source text quote */}
        {sub.source_text && (
          <div className="pending-sec">
            <label className="pending-sec-label">What they wrote</label>
            <blockquote className="pending-quote">{sub.source_text}</blockquote>
          </div>
        )}

        {/* description */}
        {skill?.description && (
          <div className="pending-sec">
            <label className="pending-sec-label">Description</label>
            <p className="pending-desc">{skill.description}</p>
          </div>
        )}

        {/* drafted steps */}
        {skill?.steps && skill.steps.length > 0 && (
          <div className="pending-sec">
            <label className="pending-sec-label">Drafted steps · When → Check → Stop → Do → Record</label>
            <ol className="pending-steps">
              {skill.steps.map((step, i) => {
                const tagClass = stepTagClass(step);
                const firstWord = step.trim().split(/\s+/)[0] ?? "";
                const rest = step.trim().slice(firstWord.length).trim();
                return (
                  <li key={i} className="pending-step">
                    <span className={`pending-step-tag ${tagClass}`}>{firstWord}</span>
                    <span>{rest || step}</span>
                  </li>
                );
              })}
            </ol>
          </div>
        )}

        {/* data feeds */}
        {skill?.data_feeds && skill.data_feeds.length > 0 && (
          <div className="pending-sec">
            <label className="pending-sec-label">Data feeds</label>
            <div className="pending-feeds">
              {skill.data_feeds.map((feed, i) => (
                <span key={i} className="pending-feed">
                  <span className="pending-feed-dot" />{feed}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* collapsible system prompt */}
        {skill?.system_prompt && (
          <details className="pending-collapse">
            <summary>
              System prompt
              <span className="pending-chev">▸</span>
            </summary>
            <pre className="pending-prompt">{skill.system_prompt}</pre>
          </details>
        )}

        {/* approve / reject footer */}
        <div className="pending-foot">
          <button
            className="pending-approve"
            onClick={() => onDecide(sub.run_id, true)}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 12l5 5L20 6" />
            </svg>
            Approve
          </button>
          {!rejecting && (
            <button className="pending-reject" onClick={startReject}>
              Reject…
            </button>
          )}
        </div>

        {/* inline reject note box */}
        {rejecting && (
          <div className="pending-reject-box">
            <label className="pending-reject-label">
              Reason — sent back to {sub.submitted_by.split(" ")[0]}
            </label>
            <textarea
              ref={textareaRef}
              className="pending-reject-textarea"
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Why? The SME sees this note."
            />
            <div className="pending-reject-foot">
              <button className="pending-reject-cancel" onClick={() => { setRejecting(false); setNote(""); }}>
                Cancel
              </button>
              <button className="pending-reject-send" onClick={() => onDecide(sub.run_id, false, note)}>
                Reject &amp; notify
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function AdminSkillsPage() {
  const [skills, setSkills] = useState<SkillFull[]>([]);
  const [pending, setPending] = useState<SkillSubmission[]>([]);
  const [deciding, setDeciding] = useState(false);
  const [err, setErr] = useState(false);
  const [form, setForm] = useState<{ open: boolean; editing: SkillFull | null }>({ open: false, editing: null });
  const [saving, setSaving] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const load = () => adminListSkills().then(setSkills).catch(() => setErr(true));
  useEffect(() => {
    load();
    getSkillSubmissions()
      .then((all) => setPending(all.filter((s) => s.status === "pending_approval")))
      .catch(() => {});
  }, []);

  const handleToggle = async (skill: SkillFull) => {
    setSkills((p) => p.map((s) => s.id === skill.id ? { ...s, enabled: !s.enabled } : s));
    try { await adminUpdateSkill(skill.id, { enabled: !skill.enabled }); }
    catch { setSkills((p) => p.map((s) => s.id === skill.id ? { ...s, enabled: skill.enabled } : s)); }
  };

  const handleSave = async (f: FormState) => {
    setSaving(true);
    try {
      if (form.editing) {
        const updated = await adminUpdateSkill(form.editing.id, f);
        setSkills((p) => p.map((s) => s.id === updated.id ? updated : s));
      } else {
        const created = await adminCreateSkill(f as SkillCreate);
        setSkills((p) => [...p, created]);
      }
      setForm({ open: false, editing: null });
    } catch (e) { alert((e as Error).message); }
    finally { setSaving(false); }
  };

  const handleDecide = async (runId: string, approve: boolean, note = "") => {
    if (deciding) return;
    setDeciding(true);
    try {
      await decideSkillSubmission(runId, approve, note);
      setPending((p) => p.filter((s) => s.run_id !== runId));
      if (approve) load();
    } catch (e) { alert((e as Error).message); }
    finally { setDeciding(false); }
  };

  const handleDelete = async (id: string) => {
    try { await adminDeleteSkill(id); setSkills((p) => p.filter((s) => s.id !== id)); }
    catch (e) { alert((e as Error).message); }
    finally { setDeleteConfirm(null); }
  };

  const initialForm: FormState = form.editing
    ? { slug: form.editing.slug, name: form.editing.name, description: form.editing.description,
        team: form.editing.team, run_scope: form.editing.run_scope, enabled: form.editing.enabled,
        steps: form.editing.steps, data_feeds: form.editing.data_feeds, system_prompt: form.editing.system_prompt }
    : EMPTY_FORM;

  return (
    <div className="admin-page">
      <div className="admin-wrap">
        <header className="admin-head" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div>
            <h1>Org Skills</h1>
            <p>Manage reusable skills invoked via <code style={{ fontFamily: "var(--font-mono)", fontSize: 11, background: "var(--paper-2)", padding: "2px 6px", borderRadius: 4 }}>/skill-name</code> in chat or auto-detected by the query pipeline.</p>
          </div>
          <button className="skill-btn-primary" onClick={() => setForm({ open: true, editing: null })} style={{ flexShrink: 0, marginTop: 4 }}>+ Add skill</button>
        </header>
        {err && <div className="admin-note">Couldn&apos;t load skills. Check the admin key / API.</div>}

        {/* Pending approval queue */}
        {pending.length > 0 && (
          <div className="pending-block">
            <div className="pending-label">
              <span className="pending-label-text">Pending approval</span>
              <span className="pending-count">{pending.length} waiting</span>
            </div>
            {pending.map((sub) => (
              <PendingCard key={sub.run_id} sub={sub} onDecide={handleDecide} />
            ))}
          </div>
        )}

        {/* Live skills label (only shown alongside the table) */}
        {skills.length > 0 && (
          <div className="pending-live-label">Live skills</div>
        )}

        {skills.length === 0 && !err ? (
          <div style={{ padding: "40px 0", textAlign: "center", color: "var(--ink-faint)", fontSize: 14 }}>No skills yet — click &quot;Add skill&quot; to create the first one.</div>
        ) : (
          <table className="skills-table">
            <thead>
              <tr><th>Skill</th><th>Team</th><th>Scope</th><th>Rating</th><th>Runs</th><th>Enabled</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {skills.map((s) => (
                <tr key={s.id}>
                  <td><span className="skill-row-name">{s.name}</span><span className="skill-row-slug">/{s.slug}</span></td>
                  <td><span className={`skill-team ${teamClass(s.team)}`}>{s.team}</span></td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-faint)" }}>{s.run_scope}</td>
                  <td>{s.rating > 0 ? <span className="skill-rating"><span className="skill-star">★</span>{s.rating.toFixed(1)}</span> : <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-faint)" }}>—</span>}</td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{s.run_count}</td>
                  <td>
                    <button className={`sw${s.enabled ? " on" : ""}`} aria-label={s.enabled ? "Disable" : "Enable"} onClick={() => handleToggle(s)} />
                  </td>
                  <td>
                    <div className="skill-row-actions">
                      <button className="skill-action-btn" onClick={() => setForm({ open: true, editing: s })}>Edit</button>
                      <button className="skill-action-btn del" onClick={() => setDeleteConfirm(s.id)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {form.open && <SkillForm initial={initialForm} onSave={handleSave} onClose={() => setForm({ open: false, editing: null })} saving={saving} />}
      {deleteConfirm && (
        <div className="skill-form-modal" onClick={() => setDeleteConfirm(null)}>
          <div className="skill-form-card" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <h3>Delete skill?</h3>
            <p style={{ fontSize: 14, color: "var(--ink-dim)", marginBottom: 20 }}>This cannot be undone. Users will no longer be able to invoke this skill.</p>
            <div className="skill-form-foot">
              <button className="skill-btn-ghost" onClick={() => setDeleteConfirm(null)}>Cancel</button>
              <button className="skill-btn-primary" style={{ background: "var(--rose)" }} onClick={() => handleDelete(deleteConfirm)}>Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
