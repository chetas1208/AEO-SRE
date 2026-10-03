"""Prompt clustering: deterministic hard constraints first, embedding similarity second, persisted versions.

* Hard constraint: prompts only ever share a cluster when their (normalized topic, language) match. Embeddings never
  merge across topics.
* Within a constraint group, a prompt joins the most similar existing cluster when cosine >= `threshold`; otherwise it
  starts a new cluster. The default embedder is a deterministic lexical hashing embedder (NOT semantic); a real
  embedding model can be injected through the `Embedder` protocol and is recorded in the version stamp.
* Membership never changes silently: an already-assigned prompt keeps its cluster across versions. Only an explicit
  `reassign=True` run may move members, and every move is listed in the version's `changes`. Prompts that disappear
  upstream are kept and flagged `retired`.
* Every change of membership produces a NEW version with a content hash; an unchanged membership produces none.

Persistence is the `settings` table (`clusters.current.<org>` and `clusters.history.<org>`) until a dedicated table is
approved (docs/notes/schema-requests.md).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

_TOKEN = re.compile(r"[a-z0-9]+")
DEFAULT_THRESHOLD = 0.55
HISTORY_LIMIT = 20


class Embedder(Protocol):
    id: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Deterministic bag of hashed unigrams+bigrams, L2-normalized. Lexical similarity only."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.id = f"hashing-v1-{dim}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for t in texts:
            toks = _TOKEN.findall((t or "").lower())
            grams = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
            v = [0.0] * self.dim
            for g in grams:
                h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
                v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def _norm(s: str | None) -> str:
    return " ".join(_TOKEN.findall((s or "").lower())) or "general"


def constraint_key(prompt: dict[str, Any]) -> str:
    return f"{_norm(prompt.get('topic'))}|{_norm(prompt.get('language')) if prompt.get('language') else '-'}"


@dataclass
class ClusterVersion:
    version: int
    embedder: str
    threshold: float
    created_at: str
    clusters: dict[str, dict[str, Any]]  # cluster_key -> {constraint, topic, members: [...], retired: [...]}
    changes: list[dict[str, Any]] = field(default_factory=list)
    content_hash: str = ""

    def membership(self) -> dict[str, str]:
        return {pid: ck for ck, c in self.clusters.items() for pid in c["members"] + c.get("retired", [])}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "embedder": self.embedder, "threshold": self.threshold,
            "created_at": self.created_at, "clusters": self.clusters, "changes": self.changes,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ClusterVersion:
        return cls(**{k: d[k] for k in ("version", "embedder", "threshold", "created_at", "clusters")},
                   changes=list(d.get("changes") or []), content_hash=d.get("content_hash", ""))


def _hash(clusters: dict[str, dict[str, Any]]) -> str:
    canon = {k: {"m": sorted(c["members"]), "r": sorted(c.get("retired", []))} for k, c in sorted(clusters.items())}
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()[:16]


def build_version(
    prompts: Sequence[dict[str, Any]],
    prior: ClusterVersion | None = None,
    *,
    embedder: Embedder | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    reassign: bool = False,
    now: datetime | None = None,
) -> tuple[ClusterVersion | None, list[dict[str, Any]]]:
    """Return (new_version_or_None_if_unchanged, changes). `prompts`: dicts with id, text, topic[, language]."""
    emb = embedder or HashingEmbedder()
    texts = {str(p["id"]): str(p.get("text") or "") for p in prompts}
    vecs = dict(zip(texts, emb.embed(list(texts.values())), strict=True))
    current = {str(p["id"]): p for p in prompts}
    prior_member = prior.membership() if prior and prior.embedder == emb.id else (prior.membership() if prior else {})
    clusters: dict[str, dict[str, Any]] = {}
    changes: list[dict[str, Any]] = []

    def new_cluster(ck: str, p: dict[str, Any]) -> str:
        key = f"{ck}#{sum(1 for k in clusters if k.startswith(ck + '#')) + 1}"
        clusters[key] = {"constraint": ck, "topic": p.get("topic") or "General", "members": [], "retired": []}
        return key

    # 1) keep every existing assignment (stable), flag retired
    if prior:
        for key, c in prior.clusters.items():
            clusters[key] = {"constraint": c["constraint"], "topic": c["topic"], "members": [], "retired": [r for r in c.get("retired", []) if r not in current]}
            for pid in c["members"]:
                if pid in current and not reassign:
                    clusters[key]["members"].append(pid)
                elif pid not in current:
                    clusters[key]["retired"].append(pid)
                    changes.append({"prompt_id": pid, "change": "retired", "cluster": key})
    # 2) place new (or, when reassign, every) prompts
    placed = {pid for c in clusters.values() for pid in c["members"]}
    for pid in sorted(current):
        if pid in placed:
            continue
        p = current[pid]
        ck = constraint_key(p)
        best, best_sim = None, -1.0
        for key, c in clusters.items():
            if c["constraint"] != ck or not c["members"]:
                continue
            sim = max(_cos(vecs[pid], vecs[m]) for m in c["members"] if m in vecs)
            if sim > best_sim:
                best, best_sim = key, sim
        if best is None or best_sim < threshold:
            best = new_cluster(ck, p)
        clusters[best]["members"].append(pid)
        was = prior_member.get(pid)
        changes.append({"prompt_id": pid, "change": "added" if was is None else "moved", "cluster": best,
                        **({"from": was} if was and was != best else {}), "similarity": round(max(best_sim, 0.0), 3)})
        if was == best:
            changes.pop()
    clusters = {k: v for k, v in clusters.items() if v["members"] or v["retired"]}
    h = _hash(clusters)
    if prior and prior.content_hash == h:
        return None, []
    ver = ClusterVersion(
        version=(prior.version + 1) if prior else 1, embedder=emb.id, threshold=threshold,
        created_at=(now or datetime.now(UTC)).isoformat(), clusters=clusters, changes=changes, content_hash=h,
    )
    return ver, changes


async def record_cluster_version(
    session: Any, org_id: uuid.UUID, prompts: Sequence[dict[str, Any]], *, reassign: bool = False,
    embedder: Embedder | None = None, now: datetime | None = None,
) -> ClusterVersion | None:
    """Persist a new cluster version when membership changed. Returns the new version or None. Flushes only."""
    from app.models.core import Setting

    if not prompts:
        return None
    cur_key, hist_key = f"clusters.current.{org_id}", f"clusters.history.{org_id}"
    row = await session.get(Setting, cur_key)
    prior = ClusterVersion.from_dict(row.value) if row is not None and row.value else None
    ver, _ = build_version(prompts, prior, embedder=embedder, reassign=reassign, now=now)
    if ver is None:
        return None
    from app.services.ingestion import _put_setting

    await _put_setting(session, cur_key, ver.to_dict())
    hrow = await session.get(Setting, hist_key)
    entry = {"version": ver.version, "content_hash": ver.content_hash, "created_at": ver.created_at,
             "changes": ver.changes[:200]}
    versions = [*((hrow.value or {}).get("versions", []) if hrow is not None else []), entry][-HISTORY_LIMIT:]
    await _put_setting(session, hist_key, {"versions": versions})
    await session.flush()
    return ver


async def current_cluster_version(session: Any, org_id: uuid.UUID) -> dict[str, Any] | None:
    from app.models.core import Setting

    row = await session.get(Setting, f"clusters.current.{org_id}")
    if row is None or not row.value:
        return None
    return {"version": row.value.get("version"), "content_hash": row.value.get("content_hash"),
            "embedder": row.value.get("embedder")}
