#!/usr/bin/env bash
set -euo pipefail
umask 077

title="${1:-}"
body="${2:-$title}"
dir="${HOME}/Notes/log"
mkdir -p "$dir"
chmod 700 "$dir"
file="${dir}/$(date +%F).md"
{
  printf '## %s — %s\n\n' "$(date +%H:%M)" "$title"
  printf '%s\n\n' "$body"
} >> "$file"
chmod 600 "$file"
printf '%s\n' "$file"
