# Phase 1, increment 8: agent Campaigns screen and Smartflo set-up from the CRM

## Why agents saw no campaigns
The agent menu was called **Dialer** and its dropdown listed only campaigns that were *switched on by the head* **and** had Smartflo ids. On a fresh UAT none are, so the list was empty and said only "No campaign is switched on yet".

## Agent screen (menu **Campaigns**)
- Every campaign is listed with how many of the agent's own customers are in it and a status: **Ready**, **Switched off by your head**, or **Not connected to Smartflo yet. Ask the Admin.**
- Pick a campaign (only Ready ones can be picked), type the Smartflo password once, **Start**. Behaviour after Start is unchanged (customers pushed to the lead list, Smartflo rings them, the agent logs an outcome to get the next call).

## Smartflo set-up from the CRM (Admin: Campaigns > Setup and Campaigns > Campaigns)
Built from Smartflo's public API reference (docs.smartflo.tatatelebusiness.com), which documents what to send for the create calls but not what comes back (only success and message). So after each create the CRM reads the lists back and finds the new item **by name**; anything it cannot find is reported and the id can still be typed in by hand. Running a step twice adopts what already exists in Smartflo.

1. **Load Smartflo users** (`GET /v1/users`): pick the Smartflo user for each CRM agent; this fills the login id and agent number.
2. **Create outcomes in Smartflo**: `POST /v1/dialer/disposition_list` ("CRM call outcomes"), then `POST /v1/dialer/disposition_status` for each of the 11 outcomes; the ids are saved in the outcome table.
3. **Create in Smartflo** on a campaign: `POST /v1/broadcast/list` (columns Phone Number, Name, Account ID = field_0, field_1, field_2) and `POST /v1/dialer/campaign` named `CRM <campaign name>`. Needs the outcomes list, an outgoing number on the server (`set-smartflo.sh caller <number>`), and at least one CRM agent whose login id matches a Smartflo user (those users become the campaign's agents).
4. Review the campaign in the Smartflo portal, then **Switch on** here.

Campaign settings chosen by the CRM (assumptions: please review in Smartflo): ratio dialling 1:1 (one customer per free agent), Dial Out (Session), agent mobile, oldest lead first, 3 retries 120 minutes apart, 30 s ring, 60 s wrap-up, dial statuses New / Not reachable / Busy, lead details shown.

## Fix found in the vendor documentation
The CRM saved call outcomes at `/v1/dialer/disposition_details_update` (from the Postman collection). The API reference shows `/v1/dialer/store-disposition`. The CRM now uses the documented address (`SMARTFLO_DISPOSITION_PATH`) and falls back to the old one once on a 404. The reference also has **Delete Bulk Leads** (`DELETE /v1/broadcast/leads/{id}`), so the earlier note that Smartflo has no delete-lead call was wrong (the client has `delete_leads`; not used yet).

## Not verified against the live account
Reply shapes of the create and list calls, whether Smartflo accepts the campaign defaults above, `/v1/dialer/session/create` (not in the public reference), and the agent login response.

## Tests
New: `dialer/tests/test_setup.py` (17). Full suite passes.
