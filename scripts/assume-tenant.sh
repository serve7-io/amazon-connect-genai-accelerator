#!/usr/bin/env bash
# Print export lines for temporary credentials in a tenant member account (AWS Organizations).
#   eval "$(scripts/assume-tenant.sh 123456789012)"            # switch this shell to the tenant account
#   unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN   # switch back
# ROLE defaults to OrganizationAccountAccessRole (created by organizations create-account).
set -euo pipefail
ACCOUNT="${1:?usage: assume-tenant.sh <account-id> [role-name]}"
ROLE="${2:-${ROLE:-OrganizationAccountAccessRole}}"
read -r AK SK ST < <(aws sts assume-role --role-arn "arn:aws:iam::${ACCOUNT}:role/${ROLE}" \
  --role-session-name "serve7-onboarding-$(date +%s)" --duration-seconds 3600 \
  --query "Credentials.[AccessKeyId,SecretAccessKey,SessionToken]" --output text)
echo "export AWS_ACCESS_KEY_ID=$AK AWS_SECRET_ACCESS_KEY=$SK AWS_SESSION_TOKEN=$ST"
echo "echo 'Now acting in account ${ACCOUNT} as ${ROLE} (1 hour)' >&2"
