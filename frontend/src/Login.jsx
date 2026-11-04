import { useState } from "react";
import { api } from "./api.js";

export default function Login({ onDone }) {
  const [step, setStep] = useState("password"); // password -> code
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [hint, setHint] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(fn) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const submitPassword = (e) => {
    e.preventDefault();
    run(async () => {
      const d = await api("/auth/login", { method: "POST", body: { email, password } });
      setHint(d.mobile_hint);
      setPassword("");
      setStep("code");
    });
  };

  const submitCode = (e) => {
    e.preventDefault();
    run(async () => {
      const d = await api("/auth/verify", { method: "POST", body: { code } });
      onDone(d.user);
    });
  };

  const resend = () =>
    run(async () => {
      const d = await api("/auth/resend", { method: "POST" });
      setHint(d.mobile_hint);
      setCode("");
    });

  return (
    <div className="center">
      <div className="card login">
        <h1>AlphaRupee Collections</h1>
        {step === "password" ? (
          <form onSubmit={submitPassword}>
            <label>
              E-mail
              <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            </label>
            <label>
              Password
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {error && <p className="error" role="alert">{error}</p>}
            <button disabled={busy}>{busy ? "Checking…" : "Continue"}</button>
          </form>
        ) : (
          <form onSubmit={submitCode}>
            <p className="muted">We sent a 6-digit code by SMS to {hint}.</p>
            <label>
              Code
              <input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required autoFocus />
            </label>
            {error && <p className="error" role="alert">{error}</p>}
            <button disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Sign in"}</button>
            <button type="button" className="ghost" onClick={resend} disabled={busy}>Send a new code</button>
            <button type="button" className="ghost" onClick={() => { setStep("password"); setError(""); }}>Back</button>
          </form>
        )}
      </div>
    </div>
  );
}
