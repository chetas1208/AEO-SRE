"""FEVER / VitaminC loaders for the EvidenceRanker. Raw files are cached under ml/datasets/raw (gitignored).

Sources (HuggingFace Hub, downloaded as plain jsonl so no loading-script execution is needed):
  * `tals/vitaminc`                 -- VitaminC (revision-derived contrastive claim/evidence pairs)
  * `copenlu/fever_gold_evidence`   -- FEVER claims with gold / retrieved evidence sentences
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ml.features.text import normalize

RAW_DIR = Path(__file__).resolve().parent / "raw"
LABELS = ("SUPPORTS", "REFUTES", "NOT ENOUGH INFO")
LABEL_TO_ID = {name: i for i, name in enumerate(LABELS)}
CLASS_NAMES = ("support", "contradiction", "insufficient")

VITAMINC_REPO = "tals/vitaminc"
FEVER_REPO = "copenlu/fever_gold_evidence"
FILES = {
    ("vitaminc", "train"): (VITAMINC_REPO, "train.jsonl"),
    ("vitaminc", "val"): (VITAMINC_REPO, "dev.jsonl"),
    ("vitaminc", "test"): (VITAMINC_REPO, "test.jsonl"),
    ("fever", "train"): (FEVER_REPO, "train.jsonl"),
    ("fever", "val"): (FEVER_REPO, "valid.jsonl"),
    ("fever", "test"): (FEVER_REPO, "test.jsonl"),
}


@dataclass
class Example:
    uid: str
    claim: str
    passage: str
    label: int
    source: str  # "vitaminc" | "fever"
    title: str = ""
    group: str = ""  # claim-group id (case_id / claim hash)
    revision_type: str = ""  # vitaminc: real | synthetic
    revision_id: int = 0
    stale: int | None = None  # freshness label (vitaminc real, claims with >=2 revisions); None = unlabeled
    extra: dict[str, Any] = field(default_factory=dict)

    def meta(self) -> dict[str, Any]:
        return {"title": self.title}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_path(source: str, split: str) -> Path:
    repo, fname = FILES[(source, split)]
    return RAW_DIR / repo.replace("/", "__") / fname


def download(sources: tuple[str, ...] = ("vitaminc", "fever")) -> dict[str, dict[str, Any]]:
    """Fetch raw files if missing; return provenance (repo, hub commit sha, file sha256, rows)."""
    from huggingface_hub import HfApi, hf_hub_download

    prov: dict[str, dict[str, Any]] = {}
    api = HfApi()
    for (source, split), (repo, fname) in FILES.items():
        if source not in sources:
            continue
        target = local_path(source, split)
        if not target.exists():
            hf_hub_download(repo, fname, repo_type="dataset", local_dir=RAW_DIR / repo.replace("/", "__"))
        try:
            commit = api.dataset_info(repo).sha
        except Exception:  # offline with cached files is fine
            commit = None
        prov[f"{source}/{split}"] = {
            "repo": repo, "file": fname, "hub_commit": commit, "sha256": sha256_file(target),
            "bytes": target.stat().st_size,
        }
    return prov


def _iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_vitaminc(split: str, derive_freshness: bool = True) -> list[Example]:
    path = local_path("vitaminc", split)
    out: list[Example] = []
    for row in _iter_jsonl(path):
        label = LABEL_TO_ID.get(row["label"])
        if label is None or not row.get("evidence") or not row.get("claim"):
            continue
        try:
            rev = int(row.get("wiki_revision_id") or 0)
        except ValueError:
            rev = 0
        out.append(Example(
            uid=f"vitaminc-{row['unique_id']}", claim=normalize(row["claim"]),
            passage=normalize(row["evidence"]), label=label, source="vitaminc",
            title=normalize(row.get("page", "")), group=str(row.get("case_id", "")),
            revision_type=row.get("revision_type", ""), revision_id=rev,
        ))
    if derive_freshness:
        derive_freshness_labels(out)
    return out


def derive_freshness_labels(examples: list[Example]) -> None:
    """Revision-derived staleness label for VitaminC *real* pairs (in place).

    VitaminC cases hold one claim paired with the evidence sentence from two Wikipedia revisions
    (`unique_id` suffix _1/_3 = old revision, _2/_4 = new revision; `wiki_revision_id` is the new revision --
    spot-checked against live Wikipedia revision text, see docs/notes/handoff-a8.md). A pair is
    `stale` (1) when its passage is the OLD revision and the verdict for the same claim under the NEW
    revision differs (the claim flipped when the page changed); NEW-revision pairs are 0.
    Synthetic pairs and incomplete cases stay unlabeled (None).
    """
    by_claim: dict[tuple[str, str], dict[str, Example]] = defaultdict(dict)
    for ex in examples:
        if ex.revision_type != "real":
            continue
        try:
            k = int(ex.uid.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            continue
        by_claim[(ex.group, ex.claim)]["old" if k % 2 == 1 else "new"] = ex
    for pair in by_claim.values():
        if "old" in pair and "new" in pair:
            pair["new"].stale = 0
            pair["old"].stale = int(pair["old"].label != pair["new"].label)


def _fever_passage(evidence: list[list[Any]], max_sents: int = 3, max_chars: int = 1200) -> tuple[str, str]:
    sents, title = [], ""
    seen: set[str] = set()
    for item in evidence:
        if len(item) < 3 or not str(item[2]).strip():
            continue
        text = normalize(str(item[2]))
        if text in seen:
            continue
        seen.add(text)
        title = title or normalize(str(item[0]))
        sents.append(text)
        if len(sents) >= max_sents:
            break
    return " ".join(sents)[:max_chars], title


def load_fever(split: str) -> list[Example]:
    path = local_path("fever", split)
    out: list[Example] = []
    for row in _iter_jsonl(path):
        label = LABEL_TO_ID.get(row["label"])
        if label is None or not row.get("claim"):
            continue
        passage, title = _fever_passage(row.get("evidence") or [])
        if not passage:
            continue
        claim = normalize(row["claim"])
        out.append(Example(
            uid=f"fever-{row.get('id') or row.get('original_id')}", claim=claim, passage=passage,
            label=label, source="fever", title=title,
            group=str(row.get("original_id") or hashlib.md5(claim.encode()).hexdigest()),  # noqa: S324
        ))
    return out


def stratified_subset(
    examples: list[Example], n: int, seed: int = 13, key=lambda e: e.label
) -> list[Example]:
    """Sample up to n examples with equal allocation per stratum (spill over from small strata)."""
    rng = random.Random(seed)
    strata: dict[Any, list[Example]] = defaultdict(list)
    for ex in examples:
        strata[key(ex)].append(ex)
    for rows in strata.values():
        rng.shuffle(rows)
    picked: list[Example] = []
    remaining = n
    order = sorted(strata, key=lambda k: (len(strata[k]), str(k)))
    for i, k in enumerate(order):
        take = min(len(strata[k]), remaining // (len(order) - i))
        picked.extend(strata[k][:take])
        remaining -= take
    rng.shuffle(picked)
    return picked


def vitaminc_stratum(e: Example) -> tuple:
    return (e.label, e.revision_type)


def summarize(examples: list[Example]) -> dict[str, Any]:
    by_label: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    for e in examples:
        by_label[CLASS_NAMES[e.label]] += 1
        by_source[e.source] += 1
    stale = [e.stale for e in examples if e.stale is not None]
    return {
        "n": len(examples), "by_label": dict(by_label), "by_source": dict(by_source),
        "freshness_labeled": len(stale), "freshness_stale_rate": (sum(stale) / len(stale)) if stale else None,
    }


def to_dict(e: Example) -> dict[str, Any]:
    return asdict(e)
