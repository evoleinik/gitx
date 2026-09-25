import base64
import importlib.machinery
import importlib.util
import json
import os

_here = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("gitx", os.path.join(_here, "gitx"))
_spec = importlib.util.spec_from_loader("gitx", _loader)
gitx = importlib.util.module_from_spec(_spec)
_loader.exec_module(gitx)

import pytest


def test_validate_repo_path_accepts_relative():
    assert gitx.validate_repo_path("docs/x.md") == "docs/x.md"
    assert gitx.validate_repo_path("./docs/x.md") == "docs/x.md"


def test_validate_repo_path_rejects_absolute():
    with pytest.raises(gitx.GitxError) as e:
        gitx.validate_repo_path("/etc/passwd")
    assert e.value.code == 3


def test_validate_repo_path_rejects_parent_escape():
    with pytest.raises(gitx.GitxError) as e:
        gitx.validate_repo_path("../secrets.md")
    assert e.value.code == 3


def test_parse_ref_path_basic():
    assert gitx.parse_ref_path("main:docs/x.md") == ("main", "docs/x.md")


def test_parse_ref_path_first_colon_only():
    # a ref never contains ':'; path never does either, but split on first is safe
    assert gitx.parse_ref_path("feature/x:a/b.md") == ("feature/x", "a/b.md")


def test_parse_ref_path_missing_colon():
    with pytest.raises(gitx.GitxError) as e:
        gitx.parse_ref_path("main-docs-x.md")
    assert e.value.code == 3


def test_parse_ref_path_rejects_bad_path():
    with pytest.raises(gitx.GitxError) as e:
        gitx.parse_ref_path("main:/etc/passwd")
    assert e.value.code == 3


def test_build_commit_body_shape():
    body = gitx.build_commit_body(
        "owner/repo", "feature-x", "add stuff", "abc123",
        [("docs/a.md", b"hello"), ("b.txt", b"world")],
    )
    assert body["query"] == gitx.COMMIT_MUTATION
    inp = body["variables"]["input"]
    assert inp["branch"] == {
        "repositoryNameWithOwner": "owner/repo",
        "branchName": "feature-x",
    }
    assert inp["message"] == {"headline": "add stuff"}
    assert inp["expectedHeadOid"] == "abc123"
    adds = inp["fileChanges"]["additions"]
    assert [a["path"] for a in adds] == ["docs/a.md", "b.txt"]
    assert base64.b64decode(adds[0]["contents"]) == b"hello"
    assert base64.b64decode(adds[1]["contents"]) == b"world"


def test_build_commit_body_mutation_names_the_field():
    assert "createCommitOnBranch" in gitx.COMMIT_MUTATION
    assert "oid" in gitx.COMMIT_MUTATION and "url" in gitx.COMMIT_MUTATION


def test_is_stale_head_error():
    assert gitx.is_stale_head_error("Expected branch to point to ... but it did not")
    assert gitx.is_stale_head_error("the branch is at a different oid than expected")
    assert not gitx.is_stale_head_error("Resource not accessible by integration")


