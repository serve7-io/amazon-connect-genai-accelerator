#!/usr/bin/env bash
# Deploy (or update) a kit demo: bash demos/_kit/deploy.sh <demo>
# Creates/updates the CloudFormation stack named in demos/<demo>/demo.json (must start with demo-), uploads the Lambda
# code, fills empty tables with the synthetic seed, smoke-tests the API and prints the ACXD data request URLs.
# Safe to re-run. Remove with: CONFIRM=<demo> bash demos/_kit/delete.sh <demo>
set -euo pipefail
NAME=${1:?usage: deploy.sh <demo>}
KIT=$(cd "$(dirname "$0")" && pwd); D="$KIT/../$NAME"
[ -f "$D/demo.json" ] || { echo "unknown demo '$NAME'"; exit 1; }
cfg() { python3 -c "import json;print(json.load(open('$D/demo.json'))$1)"; }
STACK=$(cfg "['stack']"); REGION=$(cfg "['region']"); LDIR=$(cfg "['lambda_dir']")
[[ "$STACK" == demo-* ]] || { echo "stack name must start with demo-"; exit 1; }
export AWS_DEFAULT_REGION=$REGION
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=demo-kit-artifacts-$ACCOUNT-$REGION
say() { printf '\n== %s\n' "$*"; }
echo "Demo $NAME -> stack $STACK, account $ACCOUNT, region $REGION"

say "1/6 API key secret (referenced by ARN; the value is never read here)"
# Prefer the key ACXD already holds as ccg_s5_api_key, so no new ACXD secret is needed.
SECRET_ARN=""
for s in ccg-s5/acxd-api-key demo-kit/acxd-api-key; do
  st=$(aws secretsmanager describe-secret --secret-id $s --query "[ARN,DeletedDate]" --output text 2>/dev/null || true)
  if [[ -n "$st" && "$st" != *$'\t'2* ]]; then SECRET_ARN=${st%%$'\t'*}; echo "using $s"; break; fi
done
if [[ -z "$SECRET_ARN" ]]; then
  SECRET_ARN=$(aws secretsmanager create-secret --name demo-kit/acxd-api-key --secret-string "$(openssl rand -hex 24)" \
    --description "x-api-key for kit demo APIs" --tags Key=serve7:project,Value=demo --query ARN --output text)
  echo "created demo-kit/acxd-api-key: copy its value into an ACXD secret and set acxd_secret in demo.json"
fi

say "2/6 Lambda package"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
fi
OUT=$(mktemp -d); cp -r "$D/$LDIR/." "$OUT/"; find "$OUT" -name __pycache__ -prune -exec rm -rf {} +
if [ -f "$OUT/requirements.txt" ]; then
  python3 -m pip install -q -r "$OUT/requirements.txt" --target "$OUT" --platform manylinux2014_x86_64 \
    --only-binary=:all: --python-version 3.13
fi
HASH=$(cd "$OUT" && find . -type f | sort | xargs cat | sha256sum | cut -c1-12)
KEY=$STACK/$HASH/api.zip
(cd "$OUT" && zip -qr "$OUT.zip" .) && aws s3 cp --quiet "$OUT.zip" "s3://$BUCKET/$KEY" && echo "s3://$BUCKET/$KEY"

say "3/6 CloudFormation stack $STACK"
STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo NONE)
if [[ "$STATUS" == "ROLLBACK_COMPLETE" ]]; then
  echo "removing failed first attempt"; aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK"
fi
TAGS=$(python3 -c "import json;print(' '.join(f'{k}={v}' for k,v in json.load(open('$D/demo.json')).get('tags',{}).items()))")
# Demos with a "web" block get the chat parameters (Connect instance + ACXD app ids from demo.json, the app's Production
# deployment key from Secrets Manager demo-kit/web/<demo>/acxd). Without them the stack simply has no chat flow yet.
EXTRA=()
if python3 -c "import json,sys;sys.exit(0 if 'web' in json.load(open('$D/demo.json')) else 1)"; then
  mapfile -t EXTRA < <(python3 "$KIT/web/render.py" "$NAME" --stack-params)
fi
# Any demo whose template has an AcxdAlias parameter (web demos, and demos with their own chat page such as ccg-s1)
# gets the ACXD app's Production deployment key from Secrets Manager demo-kit/web/<demo>/acxd (set-acxd-keys.sh).
if grep -qE '^  AcxdAlias:' "$D/template.yaml"; then
  ALIAS=$(aws secretsmanager get-secret-value --secret-id "demo-kit/web/$NAME/acxd" --query SecretString --output text 2>/dev/null \
    | python3 -c "import json,sys;print(json.load(sys.stdin).get('deploymentKey',''))" 2>/dev/null || true)
  [ -n "$ALIAS" ] && EXTRA+=("AcxdAlias=$ALIAS")
fi
# shellcheck disable=SC2086
aws cloudformation deploy --stack-name "$STACK" --template-file "$D/template.yaml" --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset --tags $TAGS \
  --parameter-overrides CodeBucket="$BUCKET" CodeKey="$KEY" ApiKeySecretArn="$SECRET_ARN" ${EXTRA[@]+"${EXTRA[@]}"}

say "4/6 Synthetic data"
python3 "$KIT/data.py" "$NAME" seed

say "5/6 Smoke test + ACXD data requests"
python3 "$KIT/smoke.py" "$NAME"
python3 "$KIT/datarequests.py" "$NAME"

say "6/6 Web test page (CloudFront + Touchpoint chat)"
if python3 -c "import json,sys;sys.exit(0 if 'web' in json.load(open('$D/demo.json')) else 1)"; then
  # The API is already deployed at this point, so a problem here (for example a missing CloudFront permission on the
  # CI role) is reported but does not fail the whole deploy. Re-run with:  demo: web <demo>
  if ! bash "$KIT/deploy-web.sh" "$NAME"; then
    echo "WARNING: the web page step failed (see above). The API and data are deployed; fix it and run: demo: web $NAME"
  fi
else
  echo "no \"web\" block in demo.json - skipped"
fi
echo; echo "Done: $NAME deployed."
