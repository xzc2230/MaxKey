"""Strict template rendering helpers for the AWS Compose deployment."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse


PLACEHOLDER = re.compile(r"__([A-Z0-9_]+)__")
PLACEHOLDER_BYTES = re.compile(rb"__([A-Z0-9_]+)__")
BCRYPT = re.compile(r"^\$2[aby]\$(0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}$")


@dataclass(frozen=True)
class TemplateOutput:
    template_path: Path
    output_path: Path
    is_sql: bool = False


@dataclass(frozen=True)
class RenderedOutput:
    output_path: Path
    content: bytes


def placeholders_in_bytes(template: bytes) -> set[str]:
    return {match.group(1).decode("ascii") for match in PLACEHOLDER_BYTES.finditer(template)}


def render_text(template: str, values: dict[str, str]) -> str:
    """Replace __PLACEHOLDER__ tokens and reject incomplete renders."""

    return render_bytes(template.encode("utf-8"), values).decode("utf-8")


def render_bytes(template: bytes, values: dict[str, str]) -> bytes:
    """Replace ASCII placeholders while preserving every other byte."""

    def replace(match: re.Match[bytes]) -> bytes:
        key = match.group(1)
        key = key.decode("ascii")
        if key not in values:
            raise ValueError(f"unresolved placeholder: {key}")
        return values[key].encode("utf-8")

    rendered = PLACEHOLDER_BYTES.sub(replace, template)
    if PLACEHOLDER_BYTES.search(rendered):
        raise ValueError("rendered output still contains placeholders")
    return rendered


def render_file(template_path: Path, values: dict[str, str]) -> bytes:
    return render_bytes(template_path.read_bytes(), values)


def validate_values(values: dict[str, str], declared: set[str], sql_keys: set[str]) -> None:
    unknown = sorted(set(values) - declared)
    if unknown:
        raise ValueError(f"unknown template value: {unknown[0]}")

    missing = sorted(declared - set(values))
    if missing:
        raise ValueError(f"unresolved placeholder: {missing[0]}")

    for key in sorted(declared):
        value = values[key]
        if value == "":
            raise ValueError(f"invalid value for {key}: empty")
        if key == "MAXKEY_ADMIN_BCRYPT" and not BCRYPT.fullmatch(value):
            raise ValueError("invalid value for MAXKEY_ADMIN_BCRYPT")
        if key == "PUBLIC_BASE_URL":
            validate_public_base_url(value)
        if key in sql_keys and any(char in value for char in ("'", '"', "\\", "\n", "\r")):
            raise ValueError(f"invalid SQL value for {key}")


def validate_public_base_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid value for PUBLIC_BASE_URL")
    if value.endswith("/") or any(char.isspace() for char in value) or any(char in value for char in ("'", '"', "\\")):
        raise ValueError("invalid value for PUBLIC_BASE_URL")


def render_outputs(templates: list[TemplateOutput], values: dict[str, str]) -> list[RenderedOutput]:
    template_bytes = [(template, template.template_path.read_bytes()) for template in templates]
    declared: set[str] = set()
    sql_keys: set[str] = set()
    for template, content in template_bytes:
        placeholders = placeholders_in_bytes(content)
        declared.update(placeholders)
        if template.is_sql:
            sql_keys.update(placeholders)

    validate_values(values, declared, sql_keys)
    return [
        RenderedOutput(template.output_path, render_bytes(content, values))
        for template, content in template_bytes
    ]


def write_outputs_atomically(outputs: list[RenderedOutput]) -> None:
    old_umask = os.umask(0o077) if os.name != "nt" else None
    temp_paths: list[Path] = []
    try:
        for output in outputs:
            output.output_path.parent.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(output.output_path.parent, 0o700)

            fd, temp_name = tempfile.mkstemp(
                prefix=f".{output.output_path.name}.",
                suffix=".tmp",
                dir=output.output_path.parent,
            )
            temp_path = Path(temp_name)
            temp_paths.append(temp_path)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(output.content)
                if os.name != "nt":
                    os.chmod(temp_path, 0o600)
                os.replace(temp_path, output.output_path)
                temp_paths.remove(temp_path)
            except Exception:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
    finally:
        if old_umask is not None:
            os.umask(old_umask)
        for temp_path in temp_paths:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def write_rendered_file(template_path: Path, output_path: Path, values: dict[str, str]) -> None:
    outputs = render_outputs([TemplateOutput(template_path, output_path)], values)
    write_outputs_atomically(outputs)
