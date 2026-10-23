#!/usr/bin/env bash
# Deploy the CCG Scenario 5 demo into an EXISTING Amazon Connect instance, from AWS CloudShell.
#
#   cd ~ && unzip -oq ccg-s5-scenario5.zip && INSTANCE_ID=<connect-instance-id> bash ccg-s5/scripts/cloudshell-setup.sh
#
# Works in any account (sandbox, rehearsal lab, client test account). It only ADDS things, all named ccg-s5*:
#   - 1 CloudFormation stack (ccg-s5): 2 Lambdas, 6 DynamoDB tables, 2 flows, 1 Lambda association on the instance
#   - 1 S3 bucket for the Lambda code, 2 secrets, 3 SSM parameters
#   - 1 encryption key associated to the instance (keypad card entry)
# Existing flows, queues, users, phone numbers and recording settings are never modified.
# Safe to re-run. Remove everything again with scripts/teardown.sh.
set -euo pipefail
REGION=${REGION:-ca-central-1}
export AWS_DEFAULT_REGION=$REGION
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=ccg-s5
say() { printf '\n== %s\n' "$*"; }

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
if [[ -z "${INSTANCE_ID:-}" ]]; then
  echo "Set INSTANCE_ID to the Connect instance to deploy into. Instances in $ACCOUNT / $REGION:"
  aws connect list-instances --query "InstanceSummaryList[].[InstanceAlias,Id,InstanceStatus]" --output table
  exit 1
