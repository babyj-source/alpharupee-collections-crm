import { useEffect, useState } from "react";
import { api, inr, num } from "./api.js";

export default function Dashboard({ user }) {
  const [d, setD] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/dashboard").then(setD).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!d) return <p className="muted">Loading…</p>;

  const max = Math.max(1, ...d.buckets.map((b) => Number(b.amount)));
  return (
    <div className="stack">
      <div className="kpis">
        <Kpi label="Active accounts" value={num(d.accounts)} />
        <Kpi label="Outstanding" value={inr(d.outstanding)} />
        <Kpi label="Overdue accounts" value={num(d.overdue_accounts)} />
        <Kpi label="Overdue amount" value={inr(d.overdue_amount)} />
      </div>

      <section className="card">
        <h2>Overdue by days past due</h2>
        <table>
          <thead>
            <tr><th>DPD</th><th className="r">Accounts</th><th className="r">Overdue</th><th /></tr>
          </thead>
          <tbody>
            {d.buckets.map((b) => (
              <tr key={b.bucket}>
                <td>{b.bucket === "0" ? "Current" : b.bucket}</td>
                <td className="r">{num(b.accounts)}</td>
                <td className="r">{inr(b.amount)}</td>
                <td className="barcell"><div className="bar-fill" style={{ width: `${(Number(b.amount) / max) * 100}%` }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {user.role === "head" && d.by_agent && (
        <section className="card">
          <h2>Overdue by agent</h2>
          <table>
            <thead><tr><th>Agent</th><th className="r">Accounts</th><th className="r">Overdue</th></tr></thead>
            <tbody>
              {d.by_agent.map((r) => (
                <tr key={r.agent_id ?? "none"}>
                  <td>{r.agent}</td><td className="r">{num(r.accounts)}</td><td className="r">{inr(r.amount)}</td>
                </tr>
              ))}
              {d.by_agent.length === 0 && <tr><td colSpan="3" className="muted">No overdue accounts.</td></tr>}
            </tbody>
          </table>
        </section>
      )}

      {user.role === "head" && (
        <p className="muted small">
          {d.sync
            ? d.sync.ok
              ? `Last LMS sync ${new Date(d.sync.finished_at).toLocaleString("en-IN")}: ${num(d.sync.created)} new, ${num(d.sync.updated)} updated.`
              : `Last LMS sync failed: ${d.sync.error}`
            : "No LMS sync has run yet."}
        </p>
      )}
    </div>
  );
}

function Kpi({ label, value }) {
  return (
    <div className="card kpi">
      <div className="muted small">{label}</div>
      <div className="kpi-value">{value}</div>
    </div>
  );
}
