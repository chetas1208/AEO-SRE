"""LLM-assisted patch generation, constrained to the selected action and to supplied facts.

The deterministic template defines WHICH files may be touched and the structure; the LLM may only rewrite the
section body. Output is validated (pydantic schema, path whitelist, claim citations, number/URL/acronym grounding);
any violation or LLM failure falls back to the template and is recorded in `notes`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError

from app.connectors.llm.gateway import ModelPurpose, ModelRequest
from app.connectors.llm.registry import get_prompt
from app.domain.enums import ActionType
from app.interventions.changes import FileChange, ProposedChange, wrap_section
from app.interventions.facts import FactBase, check_claims, ungrounded_tokens
from app.interventions.llm import LLMClient, PatchDraft
from app.interventions.templates import PlanContext, TemplateResult

log = structlog.get_logger()
LLM_ACTIONS = {
    ActionType.UPDATE_EXISTING_PAGE, ActionType.CREATE_FAQ, ActionType.CREATE_CANONICAL_PAGE,
    ActionType.CREATE_COMPARISON_CONTENT,
}
_PROMPT = get_prompt("intervention_drafter_v2")
PROMPT_VERSION = _PROMPT.name
SYSTEM = _PROMPT.system


@dataclass
class PatchOutcome:
    change: ProposedChange | None
    reason: str | None
    used_llm: bool
    llm_rejected: str | None = None


def _schema() -> dict:
    return PatchDraft.model_json_schema()


def _prompt_parts(action: ActionType, ctx: PlanContext, base: ProposedChange, fb: FactBase) -> tuple[dict, dict]:
    """(trusted task fields, untrusted data). Facts and the template body derive from crawled pages: DATA only."""
    facts = [{"id": f.id, "kind": f.kind, "text": f.text, "source": f.url} for f in fb.facts
             if f.id in set(base.fact_ids)]
    trusted = {"selected_action": action.value, "organization": ctx.org_name, "capability_heading": ctx.capability,
               "incident": ctx.incident_title, "allowed_files": [f.path for f in base.files]}
    data = {"facts": facts, "template_body_for_reference": base.files[0].new_content[:3000] if base.files else ""}
    return trusted, data


def _prompt(action: ActionType, ctx: PlanContext, base: ProposedChange, fb: FactBase) -> str:
    trusted, data = _prompt_parts(action, ctx, base, fb)
    return json.dumps({**trusted, **data}, indent=2)


def validate_draft(raw: dict | PatchDraft, base: ProposedChange, fb: FactBase, ctx: PlanContext,
                   action: ActionType | None = None) -> tuple[PatchDraft | None, str | None]:
    try:
        draft = raw if isinstance(raw, PatchDraft) else PatchDraft.model_validate(raw)
    except ValidationError as exc:
        return None, f"schema validation failed: {exc.errors()[0].get('msg')}"
    if action is not None and draft.action != action.value:  # policy chooses the action; the model only drafts it
        return None, f"draft action {draft.action!r} does not match the selected action {action.value!r}"
    allowed = {f.path for f in base.files}
    if {f.path for f in draft.files} != allowed:
        return None, f"draft touches files outside the allowed set {sorted(allowed)}"
    allowed_ids = set(base.fact_ids)
    claims = [c.model_dump() for c in draft.claims]
    for c in claims:
        if any(i not in allowed_ids for i in c["fact_ids"]):
            return None, "claim cites a fact that was not supplied"
    problems = check_claims(claims, fb)
    if problems:
        return None, problems[0]
    corpus = fb.corpus + "\n" + "\n".join(ctx.old_contents.values()) + f"\n{ctx.org_name} {ctx.capability} {ctx.org_domain or ''}"
    for f in draft.files:
        if "aeo-sre:" in f.new_content or "<script" in f.new_content.lower():
            return None, "draft contains section markers or script content"
        bad = ungrounded_tokens(f.new_content, corpus)
        if bad:
            return None, f"ungrounded tokens in draft: {bad[:5]}"
    return draft, None


async def generate_patch(action: ActionType, ctx: PlanContext, template: TemplateResult, fb: FactBase,
                         llm: LLMClient | None) -> PatchOutcome:
    base = template.change
    if base is None or action not in LLM_ACTIONS or llm is None or not base.files:
        return PatchOutcome(base, template.reason, used_llm=False)
    try:
        if hasattr(llm, "generate_structured"):  # ModelGateway
            trusted, data = _prompt_parts(action, ctx, base, fb)
            resp = await llm.generate_structured(ModelRequest(
                purpose=ModelPurpose.INTERVENTION_DRAFT, system=SYSTEM, input=json.dumps(trusted, indent=2),
                untrusted={"facts": json.dumps(data["facts"], indent=2),
                           "template_body_for_reference": data["template_body_for_reference"]},
                prompt_version=PROMPT_VERSION, response_schema=PatchDraft, metadata={"action": action.value},
            ), PatchDraft)
            raw: Any = resp.parsed
        else:  # legacy duck-typed client
            raw = await llm.complete_json(system=SYSTEM, user=_prompt(action, ctx, base, fb), schema=_schema())
    except Exception as exc:  # noqa: BLE001  # LLM down: deterministic template, never block the pipeline
        log.warning("patch.llm_unavailable", error=str(exc))
        base = base.model_copy(update={"notes": [*base.notes, f"LLM unavailable ({type(exc).__name__}); used template"]})
        return PatchOutcome(base, None, used_llm=False, llm_rejected=f"unavailable: {exc}")
    draft, why = validate_draft(raw, base, fb, ctx, action)
    if draft is None:
        base = base.model_copy(update={"notes": [*base.notes, f"LLM draft rejected ({why}); used template"]})
        return PatchOutcome(base, None, used_llm=False, llm_rejected=why)
    by_path = {f.path: f for f in draft.files}
    files: list[FileChange] = []
    for tf in base.files:
        body = by_path[tf.path].new_content
        files.append(tf.model_copy(update={"new_content": wrap_section(ctx.section_id, body)}))
    change = base.model_copy(update={
        "files": files, "title": draft.title, "summary": draft.summary, "generated_by": "llm",
        "notes": [*base.notes, f"content written by LLM ({PROMPT_VERSION}), validated against cited facts"],
    })
    return PatchOutcome(change, None, used_llm=True)
