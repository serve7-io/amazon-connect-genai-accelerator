#!/usr/bin/env bash
# Smoke test of a deployed Scenario 5 stack (CloudShell). Uses only synthetic data; never prints the API key.
#   bash ccg-s5/scripts/smoke.sh
set -uo pipefail
export AWS_DEFAULT_REGION=${REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}
PREFIX=ccg-s5; pass=0; fail=0
check() { if [[ "$2" == *"$3"* ]]; then echo "  PASS  $1"; pass=$((pass+1)); else echo "  FAIL  $1  (got: ${2:0:160})"; fail=$((fail+1)); fi; }
out() { aws cloudformation describe-stacks --stack-name $PREFIX --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

STATUS=$(aws cloudformation describe-stacks --stack-name $PREFIX --query "Stacks[0].StackStatus" --output text 2>&1)
check "stack status" "$STATUS" "_COMPLETE"
FN=$(out PaymentFunctionArn); URL=$(out AcxdApiUrl); FLOW=$(out SecurePaymentFlowArn)

invoke() { aws lambda invoke --function-name "$FN" --cli-binary-format raw-in-base64-out --payload "$1" /tmp/s5out.json >/dev/null 2>&1; cat /tmp/s5out.json; }
CID="smoke-$(date +%s)"
ev() { printf '{"Details":{"ContactData":{"InitialContactId":"%s"},"Parameters":%s}}' "$CID" "$1"; }
check "payment: start_session returns partner session" "$(invoke "$(ev '{"action":"start_session","policyId":"AU-88214471"}')")" '"partner_session_id": "PS-'
check "payment: charge without card input is refused" "$(invoke "$(ev '{"action":"charge","policyId":"AU-88214471","memberId":"M-1001"}')")" "input_incomplete"

INST=$(echo "$FLOW" | cut -d/ -f2); FID=$(echo "$FLOW" | cut -d/ -f4)
check "secure payment flow ACTIVE" "$(aws connect describe-contact-flow --instance-id "$INST" --contact-flow-id "$FID" --query ContactFlow.State --output text 2>&1)" "ACTIVE"
check "payment Lambda associated with the instance" "$(aws connect list-lambda-functions --instance-id "$INST" --output text 2>&1)" "${FN##*:}"

check "API rejects calls without key (401)" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "${URL}verify_member" -d '{}')" "401"
KEY=$(aws secretsmanager get-secret-value --secret-id $PREFIX/acxd-api-key --query SecretString --output text)
R=$(curl -s -X POST "${URL}verify_member" -H "x-api-key: $KEY" -H 'content-type: application/json' \
  -d '{"policy_number":"AU-88214471","dob":"1984-03-12","phone":"+14165550111"}'); unset KEY
check "API verify_member finds synthetic member M-1001" "$R" '"member_id": "M-1001"'

echo "  $pass passed, $fail failed"
[[ $fail == 0 ]]
