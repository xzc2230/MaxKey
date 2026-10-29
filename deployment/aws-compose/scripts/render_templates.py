#!/usr/bin/env python3
"""Render AWS Compose templates into their runtime locations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deployment.aws_compose_renderer import TemplateOutput, render_outputs, write_outputs_atomically


def parse_value(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("--value must be in KEY=VALUE form")
    key, value = raw.split("=", 1)
    if not key:
        raise argparse.ArgumentTypeError("--value key must not be empty")
    return key, value


def build_values(overrides: list[tuple[str, str]]) -> dict[str, str]:
    values = {}
    values.update(overrides)
    return values


def read_values_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"values file not found: {path}")
    if sys.platform != "win32":
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            raise ValueError("values file must be mode 0600")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid values file line: {line_number}")
        key, value = line.split("=", 1)
        if not key:
            raise ValueError(f"invalid values file line: {line_number}")
        values[key] = value
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
    argument_parser.add_argument(
        "--compose-output-dir",
        type=Path,
        default=None,
        help="Compatibility override for .env and rendered Nginx output location.",
    )
    argument_parser.add_argument("--values-file", type=Path)
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
    try:
        values = read_values_file(args.values_file) if args.values_file else {}
        values.update(build_values(args.value))
        compose_output_dir = args.compose_output_dir or args.output_root

        outputs = render_outputs(
            [
                TemplateOutput(args.source_sql, args.output_root / "mysql" / "init" / "00-maxkey.sql", is_sql=True),
                TemplateOutput(
                    args.hardening_template,
                    args.output_root / "mysql" / "init" / "01-hardening.sql",
                    is_sql=True,
                ),
                TemplateOutput(args.env_template, compose_output_dir / ".env"),
                TemplateOutput(args.nginx_template, compose_output_dir / "nginx" / "conf.d" / "maxkey.conf"),
            ],
            values,
        )
        write_outputs_atomically(outputs)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
