import { useEffect, useState } from "react";
import { api, num } from "./api.js";
import { downloadText, toCsv } from "./csv.js";

const MAX_BYTES = 2_000_000;
const chip = { move: "ok", ok: "ok", same: "", error: "bad" };
const word = { move: "will move", ok: "will save", same: "no change", error: "error" };

export default function Allocation() {
  const [tab, setTab] = useState("bulk");
  return (
    <div className="stack">
      <div className="row">
        {[["bulk", "Bulk allocation"], ["history", "History"], ["refs", "Reference contacts"]].map(([v, l]) => (
          <button key={v} className={tab === v ? "tab on" : "tab"} onClick={() => setTab(v)}>{l}</button>
        ))}
      </div>
      {tab === "bulk" && (
        <CsvTool
          key="bulk"
          endpoint="/allocation/csv"
          title="Allocate accounts to agents from a file"
          help={<>
            <p className="small">One row per account. The agent can be an e-mail, a name, or <code>unassigned</code>.</p>
            <pre className="sample">loan_id,agent{"\n"}L-100417,asha@alpharupee.in{"\n"}L-100552,Ravi Kumar{"\n"}L-103867,unassigned</pre>
            <p className="small"><a href="/api/allocation/template.csv" download>Download a template with today’s allocation</a> (overdue accounts), change the agent column and upload it back. Closed accounts are rejected, and accounts that are not in the CRM yet appear after the next LMS sync.</p>
          </>}
          countKeys={[["move", "Will move"], ["same", "No change"], ["errors", "Errors"]]}
          actionKey="move"
          columns={["Row", "Account", "Borrower", "Result", "Detail"]}
          cells={(r) => [r.row, r.loan, r.borrower || "—", r.status, r.message]}
          errorHead={["row", "loan_id", "agent", "reason"]}
          errorRow={(r) => [r.row, r.loan, r.agent, r.message]}
          errorFile="allocation_errors.csv"
          applyLabel={(n) => `Apply ${n} moves`}
          doneText={(c) => `Done: ${c.move} accounts re-allocated, ${c.errors} rows skipped. It is recorded in History and the audit log.`}
          showLoad
        />
      )}
      {tab === "refs" && (
        <CsvTool
          key="refs"
          endpoint="/references/csv"
          title="Load reference contacts from a file"
          help={<>
            <p className="small">One row per reference, up to two per account (<code>slot</code> 1 or 2, default 1). A row replaces what is there for that slot. Agents can also add references one by one on the account.</p>
            <pre className="sample">account_id,slot,ref_name,ref_mobile,ref_relation{"\n"}L-100417,1,Meena Rao,9876543210,Sister{"\n"}L-100417,2,Raju Kumar,9876543211,Friend</pre>
            <p className="small">Mobile numbers are checked (10 digits, not the borrower’s own) and only shown masked here.</p>
          </>}
          countKeys={[["ok", "Will save"], ["errors", "Errors"]]}
          actionKey="ok"
          columns={["Row", "Account", "Slot", "Name", "Mobile", "Result", "Detail"]}
          cells={(r) => [r.row, r.loan, r.slot, r.name, r.mobile || "—", r.status, r.message]}
          errorHead={["row", "account_id", "slot", "reason"]}
          errorRow={(r) => [r.row, r.loan, r.slot, r.message]}
          errorFile="reference_errors.csv"
          applyLabel={(n) => `Save ${n} references`}
          doneText={(c) => `Done: ${c.ok} references saved, ${c.errors} rows skipped. It is recorded in the audit log.`}
        />
      )}
      {tab === "history" && <History />}
    </div>
  );
}