def test_log_usage_appends_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("GITX_HOME", str(tmp_path))
    gitx.log_usage("put", True, 42, None)
    gitx.log_usage("cat", False, 7, "not found")
    lines = (tmp_path / "usage.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["cmd"] == "put" and first["ok"] is True and first["ms"] == 42
    assert first["error"] is None and "ts" in first
    second = json.loads(lines[1])
    assert second["ok"] is False and second["error"] == "not found"


def test_do_not_track_suppresses(tmp_path, monkeypatch):
    monkeypatch.setenv("GITX_HOME", str(tmp_path))
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    gitx.log_usage("put", True, 1, None)
    assert not (tmp_path / "usage.jsonl").exists()


def test_first_write_notice_on_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITX_HOME", str(tmp_path))
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    gitx.log_usage("put", True, 1, None)
    captured = capsys.readouterr()
    assert "logging usage to" in captured.err
    assert "DO_NOT_TRACK=1" in captured.err
    # second write must not repeat the notice
    gitx.log_usage("put", True, 1, None)
    captured2 = capsys.readouterr()
    assert "logging usage to" not in captured2.err


def test_sigterm_writes_morgue(tmp_path):
    import subprocess as sp, signal, os as _os
    env = dict(_os.environ, GITX_HOME=str(tmp_path))
    env.pop("DO_NOT_TRACK", None)
    p = sp.Popen(["python3", os.path.join(_here, "gitx"), "cat", "main:README.md"],
                 env=env, stdout=sp.PIPE, stderr=sp.PIPE)
    import time as _t
    _t.sleep(0.05)
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)
    lines = (tmp_path / "usage.jsonl").read_text().strip().splitlines() if (tmp_path / "usage.jsonl").exists() else []
    assert lines, "no record written at all"
    rec = json.loads(lines[-1])
    assert rec["ok"] is False
    assert "SIGTERM" in (rec["error"] or "")


def test_gh_maps_auth_error(monkeypatch):
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "gh auth login required"

    def fake_run(*a, **k):
        return FakeProc()

    monkeypatch.setattr(gitx.subprocess, "run", fake_run)
    with pytest.raises(gitx.GitxError) as e:
        gitx.gh(["repo", "view"])
    assert e.value.code == 2


