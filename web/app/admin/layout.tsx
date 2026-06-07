"use client";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getMe, initials, Me } from "@/lib/api";

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

// Signed in, but not in the Entra "Admin" group: the whole admin shell is
// withheld. Shows who they're signed in as, so it reads as a membership
// problem, not a broken login.
function AccessRestricted({ me }: { me: Me | null }) {
  return (
    <div className="denied">
      <div className="denied-card">
        <div className="glyph" />
        <h2>Access restricted</h2>
        <p className="why">The admin panel is limited to members of the{" "}
          <b>Admin</b> group in Microsoft&nbsp;Entra&nbsp;ID.</p>
        <span className="denied-group">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3 5 6v5c0 4 3 6.7 7 8 4-1.3 7-4 7-8V6l-7-3Z"/></svg>
          Entra ID · Admin group
        </span>
        {me && (
          <div className="denied-id">
            <div className="avatar">{initials(me.display_name)}</div>
            <div className="nm">{me.display_name}<span>{me.email}</span></div>
          </div>
        )}
        <p className="denied-hint">You&apos;re signed in, but your account isn&apos;t in the group.
          Ask your administrator to add you, then reload this page.</p>
        <Link className="denied-back" href="/">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></svg>
          Back to chat
        </Link>
      </div>
    </div>
  );
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [loaded, setLoaded] = useState(false); // true once /me resolved (avoids a denied-screen flash)
  const [denied, setDenied] = useState(false); // a backend 403 overrides whatever /me said
  // Signed-in identity (Entra name + optional Slack title); null until /me resolves.
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    getMe().then((m) => { setMe(m); setLoaded(true); });
    // Any /admin call answered 403 fires this — the backend is authoritative.
    const onAuthErr = () => setDenied(true);
    window.addEventListener("admin-auth-error", onAuthErr);
    return () => window.removeEventListener("admin-auth-error", onAuthErr);
  }, []);
  if (!loaded) return null;
  if (denied || !me?.is_admin) return <AccessRestricted me={me} />;
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
        <div className="foot">
          <div className="avatar">{me ? initials(me.display_name) : ""}</div>
          {me && <div className="who">{me.display_name}{me.title && <span>{me.title}</span>}</div>}
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
