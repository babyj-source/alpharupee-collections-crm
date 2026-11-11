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

## Link shortening (part 5)

Payment-link templates are sent with MSG91 `"short_url": "1"` so MSG91 shortens the link (a long link failed DLT with "Template variable
exceeded max length"). Templates without a link variable keep `"short_url": "0"`.

## WhatsApp: template messages and chat with the customer (part 6)

**Status: built and tested against a fake MSG91. The MSG91 WhatsApp request shapes were written from memory (MSG91's documentation could
not be read from here), so the first real send and the first real inbound message must be checked.** If MSG91 refuses, the error text is
shown on the screen and stored on the message; adjust `MSG91_WHATSAPP_TEMPLATE_URL` / `MSG91_WHATSAPP_TEXT_URL` or `msg91.py` to the spec.

* **Template message:** the account screen shows the WhatsApp template picked for the loan (only the overdue template is picked
  automatically for now) and the exact text, with **Send on WhatsApp**, a confirm step and **Send again**. Same rules as SMS: 08:00-19:00,
  not paid / dispute, valid mobile, 3 per loan per day (`WHATSAPP_MAX_PER_LOAN_PER_DAY`), repeat needs confirmation.
  Image-header templates (`wa_due0`, `wa_overdue`) refuse until an admin sets `header_image_url` (`PUT /api/messaging/whatsapp/<key>`).
* **Chat:** the same section shows the conversation (customer messages on the left, ours on the right, with Sent / Delivered / Read / Not
  delivered). It refreshes every 8 seconds. Free text can be typed only while the customer's 24-hour window is open (they wrote to us in the
  last 24 hours); otherwise the box says to use the template.
* **WhatsApp chats** (menu item for every role) is laid out like WhatsApp Web: conversations on the left (newest first, unread count, search by
  name or account number), the open chat on the right with day separators and ticks (sent, delivered, read). Searching an account that has no
  chat yet offers **Start a new chat** (the first message must be the approved template). The chat header has **Call** (borrower, same
  Smartflo click-to-call as the account screen, with Ringing / Connected / End call) and **Account details** (opens the full account to log
  the outcome). Users see only their own accounts (same visibility as the worklist). On a phone the list and the chat show one at a time.
* **Webhook:** `POST /api/messaging/whatsapp/webhook?token=<WHATSAPP_WEBHOOK_TOKEN>` receives the customer's replies and delivery
  status from MSG91. A customer is matched to the loan by the last 10 digits of the borrower's (or alternate) mobile; a message from a
  number that is not in the CRM is kept in the webhook log only. Admin can look at the last 30 bodies (numbers masked) at
  `GET /api/messaging/whatsapp/webhook-log` to confirm the format MSG91 really sends.
