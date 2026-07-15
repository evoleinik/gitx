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
