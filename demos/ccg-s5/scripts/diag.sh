#!/usr/bin/env bash
# Recent Connect flow steps + ACXD API / payment Lambda logs (no card data is ever logged).
#   bash scripts/diag.sh [minutes=30]
set -uo pipefail
export AWS_DEFAULT_REGION=${REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}
M=${1:-30}; START=$(( ($(date +%s) - M * 60) * 1000 ))
INST=$(aws ssm get-parameter --name /ccg-s5/instance-id --query Parameter.Value --output text)
ALIAS=$(aws connect describe-instance --instance-id "$INST" --query Instance.InstanceAlias --output text)
echo "== Connect flow steps, last ${M} min (/aws/connect/$ALIAS)"
aws logs filter-log-events --log-group-name "/aws/connect/$ALIAS" --start-time $START --query 'events[].message' --output json 2>/dev/null |
python3 -c "
import sys, json
for m in json.load(sys.stdin) or []:
    try: e = json.loads(m)
    except Exception: continue
    print(e.get('Timestamp','')[11:19], e.get('ContactId','')[:8], e.get('ContactFlowName',''), e.get('ContactFlowModuleType',''),
          e.get('Identifier',''), json.dumps(e.get('Results',''))[:80], json.dumps(e.get('ExternalResults',''))[:160])"
for fn in ccg-s5-acxd-api ccg-s5-payment; do
  echo; echo "== Lambda $fn, last ${M} min"
  aws logs tail "/aws/lambda/$fn" --since "${M}m" --format short 2>/dev/null | grep -v -E " (START|END|REPORT|INIT_START)" | tail -40
done
