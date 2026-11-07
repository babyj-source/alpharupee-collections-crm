import { useEffect, useState } from "react";
import { api } from "./api.js";

// The call script: read by everyone, edited by the Admin. ScriptBox is the compact version inside the account screen.
export function useScript() {
  const [s, setS] = useState(null);
  useEffect(() => { api("/scripts").then(setS).catch(() => setS([])); }, []);
  return [s, setS];
}

export function ScriptBox() {
  const [s] = useScript();
  const [open, setOpen] = useState(false);
  return (
    <section>
      <button className="ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide call script" : "Call script"}</button>
      {open && (
        <div className="script">
          {(s || []).map((x) => <div key={x.key}><h3>{x.title}</h3><p style={{ whiteSpace: "pre-wrap" }}>{x.body}</p></div>)}
          {s && s.length === 0 && <p className="muted small">No script yet.</p>}
        </div>
      )}
    </section>
  );
}

export default function Script({ user }) {
  const [s, setS] = useScript();
  const admin = user.role === "admin";
  const [edit, setEdit] = useState({});
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  async function save(x) {
    setError(""); setNote("");
    try {
      const u = await api(`/scripts/${x.key}`, { method: "PUT", body: { title: edit[x.key].title, body: edit[x.key].body } });
      setS(s.map((y) => (y.key === u.key ? u : y)));
      setEdit({ ...edit, [x.key]: undefined });
      setNote(`${u.title} saved.`);
    } catch (e) { setError(e.message); }
  }
  if (!s) return <p className="muted">Loading…</p>;
  return (
    <div className="stack">
      {error && <p className="error">{error}</p>}
      {note && <p className="muted" role="status">{note}</p>}
      {s.map((x) => {
        const e = edit[x.key];
        return (
          <section key={x.key} className="card script">
            {e ? (
              <div className="stack">
                <input value={e.title} aria-label="Heading" onChange={(ev) => setEdit({ ...edit, [x.key]: { ...e, title: ev.target.value } })} />
                <textarea rows={10} value={e.body} aria-label="Script text" onChange={(ev) => setEdit({ ...edit, [x.key]: { ...e, body: ev.target.value } })} />
                <div className="row"><button onClick={() => save(x)}>Save</button><button className="ghost" onClick={() => setEdit({ ...edit, [x.key]: undefined })}>Cancel</button></div>
              </div>
            ) : (
              <>
                <div className="row"><h2 className="grow">{x.title}</h2>{admin && <button className="ghost" onClick={() => setEdit({ ...edit, [x.key]: { title: x.title, body: x.body } })}>Edit</button>}</div>
                <p style={{ whiteSpace: "pre-wrap" }}>{x.body}</p>
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}
