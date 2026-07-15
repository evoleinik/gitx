#!/usr/bin/env bash
# Live round-trip. Requires: GITX_SMOKE=1, GITX_SMOKE_REPO=owner/repo, gh authed.
set -euo pipefail
[ "${GITX_SMOKE:-}" = "1" ] || { echo "set GITX_SMOKE=1 to run" >&2; exit 0; }
: "${GITX_SMOKE_REPO:?set GITX_SMOKE_REPO=owner/repo}"
GITX=~/src/gitx/gitx
BR="gitx-smoke-$(date +%s)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# cd into TMP so hello.md is the repo-relative path (strict path guard rejects abspaths)
cd "$TMP"
echo "smoke $(date)" > hello.md

# put (auto-creates branch off default)
SHA=$("$GITX" put "$BR" hello.md -m "smoke: put" --repo "$GITX_SMOKE_REPO" --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["sha"])')
echo "put ok: $SHA"

# cat back the committed file (path in repo is hello.md, same as what we passed)
GOT=$("$GITX" cat "$BR:hello.md" --repo "$GITX_SMOKE_REPO")
echo "cat: $GOT"

# open a PR then close it + delete branch (cleanup)
URL=$("$GITX" pr main "$BR" --title "smoke" --body "smoke" --repo "$GITX_SMOKE_REPO" --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')
echo "pr: $URL"
gh pr close "$URL" --repo "$GITX_SMOKE_REPO" --delete-branch
echo "SMOKE OK"
