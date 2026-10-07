"""Retired execution options do not remove validation of persisted diagnostics."""
import pytest

from codeintel.service import create_default_service
from codeintel.parsing.scip_diagnostics import validate_scip_failure_codes
from codeintel.storage.policy import DerivedStateValidationError


@pytest.mark.parametrize('diagnostics', [
    None, [], ['python:TOOL_UNAVAILABLE'],
    ['python:TIMEOUT', 'typescript:UNKNOWN', 'javascript:NONZERO_EXIT', 'snapshot:SNAPSHOT_LIMIT'],
])
def test_service_reads_valid_historical_diagnostics(tmp_path, diagnostics):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'a.py').write_text('value = 1\n')
    state = tmp_path / 'state'
    with create_default_service(str(repo), str(state)) as service:
        initial = service.reindex()
        service.generation_store.update_generation_metadata(initial['generation_id'], {
            'scip_failure_codes': diagnostics,
        })
    with create_default_service(str(repo), str(state)) as service:
        reused = service.reindex()
        assert reused['status'] == 'REUSED_EXISTING'
        assert reused['scip_failure_codes'] == (diagnostics or [])
        (repo / 'a.py').write_text('value = 2\n')
        assert service.reindex()['scip_failure_codes'] == []


@pytest.mark.parametrize('invalid', ['python:TIMEOUT', ['unknown:TIMEOUT'], ['python:TIMEOUT'] * 2])
def test_service_refuses_malformed_historical_diagnostics(tmp_path, invalid):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'a.py').write_text('value = 1\n')
    state = tmp_path / 'state'
    with create_default_service(str(repo), str(state)) as service:
        initial = service.reindex()
        service.generation_store.update_generation_metadata(initial['generation_id'], {
            'scip_failure_codes': invalid,
        })
    with pytest.raises(DerivedStateValidationError, match='SCIP failure'):
        create_default_service(str(repo), str(state))


@pytest.mark.parametrize('invalid', ['python:TIMEOUT', ['unknown:TIMEOUT'], ['python:TIMEOUT'] * 2])
def test_standalone_diagnostic_validator_retains_value_error_contract(invalid):
    with pytest.raises(ValueError, match='SCIP failure'):
        validate_scip_failure_codes(invalid)


def test_active_generation_adoption_rejects_invalid_diagnostics_before_updating_status(tmp_path):
    repo, state = tmp_path / 'repo', tmp_path / 'state'
    repo.mkdir()
    (repo / 'a.py').write_text('value = 1\n')
    with create_default_service(str(repo), str(state)) as service:
        initial = service.reindex()
        service.generation_store.update_generation_metadata(initial['generation_id'], {
            'semantic_status': 'LEGACY_STATUS',
            'scip_failure_codes': ['unknown:TIMEOUT'],
        })
        generation = service.generation_store.get_active_generation(service.repo_id)
        with pytest.raises(DerivedStateValidationError, match='SCIP failure') as error:
            service._adopt_active_generation(generation)
        assert isinstance(error.value.__cause__, ValueError)
        assert service._semantic_status == 'SYNTAX_ONLY'
        assert service._scip_failure_codes == []
