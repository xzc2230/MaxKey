"""Strict template rendering helpers for the AWS Compose deployment."""

from __future__ import annotations

import re
from pathlib import Path


PLACEHOLDER = re.compile(r"__([A-Z0-9_]+)__")


def render_text(template: str, values: dict[str, str]) -> str:
    """Replace __PLACEHOLDER__ tokens and reject incomplete renders."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"unresolved placeholder: {key}")
        return values[key]

    rendered = PLACEHOLDER.sub(replace, template)
    if PLACEHOLDER.search(rendered):
        raise ValueError("rendered output still contains placeholders")
    return rendered


def render_file(template_path: Path, values: dict[str, str]) -> str:
    return render_text(template_path.read_text(encoding="utf-8"), values)


def write_rendered_file(template_path: Path, output_path: Path, values: dict[str, str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_file(template_path, values), encoding="utf-8", newline="\n")
