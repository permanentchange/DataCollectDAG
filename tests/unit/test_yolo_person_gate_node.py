from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from data_collect_dag.models import NodeConfig, NodeResult
from data_collect_dag.nodes.vision import YoloPersonGateNode
from data_collect_dag.sample_context import SampleContext


class _FakeBoxes:
    def __init__(self, *, cls, conf, xyxy) -> None:
        self.cls = np.asarray(cls, dtype=np.float64)
        self.conf = np.asarray(conf, dtype=np.float64)
        self.xyxy = np.asarray(xyxy, dtype=np.float64)


class _FakeResult:
    def __init__(self, *, boxes, names=None) -> None:
        self.boxes = boxes
        self.names = names or {0: "person"}


class _FakeYOLO:
    results = []
    predict_error = None

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self.names = {0: "person", 2: "car"}

    def predict(self, **kwargs):
        if self.predict_error is not None:
            raise self.predict_error
        return list(self.results)


def _make_node(tmp_path: Path, dummy_session, monkeypatch, **config_overrides) -> YoloPersonGateNode:
    model_path = tmp_path / "person.pt"
    model_path.write_text("fake-model", encoding="utf-8")
    monkeypatch.setattr("data_collect_dag.nodes.vision._load_yolo_class", lambda: _FakeYOLO)
    config = {
        "model_path": str(model_path),
        "confidence_threshold": 0.25,
        "min_area_ratio": 0.1,
        "device": "cpu",
        "failure_policy": "skip_sample",
    }
    config.update(config_overrides)
    node = YoloPersonGateNode(
        NodeConfig("front_person_gate", "yolo_person_gate", {"image": "main_frame"}, {}, config),
        dummy_session,
    )
    node.setup()
    return node


def _make_sample(image_frame) -> SampleContext:
    sample = SampleContext(sample_id="sample-1")
    sample.put("main_frame", image_frame, "test")
    return sample


def test_yolo_person_gate_returns_ok_when_person_matches(tmp_path, dummy_session, image_frame, monkeypatch):
    _FakeYOLO.predict_error = None
    _FakeYOLO.results = [
        _FakeResult(boxes=_FakeBoxes(cls=[0], conf=[0.92], xyxy=[[0.0, 0.0, 4.0, 4.0]])),
    ]
    node = _make_node(tmp_path, dummy_session, monkeypatch)
    outcome = node.run(_make_sample(image_frame))
    assert outcome.status == NodeResult.OK
    assert outcome.metadata["passed"] is True
    assert outcome.metadata["person_count"] == 1


def test_yolo_person_gate_skips_when_no_detections(tmp_path, dummy_session, image_frame, monkeypatch):
    _FakeYOLO.predict_error = None
    _FakeYOLO.results = []
    node = _make_node(tmp_path, dummy_session, monkeypatch)
    outcome = node.run(_make_sample(image_frame))
    assert outcome.status == NodeResult.SKIP_SAMPLE
    assert outcome.reason == "front_person_gate_person_not_detected"


def test_yolo_person_gate_skips_when_confidence_below_threshold(tmp_path, dummy_session, image_frame, monkeypatch):
    _FakeYOLO.predict_error = None
    _FakeYOLO.results = [
        _FakeResult(boxes=_FakeBoxes(cls=[0], conf=[0.24], xyxy=[[0.0, 0.0, 6.0, 4.0]])),
    ]
    node = _make_node(tmp_path, dummy_session, monkeypatch)
    outcome = node.run(_make_sample(image_frame))
    assert outcome.status == NodeResult.SKIP_SAMPLE
    assert outcome.metadata["person_count"] == 0
    assert outcome.metadata["max_confidence"] == pytest.approx(0.24)


def test_yolo_person_gate_skips_when_area_below_threshold(tmp_path, dummy_session, image_frame, monkeypatch):
    _FakeYOLO.predict_error = None
    _FakeYOLO.results = [
        _FakeResult(boxes=_FakeBoxes(cls=[0], conf=[0.95], xyxy=[[0.0, 0.0, 1.0, 1.0]])),
    ]
    node = _make_node(tmp_path, dummy_session, monkeypatch, min_area_ratio=0.2)
    outcome = node.run(_make_sample(image_frame))
    assert outcome.status == NodeResult.SKIP_SAMPLE
    assert outcome.metadata["max_area_ratio"] == pytest.approx(round(1.0 / 24.0, 6))


def test_yolo_person_gate_skips_when_inference_fails(tmp_path, dummy_session, image_frame, monkeypatch):
    _FakeYOLO.results = []
    _FakeYOLO.predict_error = RuntimeError("boom")
    node = _make_node(tmp_path, dummy_session, monkeypatch)
    outcome = node.run(_make_sample(image_frame))
    assert outcome.status == NodeResult.SKIP_SAMPLE
    assert outcome.reason == "front_person_gate_inference_failed"
    assert outcome.metadata["passed"] is False


def test_yolo_person_gate_setup_fails_when_model_path_missing(tmp_path, dummy_session):
    node = YoloPersonGateNode(
        NodeConfig(
            "front_person_gate",
            "yolo_person_gate",
            {"image": "main_frame"},
            {},
            {
                "model_path": str(tmp_path / "missing.pt"),
                "confidence_threshold": 0.25,
                "min_area_ratio": 0.1,
                "failure_policy": "skip_sample",
            },
        ),
        dummy_session,
    )
    with pytest.raises(ValueError, match="missing yolo model_path"):
        node.setup()
