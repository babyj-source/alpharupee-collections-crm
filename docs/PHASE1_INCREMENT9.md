# Phase 1, increment 9: manager reports, call log, automatic WhatsApp, call script

## What is new
| Screen | Who | What it does |
|---|---|---|
| Agent scorecard | Head, Admin | Every agent side by side for today / 7 / 30 / 90 days: accounts worked, calls, contact rate, calls dialled from the CRM, promises and kept, SMS and WhatsApp sent, payment links made and paid. Click a name for outcomes and calls per day. |
| My performance | Agent | The same numbers, only the agent's own. An agent cannot ask for another agent. |
| Call log | Head, Admin | Smartflo call records (today / 7 / 30 days, answered or missed, direction, agent), matched to the account by the customer's number. A recording plays on demand. Numbers are masked; the recording link (which carries Smartflo's token) is fetched one call at a time and each play is written to the audit log. |
| Messages | Head, Admin | WhatsApp sent and SMS sent logs (including automatic ones, "Sent by: Automatic") with search and state filters. The Automatic sending tab is where the Admin switches the automatic reminder on. |
| Audit log | Admin | Who did what (sign-ins, number reveals, calls, messages, recording plays, setting changes), filter by period, action, free text. |
| Priority points | Admin | Edit the points behind the priority list (DPD bands, amount, call-back, promise, never called, stale call, called today, dispute). Validated, audited, "Back to standard" resets. |
| Call script | Everyone reads, Admin edits | Four sections (opening, objections, payment, do and don't) with placeholder text. A "Call script" button in the account drawer shows it during a call. |

## Automatic WhatsApp (missed payment)
`manage.py auto_whatsapp [--dry-run] [--limit N]`, run every 30 minutes by `crm-whatsapp-auto.timer`.
- Only if the Admin ticked "Send automatically" on the missed-payment template (and it is Active).
- Only 08:00 to 19:00, only when WhatsApp sending is on.
- Skips paid, disputed, closed accounts, accounts with a promise date still to come, a call-back booked, anyone already sent a WhatsApp template today, no valid number. The usual 3 per loan per day cap still applies.
- Highest DPD first; stops after 5 failures in a row ("Stopped early"), so a bad template or MSG91 problem does not loop.
- Due-date reminders (2 days before, 1 day before, on the day) are still not automatic: the CRM does not hold the next EMI date.

## Server steps after deploy
1. `sudo bash /opt/crm/current/server-tools/install-whatsapp-auto-timer.sh` (once).
2. To see how many would go (sends nothing): `sudo -iu deploy bash -c 'set -a; . /opt/crm/shared/.env; set +a; cd /opt/crm/current/backend && /opt/crm/current/venv/bin/python manage.py auto_whatsapp --dry-run'`
3. Messages > Automatic sending > tick "Send automatically" on the missed-payment template.

## Notes and assumptions
- Smartflo call-record fields follow the public API reference; confirm on UAT that agent names, durations and recording links appear.
- Priority list default points are unchanged, so nothing moves until the Admin edits them.
- Migrations: loans 0008 (PriorityConfig, ScriptSection with 4 placeholder sections), messaging 0007 (`auto_send`).
- Script wording is a placeholder until the real script is sent; the Admin can paste it into the Call script screen.