def test_gh_maps_general_error(monkeypatch):
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "some other failure"

    monkeypatch.setattr(gitx.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(gitx.GitxError) as e:
        gitx.gh(["repo", "view"])
    assert e.value.code == 1


def test_cmd_put_retries_once_on_stale_head(monkeypatch, tmp_path):
    monkeypatch.setenv("GITX_HOME", str(tmp_path))

    monkeypatch.setattr(gitx, "_read_files", lambda paths: [("note.md", b"hi")])
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    # first commit attempt stale, second succeeds
    calls = {"n": 0}

    def fake_run_commit(repo, branch, message, head_oid, files):
        calls["n"] += 1
        if calls["n"] == 1:
            raise gitx.GitxError(1, "Expected branch head oid mismatch")
        return {"sha": "deadbeef", "url": "https://gh/commit/deadbeef"}

    monkeypatch.setattr(gitx, "run_commit", fake_run_commit)
    monkeypatch.setattr(gitx, "branch_head_oid", lambda r, b: "oid-2")

    import argparse
    args = argparse.Namespace(
        branch="feature-x", paths=["note.md"], message="m",
        repo=None, from_ref=None, base=None, force=True, json=True,
    )
    result = gitx.cmd_put(args)
    assert result["sha"] == "deadbeef"
    assert calls["n"] == 2  # retried exactly once


def test_cmd_put_gives_up_after_second_stale(monkeypatch, tmp_path):
    monkeypatch.setenv("GITX_HOME", str(tmp_path))
    monkeypatch.setattr(gitx, "_read_files", lambda paths: [("note.md", b"hi")])
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    monkeypatch.setattr(gitx, "branch_head_oid", lambda r, b: "oid-2")

    def always_stale(*a, **k):
        raise gitx.GitxError(1, "Expected head mismatch")

    monkeypatch.setattr(gitx, "run_commit", always_stale)
    import argparse
    args = argparse.Namespace(
        branch="feature-x", paths=["note.md"], message="m",
        repo=None, from_ref=None, base=None, force=True, json=True,
    )
    with pytest.raises(gitx.GitxError) as e:
        gitx.cmd_put(args)
    assert e.value.code == 4


def test_read_files_rejects_absolute_path():
    with pytest.raises(gitx.GitxError) as e:
        gitx._read_files(["/etc/passwd"])
    assert e.value.code == 3


def test_read_files_rejects_parent_escape():
    with pytest.raises(gitx.GitxError) as e:
        gitx._read_files(["../secrets.md"])
    assert e.value.code == 3


def test_read_files_missing_file_exit3():
    with pytest.raises(gitx.GitxError) as e:
        gitx._read_files(["definitely-missing-file.md"])
    assert e.value.code == 3


def test_cmd_cat_decodes_content(monkeypatch):
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    seen = {}
    payload = {"sha": "blobsha", "size": 5, "type": "file",
               "content": base64.b64encode(b"hello").decode(), "encoding": "base64"}
    def fake_gh(a, stdin=None):
        if "/commits/" in a[-1]:
            return "c0ffee\n"
        seen["args"] = a
        return json.dumps(payload)
    monkeypatch.setattr(gitx, "gh", fake_gh)
    import argparse
    args = argparse.Namespace(ref_path="main:docs/a.md", repo=None, json=True)
    result = gitx.cmd_cat(args)
    assert result == {"ref": "main", "commit": "c0ffee", "path": "docs/a.md",
                      "sha": "blobsha", "size": 5, "content": "hello"}
    assert "-f" not in seen["args"] and "-F" not in seen["args"]
    assert any("?ref=c0ffee" in str(x) for x in seen["args"])


def test_cmd_cat_directory_exit3(monkeypatch):
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    def fake_gh(a, stdin=None):
        return json.dumps([{"name": "a.md"}])
    monkeypatch.setattr(gitx, "gh", fake_gh)
    import argparse
    args = argparse.Namespace(ref_path="main:docs", repo=None, json=True)
    with pytest.raises(gitx.GitxError) as e:
        gitx.cmd_cat(args)
    assert e.value.code == 3


def test_cmd_cat_404_guides(monkeypatch):
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")

    def fake_gh(a, stdin=None):
        raise gitx.GitxError(1, "gh api ... HTTP 404: Not Found")

    monkeypatch.setattr(gitx, "gh", fake_gh)
    import argparse
    args = argparse.Namespace(ref_path="main:missing.md", repo=None, json=True)
    with pytest.raises(gitx.GitxError) as e:
        gitx.cmd_cat(args)
    assert e.value.code == 3


def test_cmd_pr_creates_and_optionally_merges(monkeypatch):
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    seen = []

    def fake_gh(a, stdin=None):
        seen.append(a)
        if a[:2] == ["pr", "create"]:
            return "https://github.com/owner/repo/pull/7\n"
        if a[:2] == ["pr", "view"]:
            return json.dumps({"number": 7, "url": "https://github.com/owner/repo/pull/7"})
        if a[:2] == ["pr", "merge"]:
            return ""
        raise AssertionError(a)

    monkeypatch.setattr(gitx, "gh", fake_gh)
    import argparse
    args = argparse.Namespace(
        base="main", head="feature-x", title="T", body="B",
        merge=True, repo=None, json=True,
    )
    result = gitx.cmd_pr(args)
    assert result == {"number": 7, "url": "https://github.com/owner/repo/pull/7"}
    assert any(a[:2] == ["pr", "merge"] for a in seen)


def test_cmd_pr_no_merge_when_flag_absent(monkeypatch):
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    seen = []

    def fake_gh(a, stdin=None):
        seen.append(a)
        if a[:2] == ["pr", "create"]:
            return "https://github.com/owner/repo/pull/9\n"
        if a[:2] == ["pr", "view"]:
            return json.dumps({"number": 9, "url": "https://github.com/owner/repo/pull/9"})
        raise AssertionError(a)

    monkeypatch.setattr(gitx, "gh", fake_gh)
    import argparse
    args = argparse.Namespace(
        base="main", head="feature-x", title="T", body="B",
        merge=False, repo=None, json=True,
    )
    result = gitx.cmd_pr(args)
    assert result["number"] == 9
    assert not any(a[:2] == ["pr", "merge"] for a in seen)


def test_cmd_cat_large_file_escalates_to_blob_api(monkeypatch):
    # >1MB: contents API returns encoding "none" + EMPTY content at HTTP 200.
    # Naive decode would report a real file as empty; we must escalate to the blob.
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    seen = []
    contents = {"type": "file", "sha": "blobsha", "size": 2595105,
                "content": "", "encoding": "none"}
    blob = {"encoding": "base64", "size": 2595105,
            "content": base64.b64encode(b"real big content").decode()}

    def fake_gh(a, stdin=None):
        seen.append(a)
        if "/git/blobs/" in a[1]:
            return json.dumps(blob)
        return json.dumps(contents)

    monkeypatch.setattr(gitx, "gh", fake_gh)
    import argparse
    args = argparse.Namespace(ref_path="main:big.txt", repo=None, json=True)
    result = gitx.cmd_cat(args)
    assert result["content"] == "real big content"
    assert result["size"] == 2595105
    assert any("/git/blobs/blobsha" in a[1] for a in seen), "must escalate to blobs API"


def test_cmd_cat_binary_refuses_loudly(monkeypatch):
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    payload = {"type": "file", "sha": "s", "size": 4,
               "content": base64.b64encode(b"\x89PNG\xff\xfe").decode(), "encoding": "base64"}
    monkeypatch.setattr(gitx, "gh", lambda a, stdin=None: json.dumps(payload))
    import argparse
    args = argparse.Namespace(ref_path="main:img.png", repo=None, json=True)
    with pytest.raises(gitx.GitxError) as e:
        gitx.cmd_cat(args)
    assert e.value.code == 3 and "binary" in e.value.msg


def test_cmd_cat_oversized_blob_fails_loudly(monkeypatch):
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    contents = {"type": "file", "sha": "s", "size": 200_000_000, "content": "", "encoding": "none"}
    monkeypatch.setattr(gitx, "gh", lambda a, stdin=None: json.dumps(
        contents if "/contents/" in a[1] else {"content": ""}))
    import argparse
    args = argparse.Namespace(ref_path="main:huge.bin", repo=None, json=True)
    with pytest.raises(gitx.GitxError) as e:
        gitx.cmd_cat(args)
    assert e.value.code == 3 and "too large" in e.value.msg


def test_usage_error_exits_1_not_2():
    # exit 2 is documented as gh-not-authed; a missing --title must not emit it
    with pytest.raises(SystemExit) as e:
        gitx.main(["pr", "main", "branch"])  # --title/--body missing
    assert e.value.code == 1


# ---------------------------------------------------------------------------
# Overwrite guard. A local git repo plays GitHub, so every race below is a real
# commit sequence, not a mocked answer. Incident: airshelf 2026-09-25, 4cffc4f9c
# was built from an ADR read at c5212b540 and erased a29cecbce's row.
# ---------------------------------------------------------------------------
import shlex
import subprocess

ADR = "docs/adr.md"
V1 = "# ADR\n\n| Claim | Verdict |\n|---|---|\n| A | yes |\n\n## Result 1\ntext\n"
ROW_B = "| B | no |"


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _git_or_none(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


class FakeGitHub:
    """A non-bare repo on branch main that answers gitx's remote calls."""

    def __init__(self, root):
        self.dir = root / "github"
        self.dir.mkdir()
        _git(self.dir, "init", "-q", "-b", "main")
        self.race = None  # (path, content, msg) committed inside the next commit window

    def _switch(self, branch):
        if self.branch_head_oid(None, branch):  # an unborn main cannot be checked out
            _git(self.dir, "checkout", "-q", branch)

    def commit(self, path, content, msg, branch="main"):
        self._switch(branch)
        f = self.dir / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        _git(self.dir, "add", path)
        _git(self.dir, "commit", "-q", "-m", msg)
        return _git(self.dir, "rev-parse", "HEAD")

    def show(self, rev, path):
        return _git(self.dir, "show", f"{rev}:{path}") + "\n"

    # --- stand-ins for gitx's GitHub layer ---
    def branch_head_oid(self, repo, branch):
        return _git_or_none(self.dir, "rev-parse", "-q", "--verify", f"refs/heads/{branch}")

    def remote_blobs(self, repo, rev, paths):
        oid = _git_or_none(self.dir, "rev-parse", "-q", "--verify", f"{rev}^{{commit}}")
        blobs = {p: (_git_or_none(self.dir, "rev-parse", "-q", "--verify", f"{rev}:{p}")
                     if oid else None) for p in paths}
        return oid, blobs

    def path_log(self, repo, rev, path, n):
        out = _git(self.dir, "log", f"-n{n}", "--format=%H%x1f%s", rev, "--", path)
        log = []
        for line in out.splitlines():
            sha, subject = line.split("\x1f", 1)
            log.append({"sha": sha, "subject": subject,
                        "blob": _git_or_none(self.dir, "rev-parse", "-q", "--verify", f"{sha}:{path}")})
        return log

    def create_branch(self, repo, branch, oid):
        _git(self.dir, "branch", branch, oid)

    def run_commit(self, repo, branch, message, head_oid, files):
        if self.race:
            race, self.race = self.race, None
            self.commit(*race, branch=branch)
        actual = self.branch_head_oid(repo, branch)
        if actual != head_oid:
            raise gitx.GitxError(1, f"Expected branch to point to {head_oid} but it did not")
        self._switch(branch)
        for path, data in files:
            f = self.dir / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
            _git(self.dir, "add", path)
        _git(self.dir, "commit", "-q", "--allow-empty", "-m", message)
        sha = _git(self.dir, "rev-parse", "HEAD")
        return {"sha": sha, "url": f"https://github.com/owner/repo/commit/{sha}"}

    def gh(self, args, stdin=None):
        """REST calls `gitx cat` makes: resolve ref → commit, read contents at it."""
        url = args[-1]
        if "/commits/" in url:
            ref = url.rsplit("/commits/", 1)[1]
            oid = _git_or_none(self.dir, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}")
            if not oid:
                raise gitx.GitxError(1, "gh api failed: HTTP 422 No commit found")
            return oid + "\n"
        if "/git/blobs/" in url:
            oid = url.rsplit("/", 1)[1]
            data = subprocess.run(["git", "-C", str(self.dir), "cat-file", "blob", oid],
                                  capture_output=True, check=True).stdout
            return json.dumps({"content": base64.b64encode(data).decode(), "size": len(data)})
        if "/contents/" in url:
            path, _, ref = url.split("/contents/", 1)[1].partition("?ref=")
            data = subprocess.run(["git", "-C", str(self.dir), "show", f"{ref}:{path}"],
                                  capture_output=True).stdout
            return json.dumps({"type": "file", "size": len(data),
                               "sha": _git(self.dir, "rev-parse", f"{ref}:{path}"),
                               "content": base64.b64encode(data).decode()})
        raise AssertionError(f"unexpected gh call: {args}")

    def install(self, monkeypatch):
        monkeypatch.setattr(gitx, "resolve_repo", lambda explicit: "owner/repo")
        monkeypatch.setattr(gitx, "repo_default_branch", lambda repo: "main")
        for name in ("branch_head_oid", "remote_blobs", "path_log",
                     "create_branch", "run_commit", "gh"):
            monkeypatch.setattr(gitx, name, getattr(self, name), raising=False)


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                 "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                 "GITX_HOME": str(tmp_path / "gitx-home"), "DO_NOT_TRACK": "1"}.items():
        monkeypatch.setenv(k, v)
    gh = FakeGitHub(tmp_path)
    gh.install(monkeypatch)
    return gh


def _scratch_copy(tmp_path, monkeypatch, content):
    """The incident flow: the file is copied into a dir that is not a checkout."""
    scratch = tmp_path / "scratch"
    (scratch / "docs").mkdir(parents=True)
    (scratch / ADR).write_text(content)
    monkeypatch.chdir(scratch)
    return scratch


def _checkout(world, tmp_path, monkeypatch):
    """A clone of the fake GitHub whose origin names owner/repo, as a worktree's would."""
    co = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(world.dir), str(co))
    _git(co, "remote", "set-url", "origin", "https://github.com/owner/repo.git")
    monkeypatch.chdir(co)
    return co


def _put(capsys, *extra):
    code = gitx.main(["put", "main", ADR, "-m", "docs: append result 2", "--json", *extra])
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else None)


