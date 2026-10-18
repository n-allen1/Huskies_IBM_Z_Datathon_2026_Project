"""Incident classification with a pretrained embedding model (PyTorch), with a deterministic fallback.

Nothing here is trained by us. A pretrained sentence-embedding model (IBM Granite Embedding 30M English,
Apache-2.0) maps the narrative and a few example descriptions per category into the same vector space;
the category with the most similar example wins. The output is only a *suggestion*: a dispatcher must
confirm it, and requirements are derived from the confirmed category, never from this score.

The matching and the fallback wrapper are plain Python so they are testable without torch. Only
`GraniteEncoder` imports torch/transformers, lazily, so the API still starts (on the keyword classifier)
where they are missing or the model cannot be loaded.
"""

import logging
import math
import threading
from dataclasses import replace
from typing import Protocol

from .inference import ClassificationResult, Classifier
from .models import ClassificationSource, IncidentCategory

log = logging.getLogger("dispatchz.ai")

MODEL_ID = "ibm-granite/granite-embedding-30m-english"
# Commit of `main` when this was written; pinned so the demo does not change under us.
MODEL_REVISION = "9b5b096411652ec1189c68fcfb90d0a82c5b45af"

_C = IncidentCategory
# Short example descriptions per category, written for this project. Not training data.
PROTOTYPES: dict[IncidentCategory, list[str]] = {
    _C.structure_fire: [
        "a house is on fire with flames and heavy smoke coming from the roof",
        "apartment building fire, residents evacuating, smoke in the hallway",
        "kitchen fire spreading to the walls of a home",
    ],
    _C.vehicle_fire: [
        "a car is burning on the side of the road",
        "truck engine caught fire and is fully engulfed",
        "vehicle fire in a parking lot",
    ],
    _C.wildland_fire: [
        "grass fire spreading across a field toward homes",
        "brush fire on a hillside with wind pushing it",
        "wildfire smoke visible near the forest",
    ],
    _C.medical_cardiac: [
        "man has chest pain and is sweating, possible heart attack",
        "patient collapsed, not breathing, no pulse",
        "unresponsive person, cardiac arrest, CPR needed",
    ],
    _C.medical_trauma: [
        "person fell from a ladder with a serious head injury",
        "gunshot or stab wound with heavy bleeding",
        "severe injury with a broken leg after a fall",
    ],
    _C.medical_other: [
        "elderly person feels dizzy and weak and needs an ambulance",
        "child with a high fever and vomiting",
        "seizure, diabetic emergency or suspected overdose",
    ],
    _C.traffic_collision: [
        "two cars collided at an intersection",
        "vehicle rolled over on the highway with people inside",
        "motorcycle crash with injuries on the road",
    ],
    _C.technical_rescue: [
        "worker trapped in a trench after a collapse",
        "person stuck in a confined space or fallen into a well",
        "hiker stranded on a cliff needing a rope rescue",
    ],
    _C.hazmat: [
        "chemical spill with strong fumes at a warehouse",
        "natural gas leak smell inside a building",
        "toxic substance leaking from a tanker truck",
    ],
    _C.assault: [
        "someone is being beaten in a fight outside a bar",
        "a person was attacked and injured by an assailant",
        "armed assault in progress",
    ],
    _C.burglary: [
        "someone broke into my house while we were out",
        "intruder trying to enter through a window at night",
        "store was burglarized and the door forced open",
    ],
    _C.domestic_disturbance: [
        "loud yelling and things breaking next door, a couple arguing",
        "domestic dispute between family members",
        "neighbor reports violence in an apartment",
    ],
    _C.welfare_check: [
        "please check on an elderly neighbor who has not been seen for days",
        "worried relative asks for a welfare check",
        "person not answering the door, concerned about their well-being",
    ],
}


