from __future__ import annotations

from data_collect_dag.app import AppRuntime, build_parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    runtime = AppRuntime(config_path=args.config, pipeline_name=args.pipeline)
    return runtime.run()


if __name__ == "__main__":
    raise SystemExit(main())

