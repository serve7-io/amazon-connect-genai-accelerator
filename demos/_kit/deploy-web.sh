#!/usr/bin/env bash
# Publish a demo's web test page (S3 + CloudFront) and wire its chat: bash demos/_kit/deploy-web.sh <demo>
# Needs a "web" block in demos/<demo>/demo.json. Safe to re-run. Run by deploy.sh (step 6) and by the "web" command:
#   issue title  "demo: web <demo>"  re-publishes the page only.
# Creates/updates the shared stack demo-kit-web once (S3 + CloudFront + start-chat Lambda, first run takes ~5-10 min),
# writes /demo-kit/web/<demo>/chat to SSM, renders the page and uploads it to s3://<web bucket>/<demo>/index.html.
# Secrets: the demo's ACXD browser keys are read from Secrets Manager demo-kit/web/<demo>/acxd (see web/set-acxd-keys.sh)
# and only end up in the rendered page (browser-side keys by design). They are never printed.
set -euo pipefail
NAME=${1:?usage: deploy-web.sh <demo>}
KIT=$(cd "$(dirname "$0")" && pwd); D="$KIT/../$NAME"; WEB="$KIT/web"
[ -f "$D/demo.json" ] || { echo "unknown demo '$NAME'"; exit 1; }
cfg() { python3 -c "import json;print(json.load(open('$D/demo.json'))$1)"; }
if [ "$(python3 -c "import json;print('yes' if 'web' in json.load(open('$D/demo.json')) else 'no')")" != yes ]; then
  echo "$NAME has no \"web\" block in demo.json - nothing to publish"; exit 0
fi
STACK=$(cfg "['stack']"); REGION=$(cfg "['region']")
export AWS_DEFAULT_REGION=$REGION
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
CODE_BUCKET=demo-kit-artifacts-$ACCOUNT-$REGION
WEB_STACK=demo-kit-web
say() { printf '\n== %s\n' "$*"; }
out_of() { aws cloudformation describe-stacks --stack-name "$1" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null || true; }
echo "Web page for $NAME (stack $STACK) -> shared stack $WEB_STACK, account $ACCOUNT, region $REGION"

say "1/4 Shared web stack $WEB_STACK (S3 + CloudFront + start-chat)"
aws s3api head-bucket --bucket "$CODE_BUCKET" 2>/dev/null || { echo "code bucket $CODE_BUCKET missing: run 'demo: deploy $NAME' first"; exit 1; }
PKG=$(mktemp -d); cp -r "$WEB/start_chat/." "$PKG/"; find "$PKG" -name __pycache__ -prune -exec rm -rf {} +
HASH=$(cd "$PKG" && find . -type f | sort | xargs cat | sha256sum | cut -c1-12)
KEY=$WEB_STACK/$HASH/start-chat.zip
(cd "$PKG" && zip -qr "$PKG.zip" .) && aws s3 cp --quiet "$PKG.zip" "s3://$CODE_BUCKET/$KEY" && echo "s3://$CODE_BUCKET/$KEY"
STATUS=$(aws cloudformation describe-stacks --stack-name "$WEB_STACK" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo NONE)
if [[ "$STATUS" == "ROLLBACK_COMPLETE" ]]; then
  echo "removing failed first attempt"; aws cloudformation delete-stack --stack-name "$WEB_STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$WEB_STACK"
fi
if ! aws cloudformation deploy --stack-name "$WEB_STACK" --template-file "$WEB/stack.yaml" --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset --tags serve7:project=demo serve7:demo=kit-web serve7:data-classification=synthetic \
  --parameter-overrides CodeBucket="$CODE_BUCKET" CodeKey="$KEY"; then
  echo "---- CloudFormation reports these failed resources in $WEB_STACK:"
  aws cloudformation describe-stack-events --stack-name "$WEB_STACK" \
    --query "StackEvents[?contains(ResourceStatus,'FAILED')].[LogicalResourceId,ResourceStatusReason]" --output text 2>&1 \
    | cut -c1-400 | head -20 || true
  echo "(fix the cause, then run: demo: web $NAME)"
  exit 1
fi
WEB_URL=$(out_of "$WEB_STACK" WebUrl); BUCKET=$(out_of "$WEB_STACK" WebBucketName)
[ -n "$WEB_URL" ] && [ -n "$BUCKET" ] || { echo "shared web stack has no outputs"; exit 1; }

say "2/4 Chat wiring for $NAME"
FLOW_ID=$(out_of "$STACK" ChatFlowId); [ "$FLOW_ID" = "None" ] && FLOW_ID=""
INSTANCE_ID=$(python3 "$WEB/render.py" "$NAME" --get connect_instance_id)
CHAT_READY=0
if [ -n "$FLOW_ID" ] && [ -n "$INSTANCE_ID" ]; then
  VALUE=$(python3 -c "import json,sys;print(json.dumps({'instance_id':sys.argv[1],'flow_id':sys.argv[2]}))" "$INSTANCE_ID" "$FLOW_ID")
  aws ssm put-parameter --name "/demo-kit/web/$NAME/chat" --type String --overwrite --value "$VALUE" >/dev/null
  CHAT_READY=1; echo "chat flow wired (flow $FLOW_ID)"
else
  echo "no chat flow yet: it needs web.acxd ids in demo.json and the key secret demo-kit/web/$NAME/acxd"
  echo "(create it once in CloudShell: bash demos/_kit/web/set-acxd-keys.sh $NAME), then deploy again. The page says 'chat not available' until then."
fi

say "3/4 Render and upload the page"
KEYS=$(aws secretsmanager get-secret-value --secret-id "demo-kit/web/$NAME/acxd" --query SecretString --output text 2>/dev/null || true)
[ "$KEYS" = "None" ] && KEYS=""
PAGE=$(mktemp -d)
ACXD_KEYS_JSON="$KEYS" WEB_CHAT_READY="$CHAT_READY" python3 "$WEB/render.py" "$NAME" --out "$PAGE"
aws s3 cp --quiet "$PAGE/index.html" "s3://$BUCKET/$NAME/index.html" --content-type "text/html; charset=utf-8" --cache-control "no-store"
printf 'User-agent: *\nDisallow: /\n' | aws s3 cp --quiet - "s3://$BUCKET/robots.txt" --content-type text/plain

say "4/4 Done"
echo "Web test page: $WEB_URL/$NAME/"
