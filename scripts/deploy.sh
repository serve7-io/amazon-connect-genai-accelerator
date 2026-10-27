#!/usr/bin/env bash
# One-command deploy for a tenant, from AWS CloudShell or a laptop with admin credentials.
# CI uses the same script with the OIDC deploy role (bootstrap already in place).
#
#   ./scripts/deploy.sh sandbox dev            # bootstrap (if needed) + all layers, asks before each apply
#   AUTO_APPROVE=1 ./scripts/deploy.sh sandbox dev
#   ONLY=30-routing ./scripts/deploy.sh sandbox dev
#   CLAIM_PHONE=false ./scripts/deploy.sh sandbox dev
#   REBOOTSTRAP=1 ./scripts/deploy.sh sandbox dev     # re-apply bootstrap/bootstrap.yaml (e.g. boundary changes)
#   RETRUST=1 ./scripts/deploy.sh sandbox dev         # repo moved/copied to another GitHub org: re-point the CI trust
#   GITHUB_ORG=acme GITHUB_REPO=connect-acc DEPLOY_TRUST=main-branch ./scripts/deploy.sh acme dev   # first bootstrap, explicit
set -euo pipefail

TENANT="${1:?usage: deploy.sh <tenant> <env>}"
ENVIRONMENT="${2:?usage: deploy.sh <tenant> <env>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF_VERSION="${TF_VERSION:-1.10.5}"
LAYERS="${ONLY:-10-foundation 20-connect-core 40-genai 30-routing 50-observability}"
TFVARS="$ROOT/tenants/$TENANT/$ENVIRONMENT.tfvars"

TI() { python3 "$ROOT/scripts/tenant-info.py" "$TENANT" "$ENVIRONMENT" "$1"; }
REGION="$(TI region)"
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
python3 -c "import boto3" 2>/dev/null || pip3 install --user -q boto3 || pip3 install -q boto3

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="serve7-tfstate-${ACCOUNT}-${REGION}"
say "Tenant ${TENANT}/${ENVIRONMENT} -> account ${ACCOUNT}, region ${REGION}"

# Guard: never deploy a tenant into the wrong AWS account.
EXPECTED_ACCOUNT="$(TI account_id)"
if [[ -n "$EXPECTED_ACCOUNT" && "$EXPECTED_ACCOUNT" != "$ACCOUNT" ]]; then
  echo "ERROR: tenants/$TENANT/tenant.yaml expects account $EXPECTED_ACCOUNT for '$ENVIRONMENT' but credentials are for $ACCOUNT." >&2
  echo "       Switch credentials (e.g. eval \"\$(scripts/assume-tenant.sh $EXPECTED_ACCOUNT)\") and retry." >&2
  exit 1
fi

# Guard: one environment per account (state keys and the bootstrap stack are per account).
BOOT_ENV="$(aws cloudformation describe-stacks --stack-name serve7-bootstrap \
  --query "Stacks[0].Parameters[?ParameterKey=='Environment'].ParameterValue" --output text 2>/dev/null || true)"
if [[ -n "$BOOT_ENV" && "$BOOT_ENV" != "None" && "$BOOT_ENV" != "$ENVIRONMENT" ]]; then
  echo "ERROR: account $ACCOUNT is bootstrapped for environment '$BOOT_ENV', not '$ENVIRONMENT'. Use a separate account per environment." >&2
  exit 1
fi

# Guard: production needs an explicit confirmation outside CI (CI uses the GitHub environment approval instead).
if [[ "$ENVIRONMENT" == "prod" && -z "${GITHUB_ACTIONS:-}" && "${PROD_CONFIRM:-}" != "$TENANT" ]]; then
  echo "ERROR: deploying ${TENANT}/prod from a workstation requires PROD_CONFIRM=${TENANT}." >&2
  exit 1
fi

