#!/usr/bin/env sh
# Publish the five repositories as PRIVATE GitHub repositories under your account or organisation.
# This is NOT run automatically and nothing in this workshop has done it. It needs the GitHub CLI (`gh`), logged in, with permission to create repositories.
# Usage: sh suite/publish.sh <owner> [repos-dir]
set -eu
owner=${1:?usage: publish.sh <github-owner> [repos-dir]}
here=$(cd "$(dirname "$0")/.." && pwd)
dir=${2:-"$here/repos"}
command -v gh >/dev/null 2>&1 || { echo "GitHub CLI 'gh' not found. See PUBLISH.md for the manual steps."; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run 'gh auth login' first."; exit 1; }
for name in substance-designer-ai roblox-vfx-ai roblox-level-design-ai modular-set-dressing-ai concept-art-ai; do
  [ -d "$dir/$name/.git" ] || { echo "missing $dir/$name (run: sh suite/restore_repos.sh)"; exit 1; }
  echo "== $owner/$name (private)"
  ( cd "$dir/$name" && gh repo create "$owner/$name" --private --source . --remote origin --push )
done
echo "Done. Verify each at https://github.com/$owner/<name> and keep them private until you have reviewed ASSET_LICENSING.md."
