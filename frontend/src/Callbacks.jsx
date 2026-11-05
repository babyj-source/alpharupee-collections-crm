import { useCallback, useEffect, useState } from "react";
import { api, inr, isHead } from "./api.js";
import Account from "./Account.jsx";

const STATES = [["pending", "All pending"], ["due", "Due now"], ["today", "Today"], ["upcoming", "Upcoming"]];
const label = { due: "Due now", today: "Today", upcoming: "Upcoming" };
const when = (t) => new Date(t).toLocaleString("en-IN", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

export default function Callbacks({ user }) {
  const head = isHead(user);
  const [state, setState] = useState("pending");
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ results: [], count: 0 });
  const [open, setOpen] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api(`/callbacks?state=${state}&page=${page}`).then(setData).catch((e) => setError(e.message));
  }, [state, page]);
  useEffect(load, [load]);

  async function done(c) {
    setError("");
    try { await api(`/loans/${c.loan_id}/callback`, { method: "DELETE" }); load(); }
    catch (e) { setError(e.message); }
  }
  async function openAccount(c) {
    try { setOpen(await api(`/loans/${c.loan_id}`)); } catch (e) { setError(e.message); }
  }
  const pages = Math.max(1, Math.ceil(data.count / 50));

  return (
    <div className="stack">
      <div className="row">
        {STATES.map(([v, l]) => (
          <button key={v} className={state === v ? "tab on" : "tab"} onClick={() => { setState(v); setPage(1); }}>{l}</button>
        ))}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Call back at</th><th>State</th><th>Account</th><th>Borrower</th><th className="r">DPD</th>
              <th className="r">Overdue</th><th>Last outcome</th>{head && <th>Agent</th>}<th />
            </tr>
          </thead>
          <tbody>
            {data.results.map((c) => (
              <tr key={c.loan_id} className="click" onClick={() => openAccount(c)}>
                <td>{when(c.callback_at)}</td>
                <td><span className={`chip ${c.state}`}>{label[c.state]}</span></td>
                <td>{c.account_id}</td>
                <td>{c.borrower_name || <span className="muted">—</span>}</td>
                <td className="r">{c.dpd}</td>
                <td className="r">{inr(c.overdue_amount)}</td>
                <td>{c.last_disposition_label || "—"}</td>
                {head && <td>{c.agent_name || <span className="muted">Unassigned</span>}</td>}
                <td onClick={(e) => e.stopPropagation()}><button className="ghost" onClick={() => done(c)}>Mark done</button></td>
              </tr>
            ))}
            {data.results.length === 0 && <tr><td colSpan="9" className="muted">No call-backs here.</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="muted small">A call-back is closed when you log an outcome after reaching the borrower. “No answer” and “Not reachable” keep it open so you can try again.</p>
      <div className="row small muted">
        <span>{data.count} call-backs</span><span className="grow" />
        <button className="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button className="ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      {open && <Account loan={open} onClose={() => setOpen(null)} onChanged={() => load()} />}
    </div>
  );
}
