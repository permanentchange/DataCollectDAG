#!/usr/bin/env python3
"""Merge Xtreme1 split part directories into one hardlinked dataset."""

import argparse
import errno
import os
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


MergeOp = Tuple[Path, Path]
PartSummary = Tuple[str, Path, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge Xtreme1 part directories into one dataset with hardlinks."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Input part directories followed by the merged output root",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merge plan without creating directories or hardlinks",
    )
    args = parser.parse_args()
    if len(args.paths) < 2:
        parser.error("expected at least one input part directory and one output directory")
    args.part_dirs = args.paths[:-1]
    args.output_dir = args.paths[-1]
    return args


def fail(message: str) -> None:
    raise SystemExit("error: {}".format(message))


def resolve_output_path(output_dir: Path, create_parent: bool) -> Path:
    output_parent = output_dir.parent if output_dir.parent != Path("") else Path(".")
    if create_parent:
        output_parent.mkdir(parents=True, exist_ok=True)
    return output_dir.resolve(strict=False)


def ensure_output_not_inside_inputs(part_dirs: Sequence[Path], output_dir: Path) -> None:
    for part_dir in part_dirs:
        if output_dir == part_dir:
            fail("output directory must not be an input directory: {}".format(output_dir))

        try:
            output_dir.relative_to(part_dir)
        except ValueError:
            continue

        fail("output directory must not be inside an input directory: {}".format(output_dir))


def resolve_part_dirs(part_dirs: Sequence[Path]) -> List[Path]:
    resolved = []
    seen = set()
    for part_dir in part_dirs:
        if not part_dir.is_dir():
            fail("input part directory does not exist: {}".format(part_dir))
        part_dir_abs = part_dir.resolve()
        if part_dir_abs in seen:
            fail("duplicate input part directory: {}".format(part_dir_abs))
        seen.add(part_dir_abs)
        resolved.append(part_dir_abs)
    return resolved


def list_data_dir_names(part_dir: Path) -> List[str]:
    return sorted(path.name for path in part_dir.iterdir() if path.is_dir())


def ensure_consistent_data_dirs(part_dirs: Sequence[Path]) -> List[str]:
    expected = list_data_dir_names(part_dirs[0])
    if not expected:
        fail("part directory has no data subdirectories: {}".format(part_dirs[0]))

    for part_dir in part_dirs[1:]:
        actual = list_data_dir_names(part_dir)
        if actual != expected:
            fail(
                "inconsistent data subdirectories in {}: expected {}, got {}".format(
                    part_dir, expected, actual
                )
            )

    return expected


def ensure_output_data_dirs_absent(output_dir: Path, data_dir_names: Sequence[str]) -> None:
    for data_dir_name in data_dir_names:
        data_dir = output_dir / data_dir_name
        if data_dir.exists():
            fail("output data directory already exists: {}".format(data_dir))


def build_merge_plan(
    part_dirs: Sequence[Path], output_dir: Path, data_dir_names: Sequence[str]
) -> Tuple[List[MergeOp], List[PartSummary]]:
    operations = []
    summaries = []
    seen_destinations: Dict[Path, Path] = {}

    for part_dir in part_dirs:
        part_file_count = 0
        for data_dir_name in data_dir_names:
            src_data_dir = part_dir / data_dir_name
            dst_data_dir = output_dir / data_dir_name
            for src in sorted(path for path in src_data_dir.iterdir() if path.is_file()):
                dst = dst_data_dir / src.name
                previous = seen_destinations.get(dst)
                if previous is not None:
                    fail(
                        "duplicate destination file {} from {} and {}".format(
                            dst, previous, src
                        )
                    )
                seen_destinations[dst] = src
                operations.append((src, dst))
                part_file_count += 1

        summaries.append((part_dir.name, part_dir, part_file_count))

    return operations, summaries


def create_output_dirs(output_dir: Path, data_dir_names: Sequence[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for data_dir_name in data_dir_names:
        (output_dir / data_dir_name).mkdir()


def link_operations(operations: Sequence[MergeOp]) -> None:
    for src, dst in operations:
        try:
            os.link(str(src), str(dst))
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                fail("cannot hardlink across filesystems: {} -> {}".format(src, dst))
            fail("failed to hardlink {} -> {}: {}".format(src, dst, exc))


def print_summary(
    summaries: Sequence[PartSummary], output_dir: Path, total_files: int, dry_run: bool
) -> None:
    if dry_run:
        print("dry run: no files linked")

    for part_name, part_dir, file_count in summaries:
        print("{}: {} files -> {}".format(part_name, file_count, part_dir))

    print(
        "merge complete: {} files from {} parts -> {}".format(
            total_files, len(summaries), output_dir
        )
    )


def main() -> None:
    args = parse_args()

    part_dirs = resolve_part_dirs(args.part_dirs)
    output_dir = resolve_output_path(args.output_dir, create_parent=not args.dry_run)
    ensure_output_not_inside_inputs(part_dirs, output_dir)

    data_dir_names = ensure_consistent_data_dirs(part_dirs)
    ensure_output_data_dirs_absent(output_dir, data_dir_names)
    operations, summaries = build_merge_plan(part_dirs, output_dir, data_dir_names)

    if not args.dry_run:
        create_output_dirs(output_dir, data_dir_names)
        link_operations(operations)

    print_summary(summaries, output_dir, len(operations), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
