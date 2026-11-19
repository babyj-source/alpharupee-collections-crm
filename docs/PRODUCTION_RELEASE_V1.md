# First Production release (v1.0.0) and Production keys

The Production server and database exist; nothing has been deployed to them yet. Order matters.

## A. Before the tag (once)
1. DNS `crm.alpharupee.in` points to the Production Elastic IP; HTTPS is on (`enable-https.sh`); `setup-env.sh <prefix> crm.alpharupee.in production` has been run (MSG91 key and OTP template are asked there; Smartflo and MSG91 values are the same as UAT).
2. `scripts/github-production.sh` has been run (reviewers, variables, secrets). Check GitHub > Settings > Environments > production.
3. The LMS team has allow-listed the Production Elastic IP for the read-only replica, and Smartflo has been asked whether it restricts API access by IP.
4. `main` is green and UAT is verified.

## B. Release
`bash ~/alpharupee-collections-crm/scripts/release-production.sh v1.0.0` checks the account, main, the tag, CI and UAT health, shows what ships, asks for `DEPLOY v1.0.0`, then pushes the tag. A reviewer approves in Actions > Deploy to Production. Prefer after 19:00 IST.
Then create the first admin (see AWS_PRODUCTION_SETUP.md, step 9).

## C. Keys (on the Production server, as the deploy user, after the release is live)
`bash /opt/crm/current/server-tools/production-keys.sh` asks, one part at a time and with hidden input, for: Smartflo token and caller number; Easebuzz Production key, salt, sub-merchant id (then the webhook secret); the LMS read-only connection (from Secrets Manager); LMS posting (Production address, user, password, bank code, instrument); SMS; WhatsApp. It refuses UAT, refuses an LMS address that looks like a test one, and restarts the app once. It prints the two webhook addresses once: register them in the Easebuzz dashboard and in MSG91.

## D. After the keys
- `sudo bash /opt/crm/current/server-tools/install-payments-timer.sh` (needed for payments and LMS posting every minute).
- Post one small real payment through a Production link and check it in Encore (the transaction id must be Easebuzz's id) before agents send links.
- Install the sync timer only after the LMS allow-list works; WhatsApp timer only for automatic reminders.
- Restore test, alarm test, rollback test (AWS_PRODUCTION_SETUP.md, step 13); HSTS to one year after a clean week.
