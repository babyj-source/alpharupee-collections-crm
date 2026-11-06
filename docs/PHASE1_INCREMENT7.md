# Increment 7, part 1: SMS templates (MSG91)

The six approved MSG91 SMS templates are stored in the CRM (`messaging` app). Nothing is sent yet; sending needs the MSG91
auth key and, for the payment-link templates, the Easebuzz link API.

| Key | Template | MSG91 id | Used for | Variables |
|---|---|---|---|---|
| demand | DemandRemainder-1 | 6a64b0274fec41f7f80f6403 | before the due date | EMI amount, due date, loan number |
| bkt0 | PaymentRemainder-BKT0 | 6ab622427f629adee5014d73 | DPD 0 | EMI amount, link |
| bkt1 | PaymentRemainder-BKT1 | 6ab62394e384b81a5009d1e2 | DPD 1-30 | days overdue, closure amount, link |
| bkt2 | PaymentRemainder-BKT2 | 6ab6233dd16941664d09be52 | DPD 31-60 | closure amount, link |
| bkt3 | PaymentRemainder-BKT3 | 6ab6205631857c9a860bea02 | DPD 61-90 | closure amount, link |
| npa | PaymentRemainder-NPA | 6ab6227c642c9e8a59037302 | DPD 91+ | link |

* "Outstanding" is the loan's closure amount for the day (whole rupee, half-up).
* The DPD ranges are my assumption (30-day buckets, NPA from 91). Admin can change them: `PUT /api/messaging/templates/<key>`
  with `dpd_min`, `dpd_max`, `active`. The MSG91 id and approved text cannot be edited from the CRM.
* `GET /api/messaging/templates` (head or admin) lists them.
* DemandRemainder-1 needs the next EMI due date, which the CRM does not hold yet, so the CRM refuses to build that message
  until the field is wired. No wrong date is ever sent.
* If no payment link exists, the link templates also refuse to build.
* BKT0/BKT3/NPA/demand are NORMAL SMS; BKT0, BKT1 and BKT2 are UNICODE (they contain the rupee sign).

## WhatsApp templates (MSG91, namespace 544dead2_efd4_43d5_a69a_1ec9b1dbeafc)

| Key | Template | MSG91 id | Used for | {{1}} {{2}} {{3}} {{4}} |
|---|---|---|---|---|
| wa_due2 | repay_due_in_2_days | 556471 | EMI due in 2 days | first name, EMI amount, due date (today + 2), link |
| wa_due1 | repay_due_tomorrow | 556469 | EMI due tomorrow | first name, EMI amount, due date (today + 1), link |
| wa_due0 | repay_due_today_img | 556481 | EMI due today (image header) | first name, EMI amount, due date (today), link |
| wa_overdue | repay_overdue_missed | 556730 | missed payment (image header) | first name, oldest due date, closure amount, link |

* Dates are written like 05-Oct-2026, as in the approved samples. "Outstanding" is the day's closure amount.
* The two image templates need a public https image link; until Admin sets it (`PUT /api/messaging/whatsapp/<key>` with
  `header_image_url`) the CRM refuses to build them. The WhatsApp sample image links in the template files are temporary
  and are not used.
* `GET /api/messaging/whatsapp` (head or admin) lists them. The text and ids cannot be edited in the CRM.
* Which loans are "due in 2 days / tomorrow / today" needs the next EMI due date, which the CRM does not hold yet (same
  gap as the SMS Demand Reminder). Missed payments (DPD 1 or more) need no extra field.

## Sending SMS (MSG91 Flow API) - part 2

Uses the same `MSG91_AUTHKEY` as the login OTP. **Off until switched on**:

```
sudo -iu deploy
bash /opt/crm/current/server-tools/set-sms.sh on     # asks for the https link to put in the SMS, then restarts
bash /opt/crm/current/server-tools/set-sms.sh off
```

* `POST /api/messaging/sms {"loan": <id>}`: sends the template for the loan's days past due. An agent can do this only for
  their own accounts and cannot pick the template or the link. Head and Admin may add `"template": "<key>"` and `"link": "https://..."`.
* `GET /api/messaging/log?loan=<id>`: what was sent for that account (number shown as xxxxxx1234; the full number is never stored).
* The `##url##` is `PAYMENT_LINK_DEFAULT` until the Easebuzz link is wired. Every payment-link template refuses to go without a link.
* Rules that stop a message: outside 08:00-19:00, account marked paid or in dispute, no valid borrower mobile, the same
  template already sent to that loan today, missing value (for example the Demand Reminder has no due date yet).
* A message MSG91 rejects is logged as failed (with MSG91's reason) and can be sent again; it does not count as sent.
* Limits: 30 sends a minute per user. Every send is in the audit log.
* There is no button on the account screen yet.

## Messages section on the account screen (part 3)

* The account drawer has a **Messages** section: it shows the template picked for the loan's days past due and the exact text the
  borrower will receive, with **Send SMS** and a confirm step, and the last messages sent (Sent / Failed, who, when).
* `GET /api/messaging/preview?loan=<id>` returns `{template, name, text, problem}`; `problem` says why it cannot go (switched off,
  outside 08:00-19:00, no link, already sent today ...). The preview and the send use the same rules.
* `/api/meta` now carries `sms_enabled` so the screen can say when sending is switched off.
* WhatsApp sending is not built yet (templates are stored; needs the header images, the next-EMI-due date and MSG91's WhatsApp API).

## Sending again, and the timeline (part 4)

* The same SMS can be sent to a borrower again on the same day. The screen warns ("already sent today at 12:59") and the button
  becomes **Send again**; the API needs `"again": true`. A hard cap of `SMS_MAX_PER_LOAN_PER_DAY` (default 3) SMS per loan per day holds
  even when confirmed. A failed attempt does not count.
* Every send, and every attempt MSG91 rejects, is written to the account **timeline** ("SMS sent to the borrower: PaymentRemainder-BKT2").
  The timeline never holds the text or the number; the Messages section shows the text.