def _race_insert_row(world):
    """The a29cecbce analog: another session inserts a table row mid-file."""
    return world.commit(ADR, V1.replace("| A | yes |\n", f"| A | yes |\n{ROW_B}\n"),
                        "docs: thesis table row B")


def test_guard_refuses_the_incident_race_with_base(world, tmp_path, monkeypatch, capsys):
    c1 = world.commit(ADR, V1, "docs: adr")
    _scratch_copy(tmp_path, monkeypatch, world.show(c1, ADR) + "\n## Result 2\nnew\n")
    c2 = _race_insert_row(world)

    code, err = _put(capsys, "--base", c1)

    assert code == 5
    assert err["error"] == "unseen_change" and err["code"] == 5
    assert err["head"] == c2 and err["base"] == c1 and err["mode"] == "base"
    [p] = err["paths"]
    assert p["path"] == ADR
    assert [c["sha"] for c in p["commits"]] == [c2]
    assert p["commits"][0]["subject"] == "docs: thesis table row B"
    assert world.branch_head_oid("owner/repo", "main") == c2, "nothing may be written"
    assert ROW_B in world.show("main", ADR)


def test_guard_refuses_the_incident_race_without_base(world, tmp_path, monkeypatch, capsys):
    # The literal 2026-09-25 call: scratch dir, no --base. The branch has a line the
    # caller's copy lacks, so the put would delete it.
    c1 = world.commit(ADR, V1, "docs: adr")
    _scratch_copy(tmp_path, monkeypatch, world.show(c1, ADR) + "\n## Result 2\nnew\n")
    c2 = _race_insert_row(world)

    code, err = _put(capsys)

    assert code == 5
    assert err["mode"] == "content" and err["base"] is None
    assert err["paths"][0]["missing_lines"] == [ROW_B]
    assert c2 in [c["sha"] for c in err["paths"][0]["commits"]]
    assert world.branch_head_oid("owner/repo", "main") == c2


