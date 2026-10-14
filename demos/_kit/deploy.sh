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

say "1/5 API key secret (referenced by ARN; the value is never read here)"
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

say "2/5 Lambda package"
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

say "3/5 CloudFormation stack $STACK"
STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo NONE)
if [[ "$STATUS" == "ROLLBACK_COMPLETE" ]]; then
  echo "removing failed first attempt"; aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK"
fi
TAGS=$(python3 -c "import json;print(' '.join(f'{k}={v}' for k,v in json.load(open('$D/demo.json')).get('tags',{}).items()))")
# shellcheck disable=SC2086
aws cloudformation deploy --stack-name "$STACK" --template-file "$D/template.yaml" --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset --tags $TAGS \
  --parameter-overrides CodeBucket="$BUCKET" CodeKey="$KEY" ApiKeySecretArn="$SECRET_ARN"

say "4/5 Synthetic data"
python3 "$KIT/data.py" "$NAME" seed

say "5/5 Smoke test + ACXD data requests"
python3 "$KIT/smoke.py" "$NAME"
python3 "$KIT/datarequests.py" "$NAME"
echo; echo "Done: $NAME deployed."