# ------------------------------------------------------------------ bootstrap (E1)
if [[ -z "${ONLY:-}" && "${PLAN_ONLY:-0}" != "1" ]]; then
  if ! aws cloudformation describe-stacks --stack-name serve7-bootstrap >/dev/null 2>&1 || [[ "${REBOOTSTRAP:-0}" == "1" || "${RETRUST:-0}" == "1" ]]; then
    say "Deploying bootstrap stack (OIDC trust, deploy/plan roles, boundary, state bucket, CloudTrail)"
    # Decide CreateOidcProvider only on first creation; on REBOOTSTRAP keep the stack's own value,
    # otherwise the stack-owned provider would be seen as "external" and deleted.
    OIDC_ARG=()
    FIRST_BOOTSTRAP=0
    aws cloudformation describe-stacks --stack-name serve7-bootstrap >/dev/null 2>&1 || FIRST_BOOTSTRAP=1
    if [[ "$FIRST_BOOTSTRAP" == "1" ]]; then
      OIDC=true
      aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[].Arn' --output text | grep -q token.actions.githubusercontent.com && OIDC=false
      OIDC_ARG=(CreateOidcProvider="$OIDC")
    fi
    # GitHub trust for CI: derived from the git remote unless set explicitly. Done on first creation, and on
    # RETRUST=1 (repo moved to another org/name: re-points the deploy/plan roles at the new repo).
    if [[ "$FIRST_BOOTSTRAP" == "1" || "${RETRUST:-0}" == "1" ]]; then
      ORIGIN="$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)"
      if [[ "$ORIGIN" =~ github.com[:/]([^/]+)/([^/.]+) ]]; then
        GITHUB_ORG="${GITHUB_ORG:-${BASH_REMATCH[1]}}"; GITHUB_REPO="${GITHUB_REPO:-${BASH_REMATCH[2]}}"
      fi
      if [[ -z "${GITHUB_ORG:-}" || -z "${GITHUB_REPO:-}" ]]; then
        echo "ERROR: cannot tell which GitHub repo CI runs from (no github.com 'origin' remote, e.g. an uploaded zip)." >&2
        echo "       Set it explicitly: GITHUB_ORG=<org> GITHUB_REPO=<repo> ./scripts/deploy.sh $TENANT $ENVIRONMENT" >&2
        exit 1
      fi
      if [[ -z "${GITHUB_SUBJECT_PREFIX+x}" ]] && command -v gh >/dev/null; then
        GITHUB_SUBJECT_PREFIX="$(gh api "repos/${GITHUB_ORG}/${GITHUB_REPO}/actions/oidc/customization/sub" --jq '.sub_claim_prefix // ""' 2>/dev/null || true)"
      fi
      say "GitHub trust: ${GITHUB_ORG}/${GITHUB_REPO}${GITHUB_SUBJECT_PREFIX:+ (subject prefix ${GITHUB_SUBJECT_PREFIX})}"
    fi
    [[ -n "${GITHUB_ORG:-}" ]] && OIDC_ARG+=(GitHubOrg="$GITHUB_ORG")
    [[ -n "${GITHUB_REPO:-}" ]] && OIDC_ARG+=(GitHubRepo="$GITHUB_REPO")
    # On RETRUST an empty prefix is passed on purpose: it clears an old immutable-subject prefix of the previous owner.
    if [[ -n "${GITHUB_SUBJECT_PREFIX:-}" || "${RETRUST:-0}" == "1" ]]; then OIDC_ARG+=(GitHubSubjectPrefix="${GITHUB_SUBJECT_PREFIX:-}"); fi
    [[ -n "${DEPLOY_TRUST:-}" ]] && OIDC_ARG+=(DeployTrust="$DEPLOY_TRUST")
    TRAIL="${CREATE_TRAIL:-true}"
    aws cloudformation deploy \
      --stack-name serve7-bootstrap \
      --template-file "$ROOT/bootstrap/bootstrap.yaml" \
      --capabilities CAPABILITY_NAMED_IAM \
      --parameter-overrides Tenant="$TENANT" Environment="$ENVIRONMENT" "${OIDC_ARG[@]}" CreateCloudTrail="$TRAIL" \
      --no-fail-on-empty-changeset
  else
    say "Bootstrap stack already present"
  fi
fi

# ------------------------------------------------------------------ pre-flight + model discovery
say "Pre-flight"
[[ "${PLAN_ONLY:-0}" == "1" ]] && SKIP_MODEL_TEST=1
python3 "$ROOT/scripts/preflight.py" --tenant "$TENANT" --region "$REGION" ${SKIP_MODEL_TEST:+--skip-invoke-test}

