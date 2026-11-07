import { useCallback, useEffect, useState } from "react";
import { api, inr, isHead } from "./api.js";
import Account from "./Account.jsx";

const when = (t) => (t ? new Date(t).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
const linkChip = { active: "today", paid: "ok", expired: "bad" };
const lmsChip = { pending: "today", posted: "ok", failed: "bad" };
const STATES = [["", "All links"], ["active", "Waiting for payment"], ["paid", "Paid"], ["expired", "Expired"]];
const LMS = [["", "Any LMS status"], ["pending", "Waiting to be posted"], ["posted", "Posted"], ["failed", "Posting failed"], ["notpaid", "Not paid yet"]];
const DAYS = [["0", "All time"], ["1", "Today"], ["7", "Last 7 days"], ["30", "Last 30 days"]];

// Payment tracking: every payment link on the user's accounts, whether it was paid, and where the LMS posting stands.
export default function Payments({ user }) {
  const head = isHead(user);
  const [f, setF] = useState({ state: "", lms: "", days: "7", q: "" });
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ results: [], count: 0 });
  const [sum, setSum] = useState(null);
  const [open, setOpen] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(0);

  const qs = (extra = "") => `days=${f.days}&q=${encodeURIComponent(f.q.trim())}${extra}`;
  const load = useCallback(() => {
    api(`/payments/links?${qs(`&state=${f.state}&lms=${f.lms}&page=${page}`)}`).then(setData).catch((e) => setError(e.message));
    api(`/payments/summary?${qs()}`).then(setSum).catch(() => {});
  }, [f, page]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const set = (patch) => { setF({ ...f, ...patch }); setPage(1); };
  async function check(r) {
    setError(""); setBusy(r.id);
    try { await api(`/payments/link/${r.id}/refresh`, { method: "POST" }); load(); }
    catch (e) { setError(e.message); } finally { setBusy(0); }
  }
  async function openAccount(r) {
    setError("");
    try { setOpen(await api(`/loans/${r.loan_id}`)); } catch (e) { setError(e.message); }
  }
  const pages = Math.max(1, Math.ceil(data.count / 50));
  const tile = (label, key, patch, tone) => (
    <button className="card kpi tile" onClick={() => set({ state: "", lms: "", ...patch })} aria-label={`${label}: show these`}>
      <div className="muted small">{label}</div>
      <div className={`kpi-value ${tone || ""}`}>{sum ? sum[key].count : "…"}</div>
      <div className="muted small">{sum ? inr(sum[key].amount) : ""}</div>
    </button>
  );

  return (
    <div className="stack">
      <div className="kpis">
        {tile("Waiting for payment", "active", { state: "active" })}
        {tile("Paid", "paid", { state: "paid" })}
        {tile("Waiting to post to LMS", "lms_pending", { lms: "pending" })}
        {tile("LMS posting failed", "lms_failed", { lms: "failed" }, "bad")}
        {tile("Posted to LMS", "lms_posted", { lms: "posted" })}
        {tile("Expired", "expired", { state: "expired" })}
      </div>
      <div className="row">
        <input value={f.q} onChange={(e) => set({ q: e.target.value })} placeholder="Account number, name or link reference" aria-label="Search payment links" />
        <select value={f.days} onChange={(e) => set({ days: e.target.value })} aria-label="Made in">{DAYS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        <select value={f.state} onChange={(e) => set({ state: e.target.value })} aria-label="Link status">{STATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        <select value={f.lms} onChange={(e) => set({ lms: e.target.value })} aria-label="LMS status">{LMS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        <span className="grow" />
        <button className="ghost" onClick={load}>Refresh</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Made</th><th>Account</th><th>Borrower</th><th className="r">Amount</th><th>Link</th><th>Paid at</th><th>LMS posting</th>
              {head && <th>Agent</th>}<th />
            </tr>
          </thead>
          <tbody>
            {data.results.map((r) => (
              <tr key={r.id} className="click" onClick={() => openAccount(r)}>
                <td>{when(r.created_at)}</td>
                <td>{r.account_id}<div className="small muted" title="Link reference at Easebuzz">{r.ref}</div></td>
                <td>{r.borrower_name || <span className="muted">—</span>}</td>
                <td className="r">{inr(r.amount)}</td>
                <td><span className={`chip ${linkChip[r.state] || ""}`}>{r.state_label}</span></td>
                <td>{when(r.paid_at)}</td>
                <td>{r.lms ? <span className={`chip ${lmsChip[r.lms] || ""}`}>{r.lms_label}</span> : <span className="muted">—</span>}</td>
                {head && <td>{r.agent_name || <span className="muted">Unassigned</span>}</td>}
                <td onClick={(e) => e.stopPropagation()}>{r.state === "active" && <button className="ghost" disabled={busy === r.id} onClick={() => check(r)}>{busy === r.id ? "…" : "Check"}</button>}</td>
              </tr>
            ))}
            {data.results.length === 0 && <tr><td colSpan={head ? 9 : 8} className="muted">No payment links match.</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="muted small">Payments are noticed by Easebuzz's webhook and by a check every minute. Posting a paid payment to the LMS is not switched on yet, so paid payments wait here as “Waiting to be posted”.</p>
      <div className="row small muted">
        <span>{data.count} payment links</span><span className="grow" />
        <button className="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button className="ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      {open && <Account loan={open} onClose={() => { setOpen(null); load(); }} onChanged={() => load()} />}
    </div>
  );
}
