"""Feature extraction for the EvidenceRanker (pure functions)."""

from ml.features.extract import (
    EMBEDDING_FEATURE,
    FEATURE_NAMES,
    LEXICAL_FEATURES,
    META_FEATURES,
    extract_features,
    feature_vector,
    is_owned,
    source_age_days,
)
from ml.features.text import select_window

__all__ = [
    "EMBEDDING_FEATURE", "FEATURE_NAMES", "LEXICAL_FEATURES", "META_FEATURES", "extract_features",
    "feature_vector", "is_owned", "select_window", "source_age_days",
]
