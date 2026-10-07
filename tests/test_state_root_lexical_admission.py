"""Default-state construction must retain lexical no-follow boundaries."""
import pytest
from codeintel.core.security import SecurityException, get_trusted_state_dir


@pytest.mark.parametrize('mode', ['custom-absolute', 'custom-relative', 'home', 'xdg-relative', 'xdg-absolute'])
def test_factory_rejects_symlink_before_creating_state(tmp_path, monkeypatch, mode):
    repo = tmp_path / 'repo'
    repo.mkdir()
    target = tmp_path / 'real'
    target.mkdir()
    link = tmp_path / 'linked'
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    env = {'HOME': str(tmp_path / 'home')}
    if mode.startswith('custom'):
        env['CODEINTEL_STATE_DIR'] = str(link / 'state') if mode.endswith('absolute') else 'linked/state'
    elif mode == 'home':
        env['HOME'] = str(link)
    elif mode == 'xdg-relative':
        env.update(HOME=str(link), XDG_DATA_HOME='relative-ignored')
    else:
        env['XDG_DATA_HOME'] = str(link / 'data')
    with pytest.raises(SecurityException, match='symlink|reparse'):
        get_trusted_state_dir(str(repo), environ=env)
    assert list(target.iterdir()) == []


def test_factory_keeps_regular_relative_root_contract(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.chdir(tmp_path)
    result = get_trusted_state_dir(str(repo), environ={'HOME': str(tmp_path), 'CODEINTEL_STATE_DIR': 'state'})
    assert result.startswith(str(tmp_path / 'state' / 'repos') + '/')