def test_guard_rechecks_when_head_moves_inside_the_commit_window(world, tmp_path, monkeypatch, capsys):
    # The race lands between gitx's check and its commit. The old retry re-sent the
    # same bytes at the new head, which is a blind overwrite.
    c1 = world.commit(ADR, V1, "docs: adr")
    _scratch_copy(tmp_path, monkeypatch, world.show(c1, ADR) + "\n## Result 2\nnew\n")
    world.race = (ADR, V1.replace("| A | yes |\n", f"| A | yes |\n{ROW_B}\n"), "docs: row B")

    code, err = _put(capsys, "--base", c1)

    assert code == 5
    assert ROW_B in world.show("main", ADR)


def test_guard_with_base_catches_a_deletion_only_race(world, tmp_path, monkeypatch, capsys):
    c1 = world.commit(ADR, V1, "docs: adr")
    _scratch_copy(tmp_path, monkeypatch, world.show(c1, ADR) + "\n## Result 2\nnew\n")
    world.commit(ADR, V1.replace("| A | yes |\n", ""), "docs: drop row A")

    code, err = _put(capsys, "--base", c1)

    assert code == 5
    assert "| A | yes |" not in world.show("main", ADR)


def test_guard_passes_with_base_when_branch_unchanged(world, tmp_path, monkeypatch, capsys):
    c1 = world.commit(ADR, V1, "docs: adr")
    world.commit("other.md", "x\n", "docs: unrelated file")
    mine = world.show(c1, ADR).replace("text", "text, edited") + "\n## Result 2\nnew\n"
    _scratch_copy(tmp_path, monkeypatch, mine)

    code, out = _put(capsys, "--base", c1)

    assert code == 0, out
    assert world.show("main", ADR) == mine


