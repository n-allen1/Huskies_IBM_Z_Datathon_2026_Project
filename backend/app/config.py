import os
from dataclasses import dataclass
from uuid import UUID, uuid5

APP_VERSION = "0.1.0"

# Stable namespace so every synthetic ID in the stand-in seed and the demo auth
# adapter is reproducible across runs and machines.
ID_NAMESPACE = UUID("6f1c7a52-3b0e-4d57-9a4e-5a1d2c0b9e11")
DEMO_TENANT_ID = uuid5(ID_NAMESPACE, "tenant:demo-county")


def stable_id(name: str) -> UUID:
    return uuid5(ID_NAMESPACE, name)


@dataclass(frozen=True)
class Settings:
    demo_mode: bool = False
    # How long a pending recommendation stays reviewable before it expires.
    recommendation_ttl_seconds: int = 300
    # Straight-line ETA assumption used until a routing engine or XGBoost model is wired in.
    assumed_speed_kmh: float = 40.0
    # "keyword" (rules only) or "embedding" (pretrained model, keyword fallback).
    classifier: str = "keyword"
    embedding_device: str = "cpu"  # "nnpa" runs the model on the Telum accelerator (falls back to cpu if unavailable)
    embedding_dtype: str = "float32"  # "float16" is the dtype the NNPA backend accelerates
    # Below this top similarity the model suggests no category. Chosen on dev set A and verified on held-out set B
    # (tools/classifier_eval.py): at >= 0.74 all 18 suggestions were correct on each set; 0.75 adds a safety buffer.
    embedding_min_similarity: float = 0.75
    embedding_min_margin: float = 0.02
    # "haversine" (straight-line) or "xgboost" (trained at start-up on synthetic travel times, straight-line fallback).
    eta_model: str = "haversine"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            demo_mode=os.getenv("DEMO_MODE", "false").lower() == "true",
            recommendation_ttl_seconds=int(os.getenv("RECOMMENDATION_TTL_SECONDS", "300")),
            assumed_speed_kmh=float(os.getenv("ASSUMED_SPEED_KMH", "40")),
            classifier=os.getenv("CLASSIFIER", "keyword").lower(),
            embedding_device=os.getenv("EMBEDDING_DEVICE", "cpu").lower(),
            embedding_dtype=os.getenv("EMBEDDING_DTYPE", "float32").lower(),
            embedding_min_similarity=float(os.getenv("EMBEDDING_MIN_SIMILARITY", "0.75")),
            embedding_min_margin=float(os.getenv("EMBEDDING_MIN_MARGIN", "0.02")),
            eta_model=os.getenv("ETA_MODEL", "haversine").lower(),
        )