fi
ALIAS=$(aws connect describe-instance --instance-id "$INSTANCE_ID" --query Instance.InstanceAlias --output text)
INSTANCE_ARN="arn:aws:connect:${REGION}:${ACCOUNT}:instance/${INSTANCE_ID}"
BUCKET=${BUCKET:-${PREFIX}-artifacts-${ACCOUNT}-${REGION}}
VER=${VER:-${PREFIX}/$(cat "$ROOT"/lambdas/common.py "$ROOT"/lambdas/*/handler.py | sha256sum | cut -c1-12)}  # new code -> new S3 key -> CloudFormation updates the Lambdas
KEYS=$HOME/.${PREFIX}-keys

echo "Account $ACCOUNT, region $REGION, Connect instance '$ALIAS' ($INSTANCE_ID)"
if [[ "${YES:-}" != "1" ]]; then
  read -r -p "Deploy Scenario 5 into this instance? [y/N] " ok; [[ "$ok" =~ ^[Yy]$ ]] || exit 0
fi

say "0/6 Permission pre-check"
bash "$ROOT/scripts/preflight.sh"

say "1/6 Card-input encryption key"
mkdir -p "$KEYS" && chmod 700 "$KEYS"
# After teardown.sh the secrets wait 7 days before deletion: bring them back instead of failing on a "deleted" secret.
for s in card-private-key acxd-api-key; do
  if [[ "$(aws secretsmanager describe-secret --secret-id $PREFIX/$s --query DeletedDate --output text 2>/dev/null)" =~ ^[0-9] ]]; then
    aws secretsmanager restore-secret --secret-id $PREFIX/$s >/dev/null && echo "restored secret $PREFIX/$s (was scheduled for deletion)"
  fi
done
# A private key without its certificate (teardown removes the certificate) cannot be used: start a new pair.
if aws secretsmanager describe-secret --secret-id $PREFIX/card-private-key >/dev/null 2>&1 \
   && ! aws ssm get-parameter --name /$PREFIX/card-certificate >/dev/null 2>&1; then
  aws secretsmanager delete-secret --secret-id $PREFIX/card-private-key --force-delete-without-recovery >/dev/null
  echo "old card key had no certificate - replacing it"
  for i in 1 2 3 4 5 6; do aws secretsmanager describe-secret --secret-id $PREFIX/card-private-key >/dev/null 2>&1 || break; sleep 5; done
fi
if ! aws secretsmanager describe-secret --secret-id $PREFIX/card-private-key >/dev/null 2>&1; then
  openssl req -x509 -newkey rsa:4096 -sha512 -nodes -days 365 -subj "/CN=${PREFIX}-demo-card-input" \
    -keyout "$KEYS/card.key" -out "$KEYS/card.crt" 2>/dev/null
  aws secretsmanager create-secret --name $PREFIX/card-private-key --description "CCG S5 demo: decrypts Store customer input" \
    --secret-string "file://$KEYS/card.key" --query ARN --output text
  shred -u "$KEYS/card.key"                       # the only copy now lives in Secrets Manager
  aws ssm put-parameter --name /$PREFIX/card-certificate --type String --overwrite \
    --value "file://$KEYS/card.crt" >/dev/null       # certificate = public material, kept for redeploys
else
  echo "private key already in Secrets Manager - reusing"
fi
# The key must be associated with THIS instance (a stored key id may belong to another instance in the account).
KEY_ID=$(aws ssm get-parameter --name /$PREFIX/card-key-id --query Parameter.Value --output text 2>/dev/null || true)
ON_INSTANCE=$(aws connect list-security-keys --instance-id "$INSTANCE_ID" --query "SecurityKeys[?AssociationId=='${KEY_ID:-none}'] | length(@)" --output text)
if [[ -z "$KEY_ID" || "$ON_INSTANCE" == "0" ]]; then
  # Connect rejects a PEM with a trailing newline ("Key not in proper format"); $(...) strips it.
  PUB=$(aws ssm get-parameter --name /$PREFIX/card-certificate --query Parameter.Value --output text | openssl x509 -pubkey -noout)
  KEY_ID=$(aws connect associate-security-key --instance-id "$INSTANCE_ID" --key "$PUB" --query AssociationId --output text)
  aws ssm put-parameter --name /$PREFIX/card-key-id --type String --overwrite --value "$KEY_ID" >/dev/null
  aws ssm put-parameter --name /$PREFIX/instance-id --type String --overwrite --value "$INSTANCE_ID" >/dev/null
fi
CERT=$(aws ssm get-parameter --name /$PREFIX/card-certificate --query Parameter.Value --output text | awk 'NR>1{printf "\\n"} {printf "%s", $0}')
echo "Connect security key id: $KEY_ID"

say "2/6 API key for the ACXD integrations"
if ! aws secretsmanager describe-secret --secret-id $PREFIX/acxd-api-key >/dev/null 2>&1; then
  aws secretsmanager create-secret --name $PREFIX/acxd-api-key --description "CCG S5 demo: x-api-key for the ACXD integrations API" \
    --secret-string "$(openssl rand -hex 24)" --query ARN --output text
fi
PK_ARN=$(aws secretsmanager describe-secret --secret-id $PREFIX/card-private-key --query ARN --output text)
API_ARN=$(aws secretsmanager describe-secret --secret-id $PREFIX/acxd-api-key --query ARN --output text)

say "3/6 Lambda packages -> s3://$BUCKET/$VER/"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging 'TagSet=[{Key=serve7:project,Value=ccg-rfp},{Key=serve7:scenario,Value=5}]'
fi
bash "$ROOT/scripts/package.sh" "$BUCKET" "$VER"

say "4/6 CloudFormation stack $PREFIX"
STATUS=$(aws cloudformation describe-stacks --stack-name $PREFIX --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo NONE)
if [[ "$STATUS" == "ROLLBACK_COMPLETE" || "$STATUS" == "DELETE_IN_PROGRESS" ]]; then
  echo "removing the failed first attempt ($STATUS)"
  [[ "$STATUS" == "ROLLBACK_COMPLETE" ]] && aws cloudformation delete-stack --stack-name $PREFIX
  aws cloudformation wait stack-delete-complete --stack-name $PREFIX
fi
aws cloudformation deploy --stack-name $PREFIX --template-file "$ROOT/connect/template.yaml" \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --tags serve7:project=ccg-rfp serve7:scenario=5 serve7:data-classification=synthetic \
  --parameter-overrides ConnectInstanceArn="$INSTANCE_ARN" CodeBucket="$BUCKET" CodeVersion="$VER" \
    EncryptionKeyId="$KEY_ID" EncryptionCertificate="$CERT" PrivateKeySecretArn="$PK_ARN" ApiKeySecretArn="$API_ARN" \
    ${RETURN_FLOW_ARN:+ReturnFlowArn=$RETURN_FLOW_ARN} ${SMS:+SmsEnabled=$SMS} ${SEGMENT_STYLE:+SegmentStyle=$SEGMENT_STYLE}

say "5/6 Synthetic data"
python3 "$ROOT/scripts/seed.py"

say "6/6 Smoke test + ACXD data requests for this account"
bash "$ROOT/scripts/smoke.sh"
python3 "$ROOT/scripts/acxd-datarequests.py" --out "$HOME/${PREFIX}-acxd"

aws cloudformation describe-stacks --stack-name $PREFIX --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
echo; echo "Done. ACXD data requests for this account: ~/${PREFIX}-acxd/ (download with Actions > Download file)."
