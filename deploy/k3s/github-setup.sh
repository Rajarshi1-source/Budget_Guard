#!/usr/bin/env bash
# GitHub side of the gate (plan W5.11). Run once by a repository admin with `gh` signed in:
#
#   deploy/k3s/github-setup.sh <owner/repo> <app-id> <kubeconfig-deployer.yaml> <budgetguard-api-url> <dockerhub-ns>
#
# Prerequisite (browser, your account): create the GitHub App from deploy/k3s/github-app-manifest.json
# (Settings -> Developer settings -> GitHub Apps -> New, or the manifest flow), keep it private
# ("Only on this account"), install it on this repository only, and download its private key.
#
# Per production-<service> environment this sets: no admin bypass (can_admins_bypass false), deploys
# from protected branches only, BudgetGuard as the custom protection rule, and KUBECONFIG as an
# ENVIRONMENT secret, so a job without `environment:` has no cluster credentials at all (T-3).
set -euo pipefail
REPO=${1:?owner/repo}
APP_ID=${2:?numeric GitHub App id}
KUBECONFIG_FILE=${3:?deployer kubeconfig written by bootstrap.sh}
API_URL=${4:?e.g. https://api.budgetguard.example.dev}
DOCKERHUB_NS=${5:?Docker Hub namespace}
SERVICES=(auth-service orders-service inventory-service notifications-service)

for svc in "${SERVICES[@]}"; do
  env="production-$svc"
  echo "== $env"
  gh api -X PUT "repos/$REPO/environments/$env" --input - >/dev/null <<JSON
{ "can_admins_bypass": false,
  "deployment_branch_policy": { "protected_branches": true, "custom_branch_policies": false } }
JSON
  existing=$(gh api "repos/$REPO/environments/$env/deployment_protection_rules" \
             --jq "[.custom_deployment_protection_rules[] | select(.app.id == $APP_ID)] | length")
  if [ "$existing" = "0" ]; then
    gh api -X POST "repos/$REPO/environments/$env/deployment_protection_rules" -F integration_id="$APP_ID" >/dev/null
  fi
  gh secret set KUBECONFIG --env "$env" --repo "$REPO" < "$KUBECONFIG_FILE"
done

gh variable set BUDGETGUARD_URL --repo "$REPO" --body "$API_URL"
gh variable set DOCKERHUB_NS --repo "$REPO" --body "$DOCKERHUB_NS"
echo "Set the repository secret DOCKERHUB_TOKEN and variable DOCKERHUB_USER yourself (gh secret set ...)."
echo "Then delete $KUBECONFIG_FILE: the only copy that should remain is in the four environments."
