"""ETA estimation: the synthetic simulator, the XGBoost model, its fallback, and the safety invariant that
no estimator (however wrong or hostile) can make an ineligible unit eligible or ranked."""

import importlib.util
import random
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.eta_model import XgboostEta, fit_line, mae, make_dataset, simulate_seconds
from app.inference import EtaEstimate, HaversineEta
from app.main import create_app
from app.models import EtaMethod, GeoPoint, IncidentCategory

from .conftest import NOW, Api, incident_payload

requires_xgboost = pytest.mark.skipif(importlib.util.find_spec("xgboost") is None, reason="xgboost not installed here (the LinuxONE image has it)")

A, B = GeoPoint(lat=39.10, lon=-95.70), GeoPoint(lat=39.20, lon=-95.70)  # about 11 km apart
WEEKDAY_8AM = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)  # a Wednesday
WEEKDAY_3AM = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)


def mean_seconds(distance, hour, weekend, n=400):
    rng = random.Random(1)
    return sum(simulate_seconds(distance, hour, weekend, rng) for _ in range(n)) / n


# --- the simulator (pure Python) ----------------------------------------------------------------------

def test_dataset_is_deterministic_for_a_seed():
    assert make_dataset(50, 7) == make_dataset(50, 7)
    assert make_dataset(50, 7) != make_dataset(50, 8)


def test_simulator_behaves_plausibly():
    assert mean_seconds(2, 12, False) < mean_seconds(10, 12, False) < mean_seconds(20, 12, False)
    assert mean_seconds(10, 8, False) > mean_seconds(10, 3, False)  # weekday rush hour is slower than night
    assert mean_seconds(10, 8, False) > mean_seconds(10, 8, True)  # weekends have no commute peak


def test_line_fit_recovers_a_positive_slope():
    rows, seconds = make_dataset(2000, 3)
    a, b = fit_line([r[0] for r in rows], seconds)
    assert b > 30 and a > 0  # more than 30 s per km, positive turnout intercept
    assert mae([a + b * r[0] for r in rows], seconds) < mae([r[0] / 40 * 3600 for r in rows], seconds)


# --- fallback behavior (no xgboost needed) ------------------------------------------------------------

def test_before_training_the_straight_line_estimate_answers():
    eta = XgboostEta(HaversineEta())
    r = eta.estimate(A, B, at=WEEKDAY_8AM)
    assert r.method == EtaMethod.straight_line_fallback
    status, detail = eta.describe()
    assert status == "degraded" and "still training" in detail


def test_missing_xgboost_degrades_instead_of_crashing(monkeypatch):
    # `None` in sys.modules makes the import raise. Block numpy too: warm_up imports it first, and a clean
    # checkout without the optional ML libraries fails on whichever import comes first.
    monkeypatch.setitem(sys.modules, "numpy", None)
    monkeypatch.setitem(sys.modules, "xgboost", None)
    eta = XgboostEta(HaversineEta())
    eta.warm_up()
    assert not eta.ready() and "ModuleNotFoundError" in eta.error
    assert eta.estimate(A, B, at=WEEKDAY_8AM).method == EtaMethod.straight_line_fallback
    assert eta.describe()[0] == "degraded"


# --- the model ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def trained():
    eta = XgboostEta(HaversineEta(), n_samples=3000, rounds=80)
    eta.warm_up()
    return eta


@requires_xgboost
def test_model_trains_and_beats_both_baselines_on_held_out_data(trained):
    assert trained.ready() and trained.error is None
    m = trained.metrics
    assert m["n_test"] == 600 and m["n_train"] == 2400  # an 80/20 split; the test rows were never trained on
    assert m["mae_xgboost_s"] < m["mae_linear_distance_s"] < m["mae_haversine_s"]


@requires_xgboost
def test_predictions_follow_distance_and_time_of_day(trained):
    near, far = GeoPoint(lat=39.11, lon=-95.70), GeoPoint(lat=39.25, lon=-95.70)
    short, long_ = (trained.estimate(A, d, at=WEEKDAY_3AM) for d in (near, far))
    assert short.method == EtaMethod.xgboost and 0 < short.seconds < long_.seconds
    assert trained.estimate(A, B, at=WEEKDAY_8AM).seconds > trained.estimate(A, B, at=WEEKDAY_3AM).seconds
    assert trained.model_version.startswith("xgboost-") and "synthetic" in trained.model_version


