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
