import { useEffect, useState } from "react";
import { api } from "./api.js";
import Login from "./Login.jsx";
import Dashboard from "./Dashboard.jsx";
import Loans from "./Loans.jsx";

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

  return (
    <div className="shell">
      <header className="bar">
        <strong>AlphaRupee Collections</strong>
        <nav>
          <button className={tab === "dashboard" ? "tab on" : "tab"} onClick={() => setTab("dashboard")}>
            {isHead ? "Dashboard" : "My summary"}
          </button>
          <button className={tab === "loans" ? "tab on" : "tab"} onClick={() => setTab("loans")}>
            {isHead ? "All accounts" : "My worklist"}
          </button>
        </nav>
        <span className="grow" />
        <span className="muted small">
          {user.name} · {isHead ? "Collections head" : "Agent"}
        </span>
        <button className="ghost" onClick={logout}>
          Sign out
        </button>
      </header>
      <main>{tab === "dashboard" ? <Dashboard user={user} /> : <Loans user={user} />}</main>
    </div>
  );
}
