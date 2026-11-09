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
