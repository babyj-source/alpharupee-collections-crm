import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Account from "./Account.jsx";
import Whatsapp from "./WhatsappChat.jsx";
import { clock, startCall, step } from "./callphase.js";
import { openWhatsappCall } from "./whatsappcall.js";

const when = (t) => {
  const d = new Date(t), now = new Date();
  return d.toDateString() === now.toDateString()
    ? d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
};
const initials = (n) => (n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

// WhatsApp-Web style screen: conversations on the left, the open chat on the right, with Call from the chat header.
export default function Chats({ user }) {
  const isHead = user?.role === "head" || user?.role === "admin";
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [q, setQ] = useState("");
  const [found, setFound] = useState([]);       // accounts matching the search that have no chat yet
  const [sel, setSel] = useState(null);         // { id, account_id, borrower_name }
  const [loan, setLoan] = useState(null);       // full account of the open chat (masked number, dues)
  const [drawer, setDrawer] = useState(false);
  const [error, setError] = useState("");
  const [live, setLive] = useState(null);       // call panel state, see callphase.js
  const [calling, setCalling] = useState(false);
  const [ending, setEnding] = useState(false);
  const [waCalling, setWaCalling] = useState(false);
  const poll = useRef(null);
  const [view, setView] = useState("chats");    // "chats" | "unmatched" (Head and Admin)
  const [un, setUn] = useState({ unread: 0, groups: [] });
  const [selUn, setSelUn] = useState(null);     // key of the open unmatched sender

  const loadUn = () => isHead && api("/messaging/whatsapp/unmatched").then(setUn).catch(() => {});
  const load = () => { loadUn(); return api("/messaging/whatsapp/inbox").then(setRows).catch((e) => setError(e.message)); };
  useEffect(() => {
    api("/meta").then(setMeta).catch(() => {});
    load();
    const t = setInterval(load, 8000);
    return () => { clearInterval(t); clearInterval(poll.current); };
  }, []);

  // Search: filter the conversations, and look up accounts by name / account number to start a new chat.
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setFound([]); return undefined; }
    const t = setTimeout(() => {
      api(`/loans?q=${encodeURIComponent(term)}&page_size=8`)
        .then((d) => setFound(d.results || []))
        .catch(() => setFound([]));
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  async function open(id, account_id, borrower_name) {
    setError(""); setLive(null); clearInterval(poll.current);
    setSel({ id, account_id, borrower_name }); setLoan(null);
    try { setLoan(await api(`/loans/${id}`)); } catch (e) { setError(e.message); }
  }

  function watchCall(loanId) {
    clearInterval(poll.current);
    let s = startCall(Date.now()), failed = 0;
    setLive(s);
    poll.current = setInterval(async () => {
      try {
        s = step(s, await api(`/dialer/call/status?loan=${loanId}`), Date.now());
        failed = 0;
        setLive(s);
        if (s.done) clearInterval(poll.current);
      } catch (e) {
        if (++failed >= 3) { clearInterval(poll.current); setLive(null); setError(e.message); }
      }
    }, 2500);
  }
  async function call() {
    setError(""); setCalling(true);
    try {
      await api("/dialer/call", { method: "POST", body: { loan_id: loan.id, which: "borrower" } });
      watchCall(loan.id);
    } catch (e) { setError(e.message); } finally { setTimeout(() => setCalling(false), 4000); }
  }
  async function endCall() {
    setError(""); setEnding(true);
    try { await api("/dialer/call/hangup", { method: "POST", body: { loan_id: loan.id } }); }
    catch (e) { setError(e.message); }
    finally { setEnding(false); }
  }
  async function waCall() {
    setError(""); setWaCalling(true);
    try { await openWhatsappCall(loan.id, "borrower"); }
    catch (e) { setError(e.message); } finally { setTimeout(() => setWaCalling(false), 2000); }
  }
  const inCall = live && ["starting", "ringing", "connected"].includes(live.phase);

  const term = q.trim().toLowerCase();
  const shownRows = term ? rows.filter((r) => (r.borrower_name || "").toLowerCase().includes(term) || r.account_id.toLowerCase().includes(term)) : rows;
  const haveChat = new Set(rows.map((r) => r.loan_id));
  const fresh = found.filter((l) => !haveChat.has(l.id));
  const borrower = loan && (loan.contacts || []).find((c) => c.key === "borrower");

  const unKey = (g) => `${g.last4}|${g.name}`;
  const unName = (g) => g.name || "Unknown sender";
  const shownUn = term ? un.groups.filter((g) => unName(g).toLowerCase().includes(term) || g.last4.includes(term)) : un.groups;
  const openUn = un.groups.find((g) => unKey(g) === selUn);
  async function openUnmatched(g) {
    setSelUn(unKey(g));
    if (g.unread > 0) {
      try { await api("/messaging/whatsapp/unmatched", { method: "POST", body: { last4: g.last4, name: g.name } }); loadUn(); } catch (e) { setError(e.message); }
    }
  }
  const showChats = () => { setView("chats"); setSelUn(null); };

  return (
    <div className={`wweb${(view === "unmatched" ? selUn : sel) ? " chat-open" : ""}`}>
      <div className="wweb-list card">
        {isHead && (
          <div className="row wweb-tabs">
            <button className={view === "chats" ? "" : "ghost"} onClick={showChats}>Chats</button>
            <button className={view === "unmatched" ? "" : "ghost"} onClick={() => setView("unmatched")}>
              Unmatched{un.unread > 0 && <span className="chip due"> {un.unread}</span>}
            </button>
          </div>
        )}
        <div className="wweb-search">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or account number" aria-label="Search conversations" />
        </div>
        <div className="wweb-rows" role="list">
          {view === "unmatched" && (
            <>
              {shownUn.map((g) => (
                <button key={unKey(g)} role="listitem" className={`wweb-row${selUn === unKey(g) ? " on" : ""}`} onClick={() => openUnmatched(g)}>
                  <span className="avatar new">{initials(g.name)}</span>
                  <span className="wweb-main">
                    <span className="wweb-top"><strong>{unName(g)}</strong><span className="muted small">{when(g.last_at)}</span></span>
                    <span className="wweb-bottom">
                      <span className="muted small wweb-last">···{g.last4} · {g.last_text}</span>
                      {g.unread > 0 && <span className="chip due">{g.unread}</span>}
                    </span>
                  </span>
                </button>
              ))}
              {shownUn.length === 0 && <p className="muted small pad">{term ? "No sender matches." : "No messages from unknown numbers."}</p>}
            </>
          )}
          {view === "chats" && shownRows.map((r) => (
            <button key={r.loan_id} role="listitem" className={`wweb-row${sel?.id === r.loan_id ? " on" : ""}`}
                    onClick={() => open(r.loan_id, r.account_id, r.borrower_name)}>
              <span className="avatar">{initials(r.borrower_name)}</span>
              <span className="wweb-main">
                <span className="wweb-top"><strong>{r.borrower_name || r.account_id}</strong><span className="muted small">{when(r.last_at)}</span></span>
                <span className="wweb-bottom">
                  <span className="muted small wweb-last">{r.last_direction === "out" ? "You: " : ""}{r.last_text}</span>
                  {r.unread > 0 && <span className="chip due">{r.unread}</span>}
                </span>
              </span>
            </button>
          ))}
          {view === "chats" && shownRows.length === 0 && !fresh.length && <p className="muted small pad">{term ? "No conversation matches." : "No WhatsApp conversations yet. Search an account above to start one."}</p>}
          {view === "chats" && fresh.length > 0 && <div className="wweb-sub muted small">Start a new chat</div>}
          {view === "chats" && fresh.map((l) => (
            <button key={l.id} className="wweb-row" onClick={() => { open(l.id, l.account_id, l.borrower_name); setQ(""); }}>
              <span className="avatar new">{initials(l.borrower_name)}</span>
              <span className="wweb-main">
                <span className="wweb-top"><strong>{l.borrower_name || l.account_id}</strong></span>
                <span className="muted small">{l.account_id} · {l.dpd} days overdue</span>
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="wweb-chat card">
        {view === "unmatched" && !openUn && <div className="wweb-empty muted"><p>Pick a sender. These numbers are not on any account.</p></div>}
        {view === "unmatched" && openUn && (
          <>
            <div className="wweb-head">
              <button className="ghost wweb-back" onClick={() => setSelUn(null)} aria-label="Back to senders">Back</button>
              <span className="avatar new">{initials(openUn.name)}</span>
              <div className="grow">
                <strong>{unName(openUn)}</strong>
                <div className="muted small">Number ending {openUn.last4} · not on any account</div>
              </div>
            </div>
            <p className="muted small pad">This number is not a borrower or alternate mobile on any account, and the CRM keeps only the last four digits, so it cannot be answered here. Reply from the MSG91 Hello inbox, or find the account by name and add the number to it.</p>
            <div className="wa-full"><div className="chat" aria-label="Messages from this sender">
              {openUn.messages.map((m) => (
                <div key={m.id} className="bubble in">
                  {m.kind === "button" && <div className="bubble-tag">Tapped a button</div>}
                  {m.media && <a className="bubble-file" href={m.media.url} target="_blank" rel="noreferrer">{m.media.name || m.media.label}</a>}
                  {(m.text || !m.media) && <div className="bubble-text">{m.text}</div>}
                  <div className="bubble-meta">{when(m.at)}</div>
                </div>
              ))}
            </div></div>
          </>
        )}
        {view === "chats" && !sel && <div className="wweb-empty muted"><p>Pick a conversation, or search an account to start one.</p></div>}
        {view === "chats" && sel && (
          <>
            <div className="wweb-head">
              <button className="ghost wweb-back" onClick={() => { setSel(null); setLoan(null); }} aria-label="Back to conversations">Back</button>
              <span className="avatar">{initials(sel.borrower_name)}</span>
              <div className="grow">
                <strong>{sel.borrower_name || sel.account_id}</strong>
                <div className="muted small">{sel.account_id}{borrower ? ` · ${borrower.mobile}` : ""}{loan ? ` · ${loan.dpd} days overdue` : ""}</div>
              </div>
              {meta?.dialer_enabled && loan && (
                inCall
                  ? <button className="danger" disabled={ending || !["ringing", "connected"].includes(live.phase)} onClick={endCall}>{ending ? "Ending…" : "End call"}</button>
                  : <button disabled={calling} onClick={call}>{calling ? "Calling…" : "Call"}</button>
              )}
              {meta?.whatsapp_call_enabled && loan && (
                <button className="wa-call" disabled={waCalling} onClick={waCall} title="Opens this customer's chat in your WhatsApp. Tap the call icon there.">{waCalling ? "Opening…" : "WhatsApp call"}</button>
              )}
              <button className="ghost" disabled={!loan} onClick={() => setDrawer(true)}>Account details</button>
            </div>
            {live && (
              <div className={`callpanel ${live.phase}`} role="status" aria-live="polite">
                <strong>
                  {live.phase === "starting" && "Starting the call… Smartflo rings your phone first."}
                  {live.phase === "ringing" && "Ringing… answer your phone, then the customer is connected."}
                  {live.phase === "connected" && `Connected · ${clock(live.secs)}`}
                  {live.phase === "ended" && "Call ended. Log the outcome in Account details."}
                  {live.phase === "noshow" && "The call did not start. Check that your Smartflo phone is logged in, then try again."}
                </strong>
                {!inCall && <button className="ghost" onClick={() => setLive(null)}>Dismiss</button>}
              </div>
            )}
            {error && <p className="error pad" role="alert">{error}</p>}
            {loan && <Whatsapp key={loan.id} loan={loan} meta={meta} onSent={load} full />}
          </>
        )}
      </div>
      {drawer && loan && <Account loan={loan} onClose={() => setDrawer(false)} onChanged={setLoan} />}
    </div>
  );
}
