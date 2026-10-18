"""The embedding classifier's matching, fallback and safety behavior, with a fake encoder (no torch).
Whether the real Granite model classifies well is measured on the VM by tools/classifier_eval.py."""

import math
import re
import time
import zlib

import pytest
from fastapi.testclient import TestClient

from app.embedding_classifier import PROTOTYPES, EmbeddingClassifier, FallbackClassifier, GraniteEncoder
from app.inference import KeywordClassifier
from app.main import create_app
from app.models import IncidentCategory

from .conftest import NOW, Api, incident_payload


class FakeEncoder:
    """Hashed bag of words: texts that share words are similar. Deterministic and dependency-free."""

    version = "fake-bow-1"

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.fail = False

    def encode(self, texts):
        if self.fail:
            raise RuntimeError("encoder exploded")
        out = []
        for text in texts:
            v = [0.0] * self.dim
            for word in re.findall(r"[a-z]+", text.lower()):
                v[zlib.crc32(word.encode()) % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


def ready_classifier(encoder=None) -> tuple[FallbackClassifier, EmbeddingClassifier, FakeEncoder]:
    encoder = encoder or FakeEncoder()
    ml = EmbeddingClassifier(encoder)
    ml.warm_up()
    return FallbackClassifier(ml, KeywordClassifier()), ml, encoder


def test_prototypes_cover_every_category_except_other():
    assert set(PROTOTYPES) == set(IncidentCategory) - {IncidentCategory.other}
    assert all(len(texts) >= 2 for texts in PROTOTYPES.values())


@pytest.mark.parametrize("text,expected", [
    ("House fire with flames and heavy smoke coming from the roof", "structure_fire"),
    ("A car is burning on the side of the road", "vehicle_fire"),
    ("Patient collapsed and is not breathing, no pulse", "medical_cardiac"),
    ("Intruder trying to enter through a window at night", "burglary"),
    ("Chemical spill with strong fumes at a warehouse", "hazmat"),
])
def test_nearest_example_wins_and_is_marked_as_ml(text, expected):
    _, ml, _ = ready_classifier()
    r = ml.classify(text)
    assert r.category == IncidentCategory(expected)
    assert r.source.value == "ml_inferred" and r.model_version == "fake-bow-1"
    assert 0.0 < r.confidence <= 1.0
    assert r.suggested_priority is None  # priority policy is not the model's to set
    assert 0.0 < r.top_similarity <= 1.0 + 1e-9  # cosine similarity, allowing for float rounding
    assert any("uncalibrated" in line and "not a probability" in line for line in r.limitations)
    assert any("runner-up" in line for line in r.limitations)


def test_unrelated_text_suggests_no_category():
    _, ml, _ = ready_classifier()
    r = ml.classify("lorem ipsum dolor sit amet consectetur")
    assert r.category == IncidentCategory.other and r.confidence is None
    assert any("no category is suggested" in line for line in r.limitations)


def test_not_ready_model_uses_keyword_rules_and_says_so():
    ml = EmbeddingClassifier(FakeEncoder())  # never warmed up
    fb = FallbackClassifier(ml, KeywordClassifier())
    r = fb.classify("House fire with flames")
    assert r.source.value == "rule_based" and r.category == IncidentCategory.structure_fire
    assert "still loading" in r.limitations[0]
    status, detail = fb.describe()
    assert status == "degraded" and "keyword fallback" in detail


def test_warm_up_failure_is_recorded_and_degrades_gracefully():
    encoder = FakeEncoder()
    encoder.fail = True
    fb, ml, _ = ready_classifier(encoder)
    assert not ml.ready() and "encoder exploded" in ml.error
    r = fb.classify("House fire with flames")
    assert r.source.value == "rule_based" and "encoder exploded" in r.limitations[0]
    status, detail = fb.describe()
    assert status == "degraded" and "encoder exploded" in detail


def test_runtime_failure_falls_back_for_that_call_then_recovers():
    fb, _, encoder = ready_classifier()
    assert fb.describe()[0] == "ok"
    encoder.fail = True
    r = fb.classify("House fire with flames")
    assert r.source.value == "rule_based" and fb.describe()[0] == "degraded"
    encoder.fail = False
    assert fb.classify("House fire with flames").source.value == "ml_inferred"
    assert fb.describe()[0] == "ok"


def test_api_returns_ml_suggestion_that_conforms_to_the_contract(settings):
    fb, _, _ = ready_classifier()
    api = Api(TestClient(create_app(settings=settings, classifier=fb, clock=lambda: NOW)))
    inc = api.call("createIncident", json=incident_payload("Patient collapsed and is not breathing, no pulse"), expect=201).json()
    path = {"incidentId": inc["incidentId"]}
    s = api.call("createClassification", path=path, json={"mode": "infer"}, expect=201).json()
    assert s["source"] == "ml_inferred" and s["status"] == "suggested" and s["category"] == "medical_cardiac"
    assert 0 < s["confidence"] <= 1 and s["modelVersion"] == "fake-bow-1"


def test_an_ml_suggestion_alone_never_drives_requirements_or_recommendations(settings):
    fb, _, _ = ready_classifier()
    api = Api(TestClient(create_app(settings=settings, classifier=fb, clock=lambda: NOW)))
    inc = api.call("createIncident", json=incident_payload("House fire with flames and heavy smoke"), expect=201).json()
    path = {"incidentId": inc["incidentId"]}
    api.call("createClassification", path=path, json={"mode": "infer"}, expect=201)
    assert api.call("getIncidentRequirements", path=path, expect=200).json()["requirements"] == []
    assert "confirmedCategory" not in api.call("getIncident", path=path, expect=200).json()
    api.call("createRecommendation", path=path, json={}, expect=409)


def test_health_reports_degraded_while_the_model_loads_and_ok_once_ready(settings):
    loading = FallbackClassifier(EmbeddingClassifier(FakeEncoder()), KeywordClassifier())
    api = Api(TestClient(create_app(settings=settings, classifier=loading, clock=lambda: NOW)))
    h = api.call("getHealth", role=None, expect=200).json()
    assert h["status"] == "degraded"
    assert {c["name"]: c["status"] for c in h["components"]}["inference"] == "degraded"

    fb, _, _ = ready_classifier()
    api = Api(TestClient(create_app(settings=settings, classifier=fb, clock=lambda: NOW)))
    assert api.call("getHealth", role=None, expect=200).json()["status"] == "ok"


def test_startup_warms_the_model_in_the_background(settings):
    fb = FallbackClassifier(EmbeddingClassifier(FakeEncoder()), KeywordClassifier())
    app = create_app(settings=settings, classifier=fb, clock=lambda: NOW)
    with TestClient(app):  # entering the context runs the lifespan startup
        deadline = time.time() + 5
        while fb.describe()[0] != "ok" and time.time() < deadline:
            time.sleep(0.05)
        assert fb.describe()[0] == "ok"


def test_default_classifier_is_keyword_rules_without_torch(app):
    assert type(app.state.services.classifier).__name__ == "KeywordClassifier"


class AcceleratorlessEncoder(GraniteEncoder):
    """GraniteEncoder whose model loading is stubbed: the accelerator fails, the CPU works."""

    def __init__(self, cpu_works: bool = True, **kw):
        super().__init__(**kw)
        self.cpu_works = cpu_works
        self.loaded_on = []

    def _load_on(self, device, dtype):
        self.loaded_on.append((device, dtype))
        if device == "nnpa" or not self.cpu_works:
            raise RuntimeError(f"cannot load on {device}")
        self._device, self._dtype = device, dtype


def test_encoder_version_names_the_backend_that_actually_runs():
    enc = AcceleratorlessEncoder(device="nnpa", dtype="float16")
    assert enc.version.endswith("+nnpa-float16")  # what was requested, until a load says otherwise
    enc._load()
    assert enc.version.endswith("+cpu-float32") and enc.loaded_on == [("nnpa", "float16"), ("cpu", "float32")]
    assert "cannot load on nnpa" in enc.fallback_reason


def test_a_working_accelerator_needs_no_fallback():
    class Works(AcceleratorlessEncoder):
        def _load_on(self, device, dtype):
            self._device, self._dtype = device, dtype

    enc = Works(device="nnpa", dtype="float16")
    enc._load()
    assert enc.version.endswith("+nnpa-float16") and enc.fallback_reason is None


def test_cpu_failure_is_not_swallowed():
    enc = AcceleratorlessEncoder(cpu_works=False, device="cpu", dtype="float32")
    with pytest.raises(RuntimeError, match="cannot load on cpu"):
        enc._load()


def test_classifier_exposes_margin_between_best_and_runner_up():
    _, ml, _ = ready_classifier()
    r = ml.classify("A car is burning on the side of the road")
    assert r.margin is not None and r.margin > 0
