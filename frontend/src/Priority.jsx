import { useCallback, useEffect, useState } from "react";
import { api, inr, isHead } from "./api.js";
import Account from "./Account.jsx";

const BUCKETS = [["", "All buckets"], ["dpd0", "0 DPD"], ["dpd1_7", "1-7 DPD"], ["dpd8_15", "8-15 DPD"], ["dpd16", "16+ DPD"]];
const tone = (r) => (["Call-back due", "Promise missed", "Promise due today"].includes(r) ? "bad" : r === "Promise given" || r === "Called today" || r === "In dispute" ? "" : "today");

export default function Priority({ user }) {
  const head = isHead(user);
  const [bucket, setBucket] = useState("");
  const [agent, setAgent] = useState("");
  const [agents, setAgents] = useState([]);
  const [data, setData] = useState({ count: 0, results: [] });
  const [open, setOpen] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => { if (head) api("/agents").then(setAgents).catch(() => {}); }, [head]);
  const load = useCallback(() => {
    const q = new URLSearchParams({ limit: "100" });
    if (bucket) q.set("bucket", bucket);
    if (agent) q.set("agent", agent);
    api(`/priority?${q}`).then(setData).catch((e) => setError(e.message));
  }, [bucket, agent]);
  useEffect(load, [load]);

  return (
    <div className="stack">
      <div className="row">
        <select value={bucket} onChange={(e) => setBucket(e.target.value)} aria-label="Bucket">
          {BUCKETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {head && (
          <select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Agent">
            <option value="">All agents</option><option value="none">Unassigned</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        )}
        <span className="grow" />
        <span className="muted small">Top {data.results.length} of {data.count} overdue accounts</span>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="card scroll">
        <table>
          <thead>
            <tr><th className="r">#</th><th>Account</th><th>Borrower</th><th className="r">DPD</th><th className="r">Overdue</th><th className="r">Score</th><th>Why now</th>{head && <th>Agent</th>}</tr>
          </thead>
          <tbody>
            {data.results.map((l, i) => (
              <tr key={l.id} className="click" onClick={() => setOpen(l)}>
                <td className="r">{i + 1}</td><td>{l.account_id}</td><td>{l.borrower_name || <span className="muted">—</span>}</td>
                <td className="r">{l.dpd}</td><td className="r">{inr(l.overdue_amount)}</td><td className="r"><strong>{l.score}</strong></td>
                <td>{l.reasons.map((r) => <span key={r} className={`chip ${tone(r)}`} style={{ marginRight: 4 }}>{r}</span>)}</td>
                {head && <td>{l.agent ? l.agent.name : <span className="muted">Unassigned</span>}</td>}
              </tr>
            ))}
            {data.results.length === 0 && <tr><td colSpan="8" className="muted">Nothing overdue here.</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Score = days past due + overdue amount + call-back and promise timing + how long since the last call. Call-backs due and missed promises rise to the top;
        accounts with a promise still to come, or already called today, drop down. The weights are starting values for Collections leadership to tune.
      </p>
      {open && <Account loan={open} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  );
}
