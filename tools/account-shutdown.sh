#!/usr/bin/env bash
# Shut down everything from this project that costs money in ONE AWS account (accelerator tenants + demo kit).
# Use it after ./scripts/destroy.sh, or instead of it, when you are done with a trial (docs/start-here-with-claude.md).
#
#   bash tools/account-shutdown.sh            # inventory only: lists what would be deleted, changes nothing
#   bash tools/account-shutdown.sh --delete   # deletes it (asks you to type the account id first)
#
# A person runs it (AWS CloudShell or a laptop), signed in as an admin of that account, in the tenant's region
# (default ca-central-1; override with REGION=us-east-1). Claude prepares the command but never runs --delete.
# Only project resources are touched, matched by name: s7-*, serve7-*, ccg-*, demo-*, demo-kit*.
# KEPT on purpose (free or needed to rebuild): stack serve7-bootstrap (roles, state bucket, CloudTrail), the GitHub CI
# trust stacks (*-ci), IAM roles. The Terraform state files are emptied so a future deploy starts clean.
# PERMANENT: Connect instances (with their users, flows, recordings settings), phone numbers, tables with demo data,
# Lambdas, streams and buckets are deleted. KMS keys and secrets get the shortest recovery window (7 days).
# It lives in tools/, not scripts/, because a change under scripts/ triggers the CI deploy.
set -uo pipefail
export AWS_DEFAULT_REGION="${REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}"
export AWS_PAGER=""
MODE="${1:-}"
DO=0; [[ "$MODE" == "--delete" ]] && DO=1

ACCOUNT=$(aws sts get-caller-identity --query Account --output text) || { echo "No AWS credentials. Open CloudShell in the account first."; exit 1; }
PFX_RE='^(s7-|serve7-|ccg-|demo-|demo-kit)'
KEEP_STACK_RE='^(serve7-bootstrap|.*-ci)$'

say(){ printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
item(){ printf '   - %s\n' "$*"; }
run(){ if [[ $DO == 1 ]]; then "$@" >/dev/null 2>&1 && echo "     deleted" || echo "     (failed or already gone, continuing)"; fi; }
lines(){ tr '\t' '\n' | sed '/^$/d;/^None$/d'; }

echo "Account $ACCOUNT, region $AWS_DEFAULT_REGION. Mode: $([[ $DO == 1 ]] && echo DELETE || echo 'inventory only (nothing changes)')"
if [[ $DO == 1 ]]; then
  read -r -p "This PERMANENTLY deletes the project resources below in $ACCOUNT. Type the account id to continue: " ok
  [[ "$ok" == "$ACCOUNT" ]] || { echo "Nothing deleted."; exit 0; }
fi

# ---------- 1. Connect instances (project aliases) and their phone numbers
say "Connect instances and phone numbers"
INSTANCES=$(aws connect list-instances --query 'InstanceSummaryList[].[Id,InstanceAlias,Arn]' --output text 2>/dev/null || true)
PROJECT_INSTANCES=""
while read -r id alias arn; do
  [[ -z "${id:-}" ]] && continue
  if [[ "$alias" =~ $PFX_RE ]]; then
    item "instance $alias ($id)"; PROJECT_INSTANCES+="$id $arn"$'\n'
  else
    item "instance $alias: NOT a project instance, left alone"
  fi
done <<< "$INSTANCES"
while read -r id arn; do
  [[ -z "${id:-}" ]] && continue
  for num in $(aws connect list-phone-numbers-v2 --target-arn "$arn" --query 'ListPhoneNumbersSummaryList[].[PhoneNumberId,PhoneNumber]' --output text 2>/dev/null | awk '{print $1"|"$2}'); do
    item "phone number ${num#*|} (released, the number cannot be got back)"
    run aws connect release-phone-number --phone-number-id "${num%%|*}"
  done
done <<< "$PROJECT_INSTANCES"

# ---------- 2. S3 buckets: empty project buckets first (stacks cannot delete non-empty buckets)
say "S3 buckets"
BUCKETS=$(aws s3api list-buckets --query 'Buckets[].Name' --output text | lines | grep -E "$PFX_RE" || true)
empty_bucket(){  # all versions + delete markers
  local b=$1
  aws s3 rm "s3://$b" --recursive --quiet >/dev/null 2>&1 || true
  while :; do
    local v
    v=$(aws s3api list-object-versions --bucket "$b" --max-items 500 --output json \
        --query '{Objects: [Versions[].{Key:Key,VersionId:VersionId}, DeleteMarkers[].{Key:Key,VersionId:VersionId}][]}' 2>/dev/null)
    [[ -z "$v" || "$v" == *'"Objects": []'* || "$v" == *'"Objects": null'* ]] && break
    aws s3api delete-objects --bucket "$b" --delete "$v" >/dev/null 2>&1 || break
  done
}
for b in $BUCKETS; do
  case "$b" in
    serve7-tfstate-*)    item "$b: kept, Terraform state files emptied"; [[ $DO == 1 ]] && empty_bucket "$b" ;;
    serve7-cloudtrail-*) item "$b: kept (audit log, object-locked)";;
    *)                   item "$b: emptied, then deleted"; [[ $DO == 1 ]] && empty_bucket "$b" ;;
  esac
