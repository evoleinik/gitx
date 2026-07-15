import importlib.machinery
import importlib.util
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
