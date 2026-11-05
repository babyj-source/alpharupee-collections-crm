# Phase 1, increment 5: agent-started campaigns, live view for the head, custom campaigns

Changes the Smartflo flow from increment 4 to match the agreed process.

## How it works now

- **Agent starts the campaign** (menu *Dialer*, button *Start campaign*): the agent signs in to Smartflo (password typed once, never stored), the CRM opens the Smartflo session, then **sends that agent's own allocated customers in the campaign to the campaign's lead list**. Smartflo rings them by itself. The agent logs an outcome after each call to get the next one.
  - Nobody is sent twice on the same day (tracked per campaign, customer and day). Starting again, or pressing *Send my new customers to Smartflo*, sends only customers who were allocated since.
  - If Smartflo rejects the push the session stays open and the screen shows how many were taken; *Send my new customers* retries.
  - An agent with no customers in the campaign still starts; Smartflo dials whatever is already in its list.
- **The head no longer pushes.** The *Push to Smartflo* button and its endpoint are gone. The head monitors, creates campaigns and pauses them.
- **Campaigns menu (Head and Admin):**
  - *Live* (refreshes every 6 seconds): per campaign today (customers waiting, sent to Smartflo, called, connected, promises, paid); agents dialling now with their state (on a call with which account / waiting / screen idle) and outcomes logged today; the latest 40 outcomes. No phone numbers are shown. Built from CRM data and the agents' Dialer screens, so it makes no extra calls to Smartflo.
  - *Campaigns*: all campaigns with customers waiting, Smartflo ids, pause / switch on, edit, and **New campaign**.
  - *Setup* (Admin): connection test, each agent's Smartflo e-mail and number, the outcome-to-Smartflo disposition ids.
- **New campaign by rules** (Head or Admin): name, days past due from/to, overdue at least, optional product code, leave out customers with a promise or call-back to come. Smartflo campaign and lead-list ids are entered by the head, because the CRM cannot create them in Smartflo. A custom campaign can be edited, paused or deleted (deleting is refused once agents have dialled in it; pause it instead). The six built-in campaigns keep their fixed rules and cannot be renamed or deleted.

## Things to know

1. **Shared Smartflo list.** All agents in a campaign share one lead list, so Smartflo may ring a customer allocated to a colleague to whichever agent is free. The CRM already lets an agent open the account they are on a call with (3-hour access); their own list and dashboard are unchanged. For strictly separate lists, create one Smartflo campaign per agent.
2. **Old leads stay in the list.** The Smartflo API has no delete-lead call, so customers sent on earlier days stay in the list. Rotate to a fresh list now and then.
3. **Custom campaigns can overlap** with each other and with the built-in ones (a customer can be in two). Built-in campaigns never overlap among themselves.
4. The live "on a call" state depends on the agent's Dialer screen being open; a closed screen shows as idle after 30 seconds.
5. Everything still depends on the Smartflo assumptions listed in `PHASE1_INCREMENT4.md` (login token field, live_calls fields, headers).

## Tests

142 backend tests pass (push on start, no repeats, partial failure and retry, rules, campaign create/edit/delete, live view). Frontend build and a browser check of Live, Campaigns and Dialer were done.