def test_guard_passes_a_clean_append_without_base(world, tmp_path, monkeypatch, capsys):
    c1 = world.commit(ADR, V1, "docs: adr")
    mine = world.show(c1, ADR) + "\n## Result 2\nnew\n"
    _scratch_copy(tmp_path, monkeypatch, mine)

    code, out = _put(capsys)

    assert code == 0, out
    assert world.show("main", ADR) == mine


def test_guard_refuses_an_edit_without_base(world, tmp_path, monkeypatch, capsys):
    # No base and a changed line: gitx cannot tell the caller's edit from a race.
    c1 = world.commit(ADR, V1, "docs: adr")
    _scratch_copy(tmp_path, monkeypatch, world.show(c1, ADR).replace("text", "text, edited"))

    code, err = _put(capsys)

    assert code == 5
    assert err["paths"][0]["missing_lines"] == ["text"]
    assert any("--base" in cmd for cmd in err["fix"])


def test_guard_passes_a_new_file_and_an_identical_file(world, tmp_path, monkeypatch, capsys):
    c1 = world.commit(ADR, V1, "docs: adr")
    scratch = _scratch_copy(tmp_path, monkeypatch, world.show(c1, ADR).replace("text", "other"))
    _race_insert_row(world)
    (scratch / ADR).write_text(world.show("main", ADR))  # already what the branch holds
    (scratch / "docs/new.md").write_text("new\n")

    code = gitx.main(["put", "main", ADR, "docs/new.md", "-m", "m", "--json"])

    assert code == 0, capsys.readouterr()
    assert world.show("main", "docs/new.md") == "new\n"


