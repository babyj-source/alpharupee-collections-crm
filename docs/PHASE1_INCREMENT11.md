# Phase 1, increment 11: posting paid payment links to the LMS

When a customer pays through a payment link (Easebuzz), the CRM posts the repayment to the LMS with Encore's "flow collection" API
(`POST .../webservices/loans/accounts/postTransactionListDirect`, the sample Sensei sent on 11 Nov). Posting is OFF until switched on.

## What is sent
One transaction per paid link: `transactionName` Repayment, `accountId` = the loan account, `amount1` = the link amount, `valueDateStr` = the day it was
paid, `reference` = `accountCode:<bank account code>|instrumentRef:<our link reference, ARC...>`, `instrument`, `userId`, `currencyCode` INR,
`sequenceNum` -1, `safeMode` false, `actionType` EXECUTION, `commitSize` 1. The LMS user and password go as HTTP Basic authentication and are
never logged or shown. `manage.py post_lms_payments --link ARC... --dry-run` prints the exact request without sending it.

## When it is sent
The one-minute payment timer (`check_payment_links`) first asks Easebuzz about open links, then posts every paid link that waits (`pending`).
Link states: pending -> posting -> posted | failed. A link is claimed in the database before it is sent, so two runs can never send it together.

## Never twice
The LMS's answer is judged carefully:
- **posted**: the transaction's `status` or `responseCode` is in `LMS_POST_OK_WORDS` (default success, ok, executed, posted, 0, 00, 200) and no failure word is present.
- **rejected** (HTTP 4xx, or the LMS says failed/error/rejected/duplicate ...): nothing was posted; shown as Failed with what the LMS said.
- **could not be sent** (could not connect): tried again every minute, up to 5 times, then Failed.
- **unclear** (timeout, HTTP 5xx, connection broke while sending, an answer that is not recognised): the LMS may have it. It is marked Failed with a note, and the CRM does not retry on its own.
A link stuck "being posted" for over 10 minutes (the CRM stopped halfway) is marked Failed with the same warning.
Only an Admin can press **Post again** (Payments screen, failed rows), after checking the LMS. The Admin sees the LMS's answer on the row; agents do not.

## Server set-up (UAT)
```
sudo -iu deploy
bash /opt/crm/current/server-tools/set-lms-posting.sh login    # user id (crm) and password (hidden)
bash /opt/crm/current/server-tools/set-lms-posting.sh bank     # bank account code and instrument, as the LMS names them
bash /opt/crm/current/server-tools/set-lms-posting.sh status   # what is still missing
bash /opt/crm/current/server-tools/set-lms-posting.sh on
```
The UAT address is the default. For Production: `set-lms-posting.sh url https://<production LMS>/.../postTransactionListDirect` (the LMS team must allow the Production server).

## Not verified until the first real post on UAT
How the LMS authenticates this user (HTTP Basic is assumed), the exact words of its reply, which `instrument` and bank account code to use, and whether it
rejects a repeated `instrumentRef`. The first post shows all of this on the Payments screen (Admin) or with `post_lms_payments --link ... --send`.
Amount posted = the link amount (a link is for a fixed amount). Migration payments 0003.

## First UAT post (18 Nov)
The LMS answered HTTP 200 with a JSON **list** of the transactions (not `{"transactions": [...]}`), so the CRM called it "unclear" and marked the link Failed,
which is the safe direction. The reader now accepts both shapes, and the LMS's whole answer is kept on the link (`lms_reply`, payments migration 0004) and printed by
`post_lms_payments --link ... --send`, so the real success wording can be read and `LMS_POST_OK_WORDS` set from it.
