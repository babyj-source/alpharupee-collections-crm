import { useEffect, useState } from "react";
import { api } from "./api.js";
import Account from "./Account.jsx";

const when = (t) => new Date(t).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

// WhatsApp conversations on the user's accounts, newest first. Opening one shows the account with the chat.
export default function Chats() {
  const [rows, setRows] = useState([]);
  const [open, setOpen] = useState(null);
  const [error, setError] = useState("");

  const load = () => api("/messaging/whatsapp/inbox").then(setRows).catch((e) => setError(e.message));
  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  async function openAccount(r) {
    setError("");
    try { setOpen(await api(`/loans/${r.loan_id}`)); } catch (e) { setError(e.message); }
  }

  return (
    <div className="stack">
      {error && <p className="error" role="alert">{error}</p>}
      <div className="card scroll">
        <table>
          <thead><tr><th>Account</th><th>Borrower</th><th>Last message</th><th>When</th><th className="r">Unread</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.loan_id} className="click" onClick={() => openAccount(r)}>
                <td>{r.account_id}</td>
                <td>{r.borrower_name || <span className="muted">—</span>}</td>
                <td>{r.last_direction === "out" ? <span className="muted">You: </span> : null}{r.last_text}</td>
                <td>{when(r.last_at)}</td>
                <td className="r">{r.unread > 0 ? <span className="chip due">{r.unread}</span> : ""}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={5} className="muted">No WhatsApp conversations yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {open && <Account loan={open} onClose={() => { setOpen(null); load(); }} onChanged={setOpen} />}
    </div>
  );
}
