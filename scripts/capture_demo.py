"""Capture actual synthetic CLI stdout and render it verbatim for a browser screenshot.

No simulated terminal/app chrome. A native terminal screenshot may be captured separately; the generated HTML is
a verbatim transcript viewer, not a simulated screenshot.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    if not (ROOT / '.git').exists():
        raise SystemExit('This capture helper requires its own Git checkout; use the CLI directly from an extracted archive')
    top=subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=ROOT,text=True,timeout=5).strip()
    if Path(top).resolve()!=ROOT.resolve():raise SystemExit('No Git identity bound to this project root')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,timeout=5).strip()
    runtime_files={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted((ROOT/'codeintel').rglob('*'))
                   if p.is_file() and p.suffix in ('.py', '.js', '.ts')}
    runtime={name:sha for name,sha in runtime_files.items() if name.endswith('.py')}
    tracked=subprocess.check_output(['git','ls-tree','-r','--name-only','-z',commit,'--','codeintel/'],
                                    cwd=ROOT,timeout=5).decode('utf-8').split('\0')
    expected={name for name in tracked if Path(name).suffix in ('.py', '.js', '.ts')}
    if set(runtime_files)!=expected:
        raise SystemExit('Runtime file set differs from HEAD; restore or commit before capture')
    for name,sha in runtime_files.items():
        raw=subprocess.check_output(['git','show',f'{commit}:{name}'],cwd=ROOT,timeout=5)
        if hashlib.sha256(raw).hexdigest()!=sha:raise SystemExit('Runtime differs from HEAD; commit before capture')
    with tempfile.TemporaryDirectory(prefix='codeintel-capture-') as temp:
        command=[sys.executable,'-m','codeintel.lab.cli','demo','--workspace',str(Path(temp)/'synthetic-copy'),'--format','text']
        result=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=20)
    if result.returncode:raise SystemExit(result.stderr.decode(errors='replace'))
    (out/'demo-stdout.txt').write_bytes(result.stdout)
    output=result.stdout.decode('utf-8')
    visible_command='codeintel lab demo --workspace <new-synthetic-copy> --format text'
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><title>CodeIntel: captured scripted demo output</title><style>
body{margin:0;background:#fff;color:#183449;font-family:Arial,sans-serif;padding:32px 38px;box-sizing:border-box}h1{font-size:27px;margin:0 0 10px}p{font-size:15px;margin:7px 0}code{font:14px "DejaVu Sans Mono",monospace}pre{font:16px/1.43 "DejaVu Sans Mono",monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f7f8;border-left:4px solid #367d88;padding:20px;margin:21px 0}.note{color:#526776;font-size:13px}
</style><h1>Actual CLI output · scripted offline demo</h1><p>Disposable synthetic fixture. The script provides the edit; no agent is running.</p><p><code>'''+html.escape(visible_command)+'''</code></p><p class="note">Runtime commit: '''+commit+'''</p><pre>'''+html.escape(output)+'''</pre><p class="note">Browser-rendered capture of recorded stdout, not an interactive terminal screenshot. Source hashes and capture metadata accompany this image.</p></html>'''
    (out/'demo-viewer.html').write_text(page)
    metadata={'schema':'codeintel-demo-capture-v1','runtime_commit':commit,'runtime_matches_commit':True,'runtime_sha256':runtime,'runtime_files_sha256':runtime_files,'command':visible_command,'command_kind':'workspace placeholder normalized; actual fresh temporary path deliberately omitted','exit_code':result.returncode,'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(),'capture_type':'verbatim recorded stdout rendered in a local browser; not simulated terminal UI','synthetic_scripted':True,'agent_execution':False}
    (out/'capture.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps({'status':'PASS','runtime_commit':commit,'stdout_sha256':metadata['stdout_sha256']}))

if __name__=='__main__':main()
