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
    monkeypatch.setattr(gitx, "ensure_branch", lambda r, b, fr: "oid-1")
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
        repo=None, from_ref=None, json=True,
    )
    result = gitx.cmd_put(args)
    assert result["sha"] == "deadbeef"
    assert calls["n"] == 2  # retried exactly once


def test_cmd_put_gives_up_after_second_stale(monkeypatch, tmp_path):
    monkeypatch.setenv("GITX_HOME", str(tmp_path))
    monkeypatch.setattr(gitx, "_read_files", lambda paths: [("note.md", b"hi")])
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    monkeypatch.setattr(gitx, "ensure_branch", lambda r, b, fr: "oid-1")
    monkeypatch.setattr(gitx, "branch_head_oid", lambda r, b: "oid-2")

    def always_stale(*a, **k):
        raise gitx.GitxError(1, "Expected head mismatch")

    monkeypatch.setattr(gitx, "run_commit", always_stale)
    import argparse
    args = argparse.Namespace(
        branch="feature-x", paths=["note.md"], message="m",
        repo=None, from_ref=None, json=True,
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


def test_cmd_cat_decodes_content(monkeypatch):
    monkeypatch.setattr(gitx, "resolve_repo", lambda e: "owner/repo")
    payload = {
        "sha": "blobsha", "size": 5,
        "content": base64.b64encode(b"hello").decode(), "encoding": "base64",
    }
    monkeypatch.setattr(gitx, "gh", lambda a, stdin=None: json.dumps(payload))
    import argparse
    args = argparse.Namespace(ref_path="main:docs/a.md", repo=None, json=True)
    result = gitx.cmd_cat(args)
    assert result == {
        "ref": "main", "path": "docs/a.md",
        "sha": "blobsha", "size": 5, "content": "hello",
    }


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
