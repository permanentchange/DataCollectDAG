from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np

from data_collect_dag.models import LocalizationFrame, NodeOutcome, NodeResult, PointCloudFrame
from data_collect_dag.nodes.base import BaseNode
from data_collect_dag.ros_messages import ensure_pointcloud_frame_parsed
from data_collect_dag.sample_context import MissingContextKeyError
from data_collect_dag.transforms import (
    build_transform_matrix,
    invert_transform,
    quaternion_to_rotation_matrix,
    quaternion_to_rotation_matrix_many,
    transform_points,
)


class StartNode(BaseNode):
    def run(self, sample) -> NodeOutcome:
        return self.ok()


class EndNode(BaseNode):
    def run(self, sample) -> NodeOutcome:
        return self.ok()


class TimeSyncNode(BaseNode):
    def run(self, sample) -> NodeOutcome:
        reference_frame = sample.get(self.inputs["reference_frame"])
        source = str(self.config["source"])
        strategy = str(self.config.get("strategy", "nearest"))
        required = bool(self.config.get("required", False))
        wait_timeout_ms = int(self.config.get("wait_timeout_ms", 0))
        max_time_diff_ms = float(self.config.get("max_time_diff_ms", 50.0))
        if strategy == "nearest":
            frame = self.session.cache.wait_nearest(source, reference_frame.meta.source_timestamp_ns, max_time_diff_ms, wait_timeout_ms)
        elif strategy == "latest_before":
            frame = self.session.cache.query_latest_before(source, reference_frame.meta.source_timestamp_ns, max_time_diff_ms / 1000.0)
        else:
            raise ValueError(f"unsupported sync strategy: {strategy}")
        if frame is None:
            reason = f"{self.node_id}_not_matched"
            self.logger.debug(
                "sync miss sample_id=%s node_id=%s source=%s ref_ts=%s max_diff_ms=%s timeout_ms=%s",
                sample.sample_id,
                self.node_id,
                source,
                reference_frame.meta.source_timestamp_ns,
                max_time_diff_ms,
                wait_timeout_ms,
            )
            sample.metadata.setdefault("missing_inputs", {})[source] = reason
            if required:
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=reason)
            return self.ok(warnings=[reason])
        sample.put(self.outputs["matched_frame"], frame, self.node_id)
        offset_ms = (frame.meta.source_timestamp_ns - reference_frame.meta.source_timestamp_ns) / 1_000_000.0
        sample.metadata.setdefault("time_offsets_ms", {})[source] = offset_ms
        self.logger.debug(
            "sync match sample_id=%s node_id=%s source=%s ref_ts=%s matched_ts=%s offset_ms=%.3f",
            sample.sample_id,
            self.node_id,
            source,
            reference_frame.meta.source_timestamp_ns,
            frame.meta.source_timestamp_ns,
            offset_ms,
        )
        return self.ok()


