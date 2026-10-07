import pytest
from codeintel.core.security import RepoJail, SecurityException

def test_repo_jail_containment(tmp_path):
    jail = RepoJail(str(tmp_path))
    safe_file = tmp_path / "src" / "main.py"
    safe_file.parent.mkdir(parents=True)
    safe_file.write_text("print('hello')")

    assert jail.safe_relpath("src/main.py") == "src/main.py"

    with pytest.raises(SecurityException):
        jail.safe_relpath("../outside.py")
