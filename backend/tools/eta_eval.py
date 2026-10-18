"""Train the XGBoost ETA model on this machine and compare it with two baselines on held-out data.

Run inside the image on the VM:
    docker run --rm dispatchz-api sh -c 'exec $PYTHON -m tools.eta_eval'

The data is SYNTHETIC: travel times come from the documented simulator in app/eta_model.py (road circuity,
speed rising with trip length, rush-hour slowdown, turnout delay), whose parameters are assumptions. So this
shows the mechanism and its cost on LinuxONE, not accuracy on real roads. Training happens here, on the
big-endian machine itself, so no model file crosses architectures.
"""

import statistics
import time
from datetime import datetime, timezone

from app.eta_model import XgboostEta
from app.inference import HaversineEta
from app.models import GeoPoint


def main() -> None:
    eta = XgboostEta(HaversineEta())
    eta.warm_up()
    if not eta.ready():
        raise SystemExit(f"model did not train: {eta.error}")
    m = eta.metrics
    print(f"{eta.model_version}: trained on {m['n_train']:.0f} synthetic trips in {m['train_seconds']} s, "
          f"evaluated on {m['n_test']:.0f} held-out trips")
    print(f"\n{'estimator':<34} {'mean abs error':>15}")
    for label, key in (("XGBoost", "mae_xgboost_s"), ("linear fit on distance only", "mae_linear_distance_s"),
                       ("straight line @ 40 km/h", "mae_haversine_s")):
        print(f"{label:<34} {m[key]:>12.0f} s")

    a, b, at = GeoPoint(lat=39.10, lon=-95.70), GeoPoint(lat=39.17, lon=-95.66), datetime.now(timezone.utc)
    for _ in range(200):
        eta.estimate(a, b, at=at)
    samples = []
    for _ in range(3000):
        t = time.perf_counter()
        eta.estimate(a, b, at=at)
        samples.append((time.perf_counter() - t) * 1e3)
    samples.sort()
    print(f"\nprediction latency per unit: p50 {statistics.median(samples):.3f} ms, p99 {samples[int(len(samples) * 0.99)]:.3f} ms "
          f"(a 13-unit recommendation makes 13 calls)")


if __name__ == "__main__":
    main()
