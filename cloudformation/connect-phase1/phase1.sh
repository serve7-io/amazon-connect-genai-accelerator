#!/usr/bin/env bash
# Phase 1: deploy / verify / tear down / redeploy one Amazon Connect instance from template.yaml, in CloudShell.
#
#   ./phase1.sh deploy        create or update the stack, wire the phone number (if any), run verify.py
#   ./phase1.sh verify        check the live wiring (flows -> queues, users -> routing profile, ...)
#   ./phase1.sh passwords     where the two agents' initial passwords are (Secrets Manager)
#   ./phase1.sh delete        empty the storage bucket and delete the stack (instance included)
#   ./phase1.sh redeploy      delete, then deploy again, and report both timings (repeatability test)
#
# Settings (environment variables):
#   STACK=s7-connect-phase1           stack name
#   ALIAS=s7-phase1-<last 6 of acct>  instance alias (unique per region across all AWS customers)
#   PHONE_COUNTRY=CA PHONE_PREFIX=+1403   claim a number and point it at "Support Inbound" (needs quota)
set -euo pipefail
cd "$(dirname "$0")"

STACK="${STACK:-s7-connect-phase1}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGION="$(aws configure get region || true)"; REGION="${REGION:-${AWS_REGION:-ca-central-1}}"
ALIAS="${ALIAS:-s7-phase1-${ACCOUNT: -6}}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

say() { printf '\n==> %s\n' "$*"; }
out() { aws cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null; }
exists() { aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; }

deploy() {
  say "Deploying $STACK to account $ACCOUNT, $REGION, instance alias '$ALIAS'"
  # A stack whose first create failed (ROLLBACK_COMPLETE) can only be deleted: clear it automatically.
  if [[ "$(aws cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].StackStatus' --output text 2>/dev/null)" == "ROLLBACK_COMPLETE" ]]; then
    echo "Removing the failed earlier attempt of $STACK first."
    aws cloudformation delete-stack --stack-name "$STACK" && aws cloudformation wait stack-delete-complete --stack-name "$STACK"
  fi
  if ! exists; then
    n=$(aws connect list-instances --query "length(InstanceSummaryList)" --output text)
    if [[ "$n" != "0" && "${ALLOW_EXTRA_INSTANCE:-0}" != "1" ]]; then
      echo "STOP: account $ACCOUNT already has $n Connect instance(s). New accounts allow only 1, so this would fail."
      echo "Run it in the lab account, or set ALLOW_EXTRA_INSTANCE=1 if this account's instance quota was raised."
      exit 1
    fi
  fi
  local params=("InstanceAlias=$ALIAS")
  [[ -n "${PHONE_COUNTRY:-}" ]] && params+=("PhoneCountryCode=$PHONE_COUNTRY")
  [[ -n "${PHONE_PREFIX:-}" ]] && params+=("PhonePrefix=$PHONE_PREFIX")
  local t0=$SECONDS
  aws cloudformation deploy --stack-name "$STACK" --template-file template.yaml \
    --parameter-overrides "${params[@]}" --no-fail-on-empty-changeset \
    --tags serve7:component=connect-phase1 serve7:managed-by=cloudformation
  say "Stack ready in $(( (SECONDS - t0) / 60 ))m $(( (SECONDS - t0) % 60 ))s"

  # CloudFormation can claim a phone number but cannot point it at a flow: do that here.
  local num; num="$(out PhoneNumberArn)"
  if [[ -n "$num" && "$num" != "None" ]]; then
    aws connect associate-phone-number-contact-flow --phone-number-id "${num##*/}" \
      --instance-id "$(out InstanceId)" --contact-flow-id "$(out SupportInboundFlowArn | sed 's#.*/##')"
    echo "Phone number $(out PhoneNumber) -> Support Inbound"
  fi
  verify
  echo
  echo "Admin console: $(out AccessUrl)  (or Connect console -> instance '$ALIAS' -> Emergency access)"
}

verify() { say "Verifying wiring"; python3 verify.py --stack "$STACK"; }

passwords() {
  say "Initial agent passwords are in Secrets Manager (not printed here). To read one in CloudShell:"
  echo "  aws secretsmanager get-secret-value --secret-id $(out Agent1PasswordSecret) --query SecretString --output text"
  echo "  aws secretsmanager get-secret-value --secret-id $(out Agent2PasswordSecret) --query SecretString --output text"
  echo "Never paste them into chat or tickets; agents change them on first login."
}

delete() {
  exists || { echo "Stack $STACK does not exist."; return 0; }
  local num; num="$(out PhoneNumberArn)"
  if [[ -n "$num" && "$num" != "None" && "${CONFIRM_RELEASE:-}" != "yes" ]]; then
    echo "This stack holds phone number $(out PhoneNumber). Deleting RELEASES it (it cannot be reclaimed)."
    echo "Re-run with CONFIRM_RELEASE=yes to continue."; exit 1
  fi
  local bucket; bucket="$(out StorageBucket)"
  say "Deleting $STACK (instance '$ALIAS' and everything in it)"
  local t0=$SECONDS
  [[ -n "$bucket" && "$bucket" != "None" ]] && aws s3 rm "s3://$bucket" --recursive --quiet || true
  aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK"
  say "Deleted in $(( (SECONDS - t0) / 60 ))m $(( (SECONDS - t0) % 60 ))s"
}

case "${1:-}" in
  deploy) deploy ;;
  verify) verify ;;
  passwords) passwords ;;
  delete) delete ;;
  redeploy) t=$SECONDS; delete; deploy; say "Teardown + redeploy total: $(( (SECONDS - t) / 60 ))m" ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
