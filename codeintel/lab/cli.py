"""Existing CLI's offline lab commands. All expected errors have recovery advice."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import apsw

from codeintel.core.security import FreshnessBusyError, SecurityException
from codeintel.lab.retrieval import (LabError, index_repository, query_repository,
                                     serialize, verify_packet)
from codeintel.lab.scenarios import evaluate, run_demo
from codeintel.safe_artifacts import _open_parent_directory, read_regular_file_bounded, strict_json_loads
from codeintel.lab.presentation import render_demo, render_evaluation
from codeintel.storage.policy import DerivedStateValidationError


def _discard_pending_stdout():
    """Do not retry failed/interrupted delivery during interpreter shutdown.

    Python can retain buffered bytes after write/flush fails. Redirect the
    descriptor before SystemExit so finalization neither retries the recipient
    nor replaces our status with 120. Already delivered bytes cannot be recalled.
    Embedded/test streams without an OS descriptor have no redirectable sink.
    """
    try:
        stdout_fd = sys.stdout.fileno()
        null_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_fd, stdout_fd)
        finally:
            if null_fd != stdout_fd:
                os.close(null_fd)
    except (AttributeError, OSError, ValueError):
        # Best effort when no descriptor or spare descriptor is available.
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog="codeintel lab", description="Offline synthetic code-context lab; no inference")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "query", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--repo", type=Path, required=True)
        if name != "verify":
            command.add_argument("--state-dir", type=Path, required=True, help="Persistent state outside the repository")
        if name == "query":
            command.add_argument("query")
            command.add_argument("--max-bytes", type=int, default=4096)
            command.add_argument("--limit", type=int, default=5)
            command.add_argument("--literal", action="store_true",
                                 help="Scan indexed chunks for the literal query; no symbol expansion")
        if name == "verify":
            command.add_argument("--packet", type=Path, required=True)
        if name != "verify":
            command.add_argument("--record", type=Path, help="Opt-in metadata receipt, never query/source text")
    demo = commands.add_parser("demo")
    demo.add_argument("--workspace", type=Path, required=True, help="A new directory for synthetic copies")
    evaluation = commands.add_parser("evaluate")
    for report_command in (demo, evaluation):
        report_command.add_argument("--format", choices=("json", "text"), default="json",
                                    help="Machine-readable JSON (default) or a narrated terminal report")
    args = parser.parse_args(argv)
    telemetry = None
    record_parent_fd = None
    record_name = None
    stdout_started = False
    try:
        if getattr(args, "record", None) and args.record.exists():
            raise LabError("RECORD_EXISTS", "Choose a new receipt path; receipts are never overwritten.")
        if getattr(args, "record", None):
            source = args.repo.resolve()
            receipt = Path(os.path.abspath(args.record))
            if receipt.is_relative_to(Path(os.path.abspath(args.repo))) or receipt.resolve().is_relative_to(source):
                raise LabError("RECORD_INSIDE_REPOSITORY", "Choose a new receipt path outside the inspected repository.")
            try:
                # Pin a no-follow parent before any index/query work. A symlink
                # swap cannot redirect the eventual receipt open through a new path.
                record_parent_fd, record_name = _open_parent_directory(receipt, label="lab receipt")
            except (OSError, RuntimeError) as exc:
                raise LabError("INVALID_RECORD_PATH", "Use an existing, non-symlink receipt parent outside the repository.") from exc
        if args.command == "demo":
            if args.workspace.exists():
                raise LabError("WORKSPACE_EXISTS", "Choose a new directory; the demo never overwrites existing work.")
            result = run_demo(args.workspace)
        elif args.command == "evaluate":
            result = evaluate()
        elif args.command == "index":
            result, telemetry = index_repository(args.repo, args.state_dir)
        elif args.command == "query":
            result, telemetry = query_repository(args.repo, args.state_dir, args.query,
                                                 max_bytes=args.max_bytes, limit=args.limit,
                                                 baseline=args.literal)
        else:
            try:
                raw = read_regular_file_bounded(args.packet, max_bytes=8192, label="lab packet")
            except (OSError, RuntimeError) as exc:
                raise LabError("INVALID_PACKET", "Supply a regular, non-symlink query packet of at most 8192 bytes.") from exc
            try:
                result = strict_json_loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError) as exc:
                raise LabError("INVALID_PACKET", "Supply the JSON from a lab query, not a demo report.") from exc
            verify_packet(args.repo, result)
            result = {"status": "VERIFIED", "scope": "selected source matches current files"}
        if getattr(args, "format", "json") == "text":
            render = render_demo if args.command == "demo" else render_evaluation
            output = render(result).encode("utf-8")
        else:
            output = serialize(result)
        if telemetry is not None and args.record:
            # Receipt completion precedes stdout, but the two destinations are
            # not a transaction. A later stdout failure leaves this prepared
            # receipt; a receipt I/O failure may leave an incomplete file.
            telemetry["delivery"] = "prepared_for_stdout"
            receipt_bytes = serialize(telemetry)
            fd = os.open(record_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                         0o600, dir_fd=record_parent_fd)
            try:
                receipt = os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)
                raise
            with receipt:
                if receipt.write(receipt_bytes) != len(receipt_bytes):
                    raise OSError("Incomplete receipt write")
                receipt.flush()
                os.fsync(receipt.fileno())
        stdout_started = True
        if sys.stdout.buffer.write(output) != len(output):
            raise OSError("Incomplete stdout write")
        sys.stdout.buffer.flush()
        if result.get("status") == "FAIL":
            raise SystemExit(1)
    except KeyboardInterrupt:
        # Preserve the conventional SIGINT shell status without exposing a
        # traceback or host paths. Existing finally/context-manager cleanup
        # still runs; state or a partial/prepared receipt may remain.
        if stdout_started:
            _discard_pending_stdout()
        print(json.dumps({"status": "ERROR", "code": "CANCELLED",
                          "recovery": "Command interrupted. Inspect any state or receipt left behind; retry with a new receipt path. No completed delivery is confirmed."}), file=sys.stderr)
        raise SystemExit(130) from None
    except LabError as exc:
        print(json.dumps({"status": "ERROR", "code": exc.code, "recovery": exc.recovery}), file=sys.stderr)
        raise SystemExit(2) from None
    except DerivedStateValidationError as exc:
        # A newer state format is not necessarily corrupt. Never reset or
        # migrate unknown derived state as part of error recovery.
        print(json.dumps({"status": "ERROR", "code": type(exc).__name__,
                          "recovery": "Use a compatible CodeIntel version or a new state directory. Leave the existing state unchanged."}), file=sys.stderr)
        raise SystemExit(2) from None
    except (SecurityException, FreshnessBusyError, OSError, apsw.Error) as exc:
        # Expected availability failures; avoid leaking absolute host paths.
        # Unexpected programming RuntimeErrors still propagate unchanged.
        if stdout_started:
            _discard_pending_stdout()
            recovery = "Output delivery to stdout failed. Check the output destination or pipe and retry with a new receipt path. A prepared receipt does not confirm delivery; partial output may have escaped."
        else:
            recovery = "Check repository/state permissions, keep state outside source, stop writes and retry. Use a new state directory for corrupt storage."
        print(json.dumps({"status": "ERROR", "code": type(exc).__name__,
                          "recovery": recovery}), file=sys.stderr)
        raise SystemExit(2) from None
    finally:
        if record_parent_fd is not None:
            os.close(record_parent_fd)


if __name__ == "__main__":
    main()