class MultiTimeSyncNode(BaseNode):
    def run(self, sample) -> NodeOutcome:
        reference_frame = sample.get(self.inputs["reference_frame"])
        required = bool(self.config.get("required", False))
        sources = list(self.config.get("sources") or [])
        strategy = str(self.config.get("strategy", "nearest"))
        wait_timeout_ms = int(self.config.get("wait_timeout_ms", 0))
        max_time_diff_ms = float(self.config.get("max_time_diff_ms", 50.0))
        deadline = time.monotonic() + max(0.0, wait_timeout_ms / 1000.0)
        warnings: List[str] = []
        missing_required: List[str] = []
        for source in sources:
            if strategy == "nearest":
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000.0))
                frame = self.session.cache.wait_nearest(
                    source,
                    reference_frame.meta.source_timestamp_ns,
                    max_time_diff_ms,
                    remaining_ms,
                )
            elif strategy == "latest_before":
                frame = self.session.cache.query_latest_before(source, reference_frame.meta.source_timestamp_ns, max_time_diff_ms / 1000.0)
            else:
                raise ValueError(f"unsupported sync strategy: {strategy}")
            if frame is None:
                reason = f"{self.node_id}_{source}_not_matched"
                self.logger.debug(
                    "multi-sync miss sample_id=%s node_id=%s source=%s ref_ts=%s max_diff_ms=%s remaining_ms=%s",
                    sample.sample_id,
                    self.node_id,
                    source,
                    reference_frame.meta.source_timestamp_ns,
                    max_time_diff_ms,
                    remaining_ms if strategy == "nearest" else wait_timeout_ms,
                )
                sample.metadata.setdefault("missing_inputs", {})[source] = reason
                warnings.append(reason)
                if required:
                    missing_required.append(source)
                continue
            output_key = self.outputs.get(source, source)
            sample.put(output_key, frame, self.node_id)
            offset_ms = (frame.meta.source_timestamp_ns - reference_frame.meta.source_timestamp_ns) / 1_000_000.0
            sample.metadata.setdefault("time_offsets_ms", {})[source] = offset_ms
            self.logger.debug(
                "multi-sync match sample_id=%s node_id=%s source=%s matched_ts=%s offset_ms=%.3f",
                sample.sample_id,
                self.node_id,
                source,
                frame.meta.source_timestamp_ns,
                offset_ms,
            )
        if missing_required:
            self.logger.debug(
                "multi-sync required miss sample_id=%s node_id=%s missing=%s",
                sample.sample_id,
                self.node_id,
                missing_required,
            )
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_missing_required")
        return self.ok(warnings=warnings)


class LocalizationRangeNode(BaseNode):
    def run(self, sample) -> NodeOutcome:
        reference_frame = sample.get(self.inputs["reference_frame"])
        source = str(self.config["source"])
        before_ms = int(self.config.get("before_ms", 200))
        after_ms = int(self.config.get("after_ms", 200))
        frames = self.session.cache.query_range(
            source,
            reference_frame.meta.source_timestamp_ns - before_ms * 1_000_000,
            reference_frame.meta.source_timestamp_ns + after_ms * 1_000_000,
        )
        if not frames:
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_no_localization")
        sample.put(self.outputs["matched_frames"], frames, self.node_id)
        return self.ok()


class PointCloudRadiusCropNode(BaseNode):
    def run(self, sample) -> NodeOutcome:
        required = bool(self.config.get("required", True))
        try:
            cloud = sample.get(self.inputs["pointcloud"])
        except MissingContextKeyError:
            if required:
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_missing_input")
            return self.ok()
        cloud = ensure_pointcloud_frame_parsed(cloud)
        max_radius_m = float(self.config.get("max_radius_m", 30.0))
        assert cloud.points_xyz is not None
        distances = np.linalg.norm(cloud.points_xyz[:, :2], axis=1) if cloud.points_xyz.size else np.empty((0,), dtype=np.float64)
        mask = distances <= max_radius_m
        cropped = cloud.replace(
            points_xyz=cloud.points_xyz[mask],
            point_timestamps_ns=cloud.point_timestamps_ns[mask] if cloud.point_timestamps_ns is not None else None,
        )
        sample.metadata.setdefault("pointcloud_processing", {})[self.outputs["pointcloud"]] = {
            "stage": "sensor_radius_crop",
            "applied": True,
            "max_radius_m": max_radius_m,
            "points_before": int(cloud.points_xyz.shape[0]),
            "points_after": int(cropped.points_xyz.shape[0]),
            "frame_name": cropped.frame_name,
        }
        if cropped.points_xyz.shape[0] == 0 and required:
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_empty_after_crop")
        sample.put(self.outputs["pointcloud"], cropped, self.node_id)
        return self.ok()


