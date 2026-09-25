# gitx — for AI agents

Use `gitx` INSTEAD of the `git add`/`commit`/`push` dance when you just need to
land one-or-more files on a branch. It has no "here" to get wrong (no cwd/HEAD/
index), which prevents the worktree-vs-main write bug.

- Commit: `gitx put <branch> <file>... -m "msg" [--repo owner/name]` → `{sha,url}`.
  Auto-creates the branch off default if missing. Path committed = path you pass
  (must be repo-relative). Use `--json` and parse `.sha`/`.url`.
- Read: `gitx cat <ref>:<path>` → file text (or `--json` for `{ref,commit,path,sha,size,content}`).
- Overwrite guard: `put` exits 5 when the branch holds a version of a file you
  never saw. Pass `--base <commit you read at>` (`gitx cat --json` gives `.commit`),
  or run from the top of a checkout of the repo. With neither, only a put that
  drops a branch line is refused. On exit 5, follow the printed fix: cat the branch
  copy, re-apply your change, put with `--base <head>`. `--force` overwrites on purpose.
- PR: `gitx pr <base> <head> --title T --body B [--merge]` → `{number,url}`.

Still use porcelain `git` for merge conflicts, rebase, bisect, partial staging,
and file deletions/renames — gitx is additions-only and stateless by design.
Exit codes: 0 ok / 1 general / 2 gh-not-authed / 3 not-found / 4 head-conflict /
5 unseen-change. With `--json`, errors are one object on stdout.
