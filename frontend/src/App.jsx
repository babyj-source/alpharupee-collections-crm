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

  return (
    <div className="shell">
      <header className="bar">
        <Logo height={30} />
        <strong className="product">CRM</strong>
        <nav>
          {tabs.map(([id, text]) => (
            <button key={id} className={tab === id ? "tab on" : "tab"} onClick={() => setTab(id)}>{text}</button>
          ))}
        </nav>
        <span className="grow" />
        <span className="muted small">
          {user.name} · {isHead ? "Head" : "Agent"}
        </span>
        <button className="ghost" onClick={logout}>
          Sign out
        </button>
      </header>
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
