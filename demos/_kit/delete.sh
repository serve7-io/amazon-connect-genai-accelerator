#!/usr/bin/env bash
# Delete a kit demo (its stack, tables + synthetic data, Lambda, API URL, and its code in S3):
#   CONFIRM=<demo> bash demos/_kit/delete.sh <demo>
# Keeps the shared code bucket, the API key secret and the shared web stack (used by the other demos), and the demo's
# ACXD key secret demo-kit/web/<demo>/acxd. Delete the ACXD app in ACXD.
set -euo pipefail
NAME=${1:?usage: delete.sh <demo>}
[ "${CONFIRM:-}" = "$NAME" ] || { echo "Refusing: set CONFIRM=$NAME to delete demo $NAME."; exit 1; }
KIT=$(cd "$(dirname "$0")" && pwd); D="$KIT/../$NAME"
[ -f "$D/demo.json" ] || { echo "unknown demo '$NAME'"; exit 1; }
STACK=$(python3 -c "import json;print(json.load(open('$D/demo.json'))['stack'])")
AWS_DEFAULT_REGION=$(python3 -c "import json;print(json.load(open('$D/demo.json'))['region'])"); export AWS_DEFAULT_REGION
[[ "$STACK" == demo-* ]] || { echo "stack name must start with demo-"; exit 1; }
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "Deleting demo $NAME: stack $STACK (tables + synthetic data, Lambda, API URL) in $ACCOUNT"
if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK" && echo "stack deleted"
else
  echo "stack not found (already deleted)"
fi
aws s3 rm "s3://demo-kit-artifacts-$ACCOUNT-$AWS_DEFAULT_REGION/$STACK/" --recursive --quiet 2>/dev/null && echo "code removed" || true
# web test page (the shared demo-kit-web stack, the key secret demo-kit/web/<demo>/acxd and the other demos' pages stay)
WEB_BUCKET=$(aws cloudformation describe-stacks --stack-name demo-kit-web --query "Stacks[0].Outputs[?OutputKey=='WebBucketName'].OutputValue" --output text 2>/dev/null || true)
if [ -n "$WEB_BUCKET" ] && [ "$WEB_BUCKET" != "None" ]; then
  aws s3 rm "s3://$WEB_BUCKET/$NAME/" --recursive --quiet 2>/dev/null && echo "web page removed" || true
fi
aws ssm delete-parameter --name "/demo-kit/web/$NAME/chat" >/dev/null 2>&1 && echo "chat wiring removed" || true
aws ssm delete-parameter --name "/demo-kit/web/$NAME/voice" >/dev/null 2>&1 && echo "voice wiring removed" || true
echo "Done. Kept by design: shared bucket and API key secret. Remove the ACXD application in ACXD."
