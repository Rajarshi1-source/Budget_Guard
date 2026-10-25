import os

# Settings are read lazily; set the environment before any budgetguard import.
os.environ.update({
    "BG_GITHUB_REPO": "ricky/budgetguard",
    "BG_GITHUB_REPO_ID": "1001",
    "BG_GITHUB_OWNER_ID": "2002",
    "BG_GITHUB_INSTALLATION_ID": "99",
    "BG_GITHUB_APP_ID": "123",
    "BG_GITHUB_APP_PRIVATE_KEY": "unused-in-tests",
    "BG_GITHUB_WEBHOOK_SECRET": "whsec",
    "BG_ALERTMANAGER_TOKEN": "amtoken",
    "BG_DASHBOARD_TOKEN": "dashtoken",
    "BG_DASHBOARD_TOKEN_NEXT": "dashtoken-next",
    "BG_APPROVER_IDS": "[501]",
})
