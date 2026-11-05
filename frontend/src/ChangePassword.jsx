import { useState } from "react";
import { api } from "./api.js";
import Logo from "./Logo.jsx";

// Shown instead of the app while the account still has an administrator-issued password.
export default function ChangePassword({ user, onDone, onLogout }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (next !== again) return setError("The two new passwords are different.");
    setBusy(true);
    try {
      const d = await api("/auth/password", { method: "POST", body: { current_password: current, new_password: next } });
      onDone({ ...user, ...d.user });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="card login" onSubmit={submit}>
        <div className="brand"><Logo height={34} /></div>
        <h1>Choose your own password</h1>
        <p className="muted small">Your administrator gave you a temporary password. Set a new one (at least 10 characters) to continue.</p>
        <label>Temporary password<input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>
        <label>New password<input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={10} /></label>
        <label>Repeat new password<input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required minLength={10} /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button disabled={busy}>{busy ? "Saving…" : "Save and continue"}</button>
        <button type="button" className="ghost" onClick={onLogout}>Sign out</button>
      </form>
    </div>
  );
}
