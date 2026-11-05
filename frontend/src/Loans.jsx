import { useCallback, useEffect, useState } from "react";
import { api, inr } from "./api.js";
import Account, { ptpChip } from "./Account.jsx";

const STATUSES = [
  ["new", "New"], ["contacted", "Contacted"], ["reference_contacted", "Reference contacted"], ["promised", "Promise to pay"],
  ["not_reachable", "Not reachable"], ["dispute", "Dispute"], ["paid", "Paid"],
];
const label = Object.fromEntries(STATUSES);

const VIEWS = {
  overdue: { label: "Overdue only", params: { overdue: "1", active: "1" } },
  active: { label: "All active", params: { overdue: "0", active: "1" } },
  all: { label: "Everything, including closed", params: { overdue: "0", active: "0" } },
};

// mode: "worklist" opens on overdue accounts, "customers" on everything allocated (the agent's "My customers").
export default function Loans({ user, mode = "worklist" }) {
  const isHead = user.role === "head";
  const [filters, setFilters] = useState({ q: "", view: mode === "customers" ? "all" : "overdue", crm_status: "", agent: "" });
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ results: [], count: 0 });
  const [agents, setAgents] = useState([]);
  const [picked, setPicked] = useState(new Set());
  const [assignTo, setAssignTo] = useState("");
  const [open, setOpen] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api(`/loans?${query(page)}`).then(setData).catch((e) => setError(e.message));
  }, [filters, page]); // eslint-disable-line react-hooks/exhaustive-deps

  function query(pg) {
    const { view, ...rest } = filters;
    const p = new URLSearchParams(pg ? { page: pg } : {});
    Object.entries({ ...VIEWS[view].params, ...rest }).forEach(([k, v]) => v && p.set(k, v));
    return p.toString();
  }

  useEffect(load, [load]);
  useEffect(() => {
    if (isHead) api("/agents").then(setAgents).catch(() => {});
  }, [isHead]);

  const set = (k) => (e) => { setFilters({ ...filters, [k]: e.target.value }); setPage(1); setPicked(new Set()); };
  const toggle = (id) => { const s = new Set(picked); s.has(id) ? s.delete(id) : s.add(id); setPicked(s); };
  const pages = Math.max(1, Math.ceil(data.count / 50));

  async function assign() {
    setError("");
    try {
      await api("/loans/assign", { method: "POST", body: { loan_ids: [...picked], agent_id: assignTo === "none" ? null : Number(assignTo) } });
      setPicked(new Set());
      load();
    } catch (e) { setError(e.message); }
  }

  return (
    <div className="stack">
      <div className="filters">
        <input placeholder="Search account or name" value={filters.q} onChange={set("q")} />
        <select value={filters.view} onChange={set("view")} aria-label="Which accounts">
          {Object.entries(VIEWS).map(([v, x]) => <option key={v} value={v}>{x.label}</option>)}
        </select>
        <select value={filters.crm_status} onChange={set("crm_status")}>
          <option value="">Any status</option>
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {isHead && (
          <select value={filters.agent} onChange={set("agent")} aria-label="Agent">
            <option value="">Any agent</option><option value="none">Unassigned</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        )}
      </div>

      {isHead && (
        <div className="row small">
          <a href={`/api/loans/export.csv?${query()}`} download>Download these accounts (CSV)</a>
          <span className="muted">Numbers are masked in the file; each download is logged.</span>
        </div>
      )}

      {isHead && picked.size > 0 && (
        <div className="card row">
          <span>{picked.size} selected</span>
          <select value={assignTo} onChange={(e) => setAssignTo(e.target.value)}>
            <option value="">Assign to…</option><option value="none">Nobody (unassign)</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <button disabled={!assignTo} onClick={assign}>Assign</button>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}

      <div className="card scroll">
        <table>
          <thead>
            <tr>
              {isHead && <th />}
              <th>Account</th><th>Borrower</th><th className="r">DPD</th><th className="r">Overdue</th>
              <th>Status</th><th>Last outcome</th><th>Promise</th>{isHead && <th>Agent</th>}
            </tr>
          </thead>
          <tbody>
            {data.results.map((l) => (
              <tr key={l.id} className="click" onClick={() => setOpen(l)}>
                {isHead && <td onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={picked.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Select ${l.account_id}`} /></td>}
                <td>{l.account_id}</td>
                <td>{l.borrower_name || <span className="muted">—</span>}</td>
                <td className="r">{l.dpd}</td>
                <td className="r">{inr(l.overdue_amount)}</td>
                <td>{label[l.crm_status]}</td>
                <td>{l.last_disposition_label || <span className="muted">Not called</span>}</td>
                <td>{l.open_ptp ? <span className={`chip ${l.open_ptp.state}`}>{ptpChip[l.open_ptp.state]} · {new Date(`${l.open_ptp.date}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}</span> : <span className="muted">—</span>}</td>
                {isHead && <td>{l.agent ? l.agent.name : <span className="muted">Unassigned</span>}</td>}
              </tr>
            ))}
            {data.results.length === 0 && <tr><td colSpan="9" className="muted">Nothing to show.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="row small muted">
        <span>{data.count} accounts</span><span className="grow" />
        <button className="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button className="ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>

      {open && <Account loan={open} onClose={() => setOpen(null)} onChanged={() => load()} />}
    </div>
  );
}