def test_force_overwrites_on_purpose(world, tmp_path, monkeypatch, capsys):
    c1 = world.commit(ADR, V1, "docs: adr")
    mine = world.show(c1, ADR) + "\n## Result 2\nnew\n"
    _scratch_copy(tmp_path, monkeypatch, mine)
    _race_insert_row(world)

    code, out = _put(capsys, "--force")

    assert code == 0, out
    assert world.show("main", ADR) == mine


def test_checkout_mode_refuses_a_stale_checkout(world, tmp_path, monkeypatch, capsys):
    # The 2026-09-19 flow: a worktree's stale copy put to a branch that moved on.
    # A deletion-only race passes the content rule, so a refusal here proves the
    # checkout's own history is being used as the base.
    world.commit(ADR, V1, "docs: adr")
    co = _checkout(world, tmp_path, monkeypatch)
    world.commit(ADR, V1.replace("| A | yes |\n", ""), "docs: drop row A")
    (co / ADR).write_text(V1 + "\n## Result 2\nnew\n")

    code, err = _put(capsys)

    assert code == 5
    assert err["mode"] == "checkout"
    assert "| A | yes |" not in world.show("main", ADR)


def test_checkout_mode_passes_an_edit_on_a_current_checkout(world, tmp_path, monkeypatch, capsys):
    # A changed line fails the content rule, so a pass proves checkout mode ran.
    world.commit(ADR, V1, "docs: adr")
    co = _checkout(world, tmp_path, monkeypatch)
    mine = V1.replace("text", "text, edited")
    (co / ADR).write_text(mine)

    code, out = _put(capsys)

    assert code == 0, out
    assert world.show("main", ADR) == mine


