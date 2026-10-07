#!/usr/bin/env bash
# Adopt an existing Connect instance into layer 20 (when the account's instance quota is used up).
#   ./scripts/adopt-instance.sh sandbox dev [instance-id]
# Imports the instance and any existing storage associations into Terraform state.
# Afterwards run:  INSTANCE_ALIAS=<alias printed below> ./scripts/deploy.sh sandbox dev
set -euo pipefail
TENANT="${1:?usage: adopt-instance.sh <tenant> <env> [instance-id]}"
ENVIRONMENT="${2:?usage: adopt-instance.sh <tenant> <env> [instance-id]}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="$(grep -E '^region:' "$ROOT/tenants/$TENANT/tenant.yaml" | awk '{print $2}')"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" PATH="$HOME/bin:$PATH"
export TF_PLUGIN_CACHE_DIR=/tmp/tf-plugin-cache TF_DATA_DIR=/tmp/tfdata/20-connect-core
mkdir -p "$TF_PLUGIN_CACHE_DIR"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="serve7-tfstate-${ACCOUNT}-${REGION}"
DIR="$ROOT/stacks/20-connect-core"

if [[ -n "${3:-}" ]]; then
  ID="$3"
else
  ID="$(aws connect list-instances --query 'InstanceSummaryList[0].Id' --output text)"
fi
read -r ALIAS IDT STATUS <<<"$(aws connect describe-instance --instance-id "$ID" \
  --query 'Instance.[InstanceAlias,IdentityManagementType,InstanceStatus]' --output text)"
echo "Instance ${ID}  alias=${ALIAS}  identity=${IDT}  status=${STATUS}"
if [[ "$IDT" != "CONNECT_MANAGED" ]]; then
  echo "WARNING: identity management is ${IDT}; our config uses CONNECT_MANAGED, so Terraform would REPLACE the instance."
  echo "Set identity_management_type in stacks/20-connect-core/main.tf to ${IDT} before deploying."
fi

terraform -chdir="$DIR" init -input=false -reconfigure \
  -backend-config="bucket=${STATE_BUCKET}" -backend-config="key=20-connect-core/terraform.tfstate" \
  -backend-config="region=${REGION}" >/dev/null

TFARGS=(-input=false -var-file="$ROOT/tenants/$TENANT/$ENVIRONMENT.tfvars" -var "state_bucket=${STATE_BUCKET}" -var "instance_alias=${ALIAS}")

if terraform -chdir="$DIR" state list 2>/dev/null | grep -qx 'aws_connect_instance.this'; then
  echo "aws_connect_instance.this already in state - skipping"
else
  terraform -chdir="$DIR" import "${TFARGS[@]}" aws_connect_instance.this "$ID"
fi

declare -A NAMES=(
  [CALL_RECORDINGS]=call_recordings
  [CHAT_TRANSCRIPTS]=chat_transcripts
  [SCHEDULED_REPORTS]=scheduled_reports
  [CONTACT_TRACE_RECORDS]=ctr
  [AGENT_EVENTS]=agent_events
)
for TYPE in "${!NAMES[@]}"; do
  ASSOC="$(aws connect list-instance-storage-configs --instance-id "$ID" --resource-type "$TYPE" \
    --query 'StorageConfigs[0].AssociationId' --output text)"
  ADDR="aws_connect_instance_storage_config.${NAMES[$TYPE]}"
  if [[ "$ASSOC" == "None" || -z "$ASSOC" ]]; then
    echo "${TYPE}: no existing association (Terraform will create it)"
  elif terraform -chdir="$DIR" state list 2>/dev/null | grep -qx "$ADDR"; then
    echo "${TYPE}: already in state"
  else
    terraform -chdir="$DIR" import "${TFARGS[@]}" "$ADDR" "${ID}:${ASSOC}:${TYPE}"
  fi
done

echo
echo "Adopted. Now run:"
echo "  INSTANCE_ALIAS=${ALIAS} SKIP_MODEL_TEST=1 ./scripts/deploy.sh ${TENANT} ${ENVIRONMENT}"
echo "Note: ./scripts/destroy.sh will now DELETE this instance as well."
