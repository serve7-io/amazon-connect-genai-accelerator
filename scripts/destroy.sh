#!/usr/bin/env bash
# Tear down a NON-PROD tenant in reverse layer order. The bootstrap stack and state bucket are kept.
#   ./scripts/destroy.sh sandbox dev
set -euo pipefail
TENANT="${1:?usage: destroy.sh <tenant> <env>}"
ENVIRONMENT="${2:?usage: destroy.sh <tenant> <env>}"
[[ "$ENVIRONMENT" == "prod" ]] && { echo "Refusing to destroy prod."; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="$(grep -E '^region:' "$ROOT/tenants/$TENANT/tenant.yaml" | awk '{print $2}')"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" PATH="$HOME/bin:$PATH"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="serve7-tfstate-${ACCOUNT}-${REGION}"

read -r -p "Destroy ALL layers of ${TENANT}/${ENVIRONMENT} in ${ACCOUNT}? Type the tenant name to confirm: " answer
[[ "$answer" == "$TENANT" ]] || { echo "Aborted."; exit 1; }

for layer in 50-observability 30-routing 40-genai 20-connect-core 10-foundation; do
  echo "==> destroying ${layer}"
  dir="$ROOT/stacks/$layer"
  terraform -chdir="$dir" init -input=false -reconfigure \
    -backend-config="bucket=${STATE_BUCKET}" -backend-config="key=${layer}/terraform.tfstate" \
    -backend-config="region=${REGION}" >/dev/null
  terraform -chdir="$dir" destroy -input=false -auto-approve \
    -var-file="$ROOT/tenants/$TENANT/$ENVIRONMENT.tfvars" -var "state_bucket=${STATE_BUCKET}" -var "instance_alias=${INSTANCE_ALIAS:-s7-${TENANT}-${ENVIRONMENT}-${ACCOUNT: -6}}"
done
echo "Done. Recording/knowledge buckets with objects must be emptied first if destroy reported BucketNotEmpty."
