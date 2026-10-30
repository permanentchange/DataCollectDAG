"""Lazy command dispatch: help requires only the standard library."""
import argparse
from importlib import import_module
import sys

COMMANDS = {
    "cloud": {"view": ("viewer", "main"), "bev": ("bev", "main"), "project": ("projection", "main")},
    "dataset": {name: ("dataset", name) for name in ("check", "select", "merge", "split", "archive")},
    "yolo": {"filter": ("yolo", "main"), "frames": ("frames", "main")},
    "calibration": {"convert": ("calibration", "main")},
    "ros": {"sync": ("ros", "sync"), "record": ("ros", "record"), "camera-reset": ("ros", "camera_reset")},
    "monitor": {"jetson": ("monitor", "main"), "report": ("monitor", "report")},
}


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Repository dataset, geometry and vehicle tools")
    groups = parser.add_subparsers(dest="group")
    for group, commands in COMMANDS.items():
        child = groups.add_parser(group, help=", ".join(commands))
        leaves = child.add_subparsers(dest="command")
        for command, (module, function) in commands.items():
            leaf = leaves.add_parser(command, add_help=False)
            leaf.set_defaults(target=(module, function))
    if len(arguments) < 2 or arguments[0].startswith('-') or arguments[1].startswith('-'):
        parser.parse_args(arguments + (["--help"] if len(arguments) == 1 and arguments[0] in COMMANDS else []))
        if not arguments:
            parser.print_help()
        return 0
    selected = parser.parse_args(arguments[:2])
    module, function = selected.target
    previous = sys.argv
    sys.argv = ["toolkit " + " ".join(arguments[:2])] + arguments[2:]
    try:
        return getattr(import_module("tools.lib." + module), function)() or 0
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1
    finally:
        sys.argv = previous
