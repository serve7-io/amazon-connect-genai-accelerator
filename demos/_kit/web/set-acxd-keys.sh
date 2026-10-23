#!/usr/bin/env bash
# ONE-TIME per demo, in AWS CloudShell of the demo account: store the demo's ACXD browser keys in Secrets Manager
# (demo-kit/web/<demo>/acxd). The kit reads them when it renders the demo's web test page.
#   bash demos/_kit/web/set-acxd-keys.sh renault-rental
# Where to find the values: ACXD > the app > Deploy tab (code snippet) or Settings / Access.
#   deployment key = the Production deployment key (also used as the Connect flow's alias)
#   Live Sync API key = optional; leave empty to skip Live Sync on the page
# The prompts are hidden: nothing is echoed or stored in shell history. Never paste these keys into a GitHub issue,
# a PR or a chat: the repository is public.
set -euo pipefail
NAME=${1:?usage: set-acxd-keys.sh <demo>}
[[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{1,30}$ ]] || { echo "bad demo name"; exit 1; }
export AWS_DEFAULT_REGION=${REGION:-ca-central-1}
read -r -s -p "Production deployment key of the ACXD app for $NAME: " DK; echo
read -r -s -p "Live Sync API key (Enter to skip Live Sync): " AK; echo
[ -n "$DK" ] || { echo "the deployment key is required"; exit 1; }
SECRET=$(DK="$DK" AK="$AK" python3 -c "import json,os;d={'deploymentKey':os.environ['DK']};a=os.environ.get('AK');d.update({'apiKey':a} if a else {});print(json.dumps(d))")
ID="demo-kit/web/$NAME/acxd"
if aws secretsmanager describe-secret --secret-id "$ID" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id "$ID" --secret-string "$SECRET" >/dev/null && echo "updated $ID"
else
  aws secretsmanager create-secret --name "$ID" --secret-string "$SECRET" \
    --description "ACXD browser keys for the $NAME test page" --tags Key=serve7:project,Value=demo Key=serve7:demo,Value="$NAME" >/dev/null && echo "created $ID"
fi
echo "Next: open an issue titled  demo: deploy $NAME  (creates the chat flow and publishes the page)."
