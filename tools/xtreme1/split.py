#!/usr/bin/env python3
"""Split Xtreme1 datasets using hardlinks (Python 3.8+)."""

import argparse
import os
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from itertools import islice
from pathlib import Path

CANONICAL_DIR = "camera_config"
BATCH_SIZE = 64


def fail(msg):
    raise SystemExit(f"error: {msg}")


def scan(path):
    files, last = {}, time.monotonic()
    with os.scandir(path) as entries:
        for e in entries:
            if e.is_symlink():
                fail(f"symbolic link not supported: {e.path}")
            if not e.is_file():
                continue
            stem = os.path.splitext(e.name)[0]
            if stem in files:
                fail(f"duplicate frame: {stem} in {path}")
            files[stem] = e.name
            if len(files) % 8192 == 0 and time.monotonic() - last >= 1:
                print(f"\rscanning {path}: {len(files):,} files", end="", flush=True)
                last = time.monotonic()
    print(f"\rscanned {path}: {len(files):,} files")
    return files


def link_batch(batch):
    for src, dst in batch:
        try:
            os.link(src, dst)
        except OSError as e:
            raise RuntimeError(f"hardlink failed: {src} -> {dst}: {e}")
    return len(batch)


def run_links(jobs, total, workers):
    jobs, done = iter(jobs), 0
    start = last = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        def submit():
            batch = list(islice(jobs, BATCH_SIZE))
            return pool.submit(link_batch, batch) if batch else None

        pending = {f for _ in range(workers * 2) if (f := submit())}

        while pending:
            finished, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            for f in finished:
                done += f.result()
                if new := submit():
                    pending.add(new)

            now = time.monotonic()
            if now - last >= 1:
                rate = done / max(now - start, 1e-9)
                eta = (total - done) / rate if rate else 0
                print(f"\rlinked {done:,}/{total:,} ({done/total:.1%}) "
                      f"| {rate:,.0f} files/s | ETA {eta:.0f}s",
                      end="", flush=True)
                last = now

    print(f"\rlinked {done:,}/{total:,} (100%) "
          f"| {done/max(time.monotonic()-start, 1e-9):,.0f} files/s")


def main():
    p = argparse.ArgumentParser(
        description="拆分 Xtreme1 数据集，仅使用硬链接，不复制文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
目录结构:
  input/
    camera_config/   基准帧目录
    lidar/           其他数据目录
    camera/          其他数据目录

  output/
    part01/
      camera_config/
      lidar/
      camera/
    part02/
      ...

使用示例:
  python3 split.py input output
  python3 split.py input output --part-count 8 --workers 16
  python3 split.py input output --dry-run

规则与注意:
  - 按 camera_config 文件名排序，均匀分配帧
  - 各数据目录通过文件主名（去扩展名）匹配帧
  - 仅处理一级子目录中的文件，不递归
  - 输入与输出必须在同一文件系统，不支持符号链接
  - 仅创建硬链接，失败直接报错，不降级为复制
  - 输出分片目录不能已存在，失败后不自动回滚
  - --dry-run 扫描并验证数据，但不创建文件或目录
"""
    )
    p.add_argument("input", type=Path, help="输入数据集目录")
    p.add_argument("output", type=Path, help="输出分片根目录")
    p.add_argument("--part-count", type=int, default=4, metavar="N",
                   help="分片数量（默认：4）")
    p.add_argument("--workers", type=int, default=8, metavar="N",
                   help="硬链接并发线程数（默认：8）")
    p.add_argument("--dry-run", action="store_true",
                   help="仅检查拆分计划，不执行")
    a = p.parse_args()

    if not a.input.is_dir():
        fail(f"input directory not found: {a.input}")
    if a.part_count < 1 or a.workers < 1:
        fail("part-count and workers must be positive")

    src, dst = a.input.resolve(), a.output.resolve()
    if dst == src or src in dst.parents:
        fail("output cannot be inside input")
    if dst.exists() and not dst.is_dir():
        fail(f"output is not a directory: {dst}")

    dirs = sorted((e.name, e.path) for e in os.scandir(src) if e.is_dir())
    paths = dict(dirs)
    if CANONICAL_DIR not in paths:
        fail(f"missing directory: {CANONICAL_DIR}")

    # 文件系统检查
    target = dst
    while not target.exists():
        target = target.parent
    device = target.stat().st_dev
    for name, path in dirs:
        if os.stat(path).st_dev != device:
            fail(f"cross-filesystem hardlink: {path}")

    # 扫描并验证帧
    indexes = {name: scan(path) for name, path in dirs}
    canonical = indexes[CANONICAL_DIR]
    frames = sorted(canonical, key=canonical.get)
    if not frames:
        fail("no frames found")

    for name, path in dirs:
        missing = next((f for f in frames if f not in indexes[name]), None)
        if missing is not None:
            fail(f"missing frame {missing} in {path}")

    # 计算分片范围
    q, r, start, parts = divmod(len(frames), a.part_count) + (0, [])
    for i in range(a.part_count):
        end = start + q + (i < r)
        part = dst / f"part{i+1:02d}"
        if part.exists():
            fail(f"output part already exists: {part}")
        parts.append((part, start, end))
        print(f"{part.name}: {end-start:,} frames -> {part}")
        start = end

    total = len(frames) * len(dirs)
    print(f"validated: {len(frames):,} frames, {len(dirs)} dirs, {total:,} files")

    if a.dry_run:
        print("dry run: no files or directories created")
        return

    # 真实硬链接预检
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix=".hardlink_check_", dir=str(dst if dst.exists() else dst.parent)
        ) as tmp:
            for i, (name, path) in enumerate(dirs):
                fn = next(iter(indexes[name].values()))
                source = os.path.join(path, fn)
                target = os.path.join(tmp, str(i))
                os.link(source, target)
                if not os.path.samefile(source, target):
                    fail(f"hardlink verification failed: {source}")
    except OSError as e:
        fail(f"hardlink precheck failed: {e}")

    print("hardlink precheck passed")

    # 创建输出目录
    for part, _, _ in parts:
        for name, _ in dirs:
            (part / name).mkdir(parents=True, exist_ok=True)

    # 按帧生成任务，不预存全部文件路径
    def jobs():
        for part, start, end in parts:
            for frame in frames[start:end]:
                for name, path in dirs:
                    fn = indexes[name][frame]
                    yield os.path.join(path, fn), str(part / name / fn)

    print(f"linking {total:,} files with {a.workers} workers...")
    try:
        run_links(jobs(), total, a.workers)
    except RuntimeError as e:
        fail(e)

    print(f"split complete: {len(frames):,} frames, "
          f"{len(parts)} parts, {total:,} hardlinks")


if __name__ == "__main__":
    main()