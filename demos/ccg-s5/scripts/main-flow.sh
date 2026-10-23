#!/usr/bin/env bash
# Create/update the Scenario 5 MAIN voice flow "ccg-s5-main" (CloudShell, after cloudshell-setup.sh):
#   ACXD agent (Agentic CX block) -> on Escalation ask the payment Lambda why (handoff):
#     collect_new_card -> secure keypad flow -> back to this flow -> ACXD resumes (Continuation)
#     otherwise        -> "a team member will follow up" (no agents in the demo instance)
#
#   ACXD_WORKSPACE=<id> ACXD_APP=<id> ACXD_ALIAS=<deploymentKey> bash ccg-s5/scripts/main-flow.sh
#   PHONE=+1... bash ccg-s5/scripts/main-flow.sh      # ...and route that claimed number to it
# Afterwards, make the secure flow return here (printed at the end):
#   RETURN_FLOW_ARN=<main flow arn> YES=1 INSTANCE_ID=<id> bash ccg-s5/scripts/cloudshell-setup.sh
set -euo pipefail
export AWS_DEFAULT_REGION=${REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}
NAME=${FLOW_NAME:-ccg-s5-main}
# Defaults = hoplitemarketing demo (workspace Sandbox-Hoplite, app ccg-renewal-payment, Production deployment)
WS=${ACXD_WORKSPACE:-f2e8e16b-df1e-4177-a3d8-42b833becf81}
APP=${ACXD_APP:-45e1de5a-4383-401d-b0f2-3f3571f44531}
ALIAS=${ACXD_ALIAS:-7YfU20MkHjv7gBezFYCPn}
VOICE=${VOICE:-Joanna}
out() { aws cloudformation describe-stacks --stack-name ccg-s5 --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
SECURE=$(out SecurePaymentFlowArn); FN=$(out PaymentFunctionArn)
INST=$(echo "$SECURE" | cut -d/ -f2)

CONTENT=$(cat <<EOF
{"Version":"2019-10-30","StartAction":"log","Actions":[
 {"Identifier":"log","Type":"UpdateFlowLoggingBehavior","Parameters":{"FlowLoggingBehavior":"Enabled"},"Transitions":{"NextAction":"voice"}},
 {"Identifier":"voice","Type":"UpdateContactTextToSpeechVoice","Parameters":{"TextToSpeechVoice":"$VOICE"},
  "Transitions":{"NextAction":"rec","Errors":[{"NextAction":"rec","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"rec","Type":"UpdateContactRecordingBehavior",
  "Parameters":{"RecordingBehavior":{"RecordedParticipants":["Agent","Customer"],"IVRRecordingBehavior":"Enabled"},
    "AnalyticsBehavior":{"Enabled":"True","AnalyticsLanguage":"en-US","AnalyticsRedactionBehavior":"Enabled",
      "AnalyticsRedactionResults":"RedactedAndOriginal","ChannelConfiguration":{"Voice":{"AnalyticsModes":["RealTime","AutomatedInteraction"]}}}},
  "Transitions":{"NextAction":"agent"}},
 {"Identifier":"agent","Type":"ConnectParticipantWithAgenticCX",
  "Parameters":{"AgentConfiguration":{"WorkspaceId":"$WS","ApplicationId":"$APP","Alias":"$ALIAS",
                                      "ContextVariables":{"contact_id":"\$.InitialContactId"}},
                "SpeechRecognitionConfiguration":{"SpeechRecognitionEngine":"AMAZON_AGENTIC_VOICE"},
                "AudioFillerConfiguration":{"Enabled":false}},
  "Transitions":{"NextAction":"error-msg",
    "Errors":[{"NextAction":"error-msg","ErrorType":"NoMatchingError"},{"NextAction":"done","ErrorType":"NoMatchingCondition"},
              {"NextAction":"no-input","ErrorType":"InputTimeLimitExceeded"}],
    "Conditions":[{"NextAction":"handoff","Condition":{"Operator":"Equals","Operands":["Escalation"]}}]}},
 {"Identifier":"handoff","Type":"InvokeLambdaFunction",
  "Parameters":{"LambdaFunctionARN":"$FN","InvocationTimeLimitSeconds":"8","LambdaInvocationAttributes":{"action":"handoff"},
                "ResponseValidation":{"ResponseType":"STRING_MAP"}},
  "Transitions":{"NextAction":"why","Errors":[{"NextAction":"team","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"why","Type":"Compare","Parameters":{"ComparisonValue":"\$.External.reason"},
  "Transitions":{"NextAction":"team","Conditions":[{"NextAction":"set-card","Condition":{"Operator":"Equals","Operands":["collect_new_card"]}}],
    "Errors":[{"NextAction":"team","ErrorType":"NoMatchingCondition"}]}},
 {"Identifier":"set-card","Type":"UpdateContactAttributes",
  "Parameters":{"TargetContact":"Current","Attributes":{"s5_policy_id":"\$.External.policyId","s5_member_id":"\$.External.memberId","s5_entry":"acxd"}},
  "Transitions":{"NextAction":"secure","Errors":[{"NextAction":"team","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"secure","Type":"TransferToFlow","Parameters":{"ContactFlowId":"$SECURE"},
  "Transitions":{"NextAction":"error-msg","Errors":[{"NextAction":"error-msg","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"no-input","Type":"MessageParticipant","Parameters":{"Text":"I didn't hear anything, so I'll end the call for now. Please call us back any time. Goodbye."},
  "Transitions":{"NextAction":"bye","Errors":[{"NextAction":"bye","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"team","Type":"MessageParticipant","Parameters":{"Text":"I'm sorry, I wasn't able to finish that for you. A member of our team will call you back shortly, and nothing has been charged. Goodbye."},
  "Transitions":{"NextAction":"bye","Errors":[{"NextAction":"bye","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"error-msg","Type":"MessageParticipant","Parameters":{"Text":"Sorry, something went wrong on our side. Please call again in a few minutes. Goodbye."},
  "Transitions":{"NextAction":"bye","Errors":[{"NextAction":"bye","ErrorType":"NoMatchingError"}]}},
 {"Identifier":"done","Type":"DisconnectParticipant","Parameters":{},"Transitions":{}},
 {"Identifier":"bye","Type":"DisconnectParticipant","Parameters":{},"Transitions":{}}]}
EOF
)
FID=$(aws connect list-contact-flows --instance-id "$INST" --contact-flow-types CONTACT_FLOW \
  --query "ContactFlowSummaryList[?Name=='$NAME'].Id | [0]" --output text)
if [[ "$FID" == "None" || -z "$FID" ]]; then
  FID=$(aws connect create-contact-flow --instance-id "$INST" --name "$NAME" --type CONTACT_FLOW \
    --description "CCG S5 demo: ACXD renewal agent -> secure keypad payment -> back to the agent" \
    --content "$CONTENT" --query ContactFlowId --output text)
  echo "created flow $NAME ($FID)"
else
  aws connect update-contact-flow-content --instance-id "$INST" --contact-flow-id "$FID" --content "$CONTENT"
  echo "updated flow $NAME ($FID)"
fi
ARN=$(aws connect describe-contact-flow --instance-id "$INST" --contact-flow-id "$FID" --query ContactFlow.Arn --output text)
if [[ -n "${PHONE:-}" ]]; then
  PID=$(aws connect list-phone-numbers-v2 --target-arn "arn:aws:connect:${AWS_DEFAULT_REGION}:$(aws sts get-caller-identity --query Account --output text):instance/$INST" \
    --query "ListPhoneNumbersSummaryList[?PhoneNumber=='$PHONE'].PhoneNumberId | [0]" --output text)
  [[ "$PID" == "None" || -z "$PID" ]] && { echo "Number $PHONE is not claimed on this instance."; exit 1; }
  aws connect associate-phone-number-contact-flow --phone-number-id "$PID" --instance-id "$INST" --contact-flow-id "$FID"
  echo "$PHONE -> $NAME"
fi
echo
echo "Main flow ARN: $ARN"
echo "Make the secure keypad flow return here:"
echo "  RETURN_FLOW_ARN=$ARN YES=1 INSTANCE_ID=$INST bash ccg-s5/scripts/cloudshell-setup.sh"
