import { useEffect, useState } from "react";
import { api, inr, num } from "./api.js";

const PERIODS = [["1", "Today"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"]];
export const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);
const day = (d) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

// Head: every agent side by side, click a name for the detail. Agent (mine): the same numbers, only their own.
export default function Performance({ mine = false }) {
  const [days, setDays] = useState("7");
  const [agent, setAgent] = useState("");
  const [d, setD] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    api(`/performance?days=${days}${agent && !mine ? `&agent=${agent}` : ""}`).then(setD).catch((e) => setError(e.message));
  }, [days, agent, mine]);

  if (error) return <p className="error">{error}</p>;
  if (!d) return <p className="muted">Loading…</p>;
  const t = d.totals || d.rows[0];
  const det = d.detail;
  const maxDay = Math.max(1, ...(det ? det.daily.map((x) => x.calls) : [1]));
  const maxOut = Math.max(1, ...(det ? det.outcomes.map((x) => x.count) : [1]));

  return (
    <div className="stack">
      <div className="row">
        {PERIODS.map(([v, l]) => <button key={v} className={days === v ? "tab on" : "tab"} onClick={() => setDays(v)}>{l}</button>)}
        <span className="grow" />
        <span className="muted small">{day(d.period.from)} to {day(d.period.to)}</span>
      </div>

      {t && (
        <div className="kpis">
          <Kpi label="Calls logged" value={num(t.calls)} sub={`${num(t.connected)} connected`} />
          <Kpi label="Contact rate" value={pct(t.contact_rate)} />
          <Kpi label="Promises" value={num(t.promises)} sub={`${inr(t.promise_amount)} promised`} />
          <Kpi label="Messages sent" value={num(d.totals ? t.messages : t.sms_sent + t.whatsapp_sent)} />
          <Kpi label="Payment links paid" value={num(t.links_paid)} />
        </div>
      )}

      {!mine && (
        <section className="card scroll">
          <h2 style={{ padding: "0 10px" }}>Agent scorecard</h2>
          <table>
            <thead>
              <tr><th>Agent</th><th className="r">Worked</th><th className="r">Calls</th><th className="r">Connected</th><th className="r">Contact rate</th><th className="r">Dialled from CRM</th><th className="r">Promises</th><th className="r">Promise amount</th><th className="r">Kept</th><th className="r">SMS</th><th className="r">WhatsApp</th><th className="r">Links paid / made</th></tr>
            </thead>
            <tbody>
              {d.rows.map((a) => (
                <tr key={a.agent_id} className={String(a.agent_id) === String(agent) ? "on" : ""}>
                  <td><button className="ghost" onClick={() => setAgent(String(a.agent_id) === String(agent) ? "" : a.agent_id)}>{a.agent}</button></td>
                  <td className="r">{num(a.accounts_worked)}</td><td className="r">{num(a.calls)}</td><td className="r">{num(a.connected)}</td><td className="r">{pct(a.contact_rate)}</td>
                  <td className="r">{num(a.calls_placed)}</td><td className="r">{num(a.promises)}</td><td className="r">{inr(a.promise_amount)}</td><td className="r">{num(a.promises_kept)}</td>
                  <td className="r">{num(a.sms_sent)}</td><td className="r">{num(a.whatsapp_sent)}</td><td className="r">{num(a.links_paid)} / {num(a.links_made)}</td>
                </tr>
              ))}
              {d.rows.length === 0 && <tr><td colSpan="12" className="muted">No active agents.</td></tr>}
            </tbody>
          </table>
        </section>
      )}

      {det ? (
        <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
          <section className="card">
            <h2>{mine ? "My call outcomes" : `${det.agent}: call outcomes`}</h2>
            <table>
              <tbody>
                {det.outcomes.map((o) => (
                  <tr key={o.code}><td>{o.label}</td><td className="r">{num(o.count)}</td><td className="barcell"><div className="bar-fill" style={{ width: `${(o.count / maxOut) * 100}%` }} /></td></tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="card">
            <h2>Calls per day</h2>
            <div className="daybars" role="img" aria-label="Calls per day">
              {det.daily.map((x) => (
                <div key={x.date} className="daybar" title={`${day(x.date)}: ${x.calls} calls, ${x.connected} connected`}>
                  <div className="bar-fill v" style={{ height: `${Math.max(2, (x.calls / maxDay) * 100)}%` }} />
                </div>
              ))}
            </div>
            <div className="row small muted"><span>{day(det.daily[0].date)}</span><span className="grow" /><span>{day(det.daily[det.daily.length - 1].date)}</span></div>
          </section>
        </div>
      ) : !mine && <p className="muted small">Click an agent's name to see their outcomes and calls per day.</p>}

      <p className="muted small">Calls are the outcomes agents logged. "Dialled from CRM" counts calls placed from the account screen. Money received is not shown until repayments are synced from the LMS.</p>
    </div>
  );
}

function Kpi({ label, value, sub }) {
  return (
    <div className="card kpi">
      <div className="muted small">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="muted small">{sub}</div>}
    </div>
  );
}
