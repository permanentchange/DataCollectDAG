"""Validated Xtreme1 frame operations. Sources are never modified."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Dict, List
import zipfile


def index(root: Path) -> Dict[str, Dict[str, Path]]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"invalid dataset directory: {root}")
    directories = sorted(p for p in root.iterdir() if p.is_dir() and p.name != 'diagnostics')
    result = {}
    for directory in directories:
        if directory.is_symlink():
            raise ValueError(f"symbolic link unsupported: {directory}")
        frames = {}
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"expected ordinary flat data file: {path}")
            if path.stem in frames:
                raise ValueError(f"duplicate frame {path.stem}: {directory}")
            frames[path.stem] = path
        result[directory.name] = frames
    if 'camera_config' not in result:
        raise ValueError(f"missing camera_config: {root}")
    canonical = set(result['camera_config'])
    for name, frames in result.items():
        missing, extra = canonical-set(frames), set(frames)-canonical
        if missing or extra:
            raise ValueError(f"{root}/{name}: missing={sorted(missing)[:5]} orphan={sorted(extra)[:5]}")
    return result


def parser(description):
    return argparse.ArgumentParser(description=description)


def write_report(path, value):
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2)+'\n')
    print(json.dumps(value))


def check():
    p = parser('Validate matching frames in an Xtreme1 dataset')
    p.add_argument('input', type=Path)
    p.add_argument('--json', type=Path)
    a = p.parse_args()
    try:
        data = index(a.input)
        write_report(a.json, {'valid': True, 'frames': len(data['camera_config']), 'directories': sorted(data)})
    except ValueError as exc:
        write_report(a.json, {'valid': False, 'error': str(exc)})
        return 1
    return 0


def validate_target(sources, target):
    target = target.resolve()
    for source in sources:
        source = source.resolve()
        if target == source or source in target.parents or target in source.parents:
            raise ValueError('input and output directories must not overlap')
    if target.exists():
        raise ValueError(f"output already exists: {target}")
    ancestor = target.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    return ancestor.stat().st_dev


def link_batch(batch: list) -> int:
    for source, target in batch:
        os.link(source, target)
    return len(batch)


def execute(plans: list, dry_run: bool = False, workers: int = 8, log_interval: float = 5.0) -> None:
    # plans: (output, index, selected frame IDs). Precheck every plan before writing.
    for target, data, frames in plans:
        sources = [next(iter(files.values())).parent.parent for files in data.values() if files]
        device = validate_target(sources, target)
        for files in data.values():
            if any(files[frame].stat().st_dev != device for frame in frames):
                raise ValueError('cross-filesystem hardlink unsupported')
        print(f"{target}: {len(frames)} frames, {len(data)} directories")
    if dry_run:
        return
    completed = 0
    last_log = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for target, data, frames in plans:
                for name in data:
                    (target/name).mkdir(parents=True)
                jobs = ((files[frame], target/name/files[frame].name)
                        for frame in frames for name, files in data.items())
                while True:
                    batches = [list(islice(jobs, 64)) for _ in range(workers*2)]
                    batches = [batch for batch in batches if batch]
                    if not batches:
                        break
                    for count in pool.map(link_batch, batches):
                        completed += count
                        if time.monotonic() - last_log >= log_interval:
                            print(f"linked {completed} files", flush=True)
                            last_log = time.monotonic()
        print(f"linked {completed} files")
    except OSError as exc:
        raise OSError(f"incomplete outputs {[str(p[0]) for p in plans]}; completed batches={completed} files: {exc}") from exc


def split():
    p = parser('Split complete frames using hard links only')
    p.add_argument('input', type=Path); p.add_argument('output', type=Path)
    p.add_argument('--part-count', type=int, default=4); p.add_argument('--workers', type=int, default=8)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if a.part_count < 1 or a.workers < 1:
        p.error('part-count and workers must be positive')
    data = index(a.input)
    frames = sorted(data['camera_config'])
    if not frames:
        p.error('no frames found')
    validate_target([a.input], a.output) if not a.output.exists() else None
    if a.output.resolve() == a.input.resolve() or a.input.resolve() in a.output.resolve().parents:
        p.error('output cannot be inside input')
    q, r = divmod(len(frames), a.part_count)
    plans, start = [], 0
    for i in range(a.part_count):
        end = start+q+(i < r)
        plans.append((a.output/f'part{i+1:02d}', data, frames[start:end]))
        start = end
    execute(plans, a.dry_run, a.workers)


def merge():
    p = parser('Merge complete frame sets using hard links only')
    p.add_argument('parts', nargs='+', type=Path); p.add_argument('-o', '--output', required=True, type=Path)
    p.add_argument('--dry-run', action='store_true'); p.add_argument('--log-interval', type=float, default=5)
    a = p.parse_args()
    if a.log_interval <= 0 or len({x.resolve() for x in a.parts}) != len(a.parts):
        p.error('positive log interval and unique input parts required')
    combined = None
    for part in a.parts:
        data = index(part)
        if combined is None:
            combined = {name: {} for name in data}
        if set(data) != set(combined):
            p.error('data directories differ between parts')
        for name, files in data.items():
            if set(files) & set(combined[name]):
                p.error(f'frame collision in {name}')
            combined[name].update(files)
    validate_target(a.parts, a.output)
    execute([(a.output, combined, sorted(combined['camera_config']))], a.dry_run, log_interval=a.log_interval)


def select():
    p = parser('Select complete frames using detection-count JSON and hard links')
    p.add_argument('input', type=Path); p.add_argument('output', type=Path)
    p.add_argument('--frames', required=True, type=Path); p.add_argument('--min-count', type=int, default=1)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if a.min_count < 0:
        p.error('min-count must be nonnegative')
    data = index(a.input); counts = json.loads(a.frames.read_text())
    if not isinstance(counts, dict) or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts.values()):
        p.error('frames must map frame IDs to nonnegative integer counts')
    if set(counts)-set(data['camera_config']):
        p.error('selection contains unknown frame IDs')
    frames = sorted(frame for frame, count in counts.items() if count >= a.min_count)
    execute([(a.output, data, frames)], a.dry_run)


def archive():
    p = parser('Create one labeling ZIP per validated dataset')
    p.add_argument('inputs', nargs='+', type=Path); p.add_argument('-o', '--output', required=True, type=Path)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if len({path.name for path in a.inputs}) != len(a.inputs):
        p.error('archive names collide')
    plans = []
    for source in a.inputs:
        data = index(source); target = a.output/(source.name+'.zip')
        validate_target([source], target)
        plans.append((target, data))
    for target, data in plans:
        print(target)
        if a.dry_run:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.zip', delete=False) as temp:
            temporary = Path(temp.name)
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
                for name, files in data.items():
                    for frame in sorted(files):
                        bundle.write(files[frame], str(Path(name)/files[frame].name))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
