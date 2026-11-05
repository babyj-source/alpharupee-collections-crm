import Logo from "./Logo.jsx";
import { useState } from "react";
import { api } from "./api.js";

export default function Login({ onDone }) {
  const [step, setStep] = useState("password"); // password -> code; or forgot -> reset -> done
  const [newPw, setNewPw] = useState("");
  const [newPw2, setNewPw2] = useState("");
  const [notice, setNotice] = useState("");
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

  const submitForgot = (e) => {
    e.preventDefault();
    run(async () => {
      await api("/auth/forgot", { method: "POST", body: { email } });
      setCode("");
      setStep("reset");
    });
  };

  const submitReset = (e) => {
    e.preventDefault();
    if (newPw !== newPw2) { setError("The two passwords do not match."); return; }
    run(async () => {
      await api("/auth/reset", { method: "POST", body: { code, new_password: newPw } });
      setNewPw(""); setNewPw2(""); setCode(""); setPassword("");
      setNotice("Your password has been changed. Sign in with the new password.");
      setStep("password");
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
        <div className="brand"><Logo height={40} /></div>
        <h1>CRM</h1>
        {step === "forgot" && (
          <form onSubmit={submitForgot}>
            <p className="muted">Enter your e-mail address. We will send a 6-digit code by SMS to the mobile number registered for it.</p>
            <label>
              E-mail
              <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            </label>
            {error && <p className="error" role="alert">{error}</p>}
            <button disabled={busy}>{busy ? "Sending…" : "Send code"}</button>
            <button type="button" className="ghost" onClick={() => { setStep("password"); setError(""); }}>Back to sign in</button>
          </form>
        )}
        {step === "reset" && (
          <form onSubmit={submitReset}>
            <p className="muted">If that e-mail is registered, a code has been sent to its registered mobile number. It is valid for 10 minutes. No SMS? Ask your administrator to reset your password.</p>
            <label>
              Code
              <input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required autoFocus />
            </label>
            <label>
              New password (at least 10 characters)
              <input type="password" autoComplete="new-password" minLength={10} value={newPw} onChange={(e) => setNewPw(e.target.value)} required />
            </label>
            <label>
              New password again
              <input type="password" autoComplete="new-password" minLength={10} value={newPw2} onChange={(e) => setNewPw2(e.target.value)} required />
            </label>
            {error && <p className="error" role="alert">{error}</p>}
            <button disabled={busy || code.length !== 6}>{busy ? "Saving…" : "Change password"}</button>
            <button type="button" className="ghost" disabled={busy} onClick={() => run(async () => { await api("/auth/forgot", { method: "POST", body: { email } }); setCode(""); })}>Send a new code</button>
            <button type="button" className="ghost" onClick={() => { setStep("password"); setError(""); }}>Back to sign in</button>
          </form>
        )}
        {step === "password" && (
          <form onSubmit={submitPassword}>
            <label>
              E-mail
              <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            </label>
            <label>
              Password
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {notice && <p role="status">{notice}</p>}
            {error && <p className="error" role="alert">{error}</p>}
            <button disabled={busy}>{busy ? "Checking…" : "Continue"}</button>
            <button type="button" className="ghost" onClick={() => { setStep("forgot"); setError(""); setNotice(""); }}>Forgot password?</button>
          </form>
        )}
        {step === "code" && (
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