class Encoder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return one L2-normalized vector per text."""


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class EmbeddingClassifier:
    def __init__(self, encoder: Encoder, *, min_similarity: float = 0.30, temperature: float = 0.05):
        self._encoder = encoder
        self._min_similarity = min_similarity
        self._temperature = temperature
        self._lock = threading.Lock()
        self._prototypes: list[tuple[IncidentCategory, list[float]]] | None = None
        self.error: str | None = None

    # -- lifecycle: loading the model can take seconds, so it happens off the request path ------
    def warm_up(self) -> None:
        with self._lock:
            if self._prototypes is not None:
                return
            try:
                pairs = [(c, t) for c, texts in PROTOTYPES.items() for t in texts]
                vectors = self._encoder.encode([t for _, t in pairs])
                self._prototypes = [(c, v) for (c, _), v in zip(pairs, vectors)]
                self.error = None
                log.info("embedding classifier ready (%s)", self.model_version)
            except Exception as exc:  # any load/inference failure must degrade, not crash
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("embedding classifier unavailable: %s", self.error)

    def ready(self) -> bool:
        return self._prototypes is not None

    @property
    def model_version(self) -> str:
        return getattr(self._encoder, "version", MODEL_ID)

    # -- classification ---------------------------------------------------------------------------
    def classify(self, text: str) -> ClassificationResult:
        if not self.ready():
            raise RuntimeError(self.error or "model is still loading")
        vector = self._encoder.encode([text])[0]
        best_per_category: dict[IncidentCategory, float] = {}
        for category, proto in self._prototypes:
            sim = _dot(vector, proto)
            if sim > best_per_category.get(category, -2.0):
                best_per_category[category] = sim
        ranked = sorted(best_per_category.items(), key=lambda kv: kv[1], reverse=True)
        (top, top_sim), (runner_up, runner_sim) = ranked[0], ranked[1]

        # Softmax over per-category similarity. It orders and scales the scores; it is NOT a calibrated probability.
        scaled = [s / self._temperature for _, s in ranked]
        peak = max(scaled)
        weights = [math.exp(s - peak) for s in scaled]
        top_score = weights[0] / sum(weights)

        notes = [
            f"Most similar to the example descriptions of '{top.value}' ({top_sim:.2f}); runner-up '{runner_up.value}' ({runner_sim:.2f}).",
            "The score is an uncalibrated similarity measure, not a probability; no priority is suggested.",
        ]
        if top_sim < self._min_similarity:
            notes.append(f"Best similarity is below {self._min_similarity:.2f}: no category is suggested.")
            return ClassificationResult(
                category=IncidentCategory.other, source=ClassificationSource.ml_inferred,
                model_version=self.model_version, confidence=None, limitations=notes, top_similarity=top_sim,
                margin=top_sim - runner_sim,
            )
        return ClassificationResult(
            category=top, source=ClassificationSource.ml_inferred, model_version=self.model_version,
            confidence=round(top_score, 4), limitations=notes, top_similarity=top_sim,
            margin=top_sim - runner_sim,
        )


class FallbackClassifier:
    """Use the ML classifier when it is ready and healthy; otherwise the keyword rules, saying so.
    A model that is missing, still loading, or fails must never block intake."""

    def __init__(self, primary: EmbeddingClassifier, fallback: Classifier):
        self._primary = primary
        self._fallback = fallback
        self._last_error: str | None = None

    def warm_up(self) -> None:
        self._primary.warm_up()

    def classify(self, text: str) -> ClassificationResult:
        if self._primary.ready():
            try:
                result = self._primary.classify(text)
                self._last_error = None
                return result
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("embedding classification failed, using keyword rules: %s", self._last_error)
        reason = self._reason()
        result = self._fallback.classify(text)
        return replace(result, limitations=[f"ML classifier unavailable ({reason}); keyword rules were used.", *result.limitations])

    def _reason(self) -> str:
        return self._last_error or self._primary.error or "model is still loading"

    def describe(self) -> tuple[str, str]:
        """(health status, detail) for GET /health."""
        if self._primary.ready() and self._last_error is None:
            return "ok", f"embedding model {self._primary.model_version}"
        return "degraded", f"keyword fallback in use: {self._reason()}"


class GraniteEncoder:
    """Granite Embedding 30M via transformers/PyTorch. CLS pooling and L2 normalization, as the model card specifies."""

    def __init__(self, model_id: str = MODEL_ID, revision: str | None = MODEL_REVISION, device: str = "cpu", max_length: int = 256, dtype: str = "float32"):
        self._model_id, self._revision, self._device, self._max_length = model_id, revision, device, max_length
        self._dtype = dtype
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None
        self.fallback_reason: str | None = None  # set when the requested accelerator could not be used

    @property
    def version(self) -> str:
        """Model, pinned commit and the backend that actually executes it, so every result is attributable."""
        return f"{self._model_id}@{(self._revision or 'main')[:10]}+{self._device}-{self._dtype}"

    def _load_on(self, device: str, dtype: str) -> None:
        import torch  # noqa: F401  (imported lazily: the API must start without torch)
        from transformers import AutoModel, AutoTokenizer

        if device == "nnpa":
            import torch_nnpa  # noqa: F401  (registers the 'nnpa' PyTorch device)
        tokenizer = AutoTokenizer.from_pretrained(self._model_id, revision=self._revision)
        model = AutoModel.from_pretrained(self._model_id, revision=self._revision).eval()
        if dtype == "float16":  # the dtype the NNPA backend accelerates; fp32 matmuls stay on the CPU
            model = model.half()
        if device != "cpu":
            model = model.to(device)
        self._tokenizer, self._model = tokenizer, model  # only after everything succeeded
        self._device, self._dtype = device, dtype

    def _load(self) -> None:
        try:
            self._load_on(self._device, self._dtype)
        except Exception as exc:
            if self._device == "cpu" and self._dtype == "float32":
                raise
            self.fallback_reason = f"{self._device}/{self._dtype} unavailable ({type(exc).__name__}: {exc})"[:300]
            log.warning("embedding accelerator unavailable, using cpu fp32: %s", self.fallback_reason)
            self._load_on("cpu", "float32")

    def encode(self, texts: list[str]) -> list[list[float]]:
        import torch

        with self._lock:  # one inference at a time: the VM has 3 vCPUs
            if self._model is None:
                self._load()
            batch = self._tokenizer(texts, padding=True, truncation=True, max_length=self._max_length, return_tensors="pt")
            if self._device != "cpu":
                batch = {k: v.to(self._device) for k, v in batch.items()}
            with torch.inference_mode():
                cls = self._model(**batch).last_hidden_state[:, 0]
                cls = torch.nn.functional.normalize(cls.float(), dim=1)
            return cls.to("cpu").tolist()