done

# ---------- 3. CloudFormation stacks (demos, Scenario 5, Phase 1, Terraform module stacks)
say "CloudFormation stacks"
STACKS=$(aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE ROLLBACK_COMPLETE IMPORT_COMPLETE UPDATE_ROLLBACK_FAILED DELETE_FAILED \
  --query 'StackSummaries[?ParentId==null].StackName' --output text | lines | grep -E "$PFX_RE" | grep -vE "$KEEP_STACK_RE" || true)
for s in $STACKS; do item "stack $s"; [[ $DO == 1 ]] && aws cloudformation delete-stack --stack-name "$s" >/dev/null 2>&1; done
for s in $(aws cloudformation list-stacks --query 'StackSummaries[?ParentId==null && StackStatus!=`DELETE_COMPLETE`].StackName' --output text | lines | grep -E "$KEEP_STACK_RE" || true); do
  item "stack $s: kept"
done
if [[ $DO == 1 && -n "$STACKS" ]]; then
  echo "   waiting for stacks to delete (a CloudFront distribution can take 15-20 min)..."
  for s in $STACKS; do
    aws cloudformation wait stack-delete-complete --stack-name "$s" 2>/dev/null && echo "     $s deleted" \
      || echo "     $s: not deleted yet, check CloudFormation > Events (you can re-run this script)"
  done
fi

# ---------- 4. Connect instances (after the stacks that referenced them)
for id in $(awk '{print $1}' <<< "$PROJECT_INSTANCES"); do
  [[ $DO == 1 ]] && { echo "   deleting Connect instance $id"; run aws connect delete-instance --instance-id "$id"; }
done

# ---------- 5. Resources Terraform created directly (sandbox / client tenants)
say "Kinesis streams (largest steady cost)"
for s in $(aws kinesis list-streams --query 'StreamNames' --output text | lines | grep -E "$PFX_RE" || true); do
  item "$s"; run aws kinesis delete-stream --stream-name "$s" --enforce-consumer-deletion
done
say "Lambda functions"
for f in $(aws lambda list-functions --query 'Functions[].FunctionName' --output text | lines | grep -E "$PFX_RE" || true); do
  item "$f"; run aws lambda delete-function --function-name "$f"
done
say "DynamoDB tables"
for t in $(aws dynamodb list-tables --query 'TableNames' --output text | lines | grep -E "$PFX_RE" || true); do
  item "$t"
  [[ $DO == 1 ]] && aws dynamodb update-table --table-name "$t" --no-deletion-protection-enabled >/dev/null 2>&1
  run aws dynamodb delete-table --table-name "$t"
done
say "Bedrock guardrails"
for g in $(aws bedrock list-guardrails --query 'guardrails[].[id,name]' --output text 2>/dev/null | awk '{print $1"|"$2}'); do
  [[ "${g#*|}" =~ $PFX_RE ]] || continue
  item "${g#*|}"; run aws bedrock delete-guardrail --guardrail-identifier "${g%%|*}"
