#!/usr/bin/env python3
"""Merge Xtreme1 parts with hardlinks (Python 3.8+)."""

import argparse
import errno
import os
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="合并 Xtreme1 分片（仅硬链接，不复制文件）。",
        epilog="示例: python3 merge.py part1 part2 -o merged [--dry-run]"
    )
    parser.add_argument("parts", nargs="+", type=Path, help="输入分片目录")
    parser.add_argument("-o", "--output", required=True, type=Path, help="输出目录")
    parser.add_argument("--dry-run", action="store_true", help="快速预检，不扫描文件或写入")
    parser.add_argument("--log-interval", type=float, default=5, metavar="SEC",
                        help="进度日志间隔，默认 5 秒")
    args = parser.parse_args()

    def fail(msg):
        parser.error(str(msg))

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def progress(stage, total=0):
        start = last = time.monotonic()

        def update(count, location="", force=False):
            nonlocal last
            if not force and count % 64:
                return
            now = time.monotonic()
            if not force and now - last < args.log_interval:
                return
            last = now
            elapsed = max(now - start, 1e-6)
            rate = count / elapsed
            msg = f"[{stage}] {count:,}"
            if total:
                msg += f"/{total:,} ({count / total:.1%})"
            msg += f" files | {rate:,.0f}/s | {elapsed:.1f}s"
            if total and 0 < count < total:
                msg += f" | ETA {(total - count) / rate:.0f}s"
            log(msg + (f" | {location}" if location else ""))

        return update

    # 检查目录
    if args.log_interval <= 0:
        fail("--log-interval must be > 0")
    if any(not p.is_dir() for p in args.parts):
        fail("输入目录不存在或不是目录")

    parts = [p.resolve() for p in args.parts]
    output = args.output.resolve()
    if len(set(parts)) != len(parts):
        fail("输入目录重复")
    if any(output == p or p in output.parents for p in parts):
        fail("输出目录不能位于输入目录内")
    if output.exists() and not output.is_dir():
        fail("输出路径不是目录")

    def subdirs(p):
        return sorted(x.name for x in p.iterdir() if x.is_dir())

    names = subdirs(parts[0])
    if not names or any(subdirs(p) != names for p in parts[1:]):
        fail("输入分片的一级数据子目录不一致或为空")
    if any((output / name).exists() for name in names):
        fail("输出目录中已存在同名数据子目录")

    # 文件系统检查
    target = output
    while not target.exists():
        target = target.parent
    device = target.stat().st_dev
    for part in parts:
        for name in names:
            if (part / name).stat().st_dev != device:
                fail(f"无法跨文件系统创建硬链接: {part / name}")

    if args.dry_run:
        log(f"[dry-run] 通过：{len(parts)} 个分片、{len(names)} 个数据子目录；未扫描文件或写入")
        return

    # 预扫描：统计文件并检查同名冲突
    counts, samples, scanned = [0] * len(parts), [], 0
    report = progress("scan")
    log(f"[scan] 开始检查 {len(parts)} 个分片、{len(names)} 个数据子目录")

    for name in names:
        seen = set()
        for i, part in enumerate(parts):
            sample = None
            with os.scandir(str(part / name)) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    if entry.name in seen:
                        fail(f"同名文件冲突: {name}/{entry.name}")
                    seen.add(entry.name)
                    sample = sample or entry.path
                    counts[i] += 1
                    scanned += 1
                    report(scanned, f"{part.name}/{name}")
            if sample:
                samples.append(sample)

    report(scanned, "完成", force=True)

    # 使用真实文件测试硬链接，不允许降级为复制
    output.parent.mkdir(parents=True, exist_ok=True)
    probe_dir = output if output.exists() else output.parent
    try:
        with tempfile.TemporaryDirectory(
            prefix=".hardlink_check_", dir=str(probe_dir)
        ) as tmp:
            for i, src in enumerate(samples):
                os.link(src, os.path.join(tmp, str(i)))
    except OSError as e:
        fail(f"硬链接预检失败: {e}")

    log(f"[check] 硬链接预检通过（{len(samples)} 个样本）")

    # 创建输出目录
    output.mkdir(parents=True, exist_ok=True)
    for name in names:
        (output / name).mkdir()

    # 执行硬链接并输出进度
    total, linked = sum(counts), 0
    report = progress("link", total)
    log(f"[link] 开始合并 {total:,} 个文件 -> {output}")

    for i, part in enumerate(parts):
        for name in names:
            dst_dir = str(output / name)
            with os.scandir(str(part / name)) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    try:
                        os.link(entry.path, os.path.join(dst_dir, entry.name))
                    except OSError as e:
                        if e.errno == errno.EXDEV:
                            fail(f"无法跨文件系统创建硬链接: {entry.path}")
                        fail(f"创建硬链接失败: {entry.path}: {e}")
                    linked += 1
                    report(linked, f"{part.name}/{name}")

        log(f"[link] 分片完成: {part.name} ({counts[i]:,} files)")

    report(linked, "完成", force=True)
    log(f"合并完成: {linked:,} 个硬链接 -> {output}")


if __name__ == "__main__":
    main()