import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const clockTime = (t) => new Date(t).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
const dayLabel = (t) => {
  const d = new Date(t), today = new Date(), y = new Date();
  y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
};
const fmtTime = (t) => new Date(t).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

// One tick line under our own messages, like WhatsApp: sent, delivered, read.
const ticks = { sent: "✓", delivered: "✓✓", read: "✓✓", failed: "!" };

// WhatsApp for one account: the approved template message (with a confirm step) and the conversation with the customer.
// `full` = fill the height of the screen (the WhatsApp chats page); otherwise a compact box inside the account drawer.
export default function Whatsapp({ loan, meta, onSent, full = false }) {
  const [preview, setPreview] = useState(null);
  const [chat, setChat] = useState({ messages: [], window_open: false, window_closes_at: null });
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState({ ok: "", err: "" });
  const bottom = useRef(null);
  const count = useRef(0);

  const loadPreview = () => api(`/messaging/whatsapp/preview?loan=${loan.id}`).then(setPreview).catch(() => setPreview(null));
  const loadChat = () => api(`/messaging/whatsapp/chat?loan=${loan.id}`).then(setChat).catch(() => {});
  useEffect(() => {
    setConfirming(false); setMsg({ ok: "", err: "" }); setText(""); count.current = 0;
    setChat({ messages: [], window_open: false, window_closes_at: null });
    loadPreview(); loadChat();
    const t = setInterval(loadChat, 8000);       // pick up the customer's replies
    return () => clearInterval(t);
  }, [loan.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (chat.messages.length !== count.current) { count.current = chat.messages.length; bottom.current?.scrollIntoView?.({ block: "end" }); }
  }, [chat.messages.length]);

  async function sendTemplate() {
    setBusy(true); setMsg({ ok: "", err: "" });
    try {
      await api("/messaging/whatsapp/send", { method: "POST", body: { loan: loan.id, again: !!preview?.warning } });
      setMsg({ ok: "WhatsApp message sent.", err: "" });
    } catch (e) { setMsg({ ok: "", err: e.message }); }
    finally { setBusy(false); setConfirming(false); loadPreview(); loadChat(); onSent?.(); }
  }
  async function sendText(e) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true); setMsg({ ok: "", err: "" });
    try { await api("/messaging/whatsapp/chat", { method: "POST", body: { loan: loan.id, text } }); setText(""); onSent?.(); }
    catch (er) { setMsg({ ok: "", err: er.message }); }
    finally { setBusy(false); loadChat(); }
  }

  if (meta && !meta.whatsapp_enabled) return <p className="muted small">WhatsApp is switched off on this server.</p>;
  const until = chat.window_closes_at ? fmtTime(chat.window_closes_at) : "";

  let lastDay = "";
  const bubbles = [];
  chat.messages.forEach((m) => {
    const d = dayLabel(m.at);
    if (d !== lastDay) { lastDay = d; bubbles.push(<div key={`d${m.id}`} className="chat-day"><span>{d}</span></div>); }
    bubbles.push(
      <div key={m.id} className={`bubble ${m.direction}${m.state === "failed" ? " failed" : ""}`}>
        <div className="bubble-text">{m.text}</div>
        <div className="bubble-meta">
          {m.direction === "out" && m.by ? `${m.by} · ` : ""}{clockTime(m.at)}
          {m.direction === "out" && <span className={`tick ${m.state}`} title={m.state === "failed" ? "Not delivered" : m.state}> {ticks[m.state] || ""}</span>}
          {m.state === "failed" && m.error ? ` · ${m.error}` : ""}
        </div>
      </div>
    );
  });

  const templateBox = preview && (
    <>
      {preview.problem && <p className="muted small">WhatsApp: {preview.problem}</p>}
      {!preview.problem && (
        <div className="msgbox">
          <div className="muted small">{preview.name} · approved message{preview.image ? " (with the header image)" : ""}</div>
          <p className="msgtext">{preview.text}</p>
          {preview.warning && <p className="small warn">{preview.warning} You can send it again.</p>}
          {!confirming && <button disabled={busy} onClick={() => setConfirming(true)}>{preview.warning ? "Send again on WhatsApp" : "Send on WhatsApp"}</button>}
          {confirming && (
            <div className="row">
              <span className="small">Send this to the borrower now?</span>
              <button disabled={busy} onClick={sendTemplate}>{busy ? "Sending…" : "Yes, send"}</button>
              <button className="ghost" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          )}
        </div>
      )}
    </>
  );

  return (
    <div className={full ? "wa wa-full" : "wa"}>
      {!full && <h4>WhatsApp</h4>}
      {!full && templateBox}
      <div className="chat" aria-label="WhatsApp conversation">
        {chat.messages.length === 0 && <p className="muted small chat-empty">No WhatsApp messages with this borrower yet. Start with the approved message below.</p>}
        {bubbles}
        <div ref={bottom} />
      </div>
      {full && !chat.window_open && templateBox}
      <form className="row chat-input" onSubmit={sendText}>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} disabled={!chat.window_open || busy}
               placeholder={chat.window_open ? "Type a message" : "Free text opens when the customer writes to us"} aria-label="Reply on WhatsApp" />
        <button disabled={!chat.window_open || busy || !text.trim()}>Send</button>
      </form>
      <p className="muted small">{chat.window_open ? `You can reply with free text until ${until}.` : "WhatsApp allows free text only within 24 hours of the customer's last message. Until then, send the approved message."}</p>
      {msg.ok && <p role="status" className="small">{msg.ok}</p>}
      {msg.err && <p className="error" role="alert">{msg.err}</p>}
    </div>
  );
}
