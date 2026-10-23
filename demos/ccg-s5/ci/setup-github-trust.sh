#!/usr/bin/env bash
# ONE-TIME and safe to re-run, in AWS CloudShell of the demo account (hoplitemarketing 480315222980, ca-central-1).
# Creates or updates the role GitHub Actions uses for the demos (main branch of serve7-io/amazon-connect-genai-accelerator).
set -euo pipefail
export AWS_DEFAULT_REGION=${REGION:-ca-central-1}
HERE=$(cd "$(dirname "$0")" && pwd)
STACK=serve7-ccg-s5-ci
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "Account $ACCOUNT"
# The GitHub OIDC provider is created by this very stack (resource GitHubOidc, condition CreateOidcProvider).
# On a re-run the provider therefore already exists. Passing CreateOidcProvider=false then makes CloudFormation DELETE it,
# and every GitHub deploy fails with "Could not assume role with OIDC". So: keep it when this stack owns it, and only
# reuse a provider that was created some other way.
OWNED=$(aws cloudformation describe-stack-resources --stack-name "$STACK" --logical-resource-id GitHubOidc \
  --query "length(StackResources)" --output text 2>/dev/null || echo 0)
EXISTS=$(aws iam list-open-id-connect-providers \
  --query "OpenIDConnectProviderList[?ends_with(Arn,'token.actions.githubusercontent.com')] | length(@)" --output text)
CREATE=true
if [[ "$OWNED" != "0" ]]; then
  echo "GitHub OIDC provider is managed by this stack - keeping it"
elif [[ "$EXISTS" != "0" ]]; then
  CREATE=false
  echo "GitHub OIDC provider already exists outside this stack - reusing it"
fi
aws cloudformation deploy --stack-name "$STACK" --template-file "$HERE/github-trust.yaml" \
  --capabilities CAPABILITY_NAMED_IAM --parameter-overrides CreateOidcProvider=$CREATE \
  --tags serve7:project=ccg-rfp serve7:scenario=5
aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs" --output table
