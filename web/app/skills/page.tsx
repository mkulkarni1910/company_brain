"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSkills, SkillSummary } from "@/lib/skillsApi";

const TEAM_COLORS: Record<string, string> = {
  "Engineering": "t-engineering",
  "Product": "t-product",
  "HR": "t-hr",
  "HR / People": "t-hr",
  "Marketing": "t-marketing",
  "Business / Ops": "t-business",
  "Business": "t-business",
};

function teamClass(team: string): string {
  return TEAM_COLORS[team] ?? "t-default";
}

function SkillModal({ skill, onClose, onRun }: {
  skill: SkillSummary; onClose: () => void; onRun: (skill: SkillSummary) => void;
}) {
  return (
    <div className="skill-modal-bg" onClick={onClose}>
      <div className="skill-modal" onClick={(e) => e.stopPropagation()}>
        <div className="skill-modal-head">
          <h3>{skill.name}</h3>
          <button className="skill-modal-x" onClick={onClose}>✕</button>
        </div>
        <div className="skill-modal-body">
          <div className="skill-modal-label">What this skill does</div>
          <p style={{ fontSize: 14, color: "var(--ink-dim)", lineHeight: 1.55 }}>{skill.description}</p>
          {skill.steps.length > 0 && (
            <>
              <div className="skill-modal-label">Steps it runs</div>
              <ol className="skill-steps">
                {skill.steps.map((step, i) => (
                  <li key={i}><span className="n">{i + 1}</span><div>{step}</div></li>
                ))}
              </ol>
            </>
          )}
          {skill.data_feeds.length > 0 && (
            <>
              <div className="skill-modal-label">Data it reads (ACL-scoped)</div>
              <div className="skill-feeds">
                {skill.data_feeds.map((f) => <span key={f} className="skill-feed">{f}</span>)}
              </div>
            </>
          )}
          <div className="skill-modal-foot">
            <button className="skill-btn-primary" onClick={() => { onRun(skill); onClose(); }}>
              ▶ Run skill
            </button>
            <button className="skill-btn-ghost" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [activeTeam, setActiveTeam] = useState("All");
  const [modal, setModal] = useState<SkillSummary | null>(null);
  const router = useRouter();

  useEffect(() => { getSkills().then(setSkills); }, []);

  const teams = ["All", ...Array.from(new Set(skills.map((s) => s.team)))];
  const visible = activeTeam === "All" ? skills : skills.filter((s) => s.team === activeTeam);

  const handleRun = (skill: SkillSummary) => {
    router.push(`/?prefill=${encodeURIComponent("/" + skill.slug + " ")}`);
  };

  return (
    <main className="main">
      <div style={{ padding: "0 28px" }}>
        <div className="skills-page">
          <div className="skills-header">
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "2px", textTransform: "uppercase", color: "var(--amber)", marginBottom: 10 }}>
              Org Skills
            </div>
            <h1>Your team&apos;s proven workflows</h1>
            <p>Reusable skills distilled from how your org does recurring tasks. Run with <code style={{ fontFamily: "var(--font-mono)", fontSize: 12, background: "var(--panel)", padding: "2px 6px", borderRadius: 5 }}>/skill-name</code> in chat or click Run below.</p>
          </div>
          <div className="skills-filter">
            {teams.map((t) => (
              <button key={t} className={`filter-chip${activeTeam === t ? " active" : ""}`} onClick={() => setActiveTeam(t)}>
                {t}
              </button>
            ))}
          </div>
          {visible.length === 0 ? (
            <div className="skills-empty">No skills available yet. Ask an admin to add some.</div>
          ) : (
            <div className="skills-grid">
              {visible.map((skill) => (
                <div key={skill.id} className="skill-card" onClick={() => setModal(skill)}>
                  {skill.rating > 0 && (
                    <span className="star-badge">★ {skill.rating.toFixed(1)}</span>
                  )}
                  <span className={`skill-team ${teamClass(skill.team)}`}>{skill.team}</span>
                  <h3>{skill.name}</h3>
                  <p>{skill.description}</p>
                  <div className="skill-card-foot">
                    <span className="skill-rating">
                      {"★".repeat(Math.round(skill.rating))}{"☆".repeat(5 - Math.round(skill.rating))} {skill.rating > 0 ? skill.rating.toFixed(1) : "–"}
                    </span>
                    <span className="skill-runs">{skill.run_count.toLocaleString()} runs</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      {modal && (
        <SkillModal skill={modal} onClose={() => setModal(null)} onRun={handleRun} />
      )}
    </main>
  );
}
