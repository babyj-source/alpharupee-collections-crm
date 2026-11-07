import { useCallback, useEffect, useState } from "react";
import { api, num } from "./api.js";

const hm = (t) => (t ? new Date(t).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "");

// The collection head's view of dialling: live monitoring and the campaigns themselves. Agents start campaigns from their own Campaigns screen.
export default function Campaigns({ user }) {
  const [tab, setTab] = useState("live");
  const admin = user.role === "admin";
  return (
    <div className="stack">
      <div className="row">
        <button className={tab === "live" ? "tab on" : "tab"} onClick={() => setTab("live")}>Live</button>
        <button className={tab === "campaigns" ? "tab on" : "tab"} onClick={() => setTab("campaigns")}>Campaigns</button>
        {admin && <button className={tab === "setup" ? "tab on" : "tab"} onClick={() => setTab("setup")}>Setup</button>}
      </div>
      {tab === "live" && <Live />}
      {tab === "campaigns" && <CampaignList admin={admin} />}
      {tab === "setup" && admin && <Setup />}
    </div>
  );
}

const STATE = { on_call: ["On a call", "ok"], waiting: ["Waiting for a call", ""], idle: ["Screen idle", "today"] };

function Live() {
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let on = true;
    const load = () => api("/dialer/live").then((r) => { if (on) { setD(r); setError(""); } }).catch((e) => on && setError(e.message));
    load();
    const t = setInterval(load, 6000);
    return () => { on = false; clearInterval(t); };
  }, []);
  if (!d) return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
  return (
    <>
      {error && <p className="error" role="alert">{error}</p>}
      {!d.enabled && <p className="card muted">The dialler is switched off on this server, so no one can start a campaign. Nothing below will move.</p>}
      <p className="muted small">Updates every few seconds · last {hm(d.as_of)}. Agents start campaigns from their own Campaigns screen, which sends their customers to Smartflo.</p>

      <section className="card scroll">
        <h2 style={{ padding: "0 10px" }}>Campaigns today</h2>
        <table>
          <thead><tr><th>Campaign</th><th>Status</th><th className="r">Waiting</th><th className="r">Sent to Smartflo</th><th className="r">Called</th><th className="r">Connected</th><th className="r">Promises</th><th className="r">Paid</th></tr></thead>
          <tbody>
            {d.campaigns.map((c) => (
              <tr key={c.key}>
                <td>{c.label}</td>
                <td>{c.active ? <span className="chip ok">On</span> : <span className="chip">Paused</span>}</td>
                <td className="r">{num(c.waiting)}</td><td className="r">{num(c.pushed_today)}</td><td className="r">{num(c.called)}</td>
                <td className="r">{num(c.connected)}</td><td className="r">{num(c.promises)}</td><td className="r">{num(c.paid)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small" style={{ padding: "0 10px" }}>Called, connected, promises and paid count the customers sent today who have an outcome logged today.</p>
      </section>

      <section className="card scroll">
        <h2 style={{ padding: "0 10px" }}>Agents dialling now</h2>
        <table>
          <thead><tr><th>Agent</th><th>Campaign</th><th>State</th><th>On account</th><th>Started</th><th className="r">Outcomes today</th></tr></thead>
          <tbody>
            {d.agents.map((a, i) => (
              <tr key={i}>
                <td>{a.name}</td><td>{a.campaign}</td>
                <td><span className={`chip ${STATE[a.state][1]}`}>{STATE[a.state][0]}</span></td>
                <td>{a.account_id ? `${a.account_id} · ${a.borrower_name}` : "—"}</td>
                <td>{hm(a.started_at)}</td><td className="r">{num(a.outcomes_today)}</td>
              </tr>
            ))}
            {d.agents.length === 0 && <tr><td colSpan="6" className="muted">Nobody has started a campaign.</td></tr>}
          </tbody>
        </table>
      </section>

      <section className="card scroll">
        <h2 style={{ padding: "0 10px" }}>Latest outcomes</h2>
        <table>
          <thead><tr><th>Time</th><th>Agent</th><th>Account</th><th>Customer</th><th>Outcome</th></tr></thead>
          <tbody>
            {d.feed.map((f, i) => (
              <tr key={i}>
                <td>{hm(f.at)}</td><td>{f.agent}</td><td>{f.account_id}</td><td>{f.borrower_name}</td>
                <td><span className={`chip ${f.connected ? "ok" : ""}`}>{f.outcome}</span></td>
              </tr>
            ))}
            {d.feed.length === 0 && <tr><td colSpan="5" className="muted">No outcomes logged yet today.</td></tr>}
          </tbody>
        </table>
      </section>
    </>
  );
}

const BLANK = { label: "", dpd_min: "0", dpd_max: "", min_overdue: "0", product_code: "", hold_promises: true, hold_callbacks: true,
  smartflo_campaign_id: "", smartflo_list_id: "", active: false };
const digits = (v) => v.replace(/\D/g, "");

function CampaignList({ admin }) {
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState(null); // null = closed; {key?: ..., ...fields}

  const load = useCallback(() => api("/dialer/pools").then(setD).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const run = async (fn, ok) => {
    setError(""); setNotice("");
    try { await fn(); setNotice(ok); setForm(null); await load(); } catch (e) { setError(e.message); }
  };
  const body = (f, custom) => ({
    smartflo_campaign_id: f.smartflo_campaign_id, smartflo_list_id: f.smartflo_list_id, active: f.active,
    ...(custom ? { label: f.label, rules: { dpd_min: f.dpd_min, dpd_max: f.dpd_max, min_overdue: f.min_overdue, product_code: f.product_code,
      hold_promises: f.hold_promises, hold_callbacks: f.hold_callbacks } } : {}),
  });
  const edit = (c) => setForm({ key: c.key, custom: c.custom, label: c.label, ...(c.rules ? {
    dpd_min: String(c.rules.dpd_min ?? 0), dpd_max: c.rules.dpd_max == null ? "" : String(c.rules.dpd_max), min_overdue: String(c.rules.min_overdue ?? 0),
    product_code: c.rules.product_code || "", hold_promises: c.rules.hold_promises, hold_callbacks: c.rules.hold_callbacks } : {}),
    smartflo_campaign_id: c.smartflo_campaign_id, smartflo_list_id: c.smartflo_list_id, active: c.active });
  const toggle = (c) => run(() => api(`/dialer/campaigns/${c.key}`, { method: "PUT", body: { active: !c.active } }), `${c.label} ${c.active ? "paused" : "switched on"}.`);
  const save = (e) => {
    e.preventDefault();
    if (form.key) return run(() => api(`/dialer/campaigns/${form.key}`, { method: "PUT", body: body(form, form.custom) }), `${form.label} saved.`);
    return run(() => api("/dialer/campaigns", { method: "POST", body: body({ ...form }, true) }), `${form.label} created.`);
  };
  const [making, setMaking] = useState("");
  const makeInSmartflo = async (c) => {
    if (!window.confirm(`Create the lead list and the dialler campaign for “${c.label}” in Smartflo now? They are named “CRM ${c.label}”.`)) return;
    setError(""); setNotice(""); setMaking(c.key);
    try {
      const r = await api(`/dialer/smartflo/campaign/${c.key}`, { method: "POST" });
      setNotice(`${c.label}: ${r.steps.join(" ")} Review the campaign settings in Smartflo, then switch it on here.`);
      await load();
    } catch (e) { setError(e.message); await load(); } finally { setMaking(""); }
  };
  const remove = (c) => window.confirm(`Delete “${c.label}”?`) && run(() => api(`/dialer/campaigns/${c.key}`, { method: "DELETE" }), `${c.label} deleted.`);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  if (!d) return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
  return (
    <>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!d.enabled && <p className="card muted">The dialler is switched off on this server. You can still set campaigns up.</p>}

      {form && (
        <form className="card" onSubmit={save} aria-label="Campaign form">
          <h2>{form.key ? `Edit ${form.label}` : "New campaign"}</h2>
          {(!form.key || form.custom) ? (
            <>
              <label>Name<input value={form.label} onChange={set("label")} maxLength={60} required /></label>
              <div className="row">
                <label>Days past due from<input inputMode="numeric" value={form.dpd_min} onChange={(e) => setForm({ ...form, dpd_min: digits(e.target.value) })} /></label>
                <label>to (blank = no limit)<input inputMode="numeric" value={form.dpd_max} onChange={(e) => setForm({ ...form, dpd_max: digits(e.target.value) })} /></label>
                <label>Overdue at least (₹)<input inputMode="numeric" value={form.min_overdue} onChange={(e) => setForm({ ...form, min_overdue: digits(e.target.value) })} /></label>
                <label>Product code (optional)<input value={form.product_code} onChange={set("product_code")} maxLength={40} /></label>
              </div>
              <label className="row"><input type="checkbox" checked={form.hold_promises} onChange={set("hold_promises")} /> Leave out customers with a promise still to come</label>
              <label className="row"><input type="checkbox" checked={form.hold_callbacks} onChange={set("hold_callbacks")} /> Leave out customers with a call-back already booked</label>
              <p className="muted small">Always left out: closed loans, paid, in dispute, nothing overdue, no valid mobile. A customer can be in more than one campaign if the rules overlap.</p>
            </>
          ) : <p className="muted small">This is a built-in campaign: its customers are chosen automatically. You can change its Smartflo ids and switch it on or off.</p>}
          <div className="row">
            <label>Smartflo campaign id<input inputMode="numeric" value={form.smartflo_campaign_id} onChange={(e) => setForm({ ...form, smartflo_campaign_id: digits(e.target.value) })} /></label>
            <label>Smartflo lead-list id<input inputMode="numeric" value={form.smartflo_list_id} onChange={(e) => setForm({ ...form, smartflo_list_id: digits(e.target.value) })} /></label>
          </div>
          <label className="row"><input type="checkbox" checked={form.active} onChange={set("active")} /> Agents can start this campaign</label>
          <p className="muted small">Create the campaign and its lead list in Smartflo first, then enter their ids. The lead list needs three columns in this order: Mobile, Name, Account ID.</p>
          <div className="row"><button>{form.key ? "Save" : "Create campaign"}</button><button type="button" className="ghost" onClick={() => setForm(null)}>Cancel</button></div>
        </form>
      )}

      <div className="row"><span className="grow" /><button onClick={() => { setNotice(""); setForm({ ...BLANK }); }}>New campaign</button></div>
      <div className="card scroll">
        <table>
          <thead><tr><th>Campaign</th><th className="r">Customers waiting</th><th>Smartflo ids</th><th>Agents can start</th><th /></tr></thead>
          <tbody>
            {d.pools.map((c) => (
              <tr key={c.key}>
                <td>{c.label}{c.custom && <span className="chip" style={{ marginLeft: 6 }}>Custom</span>}{c.summary && <div className="muted small" style={{ maxWidth: 340, whiteSpace: "normal" }}>{c.summary}</div>}</td>
                <td className="r">{num(c.eligible)}</td>
                <td>{c.configured ? <span className="chip ok">Set</span> : <span className="chip today">Not set</span>}</td>
                <td>{c.active ? <span className="chip ok">Yes</span> : <span className="chip">Paused</span>}</td>
                <td>
                  <button className="ghost" onClick={() => toggle(c)}>{c.active ? "Pause" : "Switch on"}</button>{" "}
                  {admin && !c.configured && <><button disabled={!!making} onClick={() => makeInSmartflo(c)}>{making === c.key ? "Creating…" : "Create in Smartflo"}</button>{" "}</>}
                  <button className="ghost" onClick={() => edit(c)}>Edit</button>
                  {c.custom && <>{" "}<button className="ghost" onClick={() => remove(c)}>Delete</button></>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Customers reach Smartflo when an agent starts a campaign: only the customers allocated to that agent are sent, and nobody is sent twice in a day.
        The built-in campaigns never overlap: a call-back due comes first, then a promise due or missed, then the days-past-due bucket.
      </p>
    </>
  );
}

function Setup() {
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [check, setCheck] = useState(null);
  const [users, setUsers] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api("/dialer/setup").then(setD).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const save = async (fn, ok) => {
    setError(""); setNotice("");
    try { await fn(); setNotice(ok); } catch (e) { setError(e.message); } // no reload: it would wipe edits made in other rows
  };
  if (!d) return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
  const loadUsers = async () => {
    setError(""); setNotice("");
    try { setUsers((await api("/dialer/smartflo/users")).users); } catch (e) { setError(e.message); }
  };
  const pickUser = (i, uid) => {
    const u = (users || []).find((x) => String(x.user_id) === uid);
    if (!u) return;
    setD((cur) => ({ ...cur, agents: cur.agents.map((x, j) => (j === i ? { ...x, smartflo_email: u.login_id, agent_number: u.agent_number } : x)) }));
  };
  const makeOutcomes = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const r = await api("/dialer/smartflo/outcomes", { method: "POST" });
      setNotice(r.steps.join(" "));
      await load();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const edit = (list, key, idx, field, value) => setD({ ...d, [list]: d[list].map((x, i) => (i === idx ? { ...x, [field]: value } : x)) });

  return (
    <>
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <section className="card">
        <h2>Connection</h2>
        <p>Switch: <strong>{d.status.enabled ? "ON" : "OFF"}</strong> · Account token on the server: <strong>{d.status.token_set ? "set" : "not set"}</strong> · Outgoing number: <strong>{d.status.caller_id_set ? "set" : "not set"}</strong> · Outcomes list in Smartflo: <strong>{d.status.outcome_list_id ? "made" : "not made"}</strong></p>
        <p className="muted small">The switch (<code>SMARTFLO_ENABLED</code>) and the token (<code>SMARTFLO_API_TOKEN</code>) are set on the server, never in this screen.</p>
        <div className="row">
          <button onClick={async () => setCheck(await api("/dialer/check", { method: "POST" }))}>Test connection</button>
          {check && <span className={check.ok ? "" : "error"}>{check.ok ? `Connected. Smartflo replied with: ${check.fields.join(", ") || "(empty)"}` : check.detail}</span>}
        </div>
      </section>

      <section className="card scroll">
        <h2 style={{ padding: "0 10px" }}>Agents in Smartflo</h2>
        <div className="row" style={{ padding: "0 10px" }}>
          <button className="ghost" onClick={loadUsers}>Load Smartflo users</button>
          <span className="muted small">{users ? `${users.length} Smartflo user(s) loaded. Pick one in each row, then Save.` : "Fetches the users in your Smartflo account so you can pick instead of typing."}</span>
        </div>
        <table>
          <thead><tr><th>CRM agent</th>{users && <th>Smartflo user</th>}<th>Smartflo login id</th><th>Smartflo agent number</th><th /></tr></thead>
          <tbody>
            {d.agents.map((a, i) => (
              <tr key={a.id}>
                <td>{a.name}<div className="muted small">{a.email}</div></td>
                {users && (
                  <td>
                    <select value="" onChange={(e) => pickUser(i, e.target.value)} aria-label={`${a.name} Smartflo user`}>
                      <option value="">Pick…</option>
                      {users.map((u) => <option key={u.user_id} value={u.user_id}>{u.name || u.login_id} · {u.login_id}</option>)}
                    </select>
                  </td>
                )}
                <td><input value={a.smartflo_email} autoComplete="off" onChange={(e) => edit("agents", a.id, i, "smartflo_email", e.target.value.replace(/\s/g, ""))} aria-label={`${a.name} Smartflo login id`} /></td>
                <td><input inputMode="numeric" value={a.agent_number} onChange={(e) => edit("agents", a.id, i, "agent_number", e.target.value.replace(/\D/g, ""))} aria-label={`${a.name} agent number`} /></td>
                <td><button onClick={() => save(() => api(`/dialer/agents/${a.id}`, { method: "PUT", body: { smartflo_email: a.smartflo_email, agent_number: a.agent_number } }), `${a.name} saved.`)}>Save</button></td>
              </tr>
            ))}
            {d.agents.length === 0 && <tr><td colSpan="5" className="muted">No active agents yet. Add them under Users and roles.</td></tr>}
          </tbody>
        </table>
        <p className="muted small" style={{ padding: "0 10px" }}>The login id is what the agent types to sign in to Smartflo; the agent number is the one Smartflo rings for click-to-call (usually the same long number starting with 0). The Smartflo password is never stored: each agent types it when they start a campaign.</p>
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
          <button className="ghost" disabled={busy || !d.status.enabled} onClick={makeOutcomes}>{busy ? "Creating…" : d.status.outcome_list_id ? "Re-check outcomes in Smartflo" : "Create outcomes in Smartflo"}</button>
        </div>
        <p className="muted small" style={{ padding: "0 10px" }}>After an auto-dialled call, Smartflo needs the outcome to release the agent for the next customer. “Create outcomes in Smartflo” makes a list called “CRM call outcomes” with these {d.dispositions.length} outcomes and fills in the ids. Do this once, before creating campaigns in Smartflo.</p>
      </section>
    </>
  );
}
