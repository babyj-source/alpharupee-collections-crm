# Phase 1, increment 6: forgot password with an SMS code

A *Forgot password?* link on the sign-in page.

1. The user enters their e-mail. The CRM sends a 6-digit code by SMS (MSG91, the same sender and template as the sign-in code) to the **mobile number registered for that account**. The user cannot choose or see the number.
2. The user enters the code and a new password (at least 10 characters, same rules as everywhere else) twice.
3. The password is changed. The user is **not** signed in by this: they sign in with the new password and a fresh sign-in code, so two-factor is never skipped.

## Safety

- The screen gives the same answer whether or not the e-mail is registered (unknown, inactive and registered e-mails look identical, including at the code step), so it cannot be used to find out who has an account.
- Code rules are the sign-in ones: valid 10 minutes, single use, burned after 5 wrong guesses, at most 5 codes per account per hour, stored only as a hash. A weak password is rejected before the code is checked, so it does not use up the code.
- The reset window is 15 minutes from the first step, tied to the browser session that asked for it.
- A successful reset also clears a lockout and the forced "change your password" flag, and signs out every session the user had open under the old password.
- Audit log entries: `reset_requested`, `reset_blocked`, `reset_code_failed`, `password_reset`, `otp_send_failed`. They never hold the code, the password or the number.
- If SMS delivery fails or the hourly limit is hit, the user sees the same message and nothing is sent; the failure is in the audit log. The page tells them to ask their administrator, who can still reset a password under *Users and roles*.

## Needs MSG91

This only works where MSG91 sign-in codes already work (auth key and approved template set on the server). Reset codes use the same template, so no new DLT approval is needed.

## Tests

10 new backend tests (full flow, no account enumeration, wrong / burned / expired / used code, weak password keeps the code, timeout, lockout cleared and old sessions ended, SMS failure and rate limit, nothing secret in the audit log, throttle). 154 in total. Browser check of the whole flow done.
