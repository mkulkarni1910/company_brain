"use client";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getAdminKey, setAdminKey, getConnections } from "@/lib/adminApi";

const NAV = [
  {
    group: "Workspace",
    items: [
      {
        href: "/admin",
        label: "Overview",
        icon: (
          <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5"/>
            <rect x="14" y="3" width="7" height="7" rx="1.5"/>
            <rect x="3" y="14" width="7" height="7" rx="1.5"/>
            <rect x="14" y="14" width="7" height="7" rx="1.5"/>
          </svg>
        ),
      },
      {
        href: "/admin/runs",
        label: "Runs",
        icon: (
          <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12h4l3 8 4-16 3 8h4"/>
          </svg>
        ),
      },
    ],
  },
  {
    group: "Connect",
    items: [
      {
        href: "/admin/sources",
        label: "Data Sources",
        icon: (
          <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <ellipse cx="12" cy="5" rx="8" ry="3"/>
            <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/>
            <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>
          </svg>
        ),
      },
      {
        href: "/admin/surfaces",
        label: "Surfaces",
        icon: (
          <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3 3 8l9 5 9-5-9-5Z"/>
            <path d="m3 13 9 5 9-5"/>
          </svg>
        ),
      },
      {
        href: "/admin/permissions",
        label: "Permissions",
        icon: (
          <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3 5 6v5c0 4 3 6.7 7 8 4-1.3 7-4 7-8V6l-7-3Z"/>
            <path d="m9.5 12 1.8 1.8 3.2-3.4"/>
          </svg>
        ),
      },
      {
        href: "/admin/skills",
        label: "Org Skills",
        icon: (
          <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
          </svg>
        ),
      },
    ],
  },
];

function Gate({ onUnlock, error }: { onUnlock: () => void; error?: boolean }) {
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [localErr, setLocalErr] = useState(false);

  // Validate the key against the API BEFORE unlocking, so the content never
  // flashes for a wrong key. getConnections() throws (and clears the key) on 403.
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!val || busy) return;
    setBusy(true); setLocalErr(false);
    setAdminKey(val);
    try {
      await getConnections();
      onUnlock();
    } catch {
      setLocalErr(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin-gate">
      <form className="admin-gate-card" onSubmit={submit}>
        <div className="glyph" />
        <h2>Admin access</h2>
        <p>Enter the admin key to manage data sources.</p>
        {(error || localErr) && <p className="gate-err">That key was rejected. Check the admin key and try again.</p>}
        <input type="password" value={val} onChange={(e) => setVal(e.target.value)} placeholder="Admin key" autoFocus disabled={busy} />
        <button type="submit" disabled={busy}>{busy ? "Checking…" : "Unlock"}</button>
      </form>
    </div>
  );
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [checked, setChecked] = useState(false); // true once we've read sessionStorage (avoids gate flash)
  const [unlocked, setUnlocked] = useState(false);
  const [authErr, setAuthErr] = useState(false);
  useEffect(() => {
    setUnlocked(!!getAdminKey());
    setChecked(true);
    // A 403 from any /admin call clears the key and fires this — bounce back to the gate.
    const onAuthErr = () => { setUnlocked(false); setAuthErr(true); };
    window.addEventListener("admin-auth-error", onAuthErr);
    return () => window.removeEventListener("admin-auth-error", onAuthErr);
  }, []);
  return (
    <div className="app app--norail admin">
      <aside className="rail">
        <div className="brand">
          <div className="glyph" />
          <div>
            <h1>Substrate<span style={{ color: "var(--amber)" }}>OS</span></h1>
            <div className="sub">Admin</div>
          </div>
        </div>
        {NAV.map((g) => (
          <div key={g.group}>
            <h2>{g.group}</h2>
            <nav className="nav">
              {g.items.map((it) => (
                <Link key={it.href} href={it.href}
                  className={path === it.href ? "active" : ""}>
                  {it.icon}
                  {it.label}
                </Link>
              ))}
            </nav>
          </div>
        ))}
        <div className="foot"><div className="avatar">A</div>
          <div className="who">Admin<span>t-eval</span></div></div>
      </aside>
      <main className="main">{!checked ? null : unlocked ? children : <Gate error={authErr} onUnlock={() => { setAuthErr(false); setUnlocked(true); }} />}</main>
    </div>
  );
}