class PointCloudMotionCompensationNode(BaseNode):
    def setup(self) -> None:
        sensor_topic = str(self.config["sensor_topic"])
        if sensor_topic not in self.session.calibration["pointclouds"]:
            raise ValueError(f"missing pointcloud extrinsics for {sensor_topic}")

    def run(self, sample) -> NodeOutcome:
        required = bool(self.config.get("required", True))
        try:
            cloud: PointCloudFrame = sample.get(self.inputs["pointcloud"])
        except MissingContextKeyError:
            if required:
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_missing_input")
            return self.ok()
        cloud = ensure_pointcloud_frame_parsed(cloud)
        reference_frame = sample.get(self.inputs["reference_frame"])
        localization_source = str(self.config.get("localization_source", "localization"))
        sensor2base = np.asarray(self.session.calibration["pointclouds"][str(self.config["sensor_topic"])], dtype=np.float64)
        assert cloud.points_xyz is not None
        if cloud.point_timestamps_ns is None or cloud.point_timestamps_ns.size == 0:
            if required:
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_missing_timestamps")
            return self.ok(warnings=[f"{self.node_id}_missing_timestamps"])
        finite_mask = np.isfinite(cloud.point_timestamps_ns.astype(np.float64))
        valid_times = cloud.point_timestamps_ns[finite_mask]
        if valid_times.size == 0:
            if required:
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_invalid_timestamps")
            return self.ok(warnings=[f"{self.node_id}_invalid_timestamps"])
        start_ns = int(min(valid_times.min(), reference_frame.meta.source_timestamp_ns))
        end_ns = int(max(valid_times.max(), reference_frame.meta.source_timestamp_ns))
        margin_ns = int(float(self.config.get("localization_margin_ms", 200.0)) * 1_000_000)
        localization_frames = self._wait_localization_coverage(
            localization_source=localization_source,
            start_ns=start_ns,
            end_ns=end_ns,
            margin_ns=margin_ns,
        )
        if len(localization_frames) < 2:
            self.logger.debug(
                "motion miss sample_id=%s node_id=%s reason=no_pose_coverage frames=%s range=[%s,%s]",
                sample.sample_id,
                self.node_id,
                len(localization_frames),
                start_ns,
                end_ns,
            )
            if required:
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_no_pose_coverage")
            return self.ok(warnings=[f"{self.node_id}_no_pose_coverage"])
        traj_times = np.asarray([frame.meta.source_timestamp_ns for frame in localization_frames], dtype=np.int64)
        if start_ns < int(traj_times[0]) or end_ns > int(traj_times[-1]):
            self.logger.debug(
                "motion coverage gap sample_id=%s node_id=%s traj=[%s,%s] need=[%s,%s]",
                sample.sample_id,
                self.node_id,
                int(traj_times[0]),
                int(traj_times[-1]),
                start_ns,
                end_ns,
            )
            if required:
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_no_pose_coverage")
            return self.ok(warnings=[f"{self.node_id}_no_pose_coverage"])
        traj_positions = np.asarray([frame.position_xyz for frame in localization_frames], dtype=np.float64)
        traj_quats = np.asarray([frame.quaternion_xyzw for frame in localization_frames], dtype=np.float64)
        base_positions, base_quats = _interpolate_trajectory(traj_times, traj_positions, traj_quats, valid_times)
        ref_position, ref_quat = _interpolate_trajectory(
            traj_times,
            traj_positions,
            traj_quats,
            np.asarray([reference_frame.meta.source_timestamp_ns], dtype=np.int64),
        )
        points_sensor = cloud.points_xyz[finite_mask]
        rotation_bs = sensor2base[:3, :3]
        translation_bs = sensor2base[:3, 3]
        base_points = points_sensor @ rotation_bs.T + translation_bs[None, :]
        world_rotations = quaternion_to_rotation_matrix_many(base_quats)
        world_points = np.einsum("nij,nj->ni", world_rotations, base_points) + base_positions
        ref_rotation_wb = quaternion_to_rotation_matrix(ref_quat[0])
        ref_translation_wb = ref_position[0]
        ref_base_points = (world_points - ref_translation_wb[None, :]) @ ref_rotation_wb
        compensated = (ref_base_points - translation_bs[None, :]) @ rotation_bs
        output_points = cloud.points_xyz.copy()
        output_points[finite_mask] = compensated
        compensated_cloud = cloud.replace(points_xyz=output_points)
        sample.put(self.outputs["pointcloud"], compensated_cloud, self.node_id)
        sample.metadata["motion_compensation"] = {
            "applied": True,
            "reference_time_ns": int(reference_frame.meta.source_timestamp_ns),
            "sensor_topic": str(self.config["sensor_topic"]),
            "points_compensated": int(output_points.shape[0]),
        }
        self.logger.debug(
            "motion compensated sample_id=%s node_id=%s sensor_topic=%s points=%s localization_frames=%s",
            sample.sample_id,
            self.node_id,
            self.config["sensor_topic"],
            int(output_points.shape[0]),
            len(localization_frames),
        )
        return self.ok()

    def _wait_localization_coverage(
        self,
        *,
        localization_source: str,
        start_ns: int,
        end_ns: int,
        margin_ns: int,
    ) -> List[LocalizationFrame]:
        wait_timeout_ms = int(self.config.get("pose_wait_timeout_ms", 0))
        deadline = time.monotonic() + max(0.0, wait_timeout_ms / 1000.0)
        while True:
            frames: List[LocalizationFrame] = self.session.cache.query_range(
                localization_source,
                start_ns - margin_ns,
                end_ns + margin_ns,
            )
            if len(frames) >= 2:
                traj_times = np.asarray([frame.meta.source_timestamp_ns for frame in frames], dtype=np.int64)
                if start_ns >= int(traj_times[0]) and end_ns <= int(traj_times[-1]):
                    return frames
            if time.monotonic() >= deadline:
                return frames
            time.sleep(0.01)


