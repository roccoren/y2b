#!/usr/bin/env bash
set -euo pipefail

# Async YouTube audio download via API queue.
# Usage: ./curl-async-example.sh "https://www.youtube.com/watch?v=VIDEO_ID" [format] [quality]
# Env vars:
#   BASE_URL        (default http://localhost:8000)
#   COOKIES_FILE    (default ytb-cookie.txt)

BASE_URL="${BASE_URL:-http://localhost:8000}"
VIDEO_URL="${1:-}"
FORMAT="${2:-mp3}"
QUALITY="${3:-best}"
COOKIES_FILE="${COOKIES_FILE:-ytb-cookie.txt}"

if [[ -z "$VIDEO_URL" ]]; then
  echo "ERROR: Provide a video URL."
  echo "Example: $0 \"https://www.youtube.com/watch?v=dQw4w9WgXcQ\""
  exit 1
fi

if [[ ! -f "$COOKIES_FILE" ]]; then
  echo "WARN: Cookies file '$COOKIES_FILE' not found; proceeding without cookies."
  COOKIES_CONTENT=""
else
  # Escape any embedded quotes and strip CR for Windows-exported files
  COOKIES_CONTENT=$(sed 's/"/\\"/g' "$COOKIES_FILE" | tr -d '\r')
fi

echo "Enqueueing job..."

JSON_PAYLOAD=$(cat <<EOF
{
  "url": "$VIDEO_URL",
  "format": "$FORMAT",
  "quality": "$QUALITY",
  "cookies": "$COOKIES_CONTENT"
}
EOF
)

ENQUEUE_RESPONSE=$(curl -s -X POST -H "Content-Type: application/json" \
  -d "$JSON_PAYLOAD" "$BASE_URL/download/async")
echo "Enqueue response: $ENQUEUE_RESPONSE"

if command -v jq >/dev/null 2>&1; then
  JOB_ID=$(echo "$ENQUEUE_RESPONSE" | jq -r '.job_id')
else
  JOB_ID=$(echo "$ENQUEUE_RESPONSE" | sed -n 's/.*"job_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
fi

if [[ -z "$JOB_ID" || "$JOB_ID" == "null" ]]; then
  echo "Failed to extract job_id"
  exit 1
fi

echo "Job ID: $JOB_ID"

# Poll for completion
while true; do
  STATUS_JSON=$(curl -s "$BASE_URL/download/async/$JOB_ID")
  echo "$STATUS_JSON"
  if command -v jq >/dev/null 2>&1; then
    STATUS=$(echo "$STATUS_JSON" | jq -r '.status')
  else
    STATUS=$(echo "$STATUS_JSON" | sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  fi

  if [[ "$STATUS" == "completed" ]]; then
    echo "Job completed."
    if command -v jq >/dev/null 2>&1; then
      FILENAME=$(echo "$STATUS_JSON" | jq -r '.result.filename')
    else
      FILENAME=$(echo "$STATUS_JSON" | sed -n 's/.*"filename"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    fi
    if [[ -n "$FILENAME" && "$FILENAME" != "null" ]]; then
      echo "Downloading file: $FILENAME"
      curl -f -o "$FILENAME" "$BASE_URL/download/$FILENAME"
      echo "Saved $FILENAME"
    fi
    exit 0
  elif [[ "$STATUS" == "error" ]]; then
    echo "Job failed."
    exit 1
  else
    echo "Status: $STATUS ... waiting"
    sleep 3
  fi
done