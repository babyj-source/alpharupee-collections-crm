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
  const [sf, setSf] = useState(null); // what Smartflo's own lead list says about this agent's customers
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
    // The customer window opens by itself once per call: when the customer picks up, or (if the page missed that) when the call ends and an outcome is owed.
    const cur = session?.current;
    if (!cur) return;
    const row = (session.queue || []).find((r) => r.id === cur.id);
    const key = `${cur.id}:${row ? row.attempts : 0}`;
    const connected = session.call?.phase === "connected";
    if (shown.current !== key && (connected || (!session.call && session.awaiting_outcome))) { shown.current = key; setOpen(cur); }
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
  async function resend() {
    if (!window.confirm("Clear your customers in Smartflo and send them again from scratch? Use this when Smartflo is not ringing customers it already tried today.")) return;
    setError(""); setNotice(""); setBusy(true);
    try {
      const r = await api("/dialer/session/resend", { method: "POST" });
      setPush(r);
      setNotice(r.error ? "" : `${num(r.sent)} of your customers were sent to Smartflo again.${r.warning ? " " + r.warning : ""}`);
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  async function checkSmartflo() {
    setError(""); setBusy(true);
    try { setSf(await api("/dialer/session/leads")); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  async function endCall(loanId) {
    setError("");
    try { await api("/dialer/call/hangup", { method: "POST", body: { loan_id: loanId } }); refresh(); }
    catch (err) { setError(err.message); }
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
  const s = session;

  return (
    <div className="stack">
      {error && <p className="error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      {!s?.active && (
        <>
          <section className="card">
            <h2>Campaigns</h2>
            {!status.enabled && <p className="error">The dialler is switched off on this server.</p>}
            {status.enabled && !status.agent_ready && <p className="error">Your Smartflo login id is not set up yet. Ask the Admin to add it under Campaigns, Setup.</p>}
            {status.enabled && !status.in_window && <p className="error">Calling is only allowed between {status.window}.</p>}
            {s?.expired && <p className="muted">Your Smartflo session ran out (they last about an hour). Start again to keep calling.</p>}
            <p className="muted small">Pick a campaign, then start it. Your own customers in that campaign are sent to Smartflo, and it rings them one by one.</p>
            <ul className="camps" role="radiogroup" aria-label="Campaigns">
              {pools.map((p) => {
                const ready = p.active && p.configured;
                const why = !p.active ? "Switched off by your head" : !p.configured ? "Not connected to Smartflo yet. Ask the Admin." : "";
                return (
                  <li key={p.key} className={`camp${campaign === p.key ? " on" : ""}${ready ? "" : " off"}`}>
                    <label>
                      <input type="radio" name="campaign" value={p.key} checked={campaign === p.key} disabled={!ready} onChange={() => setCampaign(p.key)} />
                      <span className="camp-main">
                        <strong>{p.label}</strong>
                        <span className="muted small">{num(p.mine)} of your customers · {num(p.eligible)} in all</span>
                      </span>
                      {ready ? <span className="chip ok">Ready</span> : <span className="chip today">{why}</span>}
                    </label>
                  </li>
                );
              })}
            </ul>
            {pools.length === 0 && <p className="muted small">No campaigns found.</p>}
            {pools.length > 0 && !pools.some((p) => p.active && p.configured) && (
              <p className="muted small">No campaign is ready yet. Your head switches a campaign on, and the Admin connects it to Smartflo, under Campaigns.</p>
            )}
          </section>
          <form className="card" onSubmit={start}>
            <label>Your Smartflo password
              <input type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            <p className="muted small">Used once to sign you in to Smartflo. The CRM never stores it. Log an outcome after every call to get the next one.</p>
            <button disabled={busy || !campaign || !status.enabled || !status.agent_ready || !status.in_window}>{busy ? "Starting…" : campaign ? `Start ${pools.find((p) => p.key === campaign)?.label || "campaign"}` : "Pick a campaign first"}</button>
          </form>
        </>
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
            {s.warning && <p className="error" role="alert">{s.warning}</p>}
            {s.unparsed_fields?.length > 0 && (
              <p className="muted small">Smartflo sent call details in a format the CRM does not recognise yet (fields: {s.unparsed_fields.join(", ")}). Tell the Admin.</p>
            )}
            {s.stats && (
              <div className="stats" aria-label="Campaign progress">
                <div><strong>{num(s.stats.total)}</strong><span className="muted small">In the list</span></div>
                <div><strong>{num(s.stats.waiting)}</strong><span className="muted small">Waiting</span></div>
                <div><strong>{num(s.stats.calling)}</strong><span className="muted small">Calling now</span></div>
                <div><strong>{num(s.stats.done)}</strong><span className="muted small">Done</span></div>
                <div><strong>{num(s.stats.no_answer)}</strong><span className="muted small">Not answered</span></div>
                <div><strong>{num(s.stats.pending)}</strong><span className="muted small">Outcome owed</span></div>
              </div>
            )}
            <div className="row">
              <button className="ghost" disabled={busy} onClick={sendAgain}>Send my new customers to Smartflo</button>
              <button className="ghost" disabled={busy} onClick={resend}>Send my customers again from scratch</button>
              <button className="ghost" disabled={busy} onClick={checkSmartflo}>Check what Smartflo sees</button>
            </div>
            {sf && (
              <div className="tablewrap">
                <table className="queue">
                  <thead><tr><th>Customer</th><th>Account</th><th>Smartflo status</th><th>Dialler status</th><th>Calls</th><th>Retries</th></tr></thead>
                  <tbody>
                    {sf.leads.map((l) => (
                      <tr key={l.account_id}><td>{l.borrower_name || "—"}</td><td>{l.account_id}</td><td>{l.dial_status}</td><td>{l.dialer_status}</td><td>{l.call_count ?? "—"}</td><td>{l.retry_count ?? "—"}</td></tr>
                    ))}
                  </tbody>
                </table>
                {sf.missing.length > 0 && <p className="error small">Not found in Smartflo's list: {sf.missing.join(", ")}. Use "Send my customers again from scratch".</p>}
              </div>
            )}
            <p className="muted small">Smartflo does not ring a customer it has already tried today until its retry wait is over. If the agent line rings but no customer is dialled, use "from scratch".</p>
          </section>

          <section className={`card nowcall${s.call ? " live" : ""}`} aria-live="polite">
            {!s.current && !s.call && <p className="muted">Waiting for Smartflo to dial the next customer. It starts by itself; this page updates every few seconds.</p>}
            {s.call && !s.current && <p>On a call with a number that is not on any CRM account. You can end the call normally.</p>}
            {s.current && (
              <>
                <div className="row">
                  <span className={`chip ${s.call?.phase === "connected" ? "ok" : s.call ? "today" : ""}`}>
                    {!s.call ? "Call ended" : s.call.phase === "connected" ? `Connected${s.call.seconds ? ` · ${Math.floor(s.call.seconds / 60)}:${String(s.call.seconds % 60).padStart(2, "0")}` : ""}` : "Ringing…"}
                  </span>
                  <strong>{s.current.borrower_name || "—"}</strong>
                  <span className="muted small">{s.current.account_id} · {inr(s.current.overdue_amount)} · {s.current.dpd} DPD</span>
                  <span className="grow" />
                  {s.call && <button className="danger" onClick={() => endCall(s.current.id)}>End call</button>}
                  <button className="ghost" onClick={() => setOpen(s.current)}>Open customer</button>
                </div>
                {s.candidates?.length > 1 && (
                  <p className="small">This number is on {s.candidates.length} accounts:{" "}
                    {s.candidates.map((c) => <button key={c.id} className="ghost" onClick={() => pick(c)}>{c.account_id}</button>)}
                  </p>
                )}
              </>
            )}
            {s.awaiting_outcome && (
              <div className="outcome-owed" role="alert">
                <strong>Log the outcome now. Smartflo will not ring your next customer until you do.</strong>
                <div className="row">
                  {s.current && !s.call && <button onClick={() => setOpen(s.current)}>Log outcome for {s.current.borrower_name || s.current.account_id}</button>}
                  <span className="grow" />
                  <button className="ghost" onClick={dismiss}>No outcome needed (nobody answered)</button>
                </div>
              </div>
            )}
          </section>

          <section className="card">
            <h3 style={{ marginTop: 0 }}>Customers in this campaign</h3>
            {(s.queue || []).length === 0 && <p className="muted small">No customers of yours are in this campaign today.</p>}
            {(s.queue || []).length > 0 && (
              <div className="tablewrap">
                <table className="queue">
                  <thead><tr><th>Customer</th><th>Account</th><th>DPD</th><th>Overdue</th><th>Status</th><th>Tries</th><th>Last</th></tr></thead>
                  <tbody>
                    {s.queue.map((r) => (
                      <tr key={r.id} className={`q-${r.status}`}>
                        <td>{r.borrower_name || "—"}</td>
                        <td>{r.account_id}</td>
                        <td>{r.dpd}</td>
                        <td>{inr(r.overdue_amount)}</td>
                        <td><span className={`chip ${r.status === "on_call" ? "ok" : r.status === "calling" || r.status === "outcome_pending" ? "today" : ""}`}>{r.status_text}</span></td>
                        <td>{r.attempts || "—"}</td>
                        <td>{r.last_at ? clock(r.last_at) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {(s.log || []).length > 0 && (
            <section className="card">
              <h3 style={{ marginTop: 0 }}>Calls in this session</h3>
              <ul className="calllog">
                {s.log.map((l) => (
                  <li key={l.id}><span className="muted small">{clock(l.at)}</span> <strong>{l.borrower_name || l.account_id || "Unknown number"}</strong> <span className="muted small">{l.account_id}</span> <span className="chip">{l.result}</span></li>
                ))}
              </ul>
            </section>
          )}
          {s.live && (
            <p className="muted small">Live-call check: Smartflo reported {s.live.seen} call{s.live.seen === 1 ? "" : "s"} for you ({s.live.via === "agent" ? "by your agent number" : s.live.via === "account" ? "by your customers' numbers" : "none found"}).</p>
          )}
        </>
      )}

      {open && (
        <Account loan={open} onClose={() => setOpen(null)} onChanged={() => refresh()}
                 outcomePath={s?.active ? (id) => `/dialer/session/outcome/${id}` : undefined} onDialer={afterOutcome}
                 dialCall={s?.call && s?.current && s.current.id === open.id ? s.call : null} />
      )}
    </div>
  );
}
