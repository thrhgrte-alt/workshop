#!/usr/bin/env sh
# Recreate the five local repositories from the git bundles in ../bundles (each bundle holds the full history of one repository).
# Usage: sh suite/restore_repos.sh [target-dir]      (default: ./repos)
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
target=${1:-"$here/repos"}
mkdir -p "$target"
for b in "$here"/bundles/*.bundle; do
  name=$(basename "$b" .bundle)
  if [ -e "$target/$name" ]; then echo "skip $name (exists)"; continue; fi
  git clone -q -b main "$b" "$target/$name"
  echo "restored $name -> $target/$name"
done
