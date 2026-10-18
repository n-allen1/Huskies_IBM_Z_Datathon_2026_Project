"""Travel-time estimation with XGBoost, trained at start-up on a documented SYNTHETIC simulator.

There is no real travel-time history here, so the training target comes from `simulate_seconds`, whose
parameters are stated assumptions (road circuity, speed rising with trip length, rush-hour slowdown,
turnout delay), not measurements. The model therefore demonstrates the mechanism (a gradient-boosted
model in the live request path on LinuxONE, with a measured comparison to simple baselines), not accuracy on
real roads. A deployment would train on AVL/CAD travel-time history or call a routing engine.

The estimator only orders units that already passed hard eligibility; it can never make a unit eligible.
If xgboost is missing, still training, or fails, the straight-line estimate answers instead.
"""

import logging
import math
import random
import threading
import time
from datetime import datetime

from .inference import EtaEstimate, EtaEstimator, haversine_km
from .models import EtaMethod, GeoPoint

log = logging.getLogger("dispatchz.ai")

SEED = 20260417
FEATURES = ["distance_km", "hour", "is_weekend"]


# --- the simulator (assumptions, not measurements) -------------------------------------------------

def _bump(hour: float, center: float, width: float) -> float:
    d = min(abs(hour - center), 24 - abs(hour - center))
    return math.exp(-0.5 * (d / width) ** 2)


def simulate_seconds(distance_km: float, hour: float, weekend: bool, rng: random.Random) -> float:
    """Synthetic emergency travel time from straight-line distance, time of day and day type."""
    road_km = distance_km * 1.3  # assumed circuity of the road network
    free_speed = 30.0 + 35.0 * (1.0 - math.exp(-distance_km / 6.0))  # km/h: local streets for short trips, arterials for long
    congestion = 1.0
    if not weekend:
        congestion -= 0.35 * _bump(hour, 8.0, 1.2) + 0.40 * _bump(hour, 17.0, 1.5)  # commute peaks
    congestion += 0.12 * _bump(hour, 1.5, 3.0)  # light night traffic
    turnout = 60.0 + rng.gauss(0.0, 10.0)  # seconds from dispatch to wheels rolling
    noise = math.exp(rng.gauss(0.0, 0.12))
    return max(30.0, turnout + road_km / (free_speed * congestion) * 3600.0 * noise)


def make_dataset(n: int, seed: int) -> tuple[list[list[float]], list[float]]:
    rng = random.Random(seed)
    rows, targets = [], []
    for _ in range(n):
        distance, hour, weekend = rng.uniform(0.2, 25.0), rng.uniform(0.0, 24.0), rng.random() < 2 / 7
        rows.append([distance, hour, float(weekend)])
        targets.append(simulate_seconds(distance, hour, weekend, rng))
    return rows, targets


def features_for(origin: GeoPoint, destination: GeoPoint, at: datetime) -> list[float]:
    # `at` is UTC; the simulator is not tied to a local time zone, which is a stated simplification.
    return [haversine_km(origin, destination), at.hour + at.minute / 60.0, float(at.weekday() >= 5)]


# --- baselines ----------------------------------------------------------------------------------------

def fit_line(distances: list[float], seconds: list[float]) -> tuple[float, float]:
    """Least-squares seconds = a + b * distance_km: the fair 'simple model' to beat."""
    n = len(distances)
    mx, my = sum(distances) / n, sum(seconds) / n
    b = sum((x - mx) * (y - my) for x, y in zip(distances, seconds)) / sum((x - mx) ** 2 for x in distances)
    return my - b * mx, b


def mae(pred: list[float], truth: list[float]) -> float:
    return sum(abs(p - t) for p, t in zip(pred, truth)) / len(truth)


# --- the estimator ------------------------------------------------------------------------------------

class XgboostEta:
    def __init__(self, fallback: EtaEstimator, *, n_samples: int = 6000, rounds: int = 150, seed: int = SEED, baseline_speed_kmh: float = 40.0):
        self._fallback = fallback
        self._n_samples, self._rounds, self._seed = n_samples, rounds, seed
        self._baseline_speed = baseline_speed_kmh
        self._lock = threading.Lock()
        self._booster = None
        self._np = None
        self.metrics: dict[str, float] = {}
        self.error: str | None = None
        self.model_version = "xgboost-eta-synthetic-v1"

    def warm_up(self) -> None:
        with self._lock:
            if self._booster is not None:
                return
            try:
                import numpy as np
                import xgboost as xgb

                started = time.perf_counter()
                rows, targets = make_dataset(self._n_samples, self._seed)
                split = int(len(rows) * 0.8)  # held out: never seen in training
                train_x, test_x, train_y, test_y = rows[:split], rows[split:], targets[:split], targets[split:]
                booster = xgb.train(
                    {"max_depth": 5, "eta": 0.1, "nthread": 2, "objective": "reg:squarederror", "seed": self._seed},
                    xgb.DMatrix(np.asarray(train_x, dtype=np.float32), label=np.asarray(train_y, dtype=np.float32), feature_names=FEATURES),
                    num_boost_round=self._rounds,
                )
                trained_s = time.perf_counter() - started
                test = np.asarray(test_x, dtype=np.float32)
                a, b = fit_line([r[0] for r in train_x], train_y)
                self.metrics = {
                    "n_train": float(len(train_x)), "n_test": float(len(test_x)), "train_seconds": round(trained_s, 2),
                    "mae_xgboost_s": round(mae(booster.inplace_predict(test).tolist(), test_y), 1),
                    "mae_linear_distance_s": round(mae([a + b * r[0] for r in test_x], test_y), 1),
                    "mae_haversine_s": round(mae([r[0] / self._baseline_speed * 3600.0 for r in test_x], test_y), 1),
                }
                self._np = np
                self.model_version = f"xgboost-{xgb.__version__}-eta-synthetic-v1"
                self._booster = booster
                self.error = None
                log.info("ETA model ready: %s", self.metrics)
            except Exception as exc:  # missing library or training failure must degrade, not crash
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("ETA model unavailable: %s", self.error)

    def ready(self) -> bool:
        return self._booster is not None

    def estimate(self, origin: GeoPoint, destination: GeoPoint, *, at: datetime) -> EtaEstimate:
        if self._booster is not None:
            try:
                with self._lock:
                    x = self._np.asarray([features_for(origin, destination, at)], dtype=self._np.float32)
                    seconds = float(self._booster.inplace_predict(x)[0])
                self.error = None
                return EtaEstimate(seconds=max(0, round(seconds)), method=EtaMethod.xgboost, model_version=self.model_version)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("ETA prediction failed, using straight-line estimate: %s", self.error)
        return self._fallback.estimate(origin, destination, at=at)

    def describe(self) -> tuple[str, str]:
        if self.ready() and self.error is None:
            m = self.metrics
            return "ok", f"{self.model_version} (held-out synthetic MAE {m['mae_xgboost_s']:.0f} s vs {m['mae_linear_distance_s']:.0f} s linear baseline)"
        return "degraded", f"straight-line fallback in use: {self.error or 'model is still training'}"
