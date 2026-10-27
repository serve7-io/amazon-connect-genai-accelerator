#!/usr/bin/env bash
# Read-only check that the current identity can deploy Scenario 5 (CloudShell). Changes nothing.
# Uses IAM policy simulation when allowed; otherwise falls back to harmless list calls.
set -uo pipefail
export AWS_DEFAULT_REGION=${REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}
ACTIONS=(cloudformation:CreateStack cloudformation:CreateChangeSet lambda:CreateFunction lambda:CreateFunctionUrlConfig
  dynamodb:CreateTable iam:CreateRole iam:PassRole secretsmanager:CreateSecret ssm:PutParameter s3:CreateBucket
  connect:CreateContactFlow connect:AssociateSecurityKey connect:CreateIntegrationAssociation)
ARN=$(aws sts get-caller-identity --query Arn --output text)
# Simulation needs the IAM user/role ARN (not the assumed-role session ARN).
SRC=$ARN
if [[ "$ARN" == *":assumed-role/"* ]]; then
  ROLE=$(echo "$ARN" | cut -d/ -f2); ACCT=$(echo "$ARN" | cut -d: -f5)
  SRC=$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text 2>/dev/null || echo "")
fi
if [[ -n "$SRC" ]] && OUT=$(aws iam simulate-principal-policy --policy-source-arn "$SRC" --action-names "${ACTIONS[@]}" \
      --query "EvaluationResults[].[EvalActionName,EvalDecision]" --output text 2>/dev/null); then
  echo "$OUT" | awk '{printf "  %-7s %s\n", ($2=="allowed"?"OK":"DENIED"), $1}'
  if echo "$OUT" | grep -qv allowed; then
    echo "Some actions are denied for $ARN. Ask the account admin for them before deploying."; exit 1
  fi
  echo "All deploy actions allowed."
else
  echo "(policy simulation not permitted for this identity - checking with list calls only)"
  fail=0
  for c in "cloudformation list-stacks --max-items 1" "lambda list-functions --max-items 1" "dynamodb list-tables --max-items 1" \
           "secretsmanager list-secrets --max-results 1" "s3api list-buckets" "iam list-roles --max-items 1"; do
    if aws $c >/dev/null 2>&1; then echo "  OK     $c"; else echo "  DENIED $c"; fail=1; fi
  done
  [[ $fail == 0 ]] || { echo "Missing permissions - ask the account admin before deploying."; exit 1; }
  echo "Read access looks fine; create permissions are confirmed only when the deploy runs."
fi
