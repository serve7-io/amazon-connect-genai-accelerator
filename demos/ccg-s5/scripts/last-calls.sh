#!/usr/bin/env bash
# Scenario 5 results of recent calls (contact attributes + payments table; no card data is ever stored). CloudShell.
#   bash ccg-s5/scripts/last-calls.sh [hours=2]
set -uo pipefail
export AWS_DEFAULT_REGION=${REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}
H=${1:-2}
INST=$(aws ssm get-parameter --name /ccg-s5/instance-id --query Parameter.Value --output text)
START=$(date -u -d "-${H} hours" +%Y-%m-%dT%H:%M:%SZ); END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "== Voice calls, last ${H}h (contact, start, payment result, attempt, confirmation)"
aws connect search-contacts --instance-id "$INST" --time-range "Type=INITIATION_TIMESTAMP,StartTime=$START,EndTime=$END" \
  --query "Contacts[?Channel=='VOICE'].[Id,InitiationTimestamp]" --output text 2>/dev/null |
while read -r cid ts; do
  [[ -z "$cid" ]] && continue
  attrs=$(aws connect get-contact-attributes --instance-id "$INST" --initial-contact-id "$cid" --query Attributes --output json 2>/dev/null)
  [[ -z "$attrs" || "$attrs" == "null" ]] && attrs='{}'
  printf '%s' "$attrs" | python3 -c "
import json,sys; a=json.load(sys.stdin)
print('$cid'[:8], '$ts'[:19], a.get('s5_payment_result','-'), 'attempt='+a.get('s5_attempt','-'), a.get('s5_confirmation',''),
      a.get('s5_card_brand',''), a.get('s5_card_last4',''), 'policy='+a.get('s5_policy_id','-'))"
done
echo
echo "== Payments table (newest first)"
aws dynamodb scan --table-name ccg-s5-payments --output json 2>/dev/null | python3 -c "
import json,sys
rows=json.load(sys.stdin).get('Items',[])
g=lambda r,k: list(r.get(k,{'S':''}).values())[0]
for r in sorted(rows, key=lambda r: g(r,'created_at'), reverse=True)[:10]:
    print(g(r,'created_at')[:19], g(r,'policy_id'), g(r,'result'), g(r,'confirmation'), g(r,'brand'), g(r,'last4'), g(r,'channel'), 'receipt='+(g(r,'receipt') or '-'))
print(f'({len(rows)} payment rows)')"
echo
echo "== Policies (status after write-back)"
aws dynamodb scan --table-name ccg-s5-policies --output json 2>/dev/null | python3 -c "
import json,sys
g=lambda r,k: list(r.get(k,{'S':''}).values())[0]
for r in sorted(json.load(sys.stdin).get('Items',[]), key=lambda r: g(r,'policy_id')):
    print(g(r,'policy_id'), g(r,'member_id'), g(r,'status'), g(r,'last_confirmation'), 'next='+(g(r,'next_renewal_date') or '-'))"
