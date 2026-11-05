"""What each role may do. The Roles screen reads this, so the screen and the rules cannot drift apart."""

ROLES = [
    {
        "role": "admin",
        "label": "Admin",
        "summary": "Runs the system: users and roles, plus everything the Head can do.",
        "can": [
            "Create, edit, activate and deactivate users; reset passwords; unlock accounts",
            "Everything a Head can do",
        ],
    },
    {
        "role": "head",
        "label": "Head",
        "summary": "Runs collections: sees every account and the team's numbers.",
        "can": [
            "See all accounts, the dashboard, the priority list and the MIS reports",
            "Allocate accounts to agents (tick-box or CSV) and load reference contacts",
            "Download the loan report (numbers masked)",
        ],
    },
    {
        "role": "agent",
        "label": "Agent",
        "summary": "Works the accounts allocated to them.",
        "can": [
            "See only their own accounts, call-backs, promises and priority list",
            "Record call outcomes, promises to pay, notes and reference contacts",
            "Reveal a phone number when needed (every reveal is logged)",
        ],
    },
]