function CsvTool({ endpoint, title, help, countKeys, actionKey, columns, cells, errorHead, errorRow, errorFile, applyLabel, doneText, showLoad }) {
  const [text, setText] = useState("");
  const [filename, setFilename] = useState("");
  const [result, setResult] = useState(null); // preview of exactly `checked`
  const [checked, setChecked] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function pick(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > MAX_BYTES) { setError("The file is larger than 2 MB."); return; }
    setText(await f.text());
    setFilename(f.name);
    setResult(null); setDone(""); setError("");
  }
  async function send(apply) {
    setBusy(true); setError(""); setDone("");
    try {
      const r = await api(endpoint, { method: "POST", body: { text, filename, apply } });
      if (apply) { setDone(doneText(r.counts)); setResult(null); setText(""); setFilename(""); }
      else { setResult(r); setChecked(text); }
    } catch (e) { setError(e.message); setResult(null); } finally { setBusy(false); }
  }
  const clear = () => { setText(""); setFilename(""); setResult(null); setDone(""); setError(""); };
  const n = result ? result.counts[actionKey] : 0;
  const fresh = result && checked === text; // the preview matches what is in the box

  return (
    <div className="stack">
      <section className="card">
        <h2>{title}</h2>
        {help}
        <div className="stack" style={{ marginTop: 12 }}>
          <label>Choose a CSV file<input type="file" accept=".csv,.txt,text/csv" onChange={pick} /></label>
          <label>…or paste the rows{filename ? ` (loaded: ${filename})` : ""}
            <textarea rows="6" value={text} spellCheck="false" onChange={(e) => { setText(e.target.value); setResult(null); setDone(""); }} />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          {done && <p role="status">{done}</p>}
          <div className="row">
            <button disabled={busy || !text.trim()} onClick={() => send(false)}>{busy && !result ? "Checking…" : "Check file"}</button>
            <button className="ghost" onClick={clear}>Clear</button>
          </div>
        </div>
      </section>

      {result && (
        <>
          <div className="kpis">
            <div className="card kpi"><div className="muted small">Rows in file</div><div className="kpi-value">{num(result.counts.rows)}</div></div>
            {countKeys.map(([k, l]) => (
              <div className="card kpi" key={k}><div className="muted small">{l}</div><div className="kpi-value">{num(result.counts[k])}</div></div>
            ))}
          </div>
          <section className="card">
            <div className="row">
              <h2>Preview</h2><span className="muted small">Nothing changes until you apply.</span><span className="grow" />
              <button disabled={busy || !fresh || n === 0} onClick={() => send(true)}>{busy ? "Applying…" : applyLabel(n)}</button>
            </div>
            <div className="scroll">
              <table>
                <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>
                  {result.rows.map((r) => {
                    const c = cells(r);
                    return (
                      <tr key={r.row}>
                        {c.map((v, i) => columns[i] === "Result"
                          ? <td key={i}><span className={`chip ${chip[v]}`}>{word[v]}</span></td>
                          : <td key={i}>{String(v)}</td>)}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {result.counts.rows > result.rows.length && <p className="muted small">Showing the first {result.rows.length} rows.</p>}
            {result.errors.length > 0 && (
              <button className="ghost" onClick={() => downloadText(errorFile, toCsv(errorHead, result.errors.map(errorRow)))}>
                Download the {result.errors.length} error rows
              </button>
            )}
          </section>
          {showLoad && result.load && (
            <section className="card">
              <h2>Agent load, before and after</h2>
              <p className="muted small">Overdue accounts per agent. Check that nobody is overloaded before you apply.</p>
              <table>
                <thead><tr><th>Agent</th><th className="r">Before</th><th className="r">After</th><th className="r">Change</th></tr></thead>
                <tbody>
                  {result.load.map((l) => (
                    <tr key={l.agent}>
                      <td>{l.agent}</td><td className="r">{num(l.before)}</td><td className="r">{num(l.after)}</td>
                      <td className="r">{l.after - l.before === 0 ? "—" : (l.after > l.before ? "+" : "") + (l.after - l.before)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function History() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { api("/allocation/history").then(setRows).catch((e) => setError(e.message)); }, []);
  if (error) return <p className="error">{error}</p>;
  if (!rows) return <p className="muted">Loading…</p>;
  return (
    <section className="card scroll">
      <table>
        <thead><tr><th>When</th><th>By</th><th>Source</th><th>File</th><th className="r">Rows</th><th className="r">Moved</th><th className="r">No change</th><th className="r">Errors</th></tr></thead>
        <tbody>
          {rows.map((b) => (
            <tr key={b.id}>
              <td>{new Date(b.at).toLocaleString("en-IN")}</td><td>{b.by}</td><td>{b.source === "csv" ? "File" : "Tick boxes"}</td>
              <td>{b.filename || "—"}</td><td className="r">{b.rows}</td><td className="r">{b.moved}</td><td className="r">{b.same}</td><td className="r">{b.errors}</td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan="8" className="muted">No allocations yet. Files and tick-box assignments both appear here.</td></tr>}
        </tbody>
      </table>
    </section>
  );
}
