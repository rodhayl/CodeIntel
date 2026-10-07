"""Filesystem path identity survives repository-jail projection unchanged."""
import os

import pytest

from codeintel.core.security import RepoJail, SecurityException


pytestmark = pytest.mark.skipif(os.name != "posix", reason="literal POSIX backslash names")


@pytest.mark.parametrize("name", [r"..\outside.py", r"\absolute.py", r"src\item.py"])
def test_literal_backslash_filename_is_rejected_not_reinterpreted(tmp_path, name):
    root = tmp_path / "repo"
    root.mkdir()
    (root / name).write_text("repository file", encoding="utf-8")
    jail = RepoJail(str(root))
    with pytest.raises(SecurityException, match="backslash|canonical"):
        jail.safe_relpath(name)
    assert jail.is_path_allowed(name) is False


def test_accepted_path_cannot_acquire_parent_traversal_after_validation(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "outside.py").write_text("outside", encoding="utf-8")
    (root / r"..\outside.py").write_text("inside", encoding="utf-8")
    jail = RepoJail(str(root))
    try:
        relative = jail.safe_relpath(r"..\outside.py")
    except SecurityException:
        return
    resolved = (root / relative).resolve()
    assert resolved.is_relative_to(root), f"jail returned an escaping path: {relative!r}"
    assert resolved.read_text() == "inside"


def test_distinct_physical_files_cannot_collapse_to_one_returned_path(tmp_path):
    (tmp_path / "src").mkdir()
    actual = tmp_path / "src" / "item.py"
    actual.write_text("canonical", encoding="utf-8")
    (tmp_path / r"src\item.py").write_text("literal", encoding="utf-8")
    jail = RepoJail(str(tmp_path))
    assert jail.safe_relpath("src/item.py") == "src/item.py"
    with pytest.raises(SecurityException):
        jail.safe_relpath(r"src\item.py")


def test_normal_unicode_and_internal_symlinks_keep_physical_identity(tmp_path):
    directory = tmp_path / "caf\u00e9"
    directory.mkdir()
    target = directory / "item.py"
    target.write_text("valid", encoding="utf-8")
    (tmp_path / "alias").symlink_to(directory, target_is_directory=True)
    jail = RepoJail(str(tmp_path))
    for path in (str(target), "caf\u00e9/item.py", "alias/item.py"):
        result = jail.safe_relpath(path)
        assert result == "caf\u00e9/item.py"
        assert (tmp_path / result).read_text() == "valid"


def test_existing_traversal_and_external_symlink_still_rejected(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("outside", encoding="utf-8")
    (root / "link.py").symlink_to(outside)
    jail = RepoJail(str(root))
    for path in ("../outside.py", "link.py", str(outside)):
        with pytest.raises(SecurityException):
            jail.safe_relpath(path)
