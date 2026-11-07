import { api } from "./api.js";

// Ask the server for this contact's WhatsApp link (audited, logged on the timeline) and open it in the agent's own WhatsApp.
// The tab is opened first, inside the click, so the browser's pop-up blocker lets it through; the address is filled in after.
export async function openWhatsappCall(loanId, which = "borrower") {
  const tab = window.open("", "_blank");
  try {
    const d = await api("/messaging/whatsapp/call", { method: "POST", body: { loan_id: loanId, which } });
    if (tab) { tab.opener = null; tab.location.href = d.url; } else { window.location.href = d.url; }
  } catch (e) {
    if (tab) tab.close();
    throw e;
  }
}
