import { useEffect, useState } from "react";
import { api, inr, num } from "./api.js";

const PERIODS = [["1", "Today"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"]];
const pct = (v) => (v === null || v === undefined ? "—" : `${v}%`);
const day = (d) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

export default function Mis() {
  const [days, setDays] = useState("30");
  const [d, setD] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setD(null);
    api(`/mis?days=${days}`).then(setD).catch((e) => setError(e.message));
  }, [days]);

  if (error) return <p className="error">{error}</p>;
  if (!d) return <p className="muted">Loading…</p>;
  const maxDay = Math.max(1, ...d.daily.map((x) => x.calls));
  const maxOutcome = Math.max(1, ...d.outcomes.map((x) => x.count));
  const p = d.promises;

  return (
    <div className="stack">
      <div className="row">
        {PERIODS.map(([v, l]) => <button key={v} className={days === v ? "tab on" : "tab"} onClick={() => setDays(v)}>{l}</button>)}
        <span className="grow" />
        <span className="muted small">{day(d.period.from)} to {day(d.period.to)}</span>
      </div>

      <div className="kpis">
        <Kpi label="Calls logged" value={num(d.calls.total)} sub={`${num(d.calls.accounts_worked)} accounts worked`} />
        <Kpi label="Contact rate" value={pct(d.calls.contact_rate)} sub={`${num(d.calls.connected)} connected`} />
        <Kpi label="Promises kept" value={pct(p.kept_rate)} sub={`${num(p.kept)} of ${num(p.due)} due`} />
        <Kpi label="Promised amount" value={inr(p.promised_amount)} sub={`${inr(p.kept_amount)} kept`} />
        <Kpi label="Promises missed, not closed" value={num(p.missed_open)} />
      </div>

      <section className="card scroll">
        <h2 style={{ padding: "0 10px" }}>Portfolio by bucket (today)</h2>
        <table>
          <thead><tr><th>Bucket</th><th className="r">Accounts</th><th className="r">Overdue</th><th className="r">Outstanding principal</th><th className="r">Closure amount</th><th className="r">Not allocated</th></tr></thead>
          <tbody>
            {d.portfolio.map((b) => (
              <tr key={b.bucket}>
                <td>{b.label}</td><td className="r">{num(b.accounts)}</td><td className="r">{inr(b.overdue_amount)}</td>
                <td className="r">{inr(b.outstanding_principal)}</td><td className="r">{inr(b.closure_amount)}</td><td className="r">{num(b.unassigned)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card scroll">
        <h2 style={{ padding: "0 10px" }}>Agent productivity</h2>
        <table>
          <thead>
            <tr><th>Agent</th><th className="r">Accounts</th><th className="r">Overdue</th><th className="r">Worked</th><th className="r">Calls</th><th className="r">Connected</th><th className="r">Contact rate</th><th className="r">Promises</th><th className="r">Promise amount</th><th className="r">Kept</th><th className="r">Call-backs pending</th></tr>
          </thead>
          <tbody>
            {d.agents.map((a) => (
              <tr key={a.agent_id}>
                <td>{a.agent}</td><td className="r">{num(a.assigned_accounts)}</td><td className="r">{inr(a.assigned_overdue)}</td>
                <td className="r">{num(a.accounts_worked)}</td><td className="r">{num(a.calls)}</td><td className="r">{num(a.connected)}</td>
                <td className="r">{pct(a.contact_rate)}</td><td className="r">{num(a.promises)}</td><td className="r">{inr(a.promise_amount)}</td>
                <td className="r">{num(a.promises_kept)}</td><td className="r">{num(a.callbacks_pending)}</td>
              </tr>
            ))}
            {d.agents.length === 0 && <tr><td colSpan="11" className="muted">No active agents.</td></tr>}
          </tbody>
        </table>
      </section>

      <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <section className="card">
          <h2>Call outcomes</h2>
          <table>
            <tbody>
              {d.outcomes.map((o) => (
                <tr key={o.code}>
                  <td>{o.label}</td><td className="r">{num(o.count)}</td>
                  <td className="barcell"><div className="bar-fill" style={{ width: `${(o.count / maxOutcome) * 100}%` }} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card">
          <h2>Calls per day</h2>
          <div className="daybars" role="img" aria-label="Calls per day">
            {d.daily.map((x) => (
              <div key={x.date} className="daybar" title={`${day(x.date)}: ${x.calls} calls, ${x.connected} connected`}>
                <div className="bar-fill v" style={{ height: `${Math.max(2, (x.calls / maxDay) * 100)}%` }} />
              </div>
            ))}
          </div>
          <div className="row small muted"><span>{day(d.daily[0].date)}</span><span className="grow" /><span>Most in a day: {num(maxDay === 1 && d.calls.total === 0 ? 0 : maxDay)}</span><span className="grow" /><span>{day(d.daily[d.daily.length - 1].date)}</span></div>
        </section>
      </div>

      <p className="muted small">
        Contact rate = calls where the borrower or a reference was spoken to ÷ all calls logged. Promises kept = kept ÷ promises whose date fell in the period; one nobody closed counts as not kept.
        Collection efficiency (money actually received) is not shown: repayments are not synced from the LMS yet.
      </p>
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
