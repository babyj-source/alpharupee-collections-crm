# Phase 1, increment 2: allocation, call outcomes, promises, references

Built from the prototype (v2/v3). Everything here works on LMS data already synced; nothing needs Smartflo or MSG91.

## What is built

| Area | Behaviour |
|---|---|
| Call outcome | On any active account an agent (own accounts) or the head logs one of the prototype's 11 outcomes (plus "Reference contacted"). The outcome sets the CRM status, is shown as "Last outcome" in the lists, and is written to the account timeline. The list of outcomes and reasons comes from the server (`/api/meta`), so the screen cannot drift from the rules. |
| Promise to pay | Outcome "PTP given" needs a date within 7 days (today included), a reason, and an amount (default: the overdue amount). A new promise replaces the open one. "Paid today" closes it as kept. The agent can mark a promise kept or broken; the sync marks it kept by itself once the LMS shows nothing overdue. Dashboard shows open, due today and lapsed. |
| Promises screen | Open, due today, lapsed, closed, all. Agents see their own accounts' promises, the head sees all. |
| Call-backs | Outcome "Call back later" needs a date (within 7 days) and a time slot (half-hourly, 08:00 to 18:30, today's passed slots disabled). It appears in a Call-backs screen (due now, today, upcoming) and on the dashboard. "No answer" and "Not reachable" keep it open; any outcome after reaching the borrower, or "Mark done", closes it. |
| Closure amount | Shown on the account and in the CSV report with the LMS date it is for. The LMS holds no ready value, so it is calculated in the sync: principal outstanding + normal and penal interest due + fees due + interest booked but not yet due. Check it against the closure figure Encore shows for the same account. "Outstanding principal" is the LMS balance, which equals disbursed minus principal repaid on every active account. |
| Timeline | Outcomes, status and note changes, allocations, reference changes, promise closures: who and when. It never holds phone numbers. |
| Reference contacts | Two slots per account. Agents add, edit or remove them on the account; the head can bulk-load from a CSV (preview first). Reference 1 is also filled from the LMS (loan account UDF fields). Anything typed or loaded in the CRM is CRM-owned: it wins over the LMS copy and a sync never overwrites them. Numbers are validated (10-digit mobile, not the borrower's own), masked in the API, and every reveal is logged with `ref1`/`ref2`. |
| Bulk allocation | Head uploads or pastes a CSV (`loan_id`, `agent` as e-mail, name or `unassigned`). The file is checked first: counts, row-by-row result, agent load before and after, error rows downloadable. Nothing changes until Apply. Tick-box assignment also goes into the history. |
| Allocation history | Who, when, file name, rows, moved, unchanged, rejected. Also in the audit log. |
| Report download | Head only: CSV of the accounts that match the current filters (19 columns incl. assigned agent, last outcome, open promise). Mobile numbers masked, text cells neutralised against spreadsheet formulas, rate limited, every download logged. |
| My customers | Agents see everything allocated to them (overdue, all active, or including closed) next to "My worklist". |

## Differences from the prototype (on purpose)

- **No "new case" creation from the allocation file.** Accounts come from the LMS only; a loan that is not in the CRM yet is rejected and appears after the next sync.
- Calls, recordings, SMS, WhatsApp, the dialler, campaigns, priority score, MIS pages, CRIF and Push to Deal are not part of this increment.
- The prototype's report has 30 columns including call counts, priority and consent flags. Call counts and priority need the calling and priority modules; this report has the 19 that exist today.

## Database

Migration `loans/0003_workflow` adds `Activity`, `Ptp`, `ReferenceContact`, `AllocationBatch` and two columns on `Loan`. It only adds; nothing is rewritten. Accounts migration `0002_role_labels` (earlier release) already changed the role labels.

## Assumptions to confirm

- Outcome list, the 7-day promise limit and the reasons are the prototype's values, not yet signed off by Collections leadership.
- "Paid today" is an agent's claim; the LMS repayment remains the truth for money.
- A promise is "kept" when overdue is zero in the LMS; part payments leave it open for the agent to judge.
