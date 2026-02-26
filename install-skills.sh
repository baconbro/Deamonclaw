#!/usr/bin/env bash
# install-skills.sh — copies DAEMON skills into an OpenClaw workspace
# Usage: ./install-skills.sh /path/to/openclaw/workspace

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="${SCRIPT_DIR}/skills"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/openclaw/workspace"
  exit 1
fi

OPENCLAW_WORKSPACE="$1"
SKILLS_DEST="${OPENCLAW_WORKSPACE}/skills"

if [[ ! -d "${OPENCLAW_WORKSPACE}" ]]; then
  echo "Error: OpenClaw workspace not found at ${OPENCLAW_WORKSPACE}"
  exit 1
fi

echo "Installing DAEMON skills into ${SKILLS_DEST} ..."
mkdir -p "${SKILLS_DEST}"

for skill_dir in "${SKILLS_SRC}"/*/; do
  skill_name="$(basename "${skill_dir}")"
  dest="${SKILLS_DEST}/${skill_name}"
  echo "  → ${skill_name}"
  mkdir -p "${dest}"
  cp "${skill_dir}/SKILL.md" "${dest}/SKILL.md"
done

echo ""
echo "Done! Skills installed:"
ls -1 "${SKILLS_DEST}"
echo ""
echo "Restart OpenClaw to pick up the new skills:"
echo "  openclaw restart"