def test_checkout_mode_passes_a_version_from_its_own_history(world, tmp_path, monkeypatch, capsys):
    # Feature branch edited the file and committed it; main still holds the fork
    # version, which the checkout has seen. Putting to main is not a race.
    world.commit(ADR, V1, "docs: adr")
    co = _checkout(world, tmp_path, monkeypatch)
    _git(co, "checkout", "-q", "-b", "feature")
    mine = V1.replace("text", "text, edited")
    (co / ADR).write_text(mine)
    _git(co, "commit", "-q", "-am", "feature edit")
    (co / ADR).write_text(mine + "more\n")

    code, out = _put(capsys)

    assert code == 0, out


def test_refusal_fix_commands_recover_end_to_end(world, tmp_path, monkeypatch, capsys):
    c1 = world.commit(ADR, V1, "docs: adr")
    scratch = _scratch_copy(tmp_path, monkeypatch,
                            world.show(c1, ADR) + "\n## Result 2\nnew\n")
    _race_insert_row(world)
    code, err = _put(capsys, "--base", c1)
    assert code == 5

    cat_cmd, put_cmd = (shlex.split(c) for c in err["fix"][:2])
    assert cat_cmd[:2] == ["gitx", "cat"] and put_cmd[:2] == ["gitx", "put"]
    # Follow the recipe exactly: read the branch copy, re-apply, put against it.
    assert gitx.main(cat_cmd[1:] + ["--json"]) == 0
    fresh = json.loads(capsys.readouterr().out)
    assert fresh["commit"] == err["head"]
    (scratch / ADR).write_text(fresh["content"] + "\n## Result 2\nnew\n")
    assert gitx.main(put_cmd[1:] + ["--json"]) == 0, capsys.readouterr()

    final = world.show("main", ADR)
    assert ROW_B in final and "## Result 2" in final


def test_refusal_without_json_goes_to_stderr(world, tmp_path, monkeypatch, capsys):
    c1 = world.commit(ADR, V1, "docs: adr")
    _scratch_copy(tmp_path, monkeypatch, world.show(c1, ADR) + "\n## Result 2\nnew\n")
    _race_insert_row(world)

    code = gitx.main(["put", "main", ADR, "-m", "m", "--base", c1])

    cap = capsys.readouterr()
    assert code == 5 and cap.out == ""
    assert "docs: thesis table row B" in cap.err and "--force" in cap.err


def test_unknown_base_is_not_found(world, tmp_path, monkeypatch, capsys):
    world.commit(ADR, V1, "docs: adr")
    _scratch_copy(tmp_path, monkeypatch, V1 + "more\n")

    code, err = _put(capsys, "--base", "0" * 40)

    assert code == 3 and err["error"] == "not_found"


def test_cat_json_reports_the_commit_it_read(world, capsys):
    c1 = world.commit(ADR, V1, "docs: adr")
    world.commit(ADR, V1 + "later\n", "docs: later")

    assert gitx.main(["cat", f"{c1}:{ADR}", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["commit"] == c1 and got["content"] == V1


def test_fix_commands_survive_shell_quoting_and_the_real_parser():
    import argparse
    msg = "docs: it's \"done\"\n\nCo-Authored-By: X <x@y>"
    args = argparse.Namespace(branch="main", paths=["docs/a b.md"], message=msg,
                              from_ref="dev", json=True)
    refused = [{"path": "docs/a b.md", "branch_blob": "b", "commits": [], "truncated": False}]
    err = gitx._unseen_change("owner/repo", args, "h" * 40, "content", None, refused)

    cat, put = (shlex.split(c) for c in err.data["fix"])
    assert cat == ["gitx", "cat", "h" * 40 + ":docs/a b.md", "--repo", "owner/repo"]
    parsed = gitx.build_parser().parse_args(put[1:])
    assert parsed.paths == ["docs/a b.md"] and parsed.message == msg
    assert parsed.base == "h" * 40 and parsed.from_ref == "dev" and not parsed.force
