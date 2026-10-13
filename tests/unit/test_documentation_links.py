"""Keep maintained Markdown links and package metadata consistent."""

import json
import re
import shlex
from pathlib import Path

import yaml

from data_collect_dag.app import build_parser
from data_collect_dag.config import load_app_config
from data_collect_dag.demo_templates import available_car_ids, read_templates
from data_collect_dag.models import StatusSnapshot
from data_collect_dag.session import NODE_TYPES


ROOT = Path(__file__).resolve().parents[2]
LINK_PATTERN = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]+\")?\)")


def _documents():
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / "demo").rglob("*.md"))
    return files


def test_local_markdown_links_exist_and_code_fences_are_balanced():
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        assert text.count("```") % 2 == 0, str(document)
        for link in LINK_PATTERN.findall(text):
            target = link.split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "#")):
                continue
            assert (document.parent / target).is_file(), (
                "{} links to missing file {}".format(document, link)
            )


def test_python_distribution_uses_maintained_readme():
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'readme = "README.md"' in metadata


def test_documentation_has_no_removed_examples_or_historical_calibration_names():
    removed_names = [
        "xtreme1_demo.yaml", "person_1fps", "lidar_frontcamera_1fps",
        "is_fisheye", "distortion_model", "_cali.json", "calibration/cali.json",
    ]
    for document in _documents():
        text = document.read_text(encoding="utf-8")
        for name in removed_names:
            assert name not in text, "{} mentions removed interface {}".format(document, name)


def test_readme_pipeline_table_matches_current_demo():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    table = text.split("| Pipeline in `demo/cfg.yaml`", 1)[1].split("\n\n", 1)[0]
    documented = set(re.findall(r"^\| `([^`]+)` \|", table, re.MULTILINE))
    demo = yaml.safe_load((ROOT / "demo/cfg.yaml").read_text(encoding="utf-8"))
    assert documented == set(demo["pipelines"])
    for name, pipeline in demo["pipelines"].items():
        row = next(line for line in table.splitlines() if line.startswith("| `{}` |".format(name)))
        inference = any(node["type"] == "yolo_gate" for node in pipeline["nodes"])
        assert row.endswith("| YOLO |" if inference else "| No |")


def test_readme_cli_examples_parse_and_select_bundled_cars():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    cars = available_car_ids(read_templates())
    examples = 0
    for block in re.findall(r"```bash\n(.*?)```", text, re.DOTALL):
        for line in block.splitlines():
            if not line.startswith("data_collect_dag "):
                continue
            try:
                args = build_parser().parse_args(shlex.split(line)[1:])
            except SystemExit as error:
                # --help is itself a valid documented command.
                assert error.code == 0
                continue
            assert args.config is not None or args.init_demo is not None
            if args.car_id is not None:
                assert args.init_demo is not None and args.car_id.lower() in cars
            examples += 1
    assert examples >= 5


def test_architecture_node_table_matches_runtime_registration():
    text = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    table = text.split("| 类别 | 节点类型 | 功能 |", 1)[1].split("\n\n", 1)[0]
    nodes = set()
    for row in table.splitlines():
        columns = row.split("|")
        if len(columns) >= 4:
            nodes.update(re.findall(r"`([^`]+)`", columns[2]))
    assert nodes == set(NODE_TYPES)


def test_ros_status_example_matches_public_snapshot_fields():
    text = (ROOT / "docs/ros_external_system_architecture.md").read_text(encoding="utf-8")
    example = json.loads(re.search(r"```json\n(.*?)```", text, re.DOTALL).group(1))
    assert set(example) == set(StatusSnapshot().to_dict())


def test_demo_default_calibration_reference_exists():
    demo = yaml.safe_load((ROOT / "demo/cfg.yaml").read_text(encoding="utf-8"))
    path = demo["calibration"]["path"]
    assert (ROOT / path).is_file()
    assert path in (ROOT / "README.md").read_text(encoding="utf-8")


def test_architecture_yaml_example_is_valid(tmp_path):
    text = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    example = re.search(r"```yaml\n(.*?)```", text, re.DOTALL).group(1)
    path = tmp_path / "documented.yaml"
    path.write_text(example, encoding="utf-8")
    config = load_app_config(path)
    assert config.pipelines["example"].main_source == "camera"