@requires_xgboost
def test_prediction_failure_falls_back_then_recovers(trained):
    real = trained._booster

    class Broken:
        def inplace_predict(self, _):
            raise RuntimeError("predict exploded")

    try:
        trained._booster = Broken()
        r = trained.estimate(A, B, at=WEEKDAY_8AM)
        assert r.method == EtaMethod.straight_line_fallback
        assert trained.describe()[0] == "degraded" and "predict exploded" in trained.describe()[1]
    finally:
        trained._booster = real
    assert trained.estimate(A, B, at=WEEKDAY_8AM).method == EtaMethod.xgboost
    assert trained.describe()[0] == "ok"


# --- through the API ----------------------------------------------------------------------------------

def api_with(settings, estimator) -> Api:
    return Api(TestClient(create_app(settings=settings, estimator=estimator, clock=lambda: NOW)))


def recommend(api, category, **body):
    inc = api.call("createIncident", json=incident_payload(lat=39.103, lon=-95.69), expect=201).json()
    path = {"incidentId": inc["incidentId"]}
    api.call("createClassification", path=path, json={"mode": "confirm", "category": category}, expect=201)
    return api.call("createRecommendation", path=path, json={"maxCandidates": 50, **body}, expect=201).json()


@requires_xgboost
def test_recommendation_uses_the_model_and_says_so(settings, trained):
    rec = recommend(api_with(settings, trained), "medical_cardiac")
    eligible = [c for c in rec["candidates"] if c["eligible"]]
    assert eligible and all(c["etaMethod"] == "xgboost" for c in eligible)
    assert any(v.startswith("xgboost-") for v in rec["inputs"]["modelVersions"])
    assert any("XGBoost" in line and "synthetic" in line for line in rec["limitations"])
    assert not any("straight-line" in line for line in rec["limitations"])
    ranked = [c["etaSeconds"] for c in sorted(eligible, key=lambda c: c["rank"])]
    assert ranked == sorted(ranked)  # ranks follow the model's ETAs


def test_an_untrained_model_keeps_recommendations_working(settings):
    rec = recommend(api_with(settings, XgboostEta(HaversineEta())), "medical_cardiac")
    assert any(c["eligible"] for c in rec["candidates"])
    assert {c["etaMethod"] for c in rec["candidates"]} == {"straight_line_fallback"}
    assert any("straight-line" in line for line in rec["limitations"])


class HostileEta:
    """Returns arbitrary ETAs, including 0 s. Whatever it says, eligibility must not change."""

    def __init__(self):
        self.rng = random.Random(5)

    def estimate(self, origin, destination, *, at):
        return EtaEstimate(seconds=self.rng.choice([0, 1, 99999, self.rng.randint(0, 5000)]), method=EtaMethod.xgboost, model_version="hostile")


@pytest.mark.parametrize("category", [c.value for c in IncidentCategory if c.value != "other"])
def test_no_estimator_can_rank_an_ineligible_unit_or_change_who_is_eligible(settings, category):
    baseline = recommend(api_with(settings, HaversineEta()), category)
    hostile = recommend(api_with(settings, HostileEta()), category)
    assert {c["callSign"]: c["eligible"] for c in hostile["candidates"]} == {c["callSign"]: c["eligible"] for c in baseline["candidates"]}
    assert all("rank" not in c for c in hostile["candidates"] if not c["eligible"])


@requires_xgboost
def test_health_reports_the_eta_model(settings, trained):
    h = api_with(settings, trained).call("getHealth", role=None, expect=200).json()
    inference = next(c for c in h["components"] if c["name"] == "inference")
    assert inference["status"] == "ok" and "xgboost-" in inference["detail"]
    h = api_with(settings, XgboostEta(HaversineEta())).call("getHealth", role=None, expect=200).json()
    assert h["status"] == "degraded"
