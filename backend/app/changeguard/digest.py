"""Action digest: the exact change a human (or an agent) was told about, as a hash.

    digest = sha256(canonical_json({org, agent_id, target (normalized), action_type, claims (normalized, sorted),
                                    text/diff hash, experiment_context}))

`proposal_digest` is the same without `experiment_context` (it identifies the request: idempotency + stale-check);
`action_digest` adds the active-experiment context at check time (it is what an approval binds to). Any change to the
proposal changes both. Pure functions: no I/O.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

_WS = re.compile(r"\s+")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_claim(claim: str) -> str:
    """NFKC + whitespace collapsed + case-folded + trailing sentence punctuation stripped."""
    s = unicodedata.normalize("NFKC", str(claim))
    s = _WS.sub(" ", s).strip().casefold()
    return s.rstrip(".;! ").strip()


def normalize_claims(claims: Iterable[str] | None) -> list[str]:
    """Normalized, de-duplicated, sorted (order of the agent's claims never changes the digest)."""
    return sorted({c for c in (normalize_claim(x) for x in (claims or []) if x is not None) if c})


def _norm_text(p: str | None) -> str:
    return unicodedata.normalize("NFC", p or "").replace("\r\n", "\n").replace("\r", "\n")


def text_hash(text: str | None, diff: str | None = None) -> str | None:
    """Hash of the proposed text and diff (newline-normalized). None when neither is given."""
    if not text and not diff:
        return None
    return sha256_hex(canonical_json([_norm_text(text), _norm_text(diff)]))


def proposal_dict(*, org_id: Any, agent_id: str, target_key: str, action_type: str, claims: list[str],
                  content_hash: str | None) -> dict[str, Any]:
    return {"org": str(org_id), "agent_id": agent_id, "target": target_key, "action_type": action_type,
            "claims": claims, "content_hash": content_hash}


def proposal_digest(proposal: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(proposal))


def normalize_context(context: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Active experiments touching the target at check time: ids + states only, sorted by id."""
    rows = [{"experiment_id": str(c["experiment_id"]), "status": str(c["status"])} for c in (context or [])]
    return sorted(rows, key=lambda r: (r["experiment_id"], r["status"]))


def action_digest(proposal: dict[str, Any], context: Iterable[dict[str, Any]] | None) -> str:
    return sha256_hex(canonical_json({**proposal, "experiment_context": normalize_context(context)}))
