import argparse
import os
import numpy as np
import open3d as o3d
from PIL import Image


def pcd2bev(
    pcd_file: str,
    save_img_path: str,
    bev_range=(-40, 40, -40, 40),
    resolution=0.15,
    z_min=-2.5,
    z_max=3.0
):
    pcd = o3d.io.read_point_cloud(pcd_file)
    points = np.asarray(pcd.points)
    if len(points) == 0:
        raise RuntimeError(f"点云为空: {pcd_file}")

    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    xmin, xmax, ymin, ymax = bev_range

    mask = (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax) & (z >= z_min) & (z <= z_max)
    x, y, z = x[mask], y[mask], z[mask]

    img_w = int((xmax - xmin) / resolution)
    img_h = int((ymax - ymin) / resolution)

    px = ((x - xmin) / resolution).astype(np.int32)
    py = ((ymax - y) / resolution).astype(np.int32)

    px = np.clip(px, 0, img_w - 1)
    py = np.clip(py, 0, img_h - 1)

    idx = py * img_w + px
    flat_bev = np.zeros(img_h * img_w, dtype=np.float32)
    np.maximum.at(flat_bev, idx, z)
    bev = flat_bev.reshape(img_h, img_w)

    bev_norm = (bev - z_min) / (z_max - z_min)
    bev_norm = (bev_norm * 255).astype(np.uint8)

    img = Image.fromarray(bev_norm)
    img.save(save_img_path)
    print(f"saved -> {save_img_path}")


def main():
    parser = argparse.ArgumentParser(description="pcd2bev: pcd file/folder to BEV image(s)")
    parser.add_argument("input", help="输入：单个pcd文件 或者 pcd文件夹")
    parser.add_argument("output", help="输出：单张图片路径 或者 输出文件夹")

    # 可选参数
    parser.add_argument("--xmin", type=float, default=-20.0)
    parser.add_argument("--xmax", type=float, default=30.0)
    parser.add_argument("--ymin", type=float, default=-20.0)
    parser.add_argument("--ymax", type=float, default=20.0)
    parser.add_argument("--res", type=float, default=0.1, help="resolution m/px")
    parser.add_argument("--zmin", type=float, default=-2.0)
    parser.add_argument("--zmax", type=float, default=3.0)

    args = parser.parse_args()

    bev_range = (args.xmin, args.xmax, args.ymin, args.ymax)

    input_path = args.input
    output_path = args.output

    if os.path.isfile(input_path):
        # 单文件模式
        pcd2bev(
            pcd_file=input_path,
            save_img_path=output_path,
            bev_range=bev_range,
            resolution=args.res,
            z_min=args.zmin,
            z_max=args.zmax
        )

    elif os.path.isdir(input_path):
        # 文件夹批量模式
        os.makedirs(output_path, exist_ok=True)
        pcd_files = [f for f in os.listdir(input_path) if f.lower().endswith(".pcd")]
        pcd_files.sort()
        if not pcd_files:
            print("目录下没有找到pcd文件")
            return

        for fname in pcd_files:
            in_pcd = os.path.join(input_path, fname)
            base_name = os.path.splitext(fname)[0]
            out_img = os.path.join(output_path, f"{base_name}_bev.png")
            try:
                pcd2bev(
                    pcd_file=in_pcd,
                    save_img_path=out_img,
                    bev_range=bev_range,
                    resolution=args.res,
                    z_min=args.zmin,
                    z_max=args.zmax
                )
            except Exception as e:
                print(f"跳过 {fname}, error: {e}")
    else:
        raise FileNotFoundError(f"输入路径不存在: {input_path}")


if __name__ == "__main__":
    main()
