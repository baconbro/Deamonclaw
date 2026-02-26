#!/usr/bin/env bash
# clawhub/publish.sh — publish DAEMON skills to ClawHub
#
# Prerequisites:
#   npm install -g clawhub-cli   (or the equivalent ClawHub publish tool)
#
# Usage:
#   ./clawhub/publish.sh                   # publish all skills
#   ./clawhub/publish.sh daemon-memory     # publish one skill

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
SKILLS_DIR="${REPO_ROOT}/skills"

ALL_SKILLS=(
  daemon-memory
  daemon-safety
  daemon-perception
  daemon-context
)

if ! command -v clawhub &>/dev/null; then
  echo "Error: clawhub CLI not found."
  echo "Install it with:  npm install -g clawhub-cli"
  exit 1
fi

# Publish one or all skills
if [[ $# -ge 1 ]]; then
  TARGETS=("$@")
else
  TARGETS=("${ALL_SKILLS[@]}")
fi

echo "Publishing ${#TARGETS[@]} skill(s) to ClawHub ..."
echo ""

for skill in "${TARGETS[@]}"; do
  skill_path="${SKILLS_DIR}/${skill}"
  if [[ ! -d "${skill_path}" ]]; then
    echo "  [skip] ${skill} — directory not found at ${skill_path}"
    continue
  fi
  echo "  → Publishing ${skill} ..."
  clawhub publish "${skill_path}" --confirm
  echo "    ✓ ${skill} published"
done

echo ""
echo "Done. Skills are now available on ClawHub."
echo "Users can install them with:"
for skill in "${TARGETS[@]}"; do
  echo "  clawhub install ${skill}"
done
