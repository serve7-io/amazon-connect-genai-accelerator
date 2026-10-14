#!/usr/bin/env bash
# Contact Lens transcript + recording check for one call (default: the latest voice call). CloudShell.
#   bash ccg-s5/scripts/transcript.sh [contact-id]
# Shows each utterance with its time, marks the gap where recording/analytics were paused for keypad card entry,
# and confirms no card digits appear anywhere in the transcript.
set -uo pipefail
export AWS_DEFAULT_REGION=${REGION:-${AWS_DEFAULT_REGION:-ca-central-1}}
INST=$(aws ssm get-parameter --name /ccg-s5/instance-id --query Parameter.Value --output text)
CID=${1:-$(aws connect search-contacts --instance-id "$INST" \
  --time-range "Type=INITIATION_TIMESTAMP,StartTime=$(date -u -d '-6 hours' +%FT%TZ),EndTime=$(date -u +%FT%TZ)" \
  --query "reverse(sort_by(Contacts[?Channel=='VOICE'],&InitiationTimestamp))[0].Id" --output text 2>/dev/null)}
echo "Contact: $CID"
aws connect describe-contact --instance-id "$INST" --contact-id "$CID" \
  --query 'Contact.{start:InitiationTimestamp,end:DisconnectTimestamp,reason:DisconnectReason}' --output text 2>/dev/null

STORE=$(aws connect list-instance-storage-configs --instance-id "$INST" --resource-type CALL_RECORDINGS \
  --query 'StorageConfigs[0].S3Config.[BucketName,BucketPrefix]' --output text 2>/dev/null)
BUCKET=$(echo "$STORE" | awk '{print $1}'); PREFIX=$(echo "$STORE" | awk '{print $2}')
if [[ -z "$BUCKET" || "$BUCKET" == "None" ]]; then
  echo "!! No call-recording S3 bucket is configured on this instance: recordings and Contact Lens files are not stored."
  echo "   Console: Amazon Connect > instance > Data storage > Call recordings."
else
  echo "Recordings bucket: s3://$BUCKET/$PREFIX"
  echo "== Files for this call"
  aws s3 ls "s3://$BUCKET/$PREFIX" --recursive 2>/dev/null | grep "$CID" | awk '{print $1, $2, $3, $4}'
fi

echo "== Real-time Contact Lens transcript"
aws connect-contact-lens list-realtime-contact-analysis-segments --instance-id "$INST" --contact-id "$CID" \
  --max-results 100 --output json 2>/tmp/rt.err > /tmp/rt.json || true
python3 - "$BUCKET" "$PREFIX" "$CID" <<'PY'
import json, re, subprocess, sys
bucket, prefix, cid = sys.argv[1], sys.argv[2], sys.argv[3]
segs = []
try:
    segs = [s["Transcript"] for s in json.load(open("/tmp/rt.json")).get("Segments", []) if "Transcript" in s]
except Exception:
    pass
src = "real-time API"
if not segs and bucket and bucket != "None":   # after the call: the post-call analysis file in S3
    ls = subprocess.run(["aws", "s3", "ls", f"s3://{bucket}/{prefix}", "--recursive"], capture_output=True, text=True).stdout
    keys = [l.split()[-1] for l in ls.splitlines() if cid in l and "analysis" in l.lower() and l.endswith(".json")]
    if keys:
        raw = subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/{keys[-1]}", "-"], capture_output=True, text=True).stdout
        doc = json.loads(raw)
        segs = [{"ParticipantRole": t.get("ParticipantId"), "Content": t.get("Content", ""),
                 "BeginOffsetMillis": t.get("BeginOffsetMillis", 0), "EndOffsetMillis": t.get("EndOffsetMillis", 0)}
                for t in doc.get("Transcript", [])]
        src = f"s3://{bucket}/{keys[-1]}"
if not segs:
    print("No transcript found (real-time API returned nothing and no analysis file in S3).")
    print(open("/tmp/rt.err").read()[:300])
    sys.exit()
print(f"source: {src}")
prev_end, digits = 0, []
for s in sorted(segs, key=lambda s: s.get("BeginOffsetMillis", 0)):
    b, e = s.get("BeginOffsetMillis", 0), s.get("EndOffsetMillis", 0)
    if prev_end and b - prev_end > 20000:
        print(f"   ---- {(b - prev_end) // 1000}s with no transcript (recording + analytics paused: keypad card entry) ----")
    txt = s.get("Content", "")
    if re.search(r"\d(?:[\s-]?\d){12,18}", txt):
        digits.append(txt)
    print(f"{b//60000:02d}:{b//1000%60:02d} {str(s.get('ParticipantRole') or s.get('ParticipantId'))[:8]:8} {txt}")
    prev_end = max(prev_end, e)
print("\nCard-number-like digit runs in transcript:", "NONE (good)" if not digits else digits)
PY
