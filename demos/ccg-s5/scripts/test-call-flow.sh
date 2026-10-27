#!/usr/bin/env bash
# Create/update the flow "ccg-s5-test-call" (synthetic member M-1001, policy AU-88214471 -> secure payment flow) and,
# optionally, point a claimed phone number at it. CloudShell, after cloudshell-setup.sh.
#   bash ccg-s5/scripts/test-call-flow.sh                 # create/update the flow only
#   PHONE=+14035550123 bash ccg-s5/scripts/test-call-flow.sh   # ...and route that claimed number to it
set -euo pipefail
export AWS_DEFAULT_REGION=${REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}
NAME=ccg-s5-test-call
SECURE=$(aws cloudformation describe-stacks --stack-name ccg-s5 --query "Stacks[0].Outputs[?OutputKey=='SecurePaymentFlowArn'].OutputValue" --output text)
INST=$(echo "$SECURE" | cut -d/ -f2)
CONTENT=$(cat <<EOF
{"Version":"2019-10-30","StartAction":"log","Actions":[
 {"Identifier":"log","Type":"UpdateFlowLoggingBehavior","Parameters":{"FlowLoggingBehavior":"Enabled"},"Transitions":{"NextAction":"hello"}},
 {"Identifier":"hello","Type":"MessageParticipant","Parameters":{"Text":"Scenario 5 test call. Demo member Avery, auto policy ending 4 4 7 1."},
  "Transitions":{"NextAction":"set","Errors":[{"NextAction":"set","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"set","Type":"UpdateContactAttributes","Parameters":{"TargetContact":"Current","Attributes":{"s5_member_id":"M-1001","s5_policy_id":"AU-88214471"}},
  "Transitions":{"NextAction":"go","Errors":[{"NextAction":"bye","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"go","Type":"TransferToFlow","Parameters":{"ContactFlowId":"$SECURE"},
  "Transitions":{"NextAction":"bye","Errors":[{"NextAction":"bye","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"bye","Type":"DisconnectParticipant","Parameters":{},"Transitions":{}}]}
EOF
)
FID=$(aws connect list-contact-flows --instance-id "$INST" --contact-flow-types CONTACT_FLOW \
  --query "ContactFlowSummaryList[?Name=='$NAME'].Id | [0]" --output text)
if [[ "$FID" == "None" || -z "$FID" ]]; then
  FID=$(aws connect create-contact-flow --instance-id "$INST" --name "$NAME" --type CONTACT_FLOW \
    --description "CCG S5 demo: synthetic member -> secure payment" --content "$CONTENT" --query ContactFlowId --output text)
  echo "created flow $NAME ($FID)"
else
  aws connect update-contact-flow-content --instance-id "$INST" --contact-flow-id "$FID" --content "$CONTENT"
  echo "updated flow $NAME ($FID)"
fi
if [[ -n "${PHONE:-}" ]]; then
  PID=$(aws connect list-phone-numbers-v2 --target-arn "arn:aws:connect:${AWS_DEFAULT_REGION}:$(aws sts get-caller-identity --query Account --output text):instance/$INST" \
    --query "ListPhoneNumbersSummaryList[?PhoneNumber=='$PHONE'].PhoneNumberId | [0]" --output text)
  [[ "$PID" == "None" || -z "$PID" ]] && { echo "Number $PHONE is not claimed on this instance."; exit 1; }
  aws connect associate-phone-number-contact-flow --phone-number-id "$PID" --instance-id "$INST" --contact-flow-id "$FID"
  echo "$PHONE -> $NAME. Call it and enter test card 4242 4242 4242 4242, expiry 12 28, CVV 123."
fi
