import { useCallback, useEffect, useState } from "react";
import { api, num } from "./api.js";

const when = (t) => (t ? new Date(t).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "Never");

export default function Campaigns({ user }) {
  const [tab, setTab] = useState("pools");
  const admin = user.role === "admin";
  return (
    <div className="stack">
      <div className="row">
        <button className={tab === "pools" ? "tab on" : "tab"} onClick={() => setTab("pools")}>Campaign pools</button>
        {admin && <button className={tab === "setup" ? "tab on" : "tab"} onClick={() => setTab("setup")}>Setup</button>}
      </div>
      {tab === "pools" && <Pools />}
      {tab === "setup" && admin && <Setup />}
    </div>
  );
}

function Pools() {
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState(null); // {key, label, eligible, sample}
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api("/dialer/pools").then(setD).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);

  async function preview(p) {
    setError(""); setNotice("");
    try { setConfirm({ ...p, ...(await api(`/dialer/pools/${p.key}/push`, { method: "POST", body: {} })) }); }
    catch (e) { setError(e.message); }
  }
  async function push() {
    setBusy(true); setError("");
    try {
      const r = await api(`/dialer/pools/${confirm.key}/push`, { method: "POST", body: { confirm: true } });
      setNotice(`${num(r.sent)} customers pushed into “${confirm.label}”.`);
      setConfirm(null);
      load();
    } catch (e) { setError(e.message); setConfirm(null); load(); } finally { setBusy(false); }
  }

  if (!d) return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
  return (
    <>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!d.enabled && <p className="card muted">The dialler is switched off on this server, so nothing can be pushed or dialled. The counts below are still live.</p>}

      {confirm && (
        <section className="card" role="alertdialog" aria-label="Confirm push">
          <h2>Push “{confirm.label}” to Smartflo?</h2>
          <p><strong>{num(confirm.eligible)}</strong> customers will be added to the Smartflo lead list. Smartflo will start dialling them as soon as an agent is signed in to that campaign.</p>
          {confirm.sample?.length > 0 && <p className="muted small">First accounts: {confirm.sample.map((s) => s.account_id).join(", ")}…</p>}
          {!confirm.configured && <p className="error">Set this campaign's Smartflo ids first (Admin, Setup).</p>}
          <div className="row">
            <button disabled={busy || !confirm.enabled || !confirm.configured || confirm.eligible === 0} onClick={push}>{busy ? "Pushing…" : "Yes, push them"}</button>
            <button className="ghost" onClick={() => setConfirm(null)}>Cancel</button>
          </div>
        </section>
      )}

      <div className="card scroll">
        <table>
          <thead><tr><th>Campaign</th><th className="r">Customers waiting</th><th>Smartflo ids</th><th>Agents can start</th><th>Last pushed</th><th /></tr></thead>
          <tbody>
            {d.pools.map((p) => (
              <tr key={p.key}>
                <td>{p.label}</td><td className="r">{num(p.eligible)}</td>
                <td>{p.configured ? <span className="chip ok">Set</span> : <span className="chip today">Not set</span>}</td>
                <td>{p.active ? <span className="chip ok">Yes</span> : <span className="chip">No</span>}</td>
                <td>{when(p.last_pushed_at)}{p.last_pushed_at ? ` · ${num(p.last_pushed_count)}` : ""}</td>
                <td><button className="ghost" onClick={() => preview(p)}>Push to Smartflo…</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Pools never overlap. A call-back due today comes first, then a promise due or missed, then the days-past-due bucket. An account with a promise or call-back still to come
        is held back so nobody is called twice. Only the borrower's main number is pushed; references are called by hand from the account screen. Push again whenever you want the lists refreshed.
      </p>
    </>
  );
}

function Setup() {
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [check, setCheck] = useState(null);
  const load = useCallback(() => api("/dialer/setup").then(setD).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const save = async (fn, ok) => {
    setError(""); setNotice("");
    try { await fn(); setNotice(ok); } catch (e) { setError(e.message); } // no reload: it would wipe edits made in other rows
  };
  if (!d) return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
  const edit = (list, key, idx, field, value) => setD({ ...d, [list]: d[list].map((x, i) => (i === idx ? { ...x, [field]: value } : x)) });

  return (
    <>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <section className="card">
        <h2>Connection</h2>
        <p>Switch: <strong>{d.status.enabled ? "ON" : "OFF"}</strong> · Account token on the server: <strong>{d.status.token_set ? "set" : "not set"}</strong></p>
        <p className="muted small">The switch (<code>SMARTFLO_ENABLED</code>) and the token (<code>SMARTFLO_API_TOKEN</code>) are set on the server, never in this screen.</p>
        <div className="row">
          <button onClick={async () => setCheck(await api("/dialer/check", { method: "POST" }))}>Test connection</button>
          {check && <span className={check.ok ? "" : "error"}>{check.ok ? `Connected. Smartflo replied with: ${check.fields.join(", ") || "(empty)"}` : check.detail}</span>}
        </div>
      </section>

      <section className="card scroll">
        <h2 style={{ padding: "0 10px" }}>Campaigns</h2>
        <table>
          <thead><tr><th>Campaign</th><th>Smartflo campaign id</th><th>Smartflo lead-list id</th><th>Agents can start</th><th /></tr></thead>
          <tbody>
            {d.campaigns.map((c, i) => (
              <tr key={c.key}>
                <td>{c.label}</td>
                <td><input inputMode="numeric" value={c.smartflo_campaign_id} onChange={(e) => edit("campaigns", c.key, i, "smartflo_campaign_id", e.target.value.replace(/\D/g, ""))} aria-label={`${c.label} campaign id`} /></td>
                <td><input inputMode="numeric" value={c.smartflo_list_id} onChange={(e) => edit("campaigns", c.key, i, "smartflo_list_id", e.target.value.replace(/\D/g, ""))} aria-label={`${c.label} lead list id`} /></td>
                <td><input type="checkbox" checked={c.active} onChange={(e) => edit("campaigns", c.key, i, "active", e.target.checked)} aria-label={`${c.label} active`} /></td>
                <td><button onClick={() => save(() => api(`/dialer/campaigns/${c.key}`, { method: "PUT", body: { smartflo_campaign_id: c.smartflo_campaign_id, smartflo_list_id: c.smartflo_list_id, active: c.active } }), `${c.label} saved.`)}>Save</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small" style={{ padding: "0 10px" }}>Create the campaigns and lead lists in Smartflo first, then enter their ids here. The lead list needs three columns in this order: Mobile, Name, Account ID.</p>
      </section>

      <section className="card scroll">
        <h2 style={{ padding: "0 10px" }}>Agents in Smartflo</h2>
        <table>
          <thead><tr><th>CRM agent</th><th>Smartflo login e-mail</th><th>Smartflo agent number</th><th /></tr></thead>
          <tbody>
            {d.agents.map((a, i) => (
              <tr key={a.id}>
                <td>{a.name}<div className="muted small">{a.email}</div></td>
                <td><input type="email" value={a.smartflo_email} onChange={(e) => edit("agents", a.id, i, "smartflo_email", e.target.value)} aria-label={`${a.name} Smartflo e-mail`} /></td>
                <td><input inputMode="numeric" value={a.agent_number} onChange={(e) => edit("agents", a.id, i, "agent_number", e.target.value.replace(/\D/g, ""))} aria-label={`${a.name} agent number`} /></td>
                <td><button onClick={() => save(() => api(`/dialer/agents/${a.id}`, { method: "PUT", body: { smartflo_email: a.smartflo_email, agent_number: a.agent_number } }), `${a.name} saved.`)}>Save</button></td>
              </tr>
            ))}
            {d.agents.length === 0 && <tr><td colSpan="4" className="muted">No active agents yet. Add them under Users and roles.</td></tr>}
          </tbody>
        </table>
        <p className="muted small" style={{ padding: "0 10px" }}>The Smartflo password is never stored: each agent types it when they start dialling.</p>
      </section>

      <section className="card scroll">
        <h2 style={{ padding: "0 10px" }}>Call outcomes in Smartflo</h2>
        <table>
          <thead><tr><th>CRM outcome</th><th>Smartflo disposition id</th></tr></thead>
          <tbody>
            {d.dispositions.map((x, i) => (
              <tr key={x.code}>
                <td>{x.label}</td>
                <td><input inputMode="numeric" value={x.smartflo_status_id} onChange={(e) => edit("dispositions", x.code, i, "smartflo_status_id", e.target.value.replace(/\D/g, ""))} aria-label={`${x.label} Smartflo id`} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row" style={{ padding: "8px 10px" }}>
          <button onClick={() => save(() => api("/dialer/dispositions", { method: "PUT", body: { map: Object.fromEntries(d.dispositions.map((x) => [x.code, x.smartflo_status_id])) } }), "Outcome ids saved.")}>Save outcome ids</button>
          <span className="muted small">After an auto-dialled call, Smartflo needs the outcome to release the agent for the next customer.</span>
        </div>
      </section>
    </>
  );
}
