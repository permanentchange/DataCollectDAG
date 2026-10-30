"""Height BEV using shared point-cloud loading."""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image
from tools.lib.cloud_io import load_point_cloud


def height_grid(points: np.ndarray, bounds: tuple, resolution: float, zmin: float, zmax: float) -> np.ndarray:
    xmin, xmax, ymin, ymax = bounds
    if not np.isfinite((*bounds, resolution, zmin, zmax)).all() or resolution <= 0 or xmin >= xmax or ymin >= ymax or zmin >= zmax:
        raise ValueError("finite increasing bounds and positive resolution required")
    width, height = int(np.ceil((xmax-xmin)/resolution)), int(np.ceil((ymax-ymin)/resolution))
    mask = np.isfinite(points).all(axis=1)
    mask &= (points[:, 0] >= xmin) & (points[:, 0] <= xmax) & (points[:, 1] >= ymin) & (points[:, 1] <= ymax)
    mask &= (points[:, 2] >= zmin) & (points[:, 2] <= zmax)
    selected = points[mask]
    grid = np.full(width*height, -np.inf)
    px = np.clip(((selected[:, 0]-xmin)/resolution).astype(int), 0, width-1)
    py = np.clip(((ymax-selected[:, 1])/resolution).astype(int), 0, height-1)
    np.maximum.at(grid, py*width+px, selected[:, 2])
    occupied = np.isfinite(grid)
    image = np.zeros(width*height, dtype=np.uint8)
    image[occupied] = np.rint(np.clip((grid[occupied]-zmin)/(zmax-zmin), 0, 1)*255).astype(np.uint8)
    return image.reshape(height, width)


def pcd2bev(pcd_file, save_img_path, bev_range=(-40, 40, -40, 40), resolution=0.15, z_min=-2.5, z_max=3.0):
    image = height_grid(load_point_cloud(Path(pcd_file)).points, bev_range, resolution, z_min, z_max)
    output = Path(save_img_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image).save(output)
    print(f"saved -> {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    for name, default in [('xmin', -20.), ('xmax', 30.), ('ymin', -20.), ('ymax', 20.), ('res', .1), ('zmin', -2.), ('zmax', 3.)]:
        parser.add_argument('--'+name, type=float, default=default)
    a = parser.parse_args()
    if not a.input.exists():
        parser.error('input does not exist')
    files = [a.input] if a.input.is_file() else sorted(a.input.glob('*.pcd'))
    if not files:
        parser.error('no PCD files found')
    failed = 0
    for path in files:
        try:
            target = a.output if a.input.is_file() else a.output/(path.stem+'_bev.png')
            pcd2bev(path, target, (a.xmin, a.xmax, a.ymin, a.ymax), a.res, a.zmin, a.zmax)
        except (OSError, ValueError) as exc:
            failed += 1
            print(f"failed {path}: {exc}")
    print(f"processed={len(files)} failed={failed}")
    return int(bool(failed))
