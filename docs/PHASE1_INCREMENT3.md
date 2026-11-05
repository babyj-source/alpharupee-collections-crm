# Phase 1, increment 3: admin module, priority list, MIS

| Area | What it does |
|---|---|
| Roles | **Admin** (runs the system, plus everything the Head can do), **Head** (all accounts, allocation, reports) and **Agent** (own accounts). The Roles tab on the Users screen is generated from `backend/accounts/roles.py`. |
| Users (Admin only) | Create a user (e-mail, name, mobile, role), edit name / mobile / role, activate or deactivate, unlock, reset password. A new or reset user gets a random one-time password shown **once** to the admin, and must choose their own at first sign-in; until then the server refuses every endpoint except "me", "sign out" and "change password". An admin cannot change their own role or deactivate themselves. Every action is in the audit log (never the password or number). |
| First admin | On the server: `manage.py create_crm_user admin@alpharupee.in --name "..." --mobile 91XXXXXXXXXX --role admin --must-change` (password typed at a hidden prompt). |
| Priority list | Ranked call list for the agent (own accounts) and the Head (all, filter by bucket and agent). Points: days past due (5 to 70), overdue amount (0 to 20), call-back due +50 / today +20, promise missed +45 / due today +40 / still to come -30, never called +15, not called for 3+ days +10, called today -10, in dispute -20. Each row shows the reasons. Weights live in `backend/loans/priority.py`; they are starting values, not signed off. |
| MIS (Head and Admin) | Period 1 / 7 / 30 / 90 days. Portfolio by bucket (0, 1-7, 8-15, 16+ DPD: accounts, overdue, outstanding principal, closure amount, not allocated), calls and contact rate, outcome mix, calls per day, promise performance (kept rate, amounts, missed and not closed), agent productivity. No collection-efficiency figure yet: repayments are not synced from the LMS. |
| Buckets | `loans/workflow.py` `BUCKETS` (0, 1-7, 8-15, 16+) are also the campaign pools for the dialler (next increment). |
