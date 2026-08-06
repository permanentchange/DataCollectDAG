import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CALI_PARSER_PATH = ROOT / "tools" / "cali_parser.py"


def load_cali_parser():
    spec = importlib.util.spec_from_file_location("cali_parser", CALI_PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_output_path_defaults_to_vehicle_cali_json():
    parser = load_cali_parser()

    assert parser.resolve_output_path(Path("/tmp/vehicle"), None) == Path("/tmp/vehicle/cali.json")


def test_resolve_output_path_expands_explicit_output_path():
    parser = load_cali_parser()

    assert parser.resolve_output_path(Path("/tmp/vehicle"), Path("~/cali.json")) == Path.home() / "cali.json"
