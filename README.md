# gitx

Stateless git lane for agents. Commit files to a GitHub branch with no checkout —
no cwd, HEAD, index, or working tree to get wrong. Thin wrapper over
`gh api` + GraphQL `createCommitOnBranch`.

## Install

    ln -sf ~/src/gitx/gitx ~/bin/gitx   # needs gh (authed) + python3 on PATH

## Usage

    gitx put <branch> <path>... -m <msg> [--repo owner/name] [--from ref] [--json]
    gitx cat <ref>:<path>                [--repo owner/name] [--json]
    gitx pr  <base> <head> --title T --body B [--merge] [--repo owner/name] [--json]
    gitx --usage

`put` auto-creates the branch off the default branch (or `--from`) if missing,
commits atomically (server-signed), retries once on a concurrent-push head
conflict. Repo defaults to the cwd `origin`; `--repo` overrides.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | gh not authenticated |
| 3 | Repo/branch/path not found or bad path arg |
| 4 | Head conflict after retry |

## Test

    python3 -m pytest test_gitx.py -q           # unit (no network)
    GITX_SMOKE=1 GITX_SMOKE_REPO=owner/repo bash smoke_test.sh   # live round-trip

<!-- AI agents: single Python file, stdlib only, shells to `gh`. State: ~/.gitx/usage.jsonl.
     Additions-only (no delete/rename). Leaves merges/rebase/bisect to porcelain git. -->
