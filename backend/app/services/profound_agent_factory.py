"""Create, wire, and publish Profound agents via the External API (LIVE)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from app.connectors.profound.client import ProfoundClient
from app.connectors.profound.errors import ProfoundError
from app.services.profound_agents import LIVE_RUN_META

log = structlog.get_logger(__name__)

DEFAULT_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "slug": "agt-citation-recovery",
        "name": "Citation Recovery Agent",
        "description": "Drafts citation recovery actions for AI search visibility (AEO SRE).",
        "system_prompt": "You are a citation recovery specialist for B2B SaaS AI search visibility.",
        "user_prompt": (
            "Marketing objective: {{objective_var}}\n"
            "Primary metric: {{metric_var}}\n"
            "Return exactly 3 bullet actions to recover official citations in LLM answers."
        ),
    },
    {
        "slug": "agt-claim-verifier",
        "name": "Technical Claim Verification Agent",
        "description": "Checks technical marketing claims against canonical product truth.",
        "system_prompt": "You verify technical claims; flag anything that cannot be substantiated.",
        "user_prompt": (
            "Hypothesis / claim to verify: {{objective_var}}\n"
            "Target URL (if any): {{metric_var}}\n"
            "Return: verified claim, risk level (low/medium/high), and one fix suggestion."
        ),
    },
    {
        "slug": "agt-competitive-copilot",
        "name": "Competitive Differentiation Copilot",
        "description": "Summarizes competitive gaps and differentiation angles for AEO campaigns.",
        "system_prompt": "You are a competitive intelligence copilot for enterprise SaaS.",
        "user_prompt": (
            "Campaign: {{objective_var}}\n"
            "Channels: {{metric_var}}\n"
            "Return a short competitor contrast table (3 rows) and one positioning headline."
        ),
    },
    {
        "slug": "agt-token-router",
        "name": "Model API Token Router",
        "description": "Routes prompts to cost-efficient model tiers for autonomous marketing ops.",
        "system_prompt": "You classify marketing automation tasks by complexity and cost tier.",
        "user_prompt": (
            "Task: {{objective_var}}\n"
            "Context: {{metric_var}}\n"
            "Return recommended tier (fast/deep), estimated tokens, and routing rationale in 2 sentences."
        ),
    },
)


@dataclass(frozen=True)
class PublishedAgent:
    id: str
    name: str
    slug: str
    status: str
    created: bool


async def resolve_profound_organization_id(client: ProfoundClient) -> str:
    resp = await client.list_categories()
    cats = resp.data if isinstance(resp.data, list) else []
    for cat in cats:
        if isinstance(cat, dict):
            org = cat.get("organization") or {}
            if isinstance(org, dict) and org.get("id"):
                return str(org["id"])
    raise ProfoundError("no Profound organization found for this API key", endpoint="/v1/org/categories")


def _llm_graph(
    *,
    objective_var: uuid.UUID,
    metric_var: uuid.UUID,
    llm_out: uuid.UUID,
    user_prompt: str,
    system_prompt: str,
) -> dict[str, Any]:
    llm_id = "llm-main"
    prompt = user_prompt.replace("{{objective_var}}", f"{{{objective_var}}}").replace(
        "{{metric_var}}", f"{{{metric_var}}}"
    )
    return {
        "nodes": [
            {
                "id": "start",
                "width": 200,
                "height": 100,
                "type": "start",
                "position": {"x": -100.0, "y": 0.0},
                "data": {"title": "Start"},
                "input_variables": [],
                "output_variables": [
                    {
                        "variable": {
                            "id": str(objective_var),
                            "name": "objective",
                            "data_type": {"kind": "primitive", "type": "string"},
                            "required": False,
                        },
                        "expected_output_id": "objective",
                    },
                    {
                        "variable": {
                            "id": str(metric_var),
                            "name": "primary_metric",
                            "data_type": {"kind": "primitive", "type": "string"},
                            "required": False,
                        },
                        "expected_output_id": "primary_metric",
                    },
                ],
            },
            {
                "id": llm_id,
                "width": 280,
                "height": 120,
                "type": "llm",
                "position": {"x": -100.0, "y": 180.0},
                "data": {
                    "title": "Generate",
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "model_parameters": {
                        "user_prompt": prompt,
                        "system_prompt": system_prompt,
                        "temperature": 0.2,
                    },
                },
                "input_variables": [
                    {"variable_id": str(objective_var), "required": False},
                    {"variable_id": str(metric_var), "required": False},
                ],
                "output_variables": [
                    {
                        "variable": {
                            "id": str(llm_out),
                            "name": "text",
                            "data_type": {"kind": "primitive", "type": "string"},
                            "required": True,
                        },
                        "expected_output_id": "text",
                    }
                ],
            },
            {
                "id": "end",
                "width": 200,
                "height": 100,
                "type": "end",
                "position": {"x": -100.0, "y": 375.0},
                "data": {
                    "title": "End",
                    "outputs": [
                        {
                            "key": "result",
                            "name": "result",
                            "type": "string",
                            "required": True,
                            "variable_id": str(llm_out),
                        }
                    ],
                },
                "input_variables": [{"variable_id": str(llm_out), "required": True}],
                "output_variables": [],
            },
        ],
        "edges": [
            {"id": "e-start-llm", "source": "start", "target": llm_id, "sourceHandle": "output", "targetHandle": "input"},
            {"id": "e-llm-end", "source": llm_id, "target": "end", "sourceHandle": "output", "targetHandle": "input"},
        ],
    }


async def create_and_publish_agent(
    client: ProfoundClient,
    *,
    organization_id: str,
    name: str,
    description: str,
    user_prompt: str,
    system_prompt: str,
) -> PublishedAgent:
    create = await client.create_agent(
        organization_id=organization_id,
        name=name,
        description=description,
    )
    agent = create.data if isinstance(create.data, dict) else {}
    agent_id = str(agent.get("id") or "")
    if not agent_id:
        raise ProfoundError("create agent returned no id", endpoint="POST /v1/agents")

    objective_var, metric_var, llm_out = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = _llm_graph(
        objective_var=objective_var,
        metric_var=metric_var,
        llm_out=llm_out,
        user_prompt=user_prompt,
        system_prompt=system_prompt,
    )
    updated = await client.update_agent_graph(agent_id, graph)
    detail = updated.data if isinstance(updated.data, dict) else {}
    validation = detail.get("validation") or {}
    if not validation.get("valid"):
        raise ProfoundError(
            f"agent graph invalid: {validation.get('issues')}",
            endpoint=f"PATCH /v1/agents/{agent_id}",
        )
    pub = await client.publish_agent(agent_id)
    pub_agent = pub.data if isinstance(pub.data, dict) else {}
    return PublishedAgent(
        id=agent_id,
        name=name,
        slug="",
        status=str(pub_agent.get("status") or "published"),
        created=True,
    )


async def bootstrap_default_agents(*, force: bool = False) -> dict[str, Any]:
    """Create + publish the four default AEO marketing agents if missing."""
    client = ProfoundClient()
    org_id: str | None = None
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        org_id = await resolve_profound_organization_id(client)
        existing: dict[str, dict[str, Any]] = {}
        for statuses in (["published"], ["draft"]):
            resp = await client.list_agents(limit=100, statuses=statuses)
            data = resp.data.get("data") if isinstance(resp.data, dict) else []
            for row in data or []:
                if isinstance(row, dict) and row.get("name"):
                    existing[str(row["name"]).lower()] = row

        for tpl in DEFAULT_TEMPLATES:
            name = tpl["name"]
            if not force and name.lower() in existing:
                row = existing[name.lower()]
                skipped.append(
                    {
                        "slug": tpl["slug"],
                        "name": name,
                        "id": row.get("id"),
                        "status": row.get("status"),
                        **LIVE_RUN_META,
                    }
                )
                continue
            try:
                pub = await create_and_publish_agent(
                    client,
                    organization_id=org_id,
                    name=name,
                    description=tpl["description"],
                    user_prompt=tpl["user_prompt"],
                    system_prompt=tpl["system_prompt"],
                )
                created.append(
                    {"slug": tpl["slug"], "name": pub.name, "id": pub.id, "status": pub.status, **LIVE_RUN_META}
                )
            except ProfoundError as exc:
                log.warning("profound.bootstrap_agent_failed", name=name, error=type(exc).__name__)
                errors.append({"slug": tpl["slug"], "name": name, "error": type(exc).__name__, "detail": str(exc)[:200]})
    finally:
        await client.aclose()

    return {
        **LIVE_RUN_META,
        "organization_id": org_id,
        "status": "ok" if not errors else "partial",
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


async def run_inputs_for_agent(client: ProfoundClient, agent_id: str, context: dict[str, Any]) -> dict[str, Any]:
    """Map campaign/experiment context onto Profound input variable UUIDs."""
    resp = await client.get_agent(agent_id)
    detail = resp.data if isinstance(resp.data, dict) else {}
    props = ((detail.get("schema") or {}).get("input") or {}).get("properties") or {}
    if not isinstance(props, dict) or not props:
        return {k: v for k, v in context.items() if v not in (None, "", [])}

    keys = list(props.keys())
    objective = (
        context.get("objective")
        or context.get("hypothesis")
        or context.get("name")
        or ""
    )
    metric = context.get("primary_metric") or context.get("target_url") or context.get("channels") or ""
    if isinstance(metric, list):
        metric = ", ".join(str(x) for x in metric)
    out: dict[str, Any] = {}
    if keys:
        out[keys[0]] = str(objective)[:4000]
    if len(keys) > 1:
        out[keys[1]] = str(metric)[:2000]
    return out