class PointCloudTransformNode(BaseNode):
    def setup(self) -> None:
        sensor_topic = str(self.config["sensor_topic"])
        if sensor_topic not in self.session.calibration["pointclouds"]:
            raise ValueError(f"missing pointcloud extrinsics for {sensor_topic}")

    def run(self, sample) -> NodeOutcome:
        required = bool(self.config.get("required", True))
        try:
            cloud: PointCloudFrame = sample.get(self.inputs["pointcloud"])
        except MissingContextKeyError:
            if required:
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_missing_input")
            return self.ok()
        cloud = ensure_pointcloud_frame_parsed(cloud)
        assert cloud.points_xyz is not None
        sensor2base = np.asarray(self.session.calibration["pointclouds"][str(self.config["sensor_topic"])], dtype=np.float64)
        transformed = transform_points(cloud.points_xyz, sensor2base)
        transformed_cloud = cloud.replace(points_xyz=transformed, frame_name="base")
        sample.metadata.setdefault("pointcloud_processing", {})[self.outputs["pointcloud"]] = {
            "stage": "transform_to_base",
            "applied": True,
            "sensor_topic": str(self.config["sensor_topic"]),
            "target_frame": "base",
            "points_after": int(transformed_cloud.points_xyz.shape[0]),
        }
        sample.put(self.outputs["pointcloud"], transformed_cloud, self.node_id)
        self.logger.debug(
            "pointcloud transformed sample_id=%s node_id=%s sensor_topic=%s points=%s",
            sample.sample_id,
            self.node_id,
            self.config["sensor_topic"],
            int(transformed_cloud.points_xyz.shape[0]),
        )
        return self.ok()


