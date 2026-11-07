import { useEffect, useState } from "react";
import { api, num } from "./api.js";

const when = (t) => new Date(t).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });

// Admin: who did what. No numbers or passwords are ever in this log.
export default function Audit() {
  const [f, setF] = useState({ days: "7", action: "", q: "", page: 1 });
  const [d, setD] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    const q = new URLSearchParams({ days: f.days, page: String(f.page) });
    f.action && q.set("action", f.action);
    f.q && q.set("q", f.q);
    api(`/audit?${q}`).then(setD).catch((e) => setError(e.message));
  }, [f]);

  const set = (k, v) => setF({ ...f, [k]: v, page: 1 });
  return (
    <div className="stack">
      <div className="filters">
        <select value={f.days} onChange={(e) => set("days", e.target.value)} aria-label="Period">
          <option value="1">Today</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option>
        </select>
        <select value={f.action} onChange={(e) => set("action", e.target.value)} aria-label="Action">
          <option value="">All actions</option>{(d?.actions || []).map((a) => <option key={a} value={a}>{a.replace(/_/g, " ")}</option>)}
        </select>
        <input placeholder="Search person, e-mail or detail" value={f.q} onChange={(e) => set("q", e.target.value)} aria-label="Search" />
      </div>
      {error && <p className="error">{error}</p>}
      <section className="card scroll">
        <table>
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>On</th><th>Detail</th><th>IP</th></tr></thead>
          <tbody>
            {!d && !error && <tr><td colSpan="6" className="muted">Loading…</td></tr>}
            {d?.results.map((r) => (
              <tr key={r.id}><td>{when(r.at)}</td><td>{r.who || r.email || "—"}</td><td>{r.action.replace(/_/g, " ")}</td><td>{r.object || "—"}</td><td>{r.detail || "—"}</td><td>{r.ip || "—"}</td></tr>
            ))}
            {d && d.results.length === 0 && <tr><td colSpan="6" className="muted">Nothing matches.</td></tr>}
          </tbody>
        </table>
      </section>
      {d && (
        <div className="row">
          <button className="ghost" disabled={f.page <= 1} onClick={() => setF({ ...f, page: f.page - 1 })}>Previous</button>
          <span className="muted small">Page {d.page} of {Math.max(1, Math.ceil(d.count / d.page_size))} · {num(d.count)} entries</span>
          <button className="ghost" disabled={d.page * d.page_size >= d.count} onClick={() => setF({ ...f, page: f.page + 1 })}>Next</button>
        </div>
      )}
      <p className="muted small">Roles: Admin sets up users and roles under Users and roles; every sign-in, reveal of a number, call, message, recording play and setting change lands here.</p>
    </div>
  );
}
