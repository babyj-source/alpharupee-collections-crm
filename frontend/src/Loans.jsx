import { useCallback, useEffect, useRef, useState } from "react";
import { api, inr } from "./api.js";

const STATUSES = [
  ["new", "New"], ["contacted", "Contacted"], ["reference_contacted", "Reference contacted"], ["promised", "Promise to pay"],
  ["not_reachable", "Not reachable"], ["dispute", "Dispute"], ["paid", "Paid"],
];
const label = Object.fromEntries(STATUSES);

export default function Loans({ user }) {
  const isHead = user.role === "head";
  const [filters, setFilters] = useState({ q: "", overdue: "1", crm_status: "", agent: "" });
  const [page, setPage] = useState(1);
  const [data, setData] = useState({ results: [], count: 0 });
  const [agents, setAgents] = useState([]);
  const [picked, setPicked] = useState(new Set());
  const [assignTo, setAssignTo] = useState("");
  const [open, setOpen] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    const p = new URLSearchParams({ page });
    Object.entries(filters).forEach(([k, v]) => v && p.set(k, v));
    api(`/loans?${p}`).then(setData).catch((e) => setError(e.message));
  }, [filters, page]);

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
        <select value={filters.overdue} onChange={set("overdue")}>
          <option value="1">Overdue only</option><option value="0">All active</option>
        </select>
        <select value={filters.crm_status} onChange={set("crm_status")}>
          <option value="">Any status</option>
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {isHead && (
          <select value={filters.agent} onChange={set("agent")}>
            <option value="">Any agent</option><option value="none">Unassigned</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        )}
      </div>

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
              <th>Status</th>{isHead && <th>Agent</th>}
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
                {isHead && <td>{l.agent ? l.agent.name : <span className="muted">Unassigned</span>}</td>}
              </tr>
            ))}
            {data.results.length === 0 && <tr><td colSpan="7" className="muted">Nothing to show.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="row small muted">
        <span>{data.count} accounts</span><span className="grow" />
        <button className="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button className="ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>

      {open && <Detail loan={open} onClose={() => setOpen(null)} onSaved={(l) => { setOpen(l); load(); }} />}
    </div>
  );
}

function Detail({ loan, onClose, onSaved }) {
  const [status, setStatus] = useState(loan.crm_status);
  const [note, setNote] = useState(loan.note);
  const [shown, setShown] = useState({}); // contact key -> full number, only while visible
  const [error, setError] = useState("");
  const timers = useRef({});

  useEffect(() => () => Object.values(timers.current).forEach(clearTimeout), []);

  async function save() {
    setError("");
    try { onSaved(await api(`/loans/${loan.id}`, { method: "PATCH", body: { crm_status: status, note } })); }
    catch (e) { setError(e.message); }
  }
  async function reveal(key) {
    setError("");
    try {
      const d = await api(`/loans/${loan.id}/mobile`, { method: "POST", body: { which: key } });
      setShown((s) => ({ ...s, [key]: d.mobile }));
      clearTimeout(timers.current[key]);
      timers.current[key] = setTimeout(() => setShown((s) => ({ ...s, [key]: undefined })), 20000); // hide again
    } catch (e) { setError(e.message); }
  }

  const contacts = loan.contacts || [];
  return (
    <aside className="drawer card" role="dialog" aria-label={`Account ${loan.account_id}`}>
      <div className="row"><h2>{loan.account_id}</h2><span className="grow" /><button className="ghost" onClick={onClose}>Close</button></div>
      <dl>
        <dt>Borrower</dt><dd>{loan.borrower_name || "—"}</dd>
        <dt>Overdue</dt><dd>{inr(loan.overdue_amount)} ({inr(loan.demand_due)} dues + {inr(loan.fee_due)} fees)</dd>
        <dt>Outstanding</dt><dd>{inr(loan.outstanding)}</dd>
        <dt>Instalment</dt><dd>{inr(loan.installment_amount)}</dd>
        <dt>Days past due</dt><dd>{loan.dpd}</dd>
        <dt>Oldest due</dt><dd>{loan.oldest_due_date || "—"}</dd>
        <dt>Last payment</dt><dd>{loan.last_repayment_date || "—"}</dd>
        <dt>Product / branch</dt><dd>{loan.product_code || "—"} / {loan.branch_code || "—"}</dd>
      </dl>

      <section>
        <h3>Who to call</h3>
        {contacts.length === 0 && <p className="muted small">No phone numbers on file.</p>}
        <ul className="contacts">
          {contacts.map((c) => (
            <li key={c.key}>
              <div>
                <strong>{c.label}</strong>
                {c.name && c.key !== "borrower" && c.key !== "alt" ? ` · ${c.name}` : ""}
                {c.relation ? <span className="muted"> ({c.relation})</span> : null}
              </div>
              <div className="row">
                <span>{shown[c.key] || c.mobile}</span>
                {!shown[c.key] && <button className="ghost" onClick={() => reveal(c.key)}>Show number</button>}
              </div>
            </li>
          ))}
        </ul>
        {Object.values(shown).some(Boolean) && <p className="muted small">Numbers hide again after 20 seconds. Each view is logged.</p>}
        {contacts.some((c) => c.key.startsWith("ref")) && <p className="muted small">If the borrower cannot be reached, call a reference and set the status to “Reference contacted”.</p>}
      </section>

      <label>Status
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <label>Note
        <textarea rows="3" maxLength="500" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button onClick={save}>Save</button>
    </aside>
  );
}
