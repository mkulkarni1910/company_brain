"use client";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getAdminKey, setAdminKey } from "@/lib/adminApi";

const NAV = [
  { group: "Workspace", items: [{ href: "/admin", label: "Overview" }] },
  { group: "Connect", items: [
    { href: "/admin/sources", label: "Data Sources" },
    { href: "/admin/surfaces", label: "Surfaces" },
    { href: "/admin/permissions", label: "Permissions" }] },
  { group: "Build", items: [{ href: "/admin/developer", label: "Developer" }] },
];

function Gate({ onUnlock }: { onUnlock: () => void }) {
  const [val, setVal] = useState("");
  return (
    <div className="admin-gate">
      <form className="admin-gate-card" onSubmit={(e) => { e.preventDefault(); if (val) { setAdminKey(val); onUnlock(); } }}>
        <div className="glyph" />
        <h2>Admin access</h2>
        <p>Enter the admin key to manage data sources.</p>
        <input type="password" value={val} onChange={(e) => setVal(e.target.value)} placeholder="Admin key" autoFocus />
        <button type="submit">Unlock</button>
      </form>
    </div>
  );
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [unlocked, setUnlocked] = useState(false);
  useEffect(() => { setUnlocked(!!getAdminKey()); }, []);
  return (
    <div className="app app--norail admin">
      <aside className="rail">
        <div className="brand">
          <div className="glyph" />
          <div><h1>SubStrate<span style={{ color: "var(--amber)" }}>OS</span></h1>
            <div className="sub">Admin</div></div>
        </div>
        {NAV.map((g) => (
          <div key={g.group}>
            <h2>{g.group}</h2>
            <nav className="nav">
              {g.items.map((it) => (
                <Link key={it.href} href={it.href}
                  className={path === it.href ? "active" : ""}>{it.label}</Link>
              ))}
            </nav>
          </div>
        ))}
        <div className="foot"><div className="avatar">A</div>
          <div className="who">Admin<span>t-eval</span></div></div>
      </aside>
      <main className="main">{unlocked ? children : <Gate onUnlock={() => setUnlocked(true)} />}</main>
    </div>
  );
}
