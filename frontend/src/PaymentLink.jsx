import { useEffect, useRef, useState } from "react";
import { api, inr } from "./api.js";

const fmtDate = (d) => (d ? new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—");
const fmtTime = (t) => new Date(t).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const sameDay = (t) => new Date(t).toDateString() === new Date().toDateString();

// Easebuzz payment link for one account: made for the closure amount of today, then sent by SMS / WhatsApp (they pick it up by themselves).
export default function PaymentLink({ loan, meta, onChange }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [stale, setStale] = useState("");
  const [msg, setMsg] = useState({ ok: "", err: "" });
  const poll = useRef(null);

  const load = () => api(`/payments/link?loan=${loan.id}`).then(setData).catch(() => setData(null));
  useEffect(() => { setData(null); setConfirming(false); setStale(""); setMsg({ ok: "", err: "" }); load(); }, [loan.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const latest = data?.links?.[0];
  const waiting = latest && latest.state === "active";
  useEffect(() => {          // while a link waits for payment, ask Easebuzz every 20 seconds
    clearInterval(poll.current);
    if (!waiting) return undefined;
    poll.current = setInterval(async () => {
      try {
        const r = await api(`/payments/link/${latest.id}/refresh`, { method: "POST" });
        if (r.state !== "active") { await load(); onChange?.(); }
      } catch { /* a failed check is retried on the next tick */ }
    }, 20000);
    return () => clearInterval(poll.current);
  }, [waiting, latest?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function create(confirmStale) {
    setBusy(true); setMsg({ ok: "", err: "" });
    try {
      const r = await api("/payments/link", { method: "POST", body: { loan: loan.id, confirm_stale: !!confirmStale } });
      setMsg({ ok: r.state === "active" ? "Payment link is ready. Send it by SMS or WhatsApp below." : "", err: "" });
      setStale(""); setConfirming(false);
      await load(); onChange?.();
    } catch (e) {
      if (e.status === 409) { setStale(e.message); setConfirming(false); }
      else { setMsg({ ok: "", err: e.message }); setConfirming(false); }
    } finally { setBusy(false); }
  }
  async function check() {
    setBusy(true); setMsg({ ok: "", err: "" });
    try {
      const r = await api(`/payments/link/${latest.id}/refresh`, { method: "POST" });
      setMsg({ ok: r.state === "paid" ? "Payment received." : "Not paid yet.", err: "" });
      await load(); if (r.state !== "active") onChange?.();
    } catch (e) { setMsg({ ok: "", err: e.message }); } finally { setBusy(false); }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(latest.url); setMsg({ ok: "Link copied.", err: "" }); } catch { setMsg({ ok: "", err: "Could not copy. Select the link and copy it." }); }
  }

  if (meta && !meta.payments_enabled) return <p className="muted small">Payment links are switched off on this server.</p>;
  if (!data) return null;
  const p = data.preview;
  const todays = latest && sameDay(latest.created_at);
  const canMake = !p.problem && !(waiting && todays && latest.amount === p.amount);
  return (
    <div className="msgbox paylink">
      <div className="muted small">Payment link · closure amount <strong>{inr(p.amount)}</strong> as on {fmtDate(p.as_on)}</div>
      {p.problem && <p className="muted small">{p.problem}</p>}
      {latest && (
        <div className="small">
          <span className={`chip ${latest.state === "paid" ? "ok" : latest.state === "expired" ? "bad" : "today"}`}>{latest.state_label}</span>{" "}
          {inr(latest.amount)} · made {fmtTime(latest.created_at)}{latest.by ? ` by ${latest.by}` : ""}
          {latest.state === "active" && ` · works until ${fmtDate(latest.expires_on)}`}
          {latest.state === "paid" && latest.paid_at ? ` · paid ${fmtTime(latest.paid_at)}` : ""}
          {latest.state === "paid" && <div className="muted">Waiting to be posted to the LMS.</div>}
          {latest.state === "active" && !todays && <div className="warn">This link is from an earlier day, so messages will not use it. Make today's link.</div>}
          {latest.state === "active" && <div className="linkline"><span className="linktext">{latest.url}</span> <button className="ghost" onClick={copy}>Copy</button> <button className="ghost" disabled={busy} onClick={check}>Check payment</button></div>}
        </div>
      )}
      {stale && (
        <div className="warn small">
          {stale} Make the link anyway?{" "}
          <button disabled={busy} onClick={() => create(true)}>Yes, make it</button>{" "}
          <button className="ghost" disabled={busy} onClick={() => setStale("")}>Cancel</button>
        </div>
      )}
      {canMake && !stale && !confirming && <button disabled={busy} onClick={() => setConfirming(true)}>{latest ? "Make a new payment link" : "Make payment link"}</button>}
      {canMake && !stale && confirming && (
        <div className="row">
          <span className="small">Make a payment link for {inr(p.amount)}?</span>
          <button disabled={busy} onClick={() => create(false)}>{busy ? "Making…" : "Yes, make it"}</button>
          <button className="ghost" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
        </div>
      )}
      {msg.ok && <p role="status" className="small">{msg.ok}</p>}
      {msg.err && <p className="error" role="alert">{msg.err}</p>}
    </div>
  );
}
