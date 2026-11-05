import { useCallback, useEffect, useRef, useState } from "react";
import { api, inr, num } from "./api.js";
import Account from "./Account.jsx";

const clock = (t) => new Date(t).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });

// The agent's auto-dialer: start a campaign, the CRM sends this agent's customers to Smartflo, and Smartflo rings them one by one.
export default function Dialer() {
  const [status, setStatus] = useState(null);
  const [pools, setPools] = useState([]);
  const [session, setSession] = useState(null);
  const [campaign, setCampaign] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [push, setPush] = useState(null); // result of the last send to Smartflo: {sent, total, error}
  const [open, setOpen] = useState(null);
  const shown = useRef(null); // id of the account the drawer was last opened for, so a new call opens it once

  const refresh = useCallback(() => api("/dialer/session").then(setSession).catch((e) => setError(e.message)), []);
  useEffect(() => {
    api("/dialer/status").then(setStatus).catch((e) => setError(e.message));
    api("/dialer/pools").then((d) => setPools(d.pools)).catch(() => {});
    refresh();
  }, [refresh]);
  useEffect(() => {
    if (!session?.active) return undefined;
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, [session?.active, refresh]);
  useEffect(() => {
    const cur = session?.current;
    if (cur && shown.current !== cur.id && session.awaiting_outcome) { shown.current = cur.id; setOpen(cur); }
  }, [session]);

  async function start(e) {
    e.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try {
      const r = await api("/dialer/session/start", { method: "POST", body: { campaign, smartflo_password: password } });
      setPassword("");
      setPush(r.push);
      shown.current = null;
      await refresh();
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  async function sendAgain() {
    setError(""); setBusy(true);
    try { setPush(await api("/dialer/session/push", { method: "POST" })); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  async function stop() {
    setError("");
    try { await api("/dialer/session/stop", { method: "POST" }); setOpen(null); await refresh(); }
    catch (err) { setError(err.message); }
  }
  async function dismiss() {
    try { await api("/dialer/session/dismiss", { method: "POST" }); setOpen(null); await refresh(); }
    catch (err) { setError(err.message); }
  }
  async function pick(c) {
    try { setOpen(await api(`/loans/${c.id}`)); } catch (err) { setError(err.message); }
  }
  const afterOutcome = (res) => {
    setNotice(res.released ? "Outcome saved. Smartflo will ring your next customer." : res.warning);
    refresh();
  };

  if (!status) return <p className="muted">Loading…</p>;
  const startable = pools.filter((p) => p.active);
  const s = session;

  return (
    <div className="stack">
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      {!s?.active && (
        <form className="card" onSubmit={start}>
          <h2>Start a campaign</h2>
          {!status.enabled && <p className="error">The dialler is switched off on this server.</p>}
          {status.enabled && !status.agent_ready && <p className="error">Your Smartflo login is not set up yet. Ask the Admin to add it under Campaigns, Setup.</p>}
          {status.enabled && !status.in_window && <p className="error">Calling is only allowed between {status.window}.</p>}
          {s?.expired && <p className="muted">Your Smartflo session ran out (they last about an hour). Start again to keep calling.</p>}
          <label>Campaign
            <select value={campaign} onChange={(e) => setCampaign(e.target.value)} required>
              <option value="">Select…</option>
              {startable.map((p) => <option key={p.key} value={p.key}>{p.label} · {p.mine} of your customers</option>)}
            </select>
          </label>
          {startable.length === 0 && <p className="muted small">No campaign is switched on yet.</p>}
          <label>Your Smartflo password
            <input type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          <p className="muted small">Used once to sign you in to Smartflo. The CRM never stores it. When you start, your customers in this campaign are sent to Smartflo and it begins ringing them for you. Log an outcome after every call to get the next one.</p>
          <button disabled={busy || !status.enabled || !status.agent_ready || !status.in_window}>{busy ? "Starting…" : "Start campaign"}</button>
        </form>
      )}

      {s?.active && (
        <>
          <section className="card">
            <div className="row">
              <div>
                <h2 style={{ margin: 0 }}>{s.campaign.label}</h2>
                <span className="muted small">Smartflo signs you out around {clock(s.expires_at)}; start again then.</span>
              </div>
              <span className="grow" />
              <button onClick={stop}>Stop campaign</button>
            </div>
            {push && !push.error && (
              <p role="status" className="small">{push.sent > 0 ? `${num(push.sent)} of your customers were sent to Smartflo.`
                : push.total === 0 ? "You have no new customers in this campaign, so nothing was sent. Smartflo dials whoever is already in its list." : ""}</p>
            )}
            {push?.error && <p className="error" role="alert">Smartflo took {num(push.sent)} of {num(push.total)} customers. {push.error}</p>}
            <div className="row"><button className="ghost" disabled={busy} onClick={sendAgain}>Send my new customers to Smartflo</button></div>
            {s.warning && <p className="error" role="alert">{s.warning}</p>}
            {s.unparsed_fields?.length > 0 && (
              <p className="muted small">Smartflo sent call details in a format the CRM does not recognise yet (fields: {s.unparsed_fields.join(", ")}). Tell the Admin.</p>
            )}
          </section>

          {!s.current && !s.call && <p className="card muted">Waiting for the next call. Smartflo dials the next customer by itself.</p>}

          {s.current && (
            <section className="card">
              <div className="row">
                <span className={`chip ${s.call ? "today" : "ok"}`}>{s.call ? `On a call${s.call.state ? ` · ${s.call.state}` : ""}` : "Call ended"}</span>
                <span className="grow" />
                <button className="ghost" onClick={() => setOpen(s.current)}>Open account</button>
              </div>
              <dl>
                <dt>Borrower</dt><dd>{s.current.borrower_name || "—"}</dd>
                <dt>Account</dt><dd>{s.current.account_id}</dd>
                <dt>Overdue</dt><dd>{inr(s.current.overdue_amount)} · {s.current.dpd} DPD</dd>
                <dt>Closure</dt><dd>{inr(s.current.closure_amount)}</dd>
                <dt>Allocated to</dt><dd>{s.current.agent ? s.current.agent.name : "Nobody yet"}</dd>
              </dl>
              {s.candidates?.length > 1 && (
                <p className="small">This number is on {s.candidates.length} accounts:{" "}
                  {s.candidates.map((c) => <button key={c.id} className="ghost" onClick={() => pick(c)}>{c.account_id}</button>)}
                </p>
              )}
            </section>
          )}

          {s.call && !s.current && <p className="card">On a call with a number that is not on any CRM account. You can end the call normally.</p>}

          {s.awaiting_outcome && (
            <section className="card">
              <p><strong>Log the outcome to get your next call.</strong></p>
              <button className="ghost" onClick={dismiss}>No outcome needed (nobody answered)</button>
            </section>
          )}
        </>
      )}

      {open && (
        <Account loan={open} onClose={() => setOpen(null)} onChanged={() => refresh()}
                 outcomePath={s?.active ? (id) => `/dialer/session/outcome/${id}` : undefined} onDialer={afterOutcome} />
      )}
    </div>
  );
}
