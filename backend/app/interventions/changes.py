"""Proposed-change representation, section merge semantics, path safety and unified-diff preview."""
from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ChangeType = Literal["create", "update", "delete"]
PatchMode = Literal["replace", "upsert_section"]
ChangeKind = Literal["page_change", "github_pr", "manual_task", "observe"]
PAGE_KINDS = ("page_change", "github_pr")  # "github_pr" is the legacy spelling of a page change

MARKER_BEGIN = '<!-- aeo-sre:begin id="{id}" -->'
MARKER_END = '<!-- aeo-sre:end id="{id}" -->'
ALLOWED_EXTENSIONS = (".md", ".mdx", ".markdown", ".html", ".htm", ".txt", ".json", ".jsonld", ".yml", ".yaml")
MAX_FILE_BYTES = 200_000
MAX_FILES = 5


class FileChange(BaseModel):
    """One file in a proposed change.

    patch_mode="replace": `new_content` is the full file.
    patch_mode="upsert_section": `new_content` is ONE marker-delimited section that is replaced in (or appended to)
    the current file at apply time, so a stale proposal can never clobber unrelated edits.
    """

    path: str
    old_content: str | None = None
    new_content: str = ""
    change_type: ChangeType = "update"
    patch_mode: PatchMode = "replace"
    section_id: str | None = None

    @field_validator("path")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class ManualTask(BaseModel):
    """Task artifact for actions that never mutate an external system (outreach, structured data, observe)."""

    kind: str
    title: str
    body: str = ""
    recipient: str | None = None
    target_url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    checklist: list[str] = Field(default_factory=list)


class ProposedChange(BaseModel):
    kind: ChangeKind = "page_change"
    title: str | None = None
    files: list[FileChange] = Field(default_factory=list)
    target_url: str | None = None
    summary: str = ""
    generated_by: Literal["template", "llm"] = "template"
    fact_ids: list[str] = Field(default_factory=list)
    manual_task: ManualTask | None = None
    notes: list[str] = Field(default_factory=list)
    diff: str | None = None

    def with_diff(self) -> ProposedChange:
        return self.model_copy(update={"diff": build_diff(self.files) if self.files else None})

    def to_json(self) -> dict[str, Any]:
        return self.with_diff().model_dump(mode="json")


def section_markers(section_id: str) -> tuple[str, str]:
    return MARKER_BEGIN.format(id=section_id), MARKER_END.format(id=section_id)


def wrap_section(section_id: str, body: str) -> str:
    begin, end = section_markers(section_id)
    return f"{begin}\n{body.strip()}\n{end}\n"


def apply_change(old: str | None, fc: FileChange) -> str:
    """Final file content after applying `fc` on top of `old` (idempotent for upsert_section)."""
    if fc.change_type == "delete":
        return ""
    if fc.patch_mode == "replace" or not fc.section_id:
        return fc.new_content
    if old is None or not old.strip():
        return fc.new_content
    begin, end = section_markers(fc.section_id)
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
    block = fc.new_content if fc.new_content.endswith("\n") else fc.new_content + "\n"
    if pattern.search(old):
        return pattern.sub(lambda _m: block, old, count=1)
    return old.rstrip("\n") + "\n\n" + block


def _lines(text: str | None) -> list[str]:
    return [] if not text else text.splitlines(keepends=True)


def diff_file(fc: FileChange, *, context: int = 3) -> str:
    final = apply_change(fc.old_content, fc)
    a = _lines(fc.old_content)
    b = _lines(final)
    from_name = "/dev/null" if fc.change_type == "create" or fc.old_content is None else f"a/{fc.path}"
    to_name = "/dev/null" if fc.change_type == "delete" else f"b/{fc.path}"
    out = list(difflib.unified_diff(a, b, fromfile=from_name, tofile=to_name, n=context))
    if not out:
        return ""
    text = "".join(line if line.endswith("\n") else line + "\n" for line in out)
    header = f"diff --git a/{fc.path} b/{fc.path}\n"
    if fc.old_content is None and fc.change_type == "update":
        header += "# current file content unavailable at proposal time: section is appended/replaced at apply time\n"
    return header + text


def build_diff(change: ProposedChange | Iterable[FileChange] | Sequence[FileChange], *, context: int = 3) -> str:
    """Unified diff preview for a proposed change (or list of file changes)."""
    files = change.files if isinstance(change, ProposedChange) else list(change)
    return "".join(diff_file(f, context=context) for f in files)


def added_text(change: ProposedChange | Sequence[FileChange]) -> str:
    """Only the text a change introduces (diff additions), used for grounding checks."""
    files = change.files if isinstance(change, ProposedChange) else list(change)
    out: list[str] = []
    for fc in files:
        old = set(_lines(fc.old_content))
        out.extend(line for line in _lines(apply_change(fc.old_content, fc)) if line not in old)
    return "".join(out)


def validate_path(path: str, *, allowed_extensions: Sequence[str] = ALLOWED_EXTENSIONS) -> str | None:
    """Return a problem string if the path is unsafe to write through an automated PR, else None."""
    if not path or len(path) > 255:
        return "empty or too long path"
    if path.startswith("/") or "\\" in path or "\x00" in path:
        return "absolute or non-posix path"
    segments = path.split("/")
    if any(s in ("", ".", "..") for s in segments):
        return "path traversal or empty segment"
    if any(s.startswith(".") for s in segments):
        return "hidden / dot paths (e.g. .github, .env) are not writable"
    if not path.lower().endswith(tuple(allowed_extensions)):
        return f"extension not allowed (allowed: {', '.join(allowed_extensions)})"
    return None


def validate_change(change: ProposedChange) -> list[str]:
    problems: list[str] = []
    if change.kind not in PAGE_KINDS:
        return problems
    if not change.files:
        problems.append("no files in proposed change")
    if len(change.files) > MAX_FILES:
        problems.append(f"too many files ({len(change.files)} > {MAX_FILES})")
    seen: set[str] = set()
    for f in change.files:
        if (p := validate_path(f.path)) is not None:
            problems.append(f"{f.path or '<empty>'}: {p}")
        if f.path in seen:
            problems.append(f"{f.path}: duplicate path")
        seen.add(f.path)
        if f.change_type == "delete":
            problems.append(f"{f.path}: deletions are not permitted through automated execution")
        if len(f.new_content.encode()) > MAX_FILE_BYTES:
            problems.append(f"{f.path}: content too large")
        if f.patch_mode == "upsert_section" and not f.section_id:
            problems.append(f"{f.path}: upsert_section requires section_id")
        if f.change_type != "delete" and not f.new_content.strip():
            problems.append(f"{f.path}: empty content")
    return problems
