import { useEffect, useState } from "react";
import { api, num } from "./api.js";
import Account from "./Account.jsx";

const PERIODS = [["1", "Today"], ["7", "Last 7 days"], ["30", "Last 30 days"]];
const secs = (s) => { const n = Number(s) || 0; return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`; };

// Head: Smartflo's call records, matched to accounts, with the recording one click away (every play is audited).
export default function CallLog() {
  const [open, setOpen] = useState(null);
  const [f, setF] = useState({ days: "1", status: "", direction: "", agent: "", page: 1 });
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const [rec, setRec] = useState(null); // {call_id, url}
  const [busy, setBusy] = useState("");

  useEffect(() => {
    setError(""); setD(null);
    const q = new URLSearchParams({ days: f.days, page: String(f.page) });
    ["status", "direction", "agent"].forEach((k) => f[k] && q.set(k, f[k]));
    api(`/dialer/calls?${q}`).then(setD).catch((e) => setError(e.message));
  }, [f]);

  const set = (k, v) => setF({ ...f, [k]: v, page: k === "page" ? v : 1 });

  async function play(id) {
    setError(""); setBusy(id);
    try { setRec({ call_id: id, ...(await api("/dialer/calls/recording", { method: "POST", body: { call_id: id } })) }); }
    catch (e) { setError(e.message); }
    finally { setBusy(""); }
  }

  return (
    <div className="stack">
      <div className="filters">
        <select value={f.days} onChange={(e) => set("days", e.target.value)} aria-label="Period">{PERIODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        <select value={f.status} onChange={(e) => set("status", e.target.value)} aria-label="Status"><option value="">All calls</option><option value="answered">Answered</option><option value="missed">Missed</option></select>
        <select value={f.direction} onChange={(e) => set("direction", e.target.value)} aria-label="Direction"><option value="">In and out</option><option value="outbound">Outbound</option><option value="inbound">Inbound</option></select>
        <input placeholder="Agent name" value={f.agent} onChange={(e) => set("agent", e.target.value)} aria-label="Agent" />
      </div>
      {error && <p className="error">{error}</p>}
      {rec && (
        <section className="card">
          <div className="row"><strong>Recording</strong><span className="muted small">{rec.call_id}</span><span className="grow" /><button className="ghost" onClick={() => setRec(null)}>Close</button></div>
          <audio controls autoPlay src={rec.url} style={{ width: "100%", marginTop: 8 }} />
          <p className="muted small">This play is written to the audit log.</p>
        </section>
      )}
      <section className="card scroll">
        <table>
          <thead><tr><th>When</th><th>Agent</th><th>Direction</th><th>Status</th><th className="r">Talk time</th><th>Customer</th><th>Account</th><th>In the CRM</th><th>Outcome</th><th>Campaign</th><th /></tr></thead>
          <tbody>
            {!d && !error && <tr><td colSpan="11" className="muted">Loading…</td></tr>}
            {d && d.results.map((r, i) => (
              <tr key={r.call_id || i}>
                <td>{r.at}</td><td>{r.agent || "—"}</td><td>{r.direction}</td><td>{r.status}</td><td className="r">{secs(r.answered)}</td><td>{r.to || "—"}</td>
                <td><button className="ghost" onClick={() => api(`/loans/${r.loan_id}`).then(setOpen).catch((e) => setError(e.message))}>{r.account_id}{r.borrower_name ? ` · ${r.borrower_name}` : ""}</button></td>
                <td>{r.crm_user || "—"}<div className="muted small">{r.how}</div></td>
                <td>{r.outcome || "—"}</td><td>{r.campaign || "—"}</td>
                <td>{r.has_recording ? <button disabled={busy === r.call_id} onClick={() => play(r.call_id)}>{busy === r.call_id ? "…" : "Play"}</button> : <span className="muted small">none</span>}</td>
              </tr>
            ))}
            {d && d.results.length === 0 && <tr><td colSpan="11" className="muted">No CRM calls among the Smartflo records on this page.</td></tr>}
          </tbody>
        </table>
      </section>
      {d && (
        <div className="row">
          <button className="ghost" disabled={f.page <= 1} onClick={() => set("page", f.page - 1)}>Previous</button>
          <span className="muted small">Smartflo page {d.page} · {num(d.results.length)} CRM calls among {num(d.checked)} records checked · {num(d.count)} records in the period</span>
          <button className="ghost" disabled={d.page * d.page_size >= d.count} onClick={() => set("page", f.page + 1)}>Next</button>
        </div>
      )}
      <p className="muted small">Only calls placed or taken in the CRM are listed: a call to a customer on a CRM account that an agent started from the CRM, or logged an outcome for, close to the time of the call. Other Smartflo calls are left out, so some pages show few or none; use Next to go on. Numbers are masked.</p>
      {open && <Account loan={open} onClose={() => setOpen(null)} onChanged={setOpen} />}
    </div>
  );
}
