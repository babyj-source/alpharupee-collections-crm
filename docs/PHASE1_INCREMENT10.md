# Phase 1, increment 10: Smartflo auto-dialler, aligned with the vendor's guide

Smartflo's dialler team sent a step-by-step guide, a tested "add campaign" call and the limits. The CRM already followed the same
sequence (agent sign-in, session, session call, outcome, logout, lead push); this increment brings the details in line with it.

## What changed
| Area | Before | Now |
|---|---|---|
| Create the dialler campaign | `POST /v1/dialer/campaign` on the API host (public reference) | `POST https://cloudphone.tatateleservices.com/api/v1/outbound-dialer/add-dialer-campaign`, account token sent plain: the call Smartflo tested. Host in `SMARTFLO_CLOUDPHONE_BASE`. |
| Outgoing number in the campaign | the number | the number's **id** from Smartflo's My Numbers list (`GET /v1/my_number`), found automatically from `SMARTFLO_CALLER_ID`. Override: `set-smartflo.sh did <id>`. If the number is not in the list, the screen lists the numbers that are. |
| Campaign settings | connect agent through "1" (mobile) | "2" (agent extension / softphone) as in Smartflo's tested example, plus `queue_timeout` 10, `ring_strategy` 1, hide/update lead details and agent-only callback on, `list_status` "1" |
| Outcome to the dialler | `/v1/dialer/store-disposition` with a fallback | `/v1/dialer/disposition_details_update` (agent token). A reply of `success: false` is shown as an error. |
| Session call | one reply taken as success | `success: false` is an error. "Agent is on a call." (an earlier session is still open) closes that session with the logout call and starts again, once. |
| Logout | any error shown | "Agent is not logged in." counts as logged out. |
| Agent token | ends after 55 minutes | renewed with `POST /v1/auth/refresh` when 12 minutes are left (the Dialer screen checks in every few seconds). If Smartflo refuses, nothing changes and the session ends at the old time. |
| Sign-in limit | any failure: "did not accept that login id and password" | "Too many login attempts" (more than 10 in 5 minutes) says to wait 5 minutes. |
| Manual dial while in a session | ordinary click-to-call | Smartflo's "manual dial from the dialler panel": `type: "dialer"`, no caller id. Outside a session the call is unchanged (type "dialer" is refused there). If Smartflo refuses it, the message says to stop the campaign for an ordinary call. |

## The daily flow (unchanged, as the vendor describes)
1. Agent opens Campaigns, picks a campaign, types the Smartflo password (never stored). CRM: sign in, create session, session call (once per shift).
2. CRM pushes that agent's customers to the campaign's lead list (`POST /v1/broadcast/lead/<list id>`, highest days past due first, `duplicate_option` overwrite). Smartflo dials them one per free agent (ratio 1).
3. When a call is answered, the account opens. The agent saves the outcome in the CRM; the CRM tells Smartflo (`disposition_details_update`), which releases the agent for the next customer.
4. Stop (or the end of the shift) logs the agent out of the campaign.

## Limits to remember
- Agent token: 1 hour. More than 10 sign-ins in 5 minutes locks that login for 5 minutes.
- One session call per agent. Do not start two sessions for the same agent.
- The outcome is sent only after a call was answered and ended.

## Not done on purpose
- **Lead `priority` and `skill_id`:** Smartflo's example shows both on a lead, but it does not say whether a bigger priority number dials first. The CRM already pushes the highest days past due first and Smartflo dials the oldest lead first. Ask Smartflo which way priority runs before we send it.
- **`manual_dial_enabled`:** not set in the campaign; Smartflo's tested example did not use it. If the manual-dial call is refused on a running session, ask Smartflo whether the campaign needs it.

## Server step
Nothing is needed if `SMARTFLO_CALLER_ID` is the number shown in Smartflo's My Numbers. If "Create in Smartflo" says the number was not found, run
`bash /opt/crm/current/server-tools/set-smartflo.sh did <id>` as `deploy` with the id from that message.

