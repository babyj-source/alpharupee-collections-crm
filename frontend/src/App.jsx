import { useEffect, useState } from "react";
import { api } from "./api.js";
import Login from "./Login.jsx";
import Dashboard from "./Dashboard.jsx";
import Loans from "./Loans.jsx";
import Logo from "./Logo.jsx";
import Ptps from "./Ptps.jsx";
import Allocation from "./Allocation.jsx";

export default function App() {
  const [user, setUser] = useState(undefined); // undefined = still checking
  const [tab, setTab] = useState("dashboard");
  const [menuOpen, setMenuOpen] = useState(false); // only used on narrow screens

  useEffect(() => {
    api("/auth/csrf")
      .then(() => api("/auth/me"))
      .then((d) => setUser(d.user))
      .catch(() => setUser(null));
  }, []);

  if (user === undefined) return <div className="center muted">Loading…</div>;
  if (user === null) return <Login onDone={setUser} />;

  const logout = async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setUser(null);
    }
  };
  const isHead = user.role === "head";
  const tabs = isHead
    ? [["dashboard", "Dashboard"], ["loans", "All accounts"], ["ptps", "Promises"], ["allocation", "Allocation"]]
    : [["dashboard", "My summary"], ["loans", "My worklist"], ["customers", "My customers"], ["ptps", "Promises"]];

  const pick = (id) => { setTab(id); setMenuOpen(false); };

  return (
    <div className="shell">
      <aside className={menuOpen ? "side open" : "side"}>
        <div className="side-head">
          <div>
            <Logo height={30} />
            <strong className="product">CRM</strong>
          </div>
          <button className="menu-btn ghost" aria-label="Menu" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>
            {menuOpen ? "Close" : "Menu"}
          </button>
        </div>
        <nav aria-label="Main">
          {tabs.map(([id, text]) => (
            <button key={id} className={tab === id ? "nav-item on" : "nav-item"} aria-current={tab === id ? "page" : undefined} onClick={() => pick(id)}>
              <Icon name={id} />
              {text}
            </button>
          ))}
        </nav>
        <div className="side-foot">
          <div>
            <strong>{user.name}</strong>
            <div className="muted small">{isHead ? "Head" : "Agent"}</div>
          </div>
          <button className="ghost" onClick={logout}>Sign out</button>
        </div>
      </aside>
      <main>
        {tab === "dashboard" && <Dashboard user={user} />}
        {tab === "loans" && <Loans user={user} />}
        {tab === "customers" && <Loans user={user} mode="customers" />}
        {tab === "ptps" && <Ptps user={user} />}
        {tab === "allocation" && isHead && <Allocation />}
      </main>
    </div>
  );
}

// Small line icons (feather-style paths) for the menu.
const ICONS = {
  dashboard: <><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /></>,
  loans: <><line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" /><circle cx="3.5" cy="6" r="1" /><circle cx="3.5" cy="12" r="1" /><circle cx="3.5" cy="18" r="1" /></>,
  customers: <><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>,
  ptps: <><rect x="3" y="4" width="18" height="18" rx="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /><path d="M9 16l2 2 4-4" /></>,
  allocation: <><polyline points="17 1 21 5 17 9" /><path d="M3 11V9a4 4 0 0 1 4-4h14" /><polyline points="7 23 3 19 7 15" /><path d="M21 13v2a4 4 0 0 1-4 4H3" /></>,
};

function Icon({ name }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}
