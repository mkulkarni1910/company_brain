export default function Permissions() {
  return (
    <div className="admin-page">
    <div className="admin-wrap">
      <div className="perm-hero">
        <div className="perm-shield">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3 5 6v5c0 4 3 6.7 7 8 4-1.3 7-4 7-8V6l-7-3Z"/>
            <path d="m9.5 12 1.8 1.8 3.2-3.4"/>
          </svg>
        </div>
        <div>
          <h1 style={{ fontFamily: "var(--font-fraunces), serif", fontSize: 30, fontWeight: 600, margin: 0, lineHeight: 1.1 }}>Permissions</h1>
          <p style={{ color: "var(--ink-faint)", margin: "4px 0 0" }}>Who can access what across your workspace.</p>
          <span className="perm-badge">In development</span>
        </div>
      </div>

      <div className="perm-grid">
        {/* Roles & Members */}
        <div className="perm-card">
          <div className="perm-card-head">
            <div className="perm-card-hl">
              <div className="perm-card-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                </svg>
              </div>
              <div>
                <h3>Roles &amp; Members</h3>
                <div className="card-sub">Assign access levels to users</div>
              </div>
            </div>
            <span className="soon-pill">Coming soon</span>
          </div>
          <div className="perm-card-body">
            {[62, 50, 70].map((w, i) => (
              <div className="ghost-row" key={i}>
                <div className="ghost-av" />
                <div className="ghost-lines">
                  <div className="ghost-line" style={{ width: `${w}%` }} />
                  <div className="ghost-line" style={{ width: `${w - 22}%` }} />
                </div>
                <div className="ghost-pill" style={{ width: 52 + i * 8 }} />
              </div>
            ))}
            <div className="ghost-row">
              <div className="ghost-pill" style={{ width: 108, height: 28, borderRadius: 9 }} />
            </div>
          </div>
        </div>

        {/* Source Access */}
        <div className="perm-card">
          <div className="perm-card-head">
            <div className="perm-card-hl">
              <div className="perm-card-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
                  <ellipse cx="12" cy="5" rx="8" ry="3"/>
                  <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/>
                  <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>
                </svg>
              </div>
              <div>
                <h3>Source Access</h3>
                <div className="card-sub">Control which teams see which sources</div>
              </div>
            </div>
            <span className="soon-pill">Coming soon</span>
          </div>
          <div className="perm-card-body">
            {[55, 45, 60, 38].map((w, i) => (
              <div className="ghost-row" key={i}>
                <div className="ghost-av sq" />
                <div className="ghost-lines">
                  <div className="ghost-line" style={{ width: `${w}%` }} />
                </div>
                <div className="ghost-chip" style={{ width: 52 + i * 6 }} />
                <div className="ghost-tog" />
              </div>
            ))}
          </div>
        </div>

        {/* Audit Log */}
        <div className="perm-card">
          <div className="perm-card-head">
            <div className="perm-card-hl">
              <div className="perm-card-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                  <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
                </svg>
              </div>
              <div>
                <h3>Audit Log</h3>
                <div className="card-sub">See who accessed what and when</div>
              </div>
            </div>
            <span className="soon-pill">Coming soon</span>
          </div>
          <div className="perm-card-body">
            {[80, 68, 74, 60].map((w, i) => (
              <div className="ghost-row" key={i}>
                <div className="ghost-av" />
                <div className="ghost-lines">
                  <div className="ghost-line" style={{ width: `${w}%` }} />
                  <div className="ghost-line" style={{ width: `${w - 38}%` }} />
                </div>
                <div className="ghost-ts" />
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="perm-note">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        <p>
          <strong>Fine-grained access control is coming.</strong> For now, the admin key gates all admin actions and Easy Auth controls who can reach the app. Source-level ACLs from SharePoint and Microsoft 365 are already respected at query time — user-facing permission management will build on that foundation.
        </p>
      </div>
    </div>
    </div>
  );
}
