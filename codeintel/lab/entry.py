"""Offline lab command entry point; the historical agent CLI is not shipped."""
import sys

from codeintel import __version__


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        print(f"codeintel {__version__}")
        return
    if args[:1] == ["lab"]:
        from codeintel.lab.cli import main as lab_main
        lab_main(args[1:])
        return
    if not args or args == ["--help"] or args == ["-h"]:
        print("CodeIntel experimental offline lab\nUsage: codeintel lab {index,query,verify,demo,evaluate}\nRun codeintel lab --help for options.")
        return
    print("Use codeintel lab --help. Only the offline lab is included in this distribution", file=sys.stderr)
    raise SystemExit(2)