# ------------------------------------------------------------------ layers
EXTRA_VARS=(-var "state_bucket=${STATE_BUCKET}" -var "release=${RELEASE:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo manual)}")
# Connect aliases are unique per region across all AWS customers: default to one derived from the account.
TENANT_ALIAS="$(TI instance_alias)"
ALIAS="${INSTANCE_ALIAS:-${TENANT_ALIAS:-s7-${TENANT}-${ENVIRONMENT}-${ACCOUNT: -6}}}"

# CloudShell home is 1 GB and the AWS provider is ~700 MB: keep providers and .terraform in /tmp, shared by all layers.
export TF_PLUGIN_CACHE_DIR=/tmp/tf-plugin-cache; mkdir -p "$TF_PLUGIN_CACHE_DIR"
for layer in $LAYERS; do
  export TF_DATA_DIR="/tmp/tfdata/$layer"
  say "Layer ${layer}"
  dir="$ROOT/stacks/$layer"
  terraform -chdir="$dir" init -input=false -reconfigure \
    -backend-config="bucket=${STATE_BUCKET}" \
    -backend-config="key=${layer}/terraform.tfstate" \
    -backend-config="region=${REGION}" >/dev/null
  if [[ "$layer" == "30-routing" && "${PLAN_ONLY:-0}" != "1" ]]; then
    INSTANCE_ALIAS="$ALIAS" python3 "$ROOT/scripts/validate-flow.py" --tenant "$TENANT" --env "$ENVIRONMENT"
  fi
  # -var only for variables the layer declares (Terraform rejects undeclared -var values).
  LAYER_VARS=()
  [[ "$layer" == "20-connect-core" ]] && LAYER_VARS+=(-var "instance_alias=${ALIAS}")
  [[ "$layer" == "30-routing" && -n "${CLAIM_PHONE:-}" ]] && LAYER_VARS+=(-var "claim_phone_number=${CLAIM_PHONE}")
  if [[ "${PLAN_ONLY:-0}" == "1" ]]; then
    # read-only role: no state lock, no apply
    if ! terraform -chdir="$dir" plan -input=false -lock=false -var-file="$TFVARS" "${EXTRA_VARS[@]}" "${LAYER_VARS[@]}" \
         -compact-warnings -no-color > "$ROOT/plan-${layer}.txt" 2>&1; then
      cat "$ROOT/plan-${layer}.txt"; echo "Plan failed for ${layer}"; exit 1
    fi
    grep -E '^(Plan:|No changes)' "$ROOT/plan-${layer}.txt" || true
    continue
  fi
  terraform -chdir="$dir" plan -input=false -out=tfplan -var-file="$TFVARS" "${EXTRA_VARS[@]}" "${LAYER_VARS[@]}" -compact-warnings
  if [[ "${AUTO_APPROVE:-0}" != "1" ]]; then
    read -r -p "Apply ${layer}? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || { echo "Stopped before ${layer}."; exit 0; }
  fi
  terraform -chdir="$dir" apply -input=false tfplan
  if [[ "$layer" == "40-genai" ]] && [[ "$(terraform -chdir="$dir" output -raw agent_tools_gateway_audience_ok 2>/dev/null || echo true)" == "false" ]]; then
    say "Second pass: registering the new MCP gateway id as its JWT audience"
    terraform -chdir="$dir" apply -input=false -auto-approve -var-file="$TFVARS" "${EXTRA_VARS[@]}" "${LAYER_VARS[@]}" -compact-warnings
  fi
  if [[ "$layer" == "40-genai" ]]; then
    say "Syncing knowledge base content"
    python3 "$ROOT/scripts/sync-knowledge.py" --tenant "$TENANT" --env "$ENVIRONMENT" || echo "(knowledge sync skipped)"
    python3 "$ROOT/scripts/seed-demo-crm.py" --tenant "$TENANT" --env "$ENVIRONMENT" || echo "(demo CRM seed skipped)"
    say "Configuring the self-service AI agent"
    python3 "$ROOT/scripts/configure-ai-agent.py" --tenant "$TENANT" --env "$ENVIRONMENT"
  fi
done

say "Done. Run the smoke tests:  python3 tests/smoke.py --tenant ${TENANT}"
