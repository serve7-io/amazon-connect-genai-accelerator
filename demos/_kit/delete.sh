#!/usr/bin/env bash
# Delete a kit demo (its stack, tables + synthetic data, Lambda, API URL, and its code in S3):
#   CONFIRM=<demo> bash demos/_kit/delete.sh <demo>
# Keeps the shared code bucket and the API key secret (used by the other demos). Delete the ACXD app in ACXD.
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
echo "Done. Kept by design: shared bucket and API key secret. Remove the ACXD application in ACXD."