## Follow-up: no separate "call outcomes" step
"Create in Smartflo" on a campaign now makes the "CRM call outcomes" list (and saves each outcome's Smartflo id) by itself when it is missing. Agents still save the outcome once, on the CRM customer page; the CRM passes it to Smartflo, which needs an outcome list on every campaign and an outcome for each answered call to free the agent for the next customer. The "Create outcomes" button remains only for re-checking.

## Follow-up: "You are already logged in to this campaign" (422)
When the agent still has a session in Smartflo from an earlier start (for example one the CRM had already closed on its side), the session-create call answers 422 with that message and tells you to go straight to the session call. The CRM now treats it as "session already there" and carries on to the session call. Any other refusal still stops the start and is shown to the agent.

## Follow-up: the call log shows only CRM calls
The call log lists a Smartflo call only if it is to a customer number on a CRM account and the CRM has a trace of it close to the call: a "call placed" entry (click-to-call), or an outcome logged on the account, from 10 minutes before the call starts to 60 minutes after it ends. Everything else Smartflo holds is left out, and a recording can only be played for such a call. Each row says who in the CRM placed or logged it. Smartflo pages (50 records) are filtered one at a time, so a page can have few or no CRM calls; the screen says how many records were checked.
Calls that rang but were never logged in the CRM, and were not started from the CRM, do not appear.

## Follow-up: clearer "Smartflo refused (422)" messages
A 422 now shows Smartflo's field errors (for example "agent: ..."), and Create in Smartflo says whether the lead list or the campaign was the step that was refused. The lead list is kept, so pressing Create again reuses it.

## Follow-up: Smartflo lists are read in full
Smartflo returns lists (users, lead lists, campaigns, outcome lists) a page at a time. The CRM read only the first page, so a lead list that already existed further down was not found, and Create in Smartflo tried to make it again; Smartflo answers 422 to a duplicate. The lists are now read page by page (up to 30 pages of 100). A 422 or other refusal whose reply cannot be read now shows the start of the raw reply.

## Follow-up: plain names for Smartflo
Smartflo refused names with symbols (for example "CRM 8-15 DPD"; "DPD 0" and "Promise to pay" went through). The lead list and campaign names and descriptions the CRM sends are now letters, digits and single spaces only: "8-15 DPD" becomes "CRM 8 15 DPD". Campaigns already created keep their names.

## Follow-up: live campaign screen and call tracking (2026-11-09)

Reported on UAT with the Promise to pay campaign: the call connected but the customer window did not open, and the screen did not show the customer list or what happens after the first call ends.

What changed:
- The Campaigns screen (agent) now follows the prototype: progress counters, a "calling now" strip, the list of the agent's customers with a status each (Waiting, Calling, On a call, Outcome owed, Not answered, outcome name), tries and last-call time, and a log of calls in the session.
- The customer window opens by itself, once per call, when the customer picks up (or, if the page missed that, when the call ends and an outcome is owed).
- A call that rings but is never answered needs no outcome: it is released automatically, shown as "Not answered", and the next customer shows as Calling.
- Live calls are looked up by the agent's number first. If Smartflo returns nothing for that filter, the CRM looks at the account's live calls and keeps only those matching this agent's customers. The page shows a small line saying how many live calls were seen and how (by agent number, by customers' numbers, none), to diagnose if the pop-up still fails.
- New table DialAttempt (migration 0005): one row per call in a session. No phone numbers are returned by the session endpoint.

## Follow-up: send my customers again from scratch (2026-11-09)
Smartflo remembers which leads it has dialled and waits (retry_after_minutes, 120) before retrying, so customers tried earlier in a test are not rung again: the agent's line rings but no customer is dialled. The Campaigns screen has a new button, "Send my customers again from scratch": it deletes this agent's leads of today from the Smartflo list (`DELETE /v1/broadcast/leads/<list>`, field_2 = account id), forgets today's pushes and sends them again. `POST /api/dialer/session/resend`, audited as `campaign_resent`. If Smartflo refuses the delete, the customers are still sent (the list overwrites duplicates) and a warning is shown.

## Follow-up: release the agent without a mapped outcome, End call in the pop-up (2026-11-09)
UAT: after the first call the CRM said "no Smartflo disposition is set for Connected · PTP given", so Smartflo was never told the call was finished and did not dial the second customer; also the customer window had no End call button.
- When an outcome has no Smartflo id, the CRM now borrows a similar one (same group, then any "Connected" outcome for a connected call, then any mapped one), so the agent is released and the next customer is dialled. The CRM keeps the real outcome. If no outcome at all has an id, the old warning stays. The proper fix is still to map all 11 ids (Campaigns, Setup, Call outcomes in Smartflo: the Create button finds the list "CRM call outcomes" and saves every id).
- End call: button in the customer window and in the "calling now" strip. The hang-up also finds the call through the account's live calls when Smartflo's agent filter shows nothing.

