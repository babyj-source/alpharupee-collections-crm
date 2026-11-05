import { useCallback, useEffect, useState } from "react";
import { api, inr, isHead } from "./api.js";
import Account, { ptpChip } from "./Account.jsx";

const STATES = [["open", "Open"], ["today", "Due today"], ["lapsed", "Lapsed"], ["closed", "Closed"], ["all", "All"]];
const fmt = (d) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });

export default function Ptps({ user }) {
  const head = isHead(user);
  const [state, setState] = useState("open");
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ results: [], count: 0 });
  const [open, setOpen] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api(`/ptps?state=${state}&page=${page}`).then(setData).catch((e) => setError(e.message));
  }, [state, page]);
  useEffect(load, [load]);

  async function close(p, status) {
    setError("");
    try { await api(`/ptps/${p.id}`, { method: "PATCH", body: { status } }); load(); }
    catch (e) { setError(e.message); }
  }
  async function openAccount(p) {
    try { setOpen(await api(`/loans/${p.loan_id}`)); } catch (e) { setError(e.message); }
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
              <th>Account</th><th>Borrower</th><th>Promise date</th><th className="r">Promised</th><th className="r">Overdue now</th>
              <th>State</th><th>Reason</th>{head && <th>Agent</th>}<th />
            </tr>
          </thead>
          <tbody>
            {data.results.map((p) => (
              <tr key={p.id} className="click" onClick={() => openAccount(p)}>
                <td>{p.account_id}</td>
                <td>{p.borrower_name || <span className="muted">—</span>}</td>
                <td>{fmt(p.promised_date)}</td>
                <td className="r">{inr(p.amount)}</td>
                <td className="r">{inr(p.overdue_amount)}</td>
                <td><span className={`chip ${p.state}`}>{ptpChip[p.state]}</span></td>
                <td>{p.reason_label}</td>
                {head && <td>{p.agent_name || <span className="muted">Unassigned</span>}</td>}
                <td onClick={(e) => e.stopPropagation()}>
                  {p.status === "open" && (
                    <span className="row">
                      <button className="ghost" onClick={() => close(p, "kept")}>Kept</button>
                      <button className="ghost" onClick={() => close(p, "broken")}>Broken</button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
            {data.results.length === 0 && <tr><td colSpan="9" className="muted">No promises here.</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="muted small">A promise is marked kept automatically once the LMS shows nothing overdue on the account. Recording a new promise replaces the open one.</p>
      <div className="row small muted">
        <span>{data.count} promises</span><span className="grow" />
        <button className="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button className="ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      {open && <Account loan={open} onClose={() => setOpen(null)} onChanged={() => load()} />}
    </div>
  );
}