* **Privacy:** only the last 4 digits of a number are stored on a message; the timeline records "WhatsApp sent/failed" without the text.
* **Switch on (server):** `sudo -iu deploy`, then `/opt/crm/current/server-tools/set-whatsapp.sh number <business number>`, `... token`
  (prints the webhook URL to put in MSG91's inbound and status webhooks), `... on`. `off` switches it off. `/api/meta` carries `whatsapp_enabled`.
* **Not built: WhatsApp calling.** Needs the MSG91 WhatsApp calling specification; Easebuzz is a payment gateway and has no WhatsApp calls.
* Still needed: the next-EMI-due date (for the due-date reminders), header image URLs, the business number.

## Easebuzz payment links (part 7)

Built from Easebuzz's EasyCollect documentation ("Sub-merchant Create/Update Link" and "Get Easycollect Link"). Sandbox first.

* **Make a link:** the account screen, **Messages → Payment link**: shows the closure amount as on its date, **Make payment link**, a confirm step.
  The amount is the loan's closure amount (whole rupee). `udf1` is `CRM`, `udf2` is the LAN (account number); the name and 10-digit mobile are the
  borrower's; the link works until the end of today + `PAYMENT_LINK_VALID_DAYS` (default 2) days. The request is signed with Easebuzz's rule
  `sha512(key|merchant_txn|name|email|phone|amount|udf1..udf5|message|salt)`; the salt stays on the server.
* **Same day, same amount = the same link** (no duplicates). A changed closure amount makes a new link; older links stay payable at Easebuzz until they expire.
* **Stale amount:** if the closure amount is not for today (the LMS sync is stopped or late) the CRM stops and asks "Make the link anyway?".
* **Messages use the real link:** with Easebuzz on, an SMS/WhatsApp with a payment link carries only today's active link for that loan. With no such link the
  message refuses ("make the payment link first"): the dummy `PAYMENT_LINK_DEFAULT` is never sent when Easebuzz is on.
* **Payment tracking:** the CRM asks Easebuzz (`GET /easycollect/v1/get`, `payment_made`) every 20 seconds while the account is open, and
  `manage.py check_payment_links` does the same for every open link made in the last 7 days (run every minute by `crm-payments.timer`, installed with
  `deploy/server/install-payments-timer.sh`). A paid link shows "Paid", writes "Payment of ₹X received" on the timeline and is marked **waiting to be posted to the LMS**.
  Polling alone is enough; the webhook below makes it instant.
* **Not built yet: posting the payment to the LMS** (needs Encore's repayment-posting API; the CRM's LMS access is read-only). Paid links keep `lms_state = pending`.
* **Unverified until the first sandbox payment:** what Easebuzz returns in `payment_made` / `state` for a paid link (the documentation shows only unpaid examples). The CRM treats `payment_made >= 1` as paid.
* **Switch on (server, as deploy):** `set-easebuzz.sh key` (asks for the key and the hidden salt), `set-easebuzz.sh submerchant <id>`, `set-easebuzz.sh sandbox`, `set-easebuzz.sh on`. `status` shows what is set. `production` switches to the live address.
* **DLT:** the link's domain (pay.easebuzz.in) must be registered/whitelisted for the SMS templates, or operators may block the SMS.

### Easebuzz webhook (part 8)

* Easebuzz's **Transaction Webhook** (form-urlencoded POST whenever a payment changes state; only a 200 answer is accepted, retried every 30 minutes up to 5 times) is
  received at `POST /api/payments/webhook?token=<EASEBUZZ_WEBHOOK_TOKEN>`. Register it in the Easebuzz Merchant Dashboard under Account Settings (Transaction webhook).
* **The webhook never marks a payment paid by itself.** It finds the link (by `txnid` = our `merchant_txn`, or by `udf1 = CRM` and `udf2` = the LAN) and then makes the signed status call
  (`GET /easycollect/v1/get`). A forged or odd call therefore cannot create a payment. If Easebuzz cannot be reached at that moment, the one-minute check picks it up.
* Admin can read the last 30 calls at `GET /api/payments/webhook-log` (only txnid, status, amount, udf1/udf2, mode, easepayid, error, bank ref; no phone, e-mail or card data, plus `hash_ok`:
  whether Easebuzz's response hash matched our reading of its documented order, for information only).
* Make the address on the server: `set-easebuzz.sh token` (prints it). Unverified until the first real call: whether `txnid` is our `merchant_txn` for EasyCollect payments and whether `udf1`/`udf2`
  carry over from the link; the lookup by `udf1`/`udf2` covers the second case.

### Payments screen (part 9)

* Menu item **Payments** (every role; agents see links on their own accounts, head and admin see all). Tiles: waiting for payment, paid, waiting to post to the LMS, LMS posting failed,
  posted to the LMS, expired (count and amount; click a tile to filter). Filters: search (account number, name, link reference), made in (today / 7 / 30 days / all), link status, LMS status.
  The table shows made, account, borrower, amount, link status, paid at, LMS posting status, agent, and **Check** (asks Easebuzz now) for links waiting for payment. A row opens the account. Refreshes every 30 seconds.
* API: `GET /api/payments/links?state=&lms=&q=&days=&page=` and `GET /api/payments/summary?q=&days=`. LMS values: pending / posted / failed; `lms=notpaid` = links not paid.
* **LMS posting itself is not built** (needs Encore's repayment-posting API), so every paid link shows "Waiting to be posted to the LMS". The "posted" and "failed" tiles are ready for it; the Retry button for Admin/IT arrives with posting.

### WhatsApp call buttons (part 10)

**What MSG91 offers.** MSG91's only WhatsApp calling API is "WhatsApp Voice Call" (`POST /api/v5/whatsapp/integrated-number/call-settings`). It is a settings call: switch calling on or off for the business number, set call hours and holidays (max call 25 minutes, silence timeout 30 seconds). No MSG91 endpoint that places a call was found, and a business-initiated WhatsApp call also needs the customer's permission and a voice connection on the caller's side. So the CRM cannot make MSG91 ring a customer on WhatsApp.

**What was built.**
- A green **WhatsApp call** button in the WhatsApp chats header and, separately from the Smartflo **Call**, on every contact in the account screen's "Who to call" list.
- The button asks `POST /api/messaging/whatsapp/call` {loan_id, which}. The server checks the same things as the Smartflo call (user can see the loan, account open, 08:00-19:00, valid mobile), writes "WhatsApp call opened to ..." on the timeline, audits `whatsapp_call_opened`, and returns a `https://wa.me/91XXXXXXXXXX` link. The browser opens it in the agent's own WhatsApp (app or WhatsApp Web) and the agent taps the call icon. The call is made by WhatsApp from the agent's WhatsApp account, not from the business number 918065075438.
- `manage.py whatsapp_calling enable|disable [--hours]` switches calling on or off for the business number through the MSG91 settings API (so customers can call the business). The request body is the doc's plus `integrated_number`, which the doc's 400 message implies but does not show. MSG91's reply is printed as is. If it is refused, do it in the MSG91 panel.
- Flag `WHATSAPP_CALL_ENABLED` (default off) controls the buttons; `/api/meta` returns `whatsapp_call_enabled`.

**Switching on (server, as deploy):**
```
bash /opt/crm/current/server-tools/set-whatsapp.sh calls on
bash /opt/crm/current/server-tools/set-whatsapp.sh calling enable      # optional: add --hours for 08:00-19:00 only
```
Not verified until used: MSG91 accepting the call-settings body, and whether Meta has Calling approved for the number.

## Photos, documents and button replies in the WhatsApp chat (added Nov 2026)

What was wrong: the webhook kept only the customer's *text*. A photo or document arrived as a bare `[image]`, and a button the customer
tapped arrived as `[button]`. The chat also had no way to send a file.

What it does now:

- **Inbound files.** The webhook reads the file object MSG91 sends (image, document, audio, video, sticker; nested objects sent as JSON
  strings are unwrapped), downloads the file at once (https only, public hosts only, 16 MB at most) and keeps it in the CRM database
  (`WhatsappMedia`). The chat shows pictures in place, plays voice notes and videos, and offers documents as a download. Files are served
  from `/api/messaging/whatsapp/media/<id>` to a signed-in user who can see that account; anything the browser could run (html, svg ...)
  is only ever offered as a download. If MSG91 sent no link, the bubble says so.
- **Button replies.** The title of the button or list row the customer chose is shown ("Pay now", "I need more time"), tagged "Tapped a button".
  Locations, contacts and reactions are described in words; any other new kind shows "Sent a ... message that the CRM cannot show".
- **Sending a file.** The chat has an **Attach** button (JPG or PNG up to 5 MB; PDF, Word, Excel, PowerPoint, text, CSV up to 10 MB), with an
  optional caption. Only inside the 24-hour window, like free text. The file's type is checked from its first bytes. MSG91 fetches it from a
  signed public link that names that one file and expires after 6 hours (`/media/<id>/public?t=...`). The CRM's public address comes from the
  request, or from `PUBLIC_BASE_URL` in `.env` (must be `https://`).
- **Not yet checked against MSG91:** the exact body of the media-send call. The CRM first sends `image: {link, caption}` (WhatsApp Cloud API
  style); if MSG91 answers 4xx it retries once with a flat `media_url` body. If both are refused the message shows as failed with MSG91's reply
  under it. The address can be changed with `MSG91_WHATSAPP_MEDIA_URL` (default: the same address as free text). Test it once on UAT with a
  real customer chat.
- Files the customer sent *before* this change were stored as plain `[image]` text and cannot be recovered.