class PointCloudRoiCropNode(BaseNode):
    def run(self, sample) -> NodeOutcome:
        required = bool(self.config.get("required", True))
        try:
            cloud: PointCloudFrame = sample.get(self.inputs["pointcloud"])
        except MissingContextKeyError:
            if required:
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_missing_input")
            return self.ok()
        cloud = ensure_pointcloud_frame_parsed(cloud)
        assert cloud.points_xyz is not None
        front_m = float(self.config.get("front_m", 30.0))
        rear_m = float(self.config.get("rear_m", 20.0))
        side_m = float(self.config.get("side_m", 15.0))
        points = cloud.points_xyz
        mask = (
            (points[:, 0] <= front_m)
            & (points[:, 0] >= -rear_m)
            & (np.abs(points[:, 1]) <= side_m)
        )
        cropped = cloud.replace(
            points_xyz=points[mask],
            point_timestamps_ns=cloud.point_timestamps_ns[mask] if cloud.point_timestamps_ns is not None else None,
        )
        sample.metadata.setdefault("pointcloud_processing", {})[self.outputs["pointcloud"]] = {
            "stage": "base_roi_crop",
            "applied": True,
            "front_m": front_m,
            "rear_m": rear_m,
            "side_m": side_m,
            "points_before": int(points.shape[0]),
            "points_after": int(cropped.points_xyz.shape[0]),
            "frame_name": cropped.frame_name,
        }
        if cropped.points_xyz.shape[0] == 0 and required:
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_empty_after_crop")
        sample.put(self.outputs["pointcloud"], cropped, self.node_id)
        return self.ok()


class AggregatePointCloudNode(BaseNode):
    def run(self, sample) -> NodeOutcome:
        required_keys = list(self.config.get("required_inputs") or [])
        optional_keys = list(self.config.get("optional_inputs") or [])
        points_list = []
        ref_cloud: Optional[PointCloudFrame] = None
        for key in required_keys:
            if not sample.has(key):
                return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_{key}_missing")
            cloud: PointCloudFrame = ensure_pointcloud_frame_parsed(sample.get(key))
            ref_cloud = cloud if ref_cloud is None else ref_cloud
            points_list.append(cloud.points_xyz)
        for key in optional_keys:
            if sample.has(key):
                cloud = ensure_pointcloud_frame_parsed(sample.get(key))
                ref_cloud = cloud if ref_cloud is None else ref_cloud
                points_list.append(cloud.points_xyz)
        points = np.vstack([item for item in points_list if item is not None and item.size > 0]) if any(item is not None and item.size > 0 for item in points_list) else np.empty((0, 3), dtype=np.float64)
        if ref_cloud is None or points.shape[0] == 0:
            return NodeOutcome(status=NodeResult.SKIP_SAMPLE, reason=f"{self.node_id}_empty")
        aggregated_cloud = ref_cloud.replace(points_xyz=points, frame_name="base")
        sample.metadata["aggregated_pointcloud"] = {
            "required_inputs": required_keys,
            "optional_inputs_used": [key for key in optional_keys if sample.has(key)],
            "points_after": int(aggregated_cloud.points_xyz.shape[0]),
            "frame_name": aggregated_cloud.frame_name,
        }
        sample.put(self.outputs["pointcloud"], aggregated_cloud, self.node_id)
        self.logger.debug(
            "pointcloud aggregated sample_id=%s node_id=%s points=%s optional_inputs_used=%s",
            sample.sample_id,
            self.node_id,
            int(aggregated_cloud.points_xyz.shape[0]),
            sample.metadata["aggregated_pointcloud"]["optional_inputs_used"],
        )
        return self.ok()


def _interpolate_trajectory(
    times_ns: np.ndarray,
    positions: np.ndarray,
    quaternions: np.ndarray,
    query_ns: np.ndarray,
):
    upper = np.searchsorted(times_ns, query_ns, side="right")
    upper = np.clip(upper, 1, times_ns.shape[0] - 1)
    lower = upper - 1
    t0 = times_ns[lower].astype(np.float64)
    t1 = times_ns[upper].astype(np.float64)
    ratio = np.zeros(query_ns.shape[0], dtype=np.float64)
    mask = t1 > t0
    ratio[mask] = (query_ns[mask].astype(np.float64) - t0[mask]) / (t1[mask] - t0[mask])
    interpolated_pos = positions[lower] + (positions[upper] - positions[lower]) * ratio[:, None]
    from data_collect_dag.transforms import slerp_quaternion_xyzw

    interpolated_quat = slerp_quaternion_xyzw(quaternions[lower], quaternions[upper], ratio)
    return interpolated_pos, interpolated_quat
