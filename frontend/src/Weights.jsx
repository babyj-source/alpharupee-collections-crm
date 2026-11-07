import { useEffect, useState } from "react";
import { api } from "./api.js";

// Admin: the points behind the priority list. The first screen is the plain numbers; the days-past-due bands are a small table.
export default function Weights() {
  const [d, setD] = useState(null);
  const [vals, setVals] = useState({});
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const load = (x) => { setD(x); setVals(JSON.parse(JSON.stringify(x.weights))); };
  useEffect(() => { api("/priority/weights").then(load).catch((e) => setError(e.message)); }, []);

  async function save(body) {
    setError(""); setNote("");
    try { load(await api("/priority/weights", { method: "PUT", body })); setNote(body.reset ? "Back to the standard points." : "Saved. The priority list uses these now."); }
    catch (e) { setError(e.message); }
  }
  if (!d) return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;

  const simple = Object.keys(d.defaults).filter((k) => k !== "dpd_points");
  const setBand = (i, j, v) => { const b = vals.dpd_points.map((r) => [...r]); b[i][j] = v === "" ? "" : Number(v); setVals({ ...vals, dpd_points: b }); };

  return (
    <div className="stack">
      <section className="card">
        <h2>Priority list points</h2>
        <p className="muted small">Each account's score is the sum of these points. A higher score rings sooner. Negative numbers push an account down. {d.changed ? `Changed from the standard${d.updated_by ? ` by ${d.updated_by}` : ""}.` : "Standard points are in use."}</p>
        <h3>Days past due</h3>
        <table>
          <thead><tr><th>Up to (days)</th><th>Points</th></tr></thead>
          <tbody>
            {vals.dpd_points.map((r, i) => (
              <tr key={i}>
                <td>{i === vals.dpd_points.length - 1 ? "and above" : <input type="number" aria-label="Days from" value={r[0]} onChange={(e) => setBand(i, 0, e.target.value)} />}</td>
                <td><input type="number" aria-label="Points" value={r[1]} onChange={(e) => setBand(i, 1, e.target.value)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">Each row applies from its "days" upward until the next row starts.</p>
        <h3>Everything else</h3>
        <div className="stack">
          {simple.map((k) => (
            <label key={k} className="row"><span className="grow">{d.labels[k]}</span><input type="number" value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value === "" ? "" : Number(e.target.value) })} /></label>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
        {note && <p className="muted" role="status">{note}</p>}
        <div className="row" style={{ marginTop: 12 }}>
          <button onClick={() => save({ weights: vals })}>Save points</button>
          <button className="ghost" onClick={() => save({ reset: true })}>Back to standard</button>
        </div>
      </section>
    </div>
  );
}
