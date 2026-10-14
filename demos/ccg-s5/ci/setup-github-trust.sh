#!/usr/bin/env bash
# ONE-TIME, in AWS CloudShell of the demo account (hoplitemarketing 480315222980, ca-central-1).
# Creates the role GitHub Actions uses for the ccg-s5 demo (main branch of serve7-io/amazon-connect-genai-accelerator).
set -euo pipefail
export AWS_DEFAULT_REGION=${REGION:-ca-central-1}
HERE=$(cd "$(dirname "$0")" && pwd)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "Account $ACCOUNT"
EXISTS=$(aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[?ends_with(Arn,'token.actions.githubusercontent.com')] | length(@)" --output text)
CREATE=true; [[ "$EXISTS" != "0" ]] && CREATE=false && echo "GitHub OIDC provider already exists - reusing it"
aws cloudformation deploy --stack-name serve7-ccg-s5-ci --template-file "$HERE/github-trust.yaml" \
  --capabilities CAPABILITY_NAMED_IAM --parameter-overrides CreateOidcProvider=$CREATE \
  --tags serve7:project=ccg-rfp serve7:scenario=5
aws cloudformation describe-stacks --stack-name serve7-ccg-s5-ci --query "Stacks[0].Outputs" --output table