## Follow-up: clearing leads, and what Smartflo sees (2026-11-09)
UAT: "Send my customers again from scratch" got `422 {"field":["The selected field is invalid."]}` from `DELETE /v1/broadcast/leads/<list>` with field_2. Smartflo's guide gives no allowed values for `field`. The CRM now tries the list's column names in turn ("Account ID", "Phone Number", then snake_case) and uses the first Smartflo accepts; if none works it still resends and shows a warning. New "Check what Smartflo sees" button (`GET /api/dialer/session/leads`, reading `GET /v1/broadcast/leads/<list>`): per customer Smartflo's dial_status, dialer_dial_status, call_count and retry_count, or "not found in the list". No phone numbers are returned.

## Follow-up: clearing leads by Smartflo's own lead ids (2026-11-09)
Smartflo's guide for `DELETE /v1/broadcast/leads/<list>` names a required `field` but lists no values; field_0, field_2 and the column names were refused. There is no "reset lead status" endpoint in the docs (the closest are `PUT /v1/broadcast/lead/<lead id>` and `PATCH /v1/broadcast/leads/<list>`, neither documented to touch dial status; campaign `PUT /v1/dialer/campaign/<id>` only sets retry rules). The CRM now reads the list (`GET /v1/broadcast/leads/<list>`), takes each of the agent's leads' `lead_id`, and tries: bulk delete with field `lead_id`, then `id`, then `DELETE /v1/broadcast/lead/<lead_id>` one at a time, then the column names. The warning on screen lists what Smartflo answered to each try, so the accepted form can be fixed in code.

## Follow-up: dial rules (retry statuses) and a louder outcome prompt (2026-11-09)
UAT "Check what Smartflo sees": the second customer showed dial_status New / dialler status Undisposed, 1 call. Smartflo dials only the lead statuses listed in the campaign's `dial_status`. We had sent ["0","3","9"]; per Smartflo's campaign reference the codes are New 0, Undisposed -1, DND -2/-3, Emergency -4, Call drop -5, Channel limit -6, Block list -7, Missed by agent -8, Queue missed -9 (3 and 9 are lead statuses of a different list, not campaign codes). So a customer Smartflo had already tried once was never dialled again. New default: ["0","-1","-5","-8","-9"] with number_of_retry 3 and retry_after_minutes 120.
- Existing campaigns: Admin, Campaigns, "Update dial rules" per connected campaign (`POST /api/dialer/smartflo/rules/<key>`, `all` for every campaign), which sends `PUT /v1/dialer/campaign/<id>` with those three settings.
- "Check what Smartflo sees" and the lead clearing now look each customer up by phone number (`GET /v1/broadcast/leads/<list>?number=`), because the list is shared by all agents and paged (a customer missing from page one was reported "not found"). At most 40 customers per click.
- After a call the agent must save the outcome before Smartflo dials the next customer (Smartflo's rule). The screen now shows an amber "Log the outcome now" panel with a button.

## Follow-up: outcome ids made automatically, saved outcomes passed on later (2026-11-09)
UAT: after a call the agent saved the outcome several times and saw "no Smartflo disposition is set for Connected · paid today". No outcome had a Smartflo id (the Admin outcome set-up had not been run), so even the borrowed-id fallback found nothing and Smartflo never freed the agent for the next customer.
- When an outcome has no Smartflo id the CRM now makes or finds the "CRM call outcomes" list in Smartflo and saves every id itself (same code as Campaigns, Setup, Call outcomes), then tells Smartflo.
- If the agent already saved the outcome while no id existed, the screen's next refresh passes that saved outcome to Smartflo as soon as an id exists (once every 15 seconds at most; no Smartflo call is made while there is still no id). The agent does not have to save it again.
- Smartflo takes no symbols in names, so the outcomes are made there with plain words and a letters-only code (Connected PTP given / ptpgiven, Connected paid today / paidtoday, Connected will pay no date / willpaynodate, Connected refuses to pay / refuses, Connected dispute or complaint / dispute, Connected needs senior agent / needssenior, Reference contacted / referencecontacted, Wrong number / wrongnumber, Not reachable or switched off / notreachable, No answer / noanswer, Call back later / callbacklater). The CRM finds them by these names or codes (or the old ones) and saves each Smartflo id.
