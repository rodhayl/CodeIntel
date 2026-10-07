"""Offline fresh-environment validation of an allowlisted CodeIntel candidate."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# Host-side scripts deliberately inspect their source runtime. Isolated child
# acceptance imports only the installed wheel, with separately checked origins.
if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location('codeintel_validation_contract', Path(__file__).with_name('validation_contract.py'))
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)
require = contract.require
GATE_OVERHEAD_SECONDS = 300


PROBE = r'''import hashlib,importlib.metadata as m,importlib.util as u,json,sys
from pathlib import Path
import codeintel
source=Path(sys.argv[1]);root=Path(codeintel.__file__).parent
assert not root.is_relative_to(source)
for n in ('codeintel_codex_gateway','watchdog','google.protobuf','pydantic','torch','transformers','llama_cpp','numpy'):
 try:s=u.find_spec(n)
 except ModuleNotFoundError:s=None
 assert s is None,n
files={}
for f in (source/'codeintel').rglob('*.py'):
 rel=f.relative_to(source/'codeintel');actual=root/rel
 assert actual.read_bytes()==f.read_bytes(),str(rel)
 files['codeintel/'+rel.as_posix()]=hashlib.sha256(actual.read_bytes()).hexdigest()
notices=[]
for name in ('apsw','tree-sitter','tree-sitter-python','tree-sitter-javascript','tree-sitter-typescript','setuptools'):
 d=m.distribution(name); found=[]
 for f in d.files or []:
  stem=Path(str(f)).name.lower()
  if stem.endswith(('.py','.pyc')):continue
  if not (stem=='license' or stem.startswith(('license.','license-','copying','notice','copyright'))):continue
  path=d.locate_file(f)
  if path.is_file():found.append({'path':str(f),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 assert found,name
 notices.append({'name':name,'version':d.version,'expression':d.metadata.get('License-Expression'),'declared_license_files':d.metadata.get_all('License-File',[]),'notice_files':found})
print(json.dumps({'installed_python_files':files,'versions':{d.metadata['Name']:d.version for d in m.distributions()},'dependencies':notices,'python':sys.version.split()[0],'runtime_root':str(root.resolve())}))
'''


def validate_source(source, manifest):
    # Exact source manifest, no unknown files, no private roots or symlinks.
    actual = {path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file()}
    require(actual == set(manifest['files']) | {'SNAPSHOT_MANIFEST.json'},
            'Source validation failed: manifest file set differs')
    require(not any(path.is_symlink() for path in source.rglob('*')),
            'Source validation failed: symlink in curated source')
    for name, digest in manifest['files'].items():
        require(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest,
                'Source validation failed: ' + str(name))
    require(not any(name.startswith(('.git/', 'agent_exchange/', 'benchmarks/', 'audit/', 'integrations/'))
                    or name.endswith(('.pyc', '.log', '.jsonl', '.whl')) for name in actual),
            'Source validation failed: excluded path or artifact')
    # Resolve local Markdown links against the actual delivered subset.
    links = []
    for file in source.rglob('*.md'):
        for href in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', file.read_text()):
            if '://' in href or href.startswith('#'):
                continue
            target = (file.parent / href.split('#')[0]).resolve()
            require(target.is_relative_to(source) and target.exists(),
                    'Source validation failed: ' + str((file.name, href)))
            links.append([file.relative_to(source).as_posix(), href])
    return links


def validate_wheel(wheel, source):
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith('.dist-info/METADATA'))
        metadata_root = metadata_name.rsplit('/', 1)[0]
        require('/' not in metadata_root and metadata_root.startswith('codeintel-'),
                'Wheel metadata has an unsupported installation destination')
        require(len(names) == len(set(names)) and all(
            '\\' not in name and '..' not in name.split('/')
            and (name.startswith('codeintel/') or name.startswith(metadata_root + '/'))
            for name in names), 'Wheel contains unsupported installation destinations')
        # This distribution installs only its exact runtime and metadata. In
        # particular, .data/purelib and .data/platlib must not overwrite checked
        # runtime files during pip's relocation step.
        metadata = archive.read(metadata_name).decode()
        require('License-Expression: MIT' in metadata, 'Wheel metadata must declare MIT')
        license_path = next(name for name in names if name.endswith('.dist-info/licenses/LICENSE'))
        notice_path = next(name for name in names if name.endswith('.dist-info/licenses/THIRD_PARTY_NOTICES.md'))
        require(archive.read(license_path) == (source / 'LICENSE').read_bytes(), 'Wheel LICENSE bytes differ')
        require(archive.read(notice_path) == (source / 'THIRD_PARTY_NOTICES.md').read_bytes(),
                'Wheel third-party notice bytes differ')
        require(not any(name.startswith(('agent_exchange/', 'benchmarks/', 'integrations/', '.git/'))
                        or name.endswith(('.log', '.jsonl', 'scip_pb2.py', 'watcher.py')) for name in names),
                'Wheel contains excluded paths or artifacts')
        require('Provides-Extra:' not in metadata, 'Wheel unexpectedly declares optional extras')
        runtime = {path.relative_to(source).as_posix(): path.read_bytes()
                   for path in (source / 'codeintel').rglob('*')
                   if path.is_file() and path.suffix in ('.py', '.js', '.ts')}
        wheel_runtime = [name for name in names if name.startswith('codeintel/')]
        require(len(wheel_runtime) == len(set(wheel_runtime)) and set(wheel_runtime) == set(runtime),
                'Wheel runtime file set differs from curated source')
        for name, raw in runtime.items():
            require(archive.read(name) == raw, 'Wheel runtime bytes differ: ' + name)



def setup(args):
    source, out = args.source.resolve(), args.output.resolve()
    require(not out.is_relative_to(source), 'Installed output must be outside curated source')
    out.mkdir(parents=True, exist_ok=False)
    # absolute() intentionally preserves the venv interpreter symlink.
    python = str(args.python.absolute())
    cli = str(Path(python).parent / 'codeintel')
    runner = contract.StageRunner(out, cwd=out,
        replacements=[(str(out), 'VALIDATION_OUTPUT'), (str(source), 'CURATED_SOURCE'),
                      (str(Path(python).parent.parent), 'CLEAN_VENV')],
        receipt_base={'schema': 'codeintel-curated-install-attempt-v2'}, total_seconds=args.suite_timeout + GATE_OVERHEAD_SECONDS)
    return source, out, python, cli, runner


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-receipt', type=Path)
    parser.add_argument('--suite-timeout', type=contract.suite_timeout, default=contract.DEFAULT_SUITE_SECONDS,
                        help='Full-suite seconds, default 360, maximum 900; collection has its own 30-second bound')
    args = parser.parse_args(argv)
    source, out, python, cli, runner = setup(args)
    previous_handler = contract.install_cancellation_handler()
    stage = 'source-validation'
    try:
        manifest = json.loads((source / 'SNAPSHOT_MANIFEST.json').read_text())
        runner.receipt_base['source_commit'] = manifest.get('source_commit')
        links = validate_source(source, manifest)
        require(args.source_receipt is not None, 'A passing --source-receipt is required before installed validation')
        receipt_bytes = args.source_receipt.read_bytes()
        source_receipt = json.loads(receipt_bytes)
        contract.validate_source_receipt(source_receipt, manifest['source_commit'], manifest['files'])
        stage = 'fresh-env'
        runner.run(stage, [python, '-I', '-c', "import importlib.util as u; assert u.find_spec('codeintel') is None"])
        clone = out / 'build-source'
        shutil.copytree(source, clone)
        stage = 'build-wheel'
        runner.run(stage, [python, '-m', 'pip', 'wheel', '--no-index', '--no-deps', '--no-build-isolation', '--wheel-dir', str(out / 'wheels'), str(clone)])
        wheel, = list((out / 'wheels').glob('codeintel-*.whl'))
        validate_wheel(wheel, source)
        stage = 'install-wheel'
        runner.run(stage, [python, '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)])
        stage = 'pip-check'
        runner.run(stage, [python, '-m', 'pip', 'check'])
        stage = 'installed-probe'
        runtime = json.loads(runner.run(stage, [python, '-I', '-c', PROBE, str(source)]).stdout)
        versions = {key.lower().replace('_', '-'): value for key, value in runtime['versions'].items()}
        for lock in ('requirements-lab.lock', 'requirements-lab-dev.lock'):
            for line in (source / lock).read_text().splitlines():
                if '==' in line and not line.startswith('#'):
                    name, version = line.split('==')
                    require(versions[name.lower()] == version, str((name, version)))
        stage = 'version'
        runner.run(stage, [cli, '--version'])
        for fmt in ('json', 'text'):
            stage = 'demo-' + fmt
            demo = runner.run(stage, [cli, 'lab', 'demo', '--workspace', str(out / stage), '--format', fmt])
            stage = 'evaluation-' + fmt
            result = runner.run(stage, [cli, 'lab', 'evaluate', '--format', fmt])
            if fmt == 'json':
                require(json.loads(demo.stdout)['status'] == 'PASS', 'Installed demo did not pass')
                evaluation = json.loads(result.stdout)
                require(evaluation['status'] == 'PASS', 'Installed evaluation did not pass')
            else:
                require(demo.stdout == (source / 'docs/portfolio/DEMO.txt').read_bytes(), 'Installed demo text differs from retained golden')
                require(result.stdout == (source / 'docs/portfolio/EVALUATION.txt').read_bytes(), 'Installed evaluation text differs from retained golden')
        stage = 'installed-acceptance'
        report = contract.run_pytest_suite(runner, python=python, source=source,
            runtime_root=runtime['runtime_root'], mode='installed', junit_name='installed.junit.xml', timeout_seconds=args.suite_timeout)
        contract.require_matching_cases(source_receipt, report)
        stage = 'claims-from-extracted-source'
        runner.run(stage, [python, str(source / 'scripts/reproduce_claims.py'), '--output', str(out / 'claim-reproduction')])
        claim_report = json.loads((out / 'claim-reproduction/claims.json').read_text())
        require(claim_report['status'] == 'PASS' and claim_report['source_commit'] == manifest['source_commit'],
                'Claim reproduction failed or reports another source commit')
        receipt = {'schema': 'codeintel-curated-install-v3', 'status': 'PASS',
                   'source_commit': manifest['source_commit'], 'files_in_manifest': len(manifest['files']),
                   'local_markdown_links_checked': len(links), 'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
                   'license_expression': 'MIT', 'license_files_verified': True,
                   'runtime': json.loads(runner.clean(json.dumps(runtime))), 'tests': report['junit'], **report,
                   'source_receipt_sha256': hashlib.sha256(receipt_bytes).hexdigest(),
                   'source_installed_case_ids_equal': True, 'source_installed_collection_ids_equal': True,
                   'evaluation_rows': len(evaluation['rows']), 'contract_checks': len(evaluation['checks']),
                   'runs': runner.runs, 'deadline_seconds': args.suite_timeout + GATE_OVERHEAD_SECONDS, 'inference_calls': 0,
                   'network_during_build_install': False, 'claim_in_process_socket_primitives_blocked': True,
                   'model_calls': 0, 'software_production_ready': False}
        (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps({key: receipt[key] for key in ('status', 'source_commit', 'files_in_manifest', 'tests', 'license_expression', 'wheel_sha256')}))
    except (KeyboardInterrupt, Exception) as exc:
        # A stage runner has already recorded its precise process-level failure.
        # Preserve that row while also covering validation failures between stages.
        if not (out / 'receipt.json').exists():
            runner.fail(stage, exc)
        raise
    finally:
        contract.restore_cancellation_handler(previous_handler)


if __name__ == '__main__':
    main()
