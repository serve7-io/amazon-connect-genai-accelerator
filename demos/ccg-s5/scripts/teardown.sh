#!/usr/bin/env bash
# Remove everything cloudshell-setup.sh added (CloudShell). Asks before deleting. Leaves the Connect instance and
# everything that existed before untouched.
#   bash ccg-s5/scripts/teardown.sh
set -euo pipefail
export AWS_DEFAULT_REGION=${REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}
REGION=$AWS_DEFAULT_REGION
PREFIX=ccg-s5
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=${BUCKET:-${PREFIX}-artifacts-${ACCOUNT}-${REGION}}
INSTANCE_ID=$(aws ssm get-parameter --name /$PREFIX/instance-id --query Parameter.Value --output text 2>/dev/null || echo "${INSTANCE_ID:-}")
KEY_ID=$(aws ssm get-parameter --name /$PREFIX/card-key-id --query Parameter.Value --output text 2>/dev/null || true)

cat <<EOF
Account $ACCOUNT ($REGION). This deletes:
  - stack $PREFIX (Lambdas, 6 tables with synthetic data, 2 flows, the Lambda association)
  - encryption key ${KEY_ID:-<none>} from Connect instance ${INSTANCE_ID:-<unknown>}
  - secrets $PREFIX/card-private-key and $PREFIX/acxd-api-key (7-day recovery window)
  - SSM parameters /$PREFIX/*  and bucket $BUCKET
The Connect instance itself and anything else in the account are not touched.
EOF
read -r -p "Delete all of this? Type the account id to confirm: " ok
[[ "$ok" == "$ACCOUNT" ]] || { echo "Nothing deleted."; exit 0; }

if aws cloudformation describe-stacks --stack-name $PREFIX >/dev/null 2>&1; then
  aws cloudformation delete-stack --stack-name $PREFIX
  echo "deleting stack..."; aws cloudformation wait stack-delete-complete --stack-name $PREFIX
fi
if [[ -n "$INSTANCE_ID" && -n "$KEY_ID" ]]; then
  aws connect disassociate-security-key --instance-id "$INSTANCE_ID" --association-id "$KEY_ID" 2>/dev/null \
    && echo "security key removed" || echo "security key already gone"
fi
for s in card-private-key acxd-api-key; do
  aws secretsmanager delete-secret --secret-id $PREFIX/$s --recovery-window-in-days 7 >/dev/null 2>&1 && echo "secret $s scheduled for deletion" || true
done
for p in card-certificate card-key-id instance-id; do aws ssm delete-parameter --name /$PREFIX/$p 2>/dev/null || true; done
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3 rm "s3://$BUCKET" --recursive --quiet && aws s3api delete-bucket --bucket "$BUCKET" && echo "bucket removed"
fi
echo "Teardown complete. Left over (by design): nothing named $PREFIX*."
