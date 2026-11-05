import { useEffect, useRef, useState } from "react";
import { api, inr } from "./api.js";

const STATUSES = [
  ["new", "New"], ["contacted", "Contacted"], ["reference_contacted", "Reference contacted"], ["promised", "Promise to pay"],
  ["not_reachable", "Not reachable"], ["dispute", "Dispute"], ["paid", "Paid"],
];

const fmtDate = (d) => (d ? new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—");
const fmtTime = (t) => new Date(t).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
export const ptpChip = { open: "Open", today: "Due today", lapsed: "Lapsed", kept: "Kept", broken: "Broken", cancelled: "Cancelled", superseded: "Replaced" };

function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// outcomePath: send the outcome somewhere else (the Dialer screen saves it and releases the agent in Smartflo).
export default function Account({ loan: first, onClose, onChanged, outcomePath, onDialer }) {
  const [loan, setLoan] = useState(first);
  const [meta, setMeta] = useState(null);
  const [timeline, setTimeline] = useState([]);
  const [shown, setShown] = useState({}); // contact key -> full number, only while visible
  const [error, setError] = useState("");
  const [calling, setCalling] = useState("");
  const timers = useRef({});

  useEffect(() => { setLoan(first); }, [first]);
  useEffect(() => { api("/meta").then(setMeta).catch(() => {}); }, []);
  useEffect(() => () => Object.values(timers.current).forEach(clearTimeout), []);
  const loadTimeline = () => api(`/loans/${loan.id}/timeline`).then(setTimeline).catch(() => {});
  useEffect(() => { loadTimeline(); }, [loan.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const updated = (l) => { setLoan(l); onChanged?.(l); loadTimeline(); };

  async function clearCallback() {
    setError("");
    try { updated(await api(`/loans/${loan.id}/callback`, { method: "DELETE" })); }
    catch (e) { setError(e.message); }
  }
  async function call(key) {
    setError(""); setCalling(key);
    try {
      await api("/dialer/call", { method: "POST", body: { loan_id: loan.id, which: key } });
      loadTimeline();
    } catch (e) { setError(e.message); } finally { setTimeout(() => setCalling(""), 4000); }
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
  const p = loan.open_ptp;
  return (
    <aside className="drawer card" role="dialog" aria-label={`Account ${loan.account_id}`}>
      <div className="row"><h2>{loan.account_id}</h2><span className="grow" /><button className="ghost" onClick={onClose}>Close</button></div>
      <dl>
        <dt>Borrower</dt><dd>{loan.borrower_name || "—"}</dd>
        <dt>Overdue</dt><dd>{inr(loan.overdue_amount)} ({inr(loan.demand_due)} dues + {inr(loan.fee_due)} fees)</dd>
        <dt>Outstanding principal</dt><dd>{inr(loan.outstanding)}</dd>
        <dt>Closure amount</dt><dd>{inr(loan.closure_amount)}<span className="muted"> as on {fmtDate(loan.closure_as_on)}</span></dd>
        <dt>Instalment</dt><dd>{inr(loan.installment_amount)}</dd>
        <dt>Days past due</dt><dd>{loan.dpd}</dd>
        <dt>Oldest due</dt><dd>{loan.oldest_due_date || "—"}</dd>
        <dt>Last payment</dt><dd>{loan.last_repayment_date || "—"}</dd>
        <dt>Product / branch</dt><dd>{loan.product_code || "—"} / {loan.branch_code || "—"}</dd>
        <dt>Last outcome</dt><dd>{loan.last_disposition_label || "—"}{loan.last_disposition_at ? ` · ${fmtTime(loan.last_disposition_at)}` : ""}</dd>
        <dt>Call back</dt>
        <dd>{loan.callback_at ? <>{fmtTime(loan.callback_at)} <button className="ghost" onClick={clearCallback}>Mark done</button></> : "—"}</dd>
        <dt>Promise</dt>
        <dd>{p ? <>{inr(p.amount)} by {fmtDate(p.date)} <span className={`chip ${p.state}`}>{ptpChip[p.state]}</span></> : "—"}</dd>
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
                {meta?.dialer_enabled && (
                  <button disabled={!!calling} onClick={() => call(c.key)}>{calling === c.key ? "Calling…" : "Call"}</button>
                )}
              </div>
            </li>
          ))}
        </ul>
        {Object.values(shown).some(Boolean) && <p className="muted small">Numbers hide again after 20 seconds. Each view is logged.</p>}
        <p className="muted small">If the borrower cannot be reached, call a reference and log the outcome “Reference contacted”.</p>
      </section>

      <References loan={loan} onSaved={updated} />
      {meta && <Outcome loan={loan} meta={meta} onSaved={updated} outcomePath={outcomePath} onDialer={onDialer} />}
      <Timeline items={timeline} />
      <Manual loan={loan} onSaved={updated} />
      {error && <p className="error" role="alert">{error}</p>}
    </aside>
  );
}

function References({ loan, onSaved }) {
  const [editing, setEditing] = useState(null); // slot number
  const [f, setF] = useState({ name: "", relation: "", mobile: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const closed = !["0", "active"].includes(String(loan.operational_status).trim().toLowerCase());
  if (closed) return null;

  const start = (s) => { setEditing(s.slot); setF({ name: s.name, relation: s.relation, mobile: "" }); setError(""); };
  async function save(slot) {
    setBusy(true); setError("");
    try {
      onSaved(await api(`/loans/${loan.id}/references/${slot}`, { method: "PUT", body: f }));
      setEditing(null);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function remove(slot) {
    if (!window.confirm("Remove this reference contact?")) return;
    setError("");
    try { onSaved(await api(`/loans/${loan.id}/references/${slot}`, { method: "DELETE" })); setEditing(null); }
    catch (e) { setError(e.message); }
  }

  return (
    <section>
      <h3>Reference contacts</h3>
      <ul className="contacts">
        {(loan.ref_slots || []).map((s) => (
          <li key={s.slot}>
            {editing === s.slot ? (
              <form onSubmit={(e) => { e.preventDefault(); save(s.slot); }}>
                <label>Name<input value={f.name} maxLength="160" required onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
                <label>Relationship<input value={f.relation} maxLength="60" placeholder="Sister, friend, employer…" onChange={(e) => setF({ ...f, relation: e.target.value })} /></label>
                <label>Mobile number
                  <input inputMode="numeric" value={f.mobile} maxLength="20" required={!s.has_number}
                    placeholder={s.has_number ? `Leave blank to keep ${s.mobile}` : "10-digit mobile"} onChange={(e) => setF({ ...f, mobile: e.target.value })} />
                </label>
                {error && <p className="error" role="alert">{error}</p>}
                <div className="row">
                  <button disabled={busy}>{busy ? "Saving…" : "Save"}</button>
                  <button type="button" className="ghost" onClick={() => setEditing(null)}>Cancel</button>
                  <span className="grow" />
                  {s.has_number && <button type="button" className="ghost" onClick={() => remove(s.slot)}>Remove</button>}
                </div>
              </form>
            ) : (
              <div className="row">
                <span><strong>Reference {s.slot}</strong>{s.name ? ` · ${s.name}` : ""}{s.relation ? <span className="muted"> ({s.relation})</span> : null}
                  {s.has_number ? <span className="muted"> · {s.mobile}{s.source === "crm" ? "" : " · from LMS"}</span> : <span className="muted"> · not added</span>}</span>
                <span className="grow" />
                <button className="ghost" onClick={() => start(s)}>{s.has_number ? "Edit" : "Add"}</button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

const pad = (n) => String(n).padStart(2, "0");
// Half-hour slots inside the calling window, e.g. 08:00 … 18:30.
function slots(meta) {
  const mins = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
  const out = [];
  for (let m = mins(meta.call_start); m < mins(meta.call_end); m += meta.callback_step_minutes) out.push(`${pad(Math.floor(m / 60))}:${pad(m % 60)}`);
  return out;
}

function Outcome({ loan, meta, onSaved, outcomePath, onDialer }) {
  const [cbDate, setCbDate] = useState("");
  const [cbTime, setCbTime] = useState("");
  const [code, setCode] = useState("");
  const [date, setDate] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const closed = !["0", "active"].includes(String(loan.operational_status).trim().toLowerCase());
  if (closed) return <p className="muted small">This account is closed.</p>;
  const isPtp = code === "ptp_given";
  const isCb = code === "call_back_later";
  const allSlots = slots(meta);
  const slotPassed = (t) => cbDate === meta.today && t <= meta.now; // today's slots that have gone
  const days = Array.from({ length: meta.ptp_max_days + 1 }, (_, i) => addDays(meta.today, i));

  async function save(e) {
    e.preventDefault();
    setBusy(true); setError("");
    const body = { code, note };
    if (isPtp) { body.ptp_date = date; body.ptp_reason = reason; if (amount) body.ptp_amount = amount; }
    if (isCb) { body.callback_date = cbDate; body.callback_time = cbTime; }
    try {
      const res = await api(outcomePath ? outcomePath(loan.id) : `/loans/${loan.id}/disposition`, { method: "POST", body });
      if (res && res.loan) { onSaved(res.loan); onDialer?.(res); } else onSaved(res);
      setCode(""); setDate(""); setAmount(""); setReason(""); setNote(""); setCbDate(""); setCbTime("");
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }

  return (
    <section>
      <h3>Log the call outcome</h3>
      <form onSubmit={save}>
        <label>Outcome
          <select value={code} onChange={(e) => setCode(e.target.value)} required>
            <option value="">Select…</option>
            {meta.dispositions.map((d) => <option key={d.code} value={d.code}>{d.label}</option>)}
          </select>
        </label>
        {isPtp && (
          <>
            <label>Promise date (within {meta.ptp_max_days} days)
              <select value={date} onChange={(e) => setDate(e.target.value)} required>
                <option value="">Select…</option>
                {days.map((d, i) => <option key={d} value={d}>{i === 0 ? "Today · " : ""}{fmtDate(d)}</option>)}
              </select>
            </label>
            <label>Promised amount (₹)
              <input inputMode="decimal" value={amount} placeholder={`Leave blank for the overdue ${inr(loan.overdue_amount)}`} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} />
            </label>
            <label>Reason for the delay
              <select value={reason} onChange={(e) => setReason(e.target.value)} required>
                <option value="">Select…</option>
                {meta.ptp_reasons.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
              </select>
            </label>
          </>
        )}
        {isCb && (
          <>
            <label>Call back on
              <select value={cbDate} required onChange={(e) => { setCbDate(e.target.value); setCbTime(""); }}>
                <option value="">Select…</option>
                {days.map((d, i) => <option key={d} value={d}>{i === 0 ? "Today · " : ""}{fmtDate(d)}</option>)}
              </select>
            </label>
            <label>Call back at (between {meta.call_start} and {meta.call_end})
              <select value={cbTime} required disabled={!cbDate} onChange={(e) => setCbTime(e.target.value)}>
                <option value="">{cbDate ? "Select…" : "Choose the date first"}</option>
                {allSlots.map((t) => <option key={t} value={t} disabled={slotPassed(t)}>{t}{slotPassed(t) ? " (passed)" : ""}</option>)}
              </select>
            </label>
          </>
        )}
        <label>Note (optional)
          <input value={note} maxLength="300" onChange={(e) => setNote(e.target.value)} />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button disabled={busy || !code || (isCb && !(cbDate && cbTime))}>{busy ? "Saving…" : "Save outcome"}</button>
      </form>
    </section>
  );
}

function Timeline({ items }) {
  return (
    <section>
      <h3>Timeline</h3>
      {items.length === 0 && <p className="muted small">Nothing recorded yet.</p>}
      <ul className="timeline">
        {items.map((a) => (
          <li key={a.id}>
            <div className="small muted">{fmtTime(a.created_at)} · {a.by}</div>
            <div>{a.disposition_label ? <strong>{a.disposition_label}</strong> : null}{a.disposition_label && a.detail ? " — " : ""}{a.detail}</div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Manual({ loan, onSaved }) {
  const [status, setStatus] = useState(loan.crm_status);
  const [note, setNote] = useState(loan.note);
  const [error, setError] = useState("");
  useEffect(() => { setStatus(loan.crm_status); setNote(loan.note); }, [loan.crm_status, loan.note]);
  async function save() {
    setError("");
    try { onSaved(await api(`/loans/${loan.id}`, { method: "PATCH", body: { crm_status: status, note } })); }
    catch (e) { setError(e.message); }
  }
  return (
    <details>
      <summary>Change status or note by hand</summary>
      <div className="stack" style={{ marginTop: 8 }}>
        <label>Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label>Standing note
          <textarea rows="3" maxLength="500" value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button onClick={save}>Save</button>
      </div>
    </details>
  );
}
