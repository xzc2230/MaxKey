#!/usr/bin/env python3
"""Render AWS Compose templates into their runtime locations."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deployment.aws_compose_renderer import write_rendered_file


def parse_value(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("--value must be in KEY=VALUE form")
    key, value = raw.split("=", 1)
    if not key:
        raise argparse.ArgumentTypeError("--value key must not be empty")
    return key, value


def build_values(overrides: list[tuple[str, str]]) -> dict[str, str]:
    values = dict(os.environ)
    values.update(overrides)
    return values


def default_paths() -> dict[str, Path]:
    compose_dir = Path(__file__).resolve().parents[1]
    repo_root = compose_dir.parents[1]
    return {
        "source_sql": repo_root / "sql" / "v4.2.0" / "maxkey.sql",
        "hardening_template": compose_dir / "mysql" / "init" / "01-hardening.sql.template",
        "env_template": compose_dir / ".env.example",
        "nginx_template": compose_dir / "nginx" / "conf.d" / "maxkey.conf.template",
        "output_root": Path("/srv/maxkey"),
        "compose_output_dir": compose_dir,
    }


def parser() -> argparse.ArgumentParser:
    paths = default_paths()
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--source-sql", type=Path, default=paths["source_sql"])
    argument_parser.add_argument("--hardening-template", type=Path, default=paths["hardening_template"])
    argument_parser.add_argument("--env-template", type=Path, default=paths["env_template"])
    argument_parser.add_argument("--nginx-template", type=Path, default=paths["nginx_template"])
    argument_parser.add_argument("--output-root", type=Path, default=paths["output_root"])
    argument_parser.add_argument("--compose-output-dir", type=Path, default=paths["compose_output_dir"])
    argument_parser.add_argument(
        "--value",
        action="append",
        default=[],
        type=parse_value,
        metavar="KEY=VALUE",
        help="Template value override. Environment variables are also available.",
    )
    return argument_parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    values = build_values(args.value)

    write_rendered_file(args.source_sql, args.output_root / "mysql" / "init" / "00-maxkey.sql", values)
    write_rendered_file(args.hardening_template, args.output_root / "mysql" / "init" / "01-hardening.sql", values)
    write_rendered_file(args.env_template, args.compose_output_dir / ".env", values)
    write_rendered_file(
        args.nginx_template,
        args.compose_output_dir / "nginx" / "conf.d" / "maxkey.conf",
        values,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
