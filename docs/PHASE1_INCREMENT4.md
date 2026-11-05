# Phase 1, increment 4: Smartflo campaigns, click-to-call and auto-dialer

Ships **switched off**. Nothing calls Smartflo until `SMARTFLO_ENABLED=true` and an account token are set on the server.

## What it does

- **Campaign pools** (Head / Admin, menu *Campaigns*): six pools that never overlap: 0 DPD, 1-7 DPD, 8-15 DPD, 16+ DPD, Promise to pay, Call-back.
  Precedence: call-back due today (or earlier) > promise due today or missed > DPD bucket. An account with a promise or call-back still to come is held back.
  Eligible = overdue above zero, a valid 10-digit borrower mobile, status not Paid / Dispute. Only the borrower's main number is pushed.
- **Push to Smartflo**: preview the count, confirm, and the pool is sent to the campaign's lead list in chunks of 500 (`/v1/broadcast/lead/{list_id}`).
  Lead columns: `field_0` mobile, `field_1` name, `field_2` account id. The Smartflo list must have its columns in this order: **Mobile, Name, Account ID**. Duplicates are overwritten.
- **Agent Dialer** (menu *Dialer*): the agent picks a campaign, types their Smartflo password once (used to sign in, never stored; the CRM keeps only the 1-hour token, encrypted), and Smartflo dials by itself.
  The CRM polls `live_calls` every ~4 s, opens the account of the customer on the line, and the agent logs the outcome. The outcome is saved in the CRM and then sent to Smartflo as the disposition, which releases the agent for the next call.
- **Click-to-call**: a *Call* button next to each phone number on the account panel (also works without a dialler session, using the account token).
- **Shared pool**: any agent can open the account they are on a call with (3-hour grant). Lists and the dashboard still show only their own allocation.
- **Admin Setup** (tab in *Campaigns*): Smartflo campaign id and lead-list id per campaign, the "agents can start" switch, each agent's Smartflo e-mail and agent number, the 11 call-outcome to Smartflo disposition ids, and a connection test (shows field names only).

## Safety

- Master switch `SMARTFLO_ENABLED` (default false). `/api/meta` reports `dialer_enabled`; the screens say when it is off.
- Calling window 08:00 to 19:00 enforced when a session starts and on click-to-call.
- Throttles: dial 60/min, dialler login 5/min per user (Smartflo locks an agent out after 10 failed logins in 5 minutes).
- The Smartflo password is never stored or logged. Agent tokens are encrypted (Fernet, key derived from `SECRET_KEY`) and expire after 55 minutes.
- Phone numbers are not shown in the Dialer screen; the existing mask / reveal rules and audit log still apply.

## Assumptions to check on UAT (the API document does not settle these)

1. The login response field that holds the agent token, and the fields in `live_calls` (parsed defensively; if the shape is unknown the Dialer shows the field names it saw).
2. Header form per endpoint: the account token is sent raw for login, bulk leads and the connection test; the agent token is sent as Bearer for session create / session_call / logout, and raw for disposition, live_calls and click_to_call.
3. Meaning of `startOrEnd` / `logout` in `session_call`.
4. Which token click_to_call wants (we use the agent token when a session is active, otherwise the account token).
5. Lead-list column order (above).
6. There is no delete-lead call in the API document, so old leads stay in a list. Rotate to a fresh list when a pool gets stale.
7. There are no webhooks, so the CRM polls.

## Set-up steps (after deploy)

1. On the server: `bash /opt/crm/current/server-tools/set-smartflo.sh on` (asks for the long-lived account token with hidden input, switches the dialler on and restarts the app). `... set-smartflo.sh off` switches it off again.
2. In Smartflo create the six campaigns and six lead lists (columns Mobile, Name, Account ID).
3. *Campaigns, Setup*: enter the ids, tick "Agents can start" for the campaigns to run, map each agent's Smartflo e-mail and agent number, enter the disposition ids, press *Test connection*.
4. *Campaigns*: Push the first pool with a small test list, then have one agent start dialling.

## Tests

36 new backend tests (`dialer/tests/test_dialer.py`, Smartflo faked); 129 in total. Frontend build and a browser check of Campaigns, Setup and Dialer were run against a local server.
