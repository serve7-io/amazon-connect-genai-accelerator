#!/usr/bin/env bash
# One-command deploy for a tenant, from AWS CloudShell or a laptop with admin credentials.
# CI uses the same script with the OIDC deploy role (bootstrap already in place).
#
#   ./scripts/deploy.sh sandbox dev            # bootstrap (if needed) + all layers, asks before each apply
#   AUTO_APPROVE=1 ./scripts/deploy.sh sandbox dev
#   ONLY=30-routing ./scripts/deploy.sh sandbox dev
#   CLAIM_PHONE=false ./scripts/deploy.sh sandbox dev
set -euo pipefail

TENANT="${1:?usage: deploy.sh <tenant> <env>}"
ENVIRONMENT="${2:?usage: deploy.sh <tenant> <env>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF_VERSION="${TF_VERSION:-1.10.5}"
LAYERS="${ONLY:-10-foundation 20-connect-core 40-genai 30-routing 50-observability}"
TFVARS="$ROOT/tenants/$TENANT/$ENVIRONMENT.tfvars"

REGION="$(grep -E '^region:' "$ROOT/tenants/$TENANT/tenant.yaml" | awk '{print $2}')"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# ------------------------------------------------------------------ tools
if ! command -v terraform >/dev/null || ! terraform version | grep -q "v${TF_VERSION}"; then
  say "Installing Terraform ${TF_VERSION} into ~/bin"
  mkdir -p "$HOME/bin"
  curl -sSfL -o /tmp/tf.zip "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip"
  unzip -o -q /tmp/tf.zip -d "$HOME/bin"
  export PATH="$HOME/bin:$PATH"
fi
python3 -c "import boto3" 2>/dev/null || pip3 install --user -q boto3

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="serve7-tfstate-${ACCOUNT}-${REGION}"
say "Tenant ${TENANT}/${ENVIRONMENT} -> account ${ACCOUNT}, region ${REGION}"

# ------------------------------------------------------------------ bootstrap (E1)
if [[ -z "${ONLY:-}" ]]; then
  if ! aws cloudformation describe-stacks --stack-name serve7-bootstrap >/dev/null 2>&1 || [[ "${REBOOTSTRAP:-0}" == "1" ]]; then
    say "Deploying bootstrap stack (OIDC trust, deploy/plan roles, boundary, state bucket, CloudTrail)"
    OIDC=true
    aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[].Arn' --output text | grep -q token.actions.githubusercontent.com && OIDC=false
    TRAIL="${CREATE_TRAIL:-true}"
    aws cloudformation deploy \
      --stack-name serve7-bootstrap \
      --template-file "$ROOT/bootstrap/bootstrap.yaml" \
      --capabilities CAPABILITY_NAMED_IAM \
      --parameter-overrides Tenant="$TENANT" Environment="$ENVIRONMENT" CreateOidcProvider="$OIDC" CreateCloudTrail="$TRAIL" \
      --no-fail-on-empty-changeset
  else
    say "Bootstrap stack already present"
  fi
fi

# ------------------------------------------------------------------ pre-flight + model discovery
say "Pre-flight"
python3 "$ROOT/scripts/preflight.py" --tenant "$TENANT" --region "$REGION" ${SKIP_MODEL_TEST:+--skip-invoke-test}

# ------------------------------------------------------------------ layers
EXTRA_VARS=(-var "state_bucket=${STATE_BUCKET}" -var "release=${RELEASE:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo manual)}")
# Connect aliases are unique per region across all AWS customers: default to one derived from the account.
EXTRA_VARS+=(-var "instance_alias=${INSTANCE_ALIAS:-s7-${TENANT}-${ENVIRONMENT}-${ACCOUNT: -6}}")
[[ -n "${CLAIM_PHONE:-}" ]] && EXTRA_VARS+=(-var "claim_phone_number=${CLAIM_PHONE}")

for layer in $LAYERS; do
  say "Layer ${layer}"
  dir="$ROOT/stacks/$layer"
  terraform -chdir="$dir" init -input=false -reconfigure \
    -backend-config="bucket=${STATE_BUCKET}" \
    -backend-config="key=${layer}/terraform.tfstate" \
    -backend-config="region=${REGION}" >/dev/null
  terraform -chdir="$dir" plan -input=false -out=tfplan -var-file="$TFVARS" "${EXTRA_VARS[@]}" -compact-warnings
  if [[ "${AUTO_APPROVE:-0}" != "1" ]]; then
    read -r -p "Apply ${layer}? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || { echo "Stopped before ${layer}."; exit 0; }
  fi
  terraform -chdir="$dir" apply -input=false tfplan
done

say "Done. Run the smoke tests:  python3 tests/smoke.py --tenant ${TENANT}"
