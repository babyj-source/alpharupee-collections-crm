import { useEffect, useState } from "react";
import { api, num } from "./api.js";

const when = (t) => new Date(t).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

// Head: what was sent (SMS and WhatsApp, including automatic ones). Admin: switch the automatic WhatsApp reminder on or off.
export default function Messages({ user }) {
  const [view, setView] = useState("whatsapp");
  return (
    <div className="stack">
      <div className="row">
        <button className={view === "whatsapp" ? "tab on" : "tab"} onClick={() => setView("whatsapp")}>WhatsApp sent</button>
        <button className={view === "sms" ? "tab on" : "tab"} onClick={() => setView("sms")}>SMS sent</button>
        <button className={view === "auto" ? "tab on" : "tab"} onClick={() => setView("auto")}>Automatic sending</button>
      </div>
      {view === "auto" ? <Auto admin={user.role === "admin"} /> : <Sent key={view} channel={view} />}
    </div>
  );
}

function Sent({ channel }) {
  const [f, setF] = useState({ days: "7", state: "", q: "", page: 1 });
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
    const q = new URLSearchParams({ channel, days: f.days, page: String(f.page) });
    f.state && q.set("state", f.state);
    f.q && q.set("q", f.q);
    api(`/messaging/sent?${q}`).then(setD).catch((e) => setError(e.message));
  }, [f, channel]);
  const set = (k, v) => setF({ ...f, [k]: v, page: 1 });
  return (
    <>
      <div className="filters">
        <select value={f.days} onChange={(e) => set("days", e.target.value)} aria-label="Period"><option value="1">Today</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option></select>
        <select value={f.state} onChange={(e) => set("state", e.target.value)} aria-label="State">
          <option value="">Any state</option><option value="sent">Sent</option><option value="delivered">Delivered</option><option value="read">Read</option><option value="failed">Failed</option>
        </select>
        <input placeholder="Account, borrower or template" value={f.q} onChange={(e) => set("q", e.target.value)} aria-label="Search" />
      </div>
      {error && <p className="error">{error}</p>}
      {d && <p className="muted small">{num(d.count)} messages{d.failed ? ` · ${num(d.failed)} failed` : ""}</p>}
      <section className="card scroll">
        <table>
          <thead><tr><th>When</th><th>Account</th><th>To</th><th>Template</th><th>State</th><th>Sent by</th><th>Message / error</th></tr></thead>
          <tbody>
            {!d && !error && <tr><td colSpan="7" className="muted">Loading…</td></tr>}
            {d?.results.map((m) => (
              <tr key={m.id}>
                <td>{when(m.at)}</td><td>{m.account_id || "—"}{m.borrower_name ? ` · ${m.borrower_name}` : ""}</td><td>{m.to}</td><td>{m.template || "—"}</td>
                <td><span className={`chip ${m.state === "failed" ? "lapsed" : ""}`}>{m.state}</span></td><td>{m.by}</td><td>{m.error || m.text || "—"}</td>
              </tr>
            ))}
            {d && d.results.length === 0 && <tr><td colSpan="7" className="muted">Nothing sent in this period.</td></tr>}
          </tbody>
        </table>
      </section>
      {d && (
        <div className="row">
          <button className="ghost" disabled={f.page <= 1} onClick={() => setF({ ...f, page: f.page - 1 })}>Previous</button>
          <span className="muted small">Page {d.page}</span>
          <button className="ghost" disabled={d.page * d.page_size >= d.count} onClick={() => setF({ ...f, page: f.page + 1 })}>Next</button>
        </div>
      )}
    </>
  );
}

function Auto({ admin }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState("");
  useEffect(() => { api("/messaging/whatsapp").then(setRows).catch((e) => setError(e.message)); }, []);

  async function save(t, patch) {
    setError(""); setSaving(t.key);
    try {
      const u = await api(`/messaging/whatsapp/${t.key}`, { method: "PUT", body: patch });
      setRows(rows.map((r) => (r.key === u.key ? u : r)));
    } catch (e) { setError(e.message); }
    finally { setSaving(""); }
  }
  const overdue = (rows || []).filter((t) => t.kind === "overdue");
  return (
    <>
      <section className="card">
        <h2>Automatic missed-payment WhatsApp</h2>
        <p className="muted small">
          Once a day, inside calling hours (08:00 to 19:00), the CRM sends the template below to every overdue account that is not paid, in dispute or closed,
          has no promise still to come or call-back booked, and has not already had a WhatsApp template today. The usual daily limit per borrower applies.
          It stops by itself after repeated failures. The reminders for payments due soon need the next instalment date, which the CRM does not hold yet.
        </p>
        {error && <p className="error">{error}</p>}
        {!rows && !error && <p className="muted">Loading…</p>}
        {overdue.map((t) => (
          <div key={t.key} className="row" style={{ borderTop: "1px solid var(--line)", paddingTop: 10, marginTop: 10 }}>
            <div className="grow"><strong>{t.name}</strong><div className="muted small">{t.body}</div>{t.has_image_header && !t.header_image_url && <div className="error small">Needs a header image link first (set it under the template).</div>}</div>
            <label className="row"><input type="checkbox" checked={t.active} disabled={!admin || saving === t.key} onChange={(e) => save(t, { active: e.target.checked })} /> Active</label>
            <label className="row"><input type="checkbox" checked={t.auto_send} disabled={!admin || saving === t.key || !t.active} onChange={(e) => save(t, { auto_send: e.target.checked })} /> Send automatically</label>
          </div>
        ))}
        {rows && overdue.length === 0 && <p className="muted">No missed-payment template is set up.</p>}
        {!admin && <p className="muted small">Only the Admin can change this.</p>}
      </section>
    </>
  );
}