done
say "CloudWatch dashboards, alarms, SNS topics, log groups"
for d in $(aws cloudwatch list-dashboards --query 'DashboardEntries[].DashboardName' --output text | lines | grep -E "$PFX_RE" || true); do
  item "dashboard $d"; run aws cloudwatch delete-dashboards --dashboard-names "$d"
done
ALARMS=$(aws cloudwatch describe-alarms --query 'MetricAlarms[].AlarmName' --output text | lines | grep -E "$PFX_RE" || true)
[[ -n "$ALARMS" ]] && { item "$(wc -l <<< "$ALARMS") alarms"; [[ $DO == 1 ]] && while read -r a; do aws cloudwatch delete-alarms --alarm-names "$a" >/dev/null 2>&1; done <<< "$ALARMS"; }
for t in $(aws sns list-topics --query 'Topics[].TopicArn' --output text | lines || true); do
  [[ "${t##*:}" =~ $PFX_RE ]] || continue
  item "topic ${t##*:}"; run aws sns delete-topic --topic-arn "$t"
done
LOGS=$(aws logs describe-log-groups --query 'logGroups[].logGroupName' --output text | lines | grep -E '^/aws/(lambda|connect|vendedlogs)/(s7-|serve7-|ccg-|demo-)' || true)
[[ -n "$LOGS" ]] && { item "$(wc -l <<< "$LOGS") log groups"; [[ $DO == 1 ]] && while read -r l; do aws logs delete-log-group --log-group-name "$l" >/dev/null 2>&1; done <<< "$LOGS"; }

# ---------- 6. Remaining project buckets (stacks with Retain, Terraform buckets)
say "Deleting emptied buckets"
for b in $BUCKETS; do
  case "$b" in serve7-tfstate-*|serve7-cloudtrail-*) continue;; esac
  if aws s3api head-bucket --bucket "$b" >/dev/null 2>&1; then item "$b"; [[ $DO == 1 ]] && { empty_bucket "$b"; run aws s3api delete-bucket --bucket "$b"; }; fi
done

# ---------- 7. Secrets, parameters, KMS keys (small monthly fees each)
say "Secrets Manager (7-day recovery window)"
for s in $(aws secretsmanager list-secrets --query 'SecretList[].Name' --output text | lines | grep -E '^(s7-|serve7-|ccg-s5/|ccg-|demo-kit/|demo-)' || true); do
  item "$s"; run aws secretsmanager delete-secret --secret-id "$s" --recovery-window-in-days 7
done
say "SSM parameters"
PARAMS=$(aws ssm describe-parameters --query 'Parameters[].Name' --output text | lines | grep -E '^/(ccg-s5|demo-kit|s7-)' || true)
[[ -n "$PARAMS" ]] && { item "$(wc -l <<< "$PARAMS") parameters"; [[ $DO == 1 ]] && while read -r p; do aws ssm delete-parameter --name "$p" >/dev/null 2>&1; done <<< "$PARAMS"; }
say "KMS keys (scheduled for deletion in 7 days; the bootstrap key is kept)"
for k in $(aws kms list-aliases --query 'Aliases[?TargetKeyId!=null].[AliasName,TargetKeyId]' --output text | awk '{print $1"|"$2}'); do
  alias=${k%%|*}; key=${k#*|}
  [[ "$alias" =~ ^alias/(s7-|ccg-|demo-) ]] || continue
  item "$alias"; run aws kms schedule-key-deletion --key-id "$key" --pending-window-in-days 7
done

say "Done"
if [[ $DO == 1 ]]; then
  echo "Re-run 'bash tools/account-shutdown.sh' (inventory mode) to see anything left; a stack still deleting shows up again."
else
  echo "Nothing was changed. To delete the items above: bash tools/account-shutdown.sh --delete"
fi
echo "Also stop CI from rebuilding: GitHub repo > Settings > Secrets and variables > Actions > Variables > AWS_CI = false"
