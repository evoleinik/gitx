#!/usr/bin/env bash
# Live round-trip. Requires: GITX_SMOKE=1, GITX_SMOKE_REPO=owner/repo, gh authed.
set -euo pipefail
[ "${GITX_SMOKE:-}" = "1" ] || { echo "set GITX_SMOKE=1 to run" >&2; exit 0; }
: "${GITX_SMOKE_REPO:?set GITX_SMOKE_REPO=owner/repo}"
GITX=${GITX:-"$(cd "$(dirname "$0")" && pwd)/gitx"}
BR="gitx-smoke-$(date +%s)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"; gh api -X DELETE "repos/$GITX_SMOKE_REPO/git/refs/heads/$BR" >/dev/null 2>&1 || true' EXIT
jqr() { python3 -c "import sys,json;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }

# cd into TMP so hello.md is the repo-relative path (strict path guard rejects abspaths)
cd "$TMP"
echo "smoke $(date)" > hello.md

# put (auto-creates branch off default)
SHA=$("$GITX" put "$BR" hello.md -m "smoke: put" --repo "$GITX_SMOKE_REPO" --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["sha"])')
echo "put ok: $SHA"

# cat back the committed file (path in repo is hello.md, same as what we passed)
GOT=$("$GITX" cat "$BR:hello.md" --repo "$GITX_SMOKE_REPO")
echo "cat: $GOT"
[[ "$GOT" == smoke* ]] || { echo "FAIL: cat returned wrong content: $GOT" >&2; exit 1; }

# overwrite guard: plant the 2026-09-25 race for real. Read at BASE, another session
# inserts a row, then our append must be refused rather than erase that row.
mkdir -p "$TMP/race/docs" "$TMP/other/docs"
printf '# ADR\n\n| A | yes |\n\n## Result 1\n' > "$TMP/race/docs/adr.md"
cd "$TMP/race"
BASE=$("$GITX" put "$BR" docs/adr.md -m "smoke: adr v1" --repo "$GITX_SMOKE_REPO" --json | jqr 'd["sha"]')
printf '# ADR\n\n| A | yes |\n| B | no |\n\n## Result 1\n' > "$TMP/other/docs/adr.md"
cd "$TMP/other"
RACE=$("$GITX" put "$BR" docs/adr.md -m "smoke: other session adds row B" --repo "$GITX_SMOKE_REPO" --json | jqr 'd["sha"]')
cd "$TMP/race"
printf '\n## Result 2\n' >> docs/adr.md
for flags in "" "--base $BASE"; do
  set +e
  OUT=$("$GITX" put "$BR" docs/adr.md -m "smoke: append result 2" --repo "$GITX_SMOKE_REPO" --json $flags)
  RC=$?
  set -e
  [ "$RC" = 5 ] || { echo "FAIL: put ${flags:-without --base} exited $RC, want 5 (refuse): $OUT" >&2; exit 1; }
  echo "$OUT" | jqr 'd["paths"][0]["commits"][0]["sha"]' | grep -qx "$RACE" \
    || { echo "FAIL: refusal does not name the racing commit $RACE: $OUT" >&2; exit 1; }
  echo "refused ${flags:-without --base}: ok"
done
# follow the printed recovery: read the branch copy, re-apply, put against it
HEAD=$(echo "$OUT" | jqr 'd["head"]')
"$GITX" cat "$HEAD:docs/adr.md" --repo "$GITX_SMOKE_REPO" > docs/adr.md
printf '\n## Result 2\n' >> docs/adr.md
"$GITX" put "$BR" docs/adr.md -m "smoke: append result 2" --repo "$GITX_SMOKE_REPO" --base "$HEAD" --json >/dev/null
FINAL=$("$GITX" cat "$BR:docs/adr.md" --repo "$GITX_SMOKE_REPO")
grep -q '| B | no |' <<<"$FINAL" && grep -q 'Result 2' <<<"$FINAL" \
  || { echo "FAIL: recovery lost a change: $FINAL" >&2; exit 1; }
echo "race guard: ok"
cd "$TMP"

# open a PR then close it + delete branch (cleanup)
DEFAULT=$(gh repo view "$GITX_SMOKE_REPO" --json defaultBranchRef -q .defaultBranchRef.name)
URL=$("$GITX" pr "$DEFAULT" "$BR" --title "smoke" --body "smoke" --repo "$GITX_SMOKE_REPO" --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')
echo "pr: $URL"
PR_NUM=$(basename "$URL")
gh pr close "$PR_NUM" --repo "$GITX_SMOKE_REPO" --delete-branch
echo "SMOKE OK"
