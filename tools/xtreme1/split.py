#!/usr/bin/env python3
"""Split an Xtreme1 merged dataset into hardlinked part directories."""

import argparse, errno, os, time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from itertools import islice
from pathlib import Path

CANONICAL_DIR = "camera_config"
BATCH_SIZE = 64


def fail(msg):
    raise SystemExit("error: " + str(msg))


def scan(path):
    index = {}
    for e in os.scandir(path):
        if e.is_file():
            stem = os.path.splitext(e.name)[0]
            if stem in index:
                fail("multiple files for frame {} in {}".format(stem, path))
            index[stem] = e.name
    return index


def split_bounds(n, k):
    q, r, start = n // k, n % k, 0
    for i in range(k):
        end = start + q + (i < r)
        yield start, end
        start = end


def link_batch(batch):
    for src, dst in batch:
        try:
            os.link(src, dst)
        except OSError as e:
            msg = "cannot hardlink across filesystems" if e.errno == errno.EXDEV else "failed to hardlink"
            raise RuntimeError("{}: {} -> {}: {}".format(msg, src, dst, e))
    return len(batch)


def run_links(jobs, total, workers):
    jobs, done, t0, last = iter(jobs), 0, time.monotonic(), 0

    def submit(pool):
        batch = list(islice(jobs, BATCH_SIZE))
        return pool.submit(link_batch, batch) if batch else None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {f for _ in range(workers * 2) if (f := submit(pool))}
        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for f in finished:
                done += f.result()
                if new := submit(pool):
                    pending.add(new)
            now = time.monotonic()
            if now - last >= 1:
                print(
                    "\rlinked {}/{} files ({:.0f} files/s)".format(
                        done, total, done / max(now - t0, 1e-9)
                    ),
                    end="", flush=True,
                )
                last = now

    dt = max(time.monotonic() - t0, 1e-9)
    print("\rlinked {}/{} files ({:.0f} files/s)".format(done, total, done / dt))


def split(input_dir, output_dir, part_count=4, workers=8, dry_run=False):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if not input_dir.is_dir():
        fail("input directory does not exist: {}".format(input_dir))
    if part_count <= 0 or workers <= 0:
        fail("part-count and workers must be greater than 0")

    input_dir = input_dir.resolve()
    output_dir = output_dir.parent.resolve() / output_dir.name

    try:
        output_dir.relative_to(input_dir)
        fail("output directory must not be inside input directory: {}".format(output_dir))
    except ValueError:
        pass

    dirs = sorted((e.name, e.path) for e in os.scandir(input_dir) if e.is_dir())
    paths = dict(dirs)

    if not dirs:
        fail("input directory has no data subdirectories: {}".format(input_dir))
    if CANONICAL_DIR not in paths:
        fail("missing canonical frame directory: {}".format(input_dir / CANONICAL_DIR))

    indexes = {name: scan(path) for name, path in dirs}
    canonical = indexes[CANONICAL_DIR]

    if not canonical:
        fail("no frame files found in {}".format(paths[CANONICAL_DIR]))

    frames = [stem for stem, _ in sorted(canonical.items(), key=lambda x: x[1])]

    for name, path in dirs:
        missing = next((f for f in frames if f not in indexes[name]), None)
        if missing is not None:
            fail("missing file for frame {} in {}".format(missing, path))

    parts = list(split_bounds(len(frames), part_count))

    for i, (start, end) in enumerate(parts, 1):
        part = output_dir / "part{:02d}".format(i)
        if part.exists():
            fail("output part already exists: {}".format(part))
        print("part{:02d}: {} frames -> {}".format(i, end - start, part))

    print("validated: {} frames, {} data dirs".format(len(frames), len(dirs)))

    if dry_run:
        print("dry run: no directories or hardlinks created")
        return

    for i in range(1, part_count + 1):
        for name, _ in dirs:
            (output_dir / "part{:02d}".format(i) / name).mkdir(
                parents=True, exist_ok=True
            )

    def jobs():
        for i, (start, end) in enumerate(parts, 1):
            part = output_dir / "part{:02d}".format(i)
            for frame in frames[start:end]:
                for name, src_dir in dirs:
                    fn = indexes[name][frame]
                    yield os.path.join(src_dir, fn), str(part / name / fn)

    total = len(frames) * len(dirs)

    try:
        run_links(jobs(), total, workers)
    except RuntimeError as e:
        fail(e)

    print(
        "split complete: {} frames into {} parts, {} hardlinks".format(
            len(frames), part_count, total
        )
    )


def main():
    p = argparse.ArgumentParser(
        description="Split Xtreme1 dataset into hardlinked parts"
    )
    p.add_argument("input_dir", type=Path)
    p.add_argument("output_dir", type=Path)
    p.add_argument("--part-count", type=int, default=4)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    split(a.input_dir, a.output_dir, a.part_count, a.workers, a.dry_run)


if __name__ == "__main__":
    main()