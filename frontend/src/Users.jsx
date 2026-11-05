import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

const when = (t) => (t ? new Date(t).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "Never");
const EMPTY = { email: "", name: "", mobile: "", role: "agent" };

export default function Users({ me }) {
  const [tab, setTab] = useState("users");
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState(null); // user being edited
  const [secret, setSecret] = useState(null); // {name, email, temp_password}: shown once
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api("/users").then(setUsers).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);
  useEffect(() => { api("/roles").then(setRoles).catch(() => {}); }, []);

  async function run(fn) {
    setBusy(true);
    setError("");
    try { await fn(); load(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  const create = (e) => {
    e.preventDefault();
    run(async () => {
      const d = await api("/users", { method: "POST", body: form });
      setSecret({ name: d.user.name, email: d.user.email, temp_password: d.temp_password });
      setForm(EMPTY);
    });
  };
  const save = (e) => {
    e.preventDefault();
    const body = { name: editing.name, role: editing.role };
    if (editing.mobile_new) body.mobile = editing.mobile_new;
    run(async () => { await api(`/users/${editing.id}`, { method: "PATCH", body }); setEditing(null); });
  };
  const setActive = (u, is_active) => run(() => api(`/users/${u.id}`, { method: "PATCH", body: { is_active } }));
  const unlock = (u) => run(() => api(`/users/${u.id}/unlock`, { method: "POST" }));
  const reset = (u) => {
    if (!window.confirm(`Reset the password for ${u.name}? Their current password stops working.`)) return;
    run(async () => {
      const d = await api(`/users/${u.id}/reset-password`, { method: "POST" });
      setSecret({ name: u.name, email: u.email, temp_password: d.temp_password });
    });
  };

  const state = (u) =>
    !u.is_active ? ["Inactive", "bad"] : u.locked ? ["Locked", "bad"] : u.must_change_password ? ["Password not changed yet", "today"] : ["Active", "ok"];

  return (
    <div className="stack">
      <div className="row">
        <button className={tab === "users" ? "tab on" : "tab"} onClick={() => setTab("users")}>Users</button>
        <button className={tab === "roles" ? "tab on" : "tab"} onClick={() => setTab("roles")}>Roles and access</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}

      {secret && (
        <section className="card" role="status">
          <h2>Temporary password for {secret.name}</h2>
          <p>Give this to <strong>{secret.email}</strong> now. It is shown only once and they must change it when they first sign in.</p>
          <p><code style={{ fontSize: "1.2rem" }}>{secret.temp_password}</code></p>
          <div className="row">
            <button onClick={() => navigator.clipboard && navigator.clipboard.writeText(secret.temp_password)}>Copy</button>
            <button className="ghost" onClick={() => setSecret(null)}>I have noted it</button>
          </div>
        </section>
      )}

      {tab === "users" && (
        <>
          <form className="card" onSubmit={create}>
            <h2>Add a user</h2>
            <div className="filters">
              <input placeholder="E-mail (their sign-in name)" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
              <input placeholder="Full name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              <input placeholder="Mobile (10 digits, gets the OTP)" required inputMode="numeric" value={form.mobile} onChange={(e) => setForm({ ...form, mobile: e.target.value })} />
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} aria-label="Role">
                <option value="agent">Agent</option><option value="head">Head</option><option value="admin">Admin</option>
              </select>
              <button disabled={busy}>Create user</button>
            </div>
          </form>

          {editing && (
            <form className="card" onSubmit={save}>
              <h2>Edit {editing.email}</h2>
              <div className="filters">
                <input value={editing.name} required onChange={(e) => setEditing({ ...editing, name: e.target.value })} aria-label="Name" />
                <input placeholder={`New mobile (now ${editing.mobile}), leave blank to keep`} inputMode="numeric" value={editing.mobile_new || ""} onChange={(e) => setEditing({ ...editing, mobile_new: e.target.value })} />
                <select value={editing.role} disabled={editing.id === me.id} onChange={(e) => setEditing({ ...editing, role: e.target.value })} aria-label="Role">
                  <option value="agent">Agent</option><option value="head">Head</option><option value="admin">Admin</option>
                </select>
                <button disabled={busy}>Save</button>
                <button type="button" className="ghost" onClick={() => setEditing(null)}>Cancel</button>
              </div>
            </form>
          )}

          <div className="card scroll">
            <table>
              <thead><tr><th>Name</th><th>E-mail</th><th>Role</th><th>Mobile</th><th>Status</th><th>Last sign-in</th><th /></tr></thead>
              <tbody>
                {users.map((u) => {
                  const [text, cls] = state(u);
                  return (
                    <tr key={u.id}>
                      <td>{u.name}</td><td>{u.email}</td><td>{u.role_label}</td><td>{u.mobile}</td>
                      <td><span className={`chip ${cls}`}>{text}</span></td><td>{when(u.last_login)}</td>
                      <td><div className="row" style={{ flexWrap: "wrap", minWidth: 250, gap: 0 }}>
                        <button className="ghost" onClick={() => setEditing({ ...u })}>Edit</button>
                        <button className="ghost" onClick={() => reset(u)}>Reset password</button>
                        {u.locked && <button className="ghost" onClick={() => unlock(u)}>Unlock</button>}
                        {u.id !== me.id && (u.is_active
                          ? <button className="ghost" onClick={() => setActive(u, false)}>Deactivate</button>
                          : <button className="ghost" onClick={() => setActive(u, true)}>Activate</button>)}
                      </div></td>
                    </tr>
                  );
                })}
                {users.length === 0 && <tr><td colSpan="7" className="muted">No users yet.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="muted small">Sign-in is e-mail, password and an SMS code sent to the user's mobile. A deactivated user cannot sign in; their allocated accounts stay with them until you move them on the Allocation screen.</p>
        </>
      )}

      {tab === "roles" && (
        <div className="kpis">
          {roles.map((r) => (
            <section key={r.role} className="card">
              <h2>{r.label}</h2>
              <p className="muted">{r.summary}</p>
              <ul>{r.can.map((c) => <li key={c}>{c}</li>)}</ul>
              <p className="small muted">{users.filter((u) => u.role === r.role && u.is_active).length} active user(s)</p>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
