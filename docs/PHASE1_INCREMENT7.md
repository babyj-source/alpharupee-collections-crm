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
