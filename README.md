# gitx

Stateless git lane for agents. Commit files to a GitHub branch with no checkout —
no cwd, HEAD, index, or working tree to get wrong. Thin wrapper over
`gh api` + GraphQL `createCommitOnBranch`.

One of a stateless trio: `gitx` writes, [`gitw`](https://github.com/evoleinik/gitw) orients (where am I, in one read), and
[`ghpr`](https://github.com/evoleinik/ghpr) reads a PR's merge safety before you merge it.

## Install

    ln -sf ~/src/gitx/gitx ~/bin/gitx   # needs gh (authed) + python3 on PATH

## Usage

    gitx put <branch> <path>... -m <msg> [--repo owner/name] [--from ref] [--base rev] [--force] [--json]
    gitx cat <ref>:<path>                [--repo owner/name] [--json]
    gitx pr  <base> <head> --title T --body B [--merge] [--repo owner/name] [--json]
    gitx --usage

`put` auto-creates the branch off the default branch (or `--from`) if missing,
commits atomically (server-signed), retries once on a concurrent-push head
conflict. Repo defaults to the cwd `origin`; `--repo` overrides.

### Overwrite guard (on by default)

`put` sends whole files. If the branch changed a file after you read it, a blind
put erases that change. It happened four times in airshelf between 2026-08-31 and
2026-09-25. On 2026-09-25, 4cffc4f9c was built from an ADR read at c5212b540 and
erased a29cecbce's row, which had landed seconds earlier.

So `put` refuses with exit 5 when the branch holds a version of a file you never
saw. What "seen" means depends on what gitx can know about your copy:

| Mode | When | Seen versions |
|------|------|---------------|
| `base` | `--base <rev>` given | the file at that commit |
| `checkout` | cwd is the top of a checkout of the target repo | every version in HEAD's history of the file, plus the staged copy |
| `content` | neither | none. Refuse only if the put would drop a line the branch has |

A file missing on the branch, or already identical to yours, always passes. The
check runs again after a head-conflict retry, since the commit that moved the head
may be the race itself. `--force` skips the check.

Get a base by reading through gitx: `gitx cat <branch>:<path> --json` prints the
commit it read as `.commit`. From a local git read, use the `git rev-parse` of the
ref at the time you read, never at put time.

The refusal lists the commits you have not seen and prints the fix: read the
branch copy with `gitx cat <head>:<path>`, re-apply your change, then
`gitx put … --base <head>`. With `--json` it is one object on stdout:
`{error:"unseen_change", code:5, message, repo, branch, head, mode, base,
paths:[{path, branch_blob, commits:[{sha,subject}], truncated, missing_lines?}], fix:[cmd]}`.

Known limit: in `content` mode a race that only deleted lines passes, because the
branch copy is then a subsequence of yours. Pass `--base` to close it.

`cat` serves **text**. Above 1MB the GitHub contents API returns an empty body at
HTTP 200, so `cat` auto-escalates to the blobs API by sha (100MB cap) rather than
reporting a real file as empty. Binary files exit 3 and print the
`gh api …/git/blobs/<sha>` command to fetch them, instead of emitting mojibake.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | gh not authenticated |
| 3 | Repo/branch/path not found or bad path arg |
| 4 | Head conflict after retry |
| 5 | Refused: the branch holds a version of a file you never saw (see Overwrite guard) |

With `--json`, every error is one object on stdout:
`{error, code, message, ...}`. Without it, the message goes to stderr.

## Test

    python3 -m pytest test_gitx.py -q           # unit (no network)
    GITX_SMOKE=1 GITX_SMOKE_REPO=owner/repo bash smoke_test.sh   # live round-trip + real race

<!-- AI agents: single Python file, stdlib only, shells to `gh`. State: ~/.gitx/usage.jsonl.
     Additions-only (no delete/rename). Leaves merges/rebase/bisect to porcelain git. -->
