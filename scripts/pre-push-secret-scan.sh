#!/usr/bin/env bash
set -euo pipefail

patterns='(sk-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH|PRIVATE) KEY)'

if git grep -I -n -E "$patterns" -- . ':!.env.example' ':!scripts/pre-push-secret-scan.sh'; then
  echo "Potential secret-like value found in tracked files. Remove it before pushing." >&2
  exit 1
fi

if git diff --cached --name-only | grep -E '(^|/)\.env($|\.|/)|\.pem$|\.key$' >/dev/null; then
  echo "Sensitive-looking file is staged. Remove it before pushing." >&2
  exit 1
fi

echo "No tracked secret-like values found."
