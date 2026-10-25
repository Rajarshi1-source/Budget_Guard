# Copied to deploy/compose/secrets/ by `make secrets` (that folder is git-ignored).
# slack_*_url: Slack incoming-webhook URLs. The placeholders point at 127.0.0.1:9, so nothing
#   leaves the machine until real URLs are set.
# bg_token: the bearer Alertmanager sends to the platform; must equal BG_ALERTMANAGER_TOKEN in .env.
#   Generate with: openssl rand -base64 32
