"""Compare the pretrained embedding classifier with the keyword baseline, and choose an abstain threshold.

Run inside the image on the VM (needs torch + the model):
    docker run --rm dispatchz-api sh -c 'exec $PYTHON -m tools.classifier_eval'
    docker run --rm dispatchz-api sh -c 'exec $PYTHON -m tools.classifier_eval --device nnpa --dtype float16'

Two small sets of synthetic narratives written for this project (13 categories x 2: one with obvious
keywords, one paraphrased without them):
  A  development set. We looked at its results and may tune on it (the abstain threshold).
  B  held-out set, written before any model output was seen. Never tune on it; report it.
Both are indicative only: they are not operational accuracy and say nothing about real callers. The
model is pretrained; nothing is fitted to either set.

Abstaining ("no category suggested") is the safe failure: the dispatcher then chooses, instead of
being nudged toward a wrong category. The sweep shows what each threshold costs in coverage.
"""

import argparse
import statistics
import time

from app.embedding_classifier import MODEL_ID, MODEL_REVISION, EmbeddingClassifier, GraniteEncoder
from app.inference import KeywordClassifier

SET_A = [
    ("structure_fire", "Smoke showing from a two story house fire on Elm Street"),
    ("structure_fire", "Neighbors say the whole back of the home is glowing orange and the roof is starting to cave in"),
    ("vehicle_fire", "Car fire on the highway shoulder, the driver is out"),
    ("vehicle_fire", "A pickup is smoking badly under the hood at the gas station pump"),
    ("wildland_fire", "Brush fire moving up the hillside toward the houses"),
    ("wildland_fire", "The dry field is burning and the wind is carrying it toward the barns"),
    ("medical_cardiac", "Chest pain and sweating, 62 year old male"),
    ("medical_cardiac", "My husband just dropped to the floor and will not wake up, his lips are turning blue"),
    ("medical_trauma", "Bleeding heavily after a gunshot wound"),
    ("medical_trauma", "He came off the roof onto concrete and his arm is bent the wrong way"),
    ("medical_other", "Elderly woman is dizzy and says she might pass out"),
    ("medical_other", "Toddler has been throwing up all night and is very hot and drowsy"),
    ("traffic_collision", "Two vehicle collision at 5th and Main, one rollover"),
    ("traffic_collision", "A truck ran the red light and hit a sedan, the airbags went off"),
    ("technical_rescue", "Worker trapped in a trench, the dirt walls collapsed"),
    ("technical_rescue", "A teenager is stuck about thirty feet down a drainage pipe and cannot climb out"),
    ("hazmat", "Strong chlorine smell near the pool supply store, people are coughing"),
    ("hazmat", "Barrels tipped over at the loading dock and something is leaking across the floor"),
    ("assault", "A man was assaulted outside the bar and the attacker is running away"),
    ("assault", "Two guys are punching another man on the corner right now"),
    ("burglary", "Someone broke into the garage while we were at work"),
    ("burglary", "I came home and my back door is kicked in and the television is gone"),
    ("domestic_disturbance", "Domestic argument next door, yelling and slamming doors"),
    ("domestic_disturbance", "I can hear my neighbors screaming at each other and something shattered"),
    ("welfare_check", "Requesting a welfare check on my elderly mother"),
    ("welfare_check", "Have not heard from my brother in a week and he is not picking up his phone"),
]

SET_B = [
    ("structure_fire", "Fully involved structure fire at the old mill, flames through the roof"),
    ("structure_fire", "Thick black smoke is pouring out of the upstairs windows and the family is standing on the lawn"),
    ("vehicle_fire", "Truck fire on the on-ramp, the cab is fully engulfed"),
    ("vehicle_fire", "A delivery van is burning hot beside the loading bay and the tires are popping"),
    ("wildland_fire", "Grass fire along the railway embankment spreading fast"),
    ("wildland_fire", "Sparks from a tractor lit the dry stubble and the line of fire is half a mile wide"),
    ("medical_cardiac", "Suspected heart attack, woman clutching her chest, pale and sweating"),
    ("medical_cardiac", "He slumped over at the dinner table and his wife cannot find a pulse"),
    ("medical_trauma", "Fell from a scaffold with a head injury, bleeding from the scalp"),
    ("medical_trauma", "Boy jumped off the swing set and landed on his wrist, the bone is poking through the skin"),
    ("medical_other", "Seizure in the grocery store, an adult man is on the floor shaking"),
    ("medical_other", "My grandmother is very confused, burning up with a fever and cannot keep water down"),
    ("traffic_collision", "Rollover crash on the interstate, multiple vehicles involved"),
    ("traffic_collision", "A motorbike and a van met head on at the roundabout and the riders are on the tarmac"),
    ("technical_rescue", "Person trapped in a collapsed trench at a construction site"),
    ("technical_rescue", "A caver is wedged in a narrow passage about two hundred feet from the entrance"),
    ("hazmat", "Ammonia leak at the cold storage plant, workers are evacuating"),
    ("hazmat", "A sharp acrid vapor is rolling off a damaged tank at the depot and everyone's eyes are burning"),
    ("assault", "Armed assault at the gas station, the suspect fled on foot"),
    ("assault", "A group jumped a delivery driver in the alley and kicked him while he was down"),
    ("burglary", "Burglary in progress, an intruder was seen climbing through the kitchen window"),
    ("burglary", "Someone pried open the shop door overnight and the register and stock are gone"),
    ("domestic_disturbance", "Domestic dispute upstairs, a man and woman shouting and furniture being thrown"),
    ("domestic_disturbance", "Through the wall I can hear a couple screaming and a child crying, it sounds like it is getting violent"),
    ("welfare_check", "Requesting a welfare check on a neighbor, newspapers have been piling up for a week"),
    ("welfare_check", "Her family has not been able to reach grandpa since Tuesday and fears he may have fallen at home"),
]

SIM_THRESHOLDS = [0.60, 0.64, 0.68, 0.70, 0.72, 0.74, 0.75, 0.76, 0.78, 0.80]
MARGIN_THRESHOLDS = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08]


def run_set(cases, ml, keyword):
    rows, latencies = [], []
    for i, (truth, text) in enumerate(cases):
        a = time.perf_counter()
        r = ml.classify(text)  # classifier built with min_similarity=-1: never abstains, so we can sweep offline
        latencies.append((time.perf_counter() - a) * 1e3)
        rows.append({
            "truth": truth, "text": text, "pred": r.category.value, "sim": r.top_similarity, "margin": r.margin,
            "kw": keyword.classify(text).category.value, "paraphrased": i % 2,
        })
    return rows, latencies


def report_set(name, rows):
    n, half = len(rows), len(rows) // 2

    def hits(key, para=None):
        return sum(r[key] == r["truth"] for r in rows if para is None or r["paraphrased"] == para)

    print(f"\nSet {name} ({n} narratives)")
    print(f"  {'classifier':<10} {'overall':>12} {'obvious':>9} {'paraphrased':>12}")
    for label, key in (("keyword", "kw"), ("embedding", "pred")):
        print(f"  {label:<10} {hits(key):>4}/{n} {hits(key) / n:>5.0%} {hits(key, 0):>6}/{half} {hits(key, 1):>9}/{half}")
    for r in rows:
        if r["pred"] != r["truth"]:
            print(f"    miss (sim {r['sim']:.2f}, margin {r['margin']:.3f}) {r['truth']} -> {r['pred']}: {r['text']}")


def sweep(title, key, thresholds, rows_a, rows_b):
    print(f"\nAbstain sweep on {title} (below the threshold: no category suggested; the dispatcher chooses)")
    print(f"  {'threshold':>9} | {'A answered':>10} {'A correct of answered':>22} | {'B answered':>10} {'B correct of answered':>22}")
    for t in thresholds:
        cells = []
        for rows in (rows_a, rows_b):
            answered = [r for r in rows if r[key] >= t]
            cells.append((len(answered), len(rows), sum(r["pred"] == r["truth"] for r in answered)))
        (na, ta, ra), (nb, tb, rb) = cells
        pa = f"{ra}/{na} ({ra / na:.0%})" if na else "-"
        pb = f"{rb}/{nb} ({rb / nb:.0%})" if nb else "-"
        print(f"  {t:>9.2f} | {na:>5}/{ta:<4} {pa:>22} | {nb:>5}/{tb:<4} {pb:>22}")
    right = sorted(r[key] for r in rows_a + rows_b if r["pred"] == r["truth"])
    wrong = sorted(r[key] for r in rows_a + rows_b if r["pred"] != r["truth"])
    print(f"  {key} of correct suggestions: median {statistics.median(right):.3f}, min {right[0]:.3f}"
          + (f" | of wrong ones: median {statistics.median(wrong):.3f}, max {wrong[-1]:.3f}" if wrong else ""))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu", choices=["cpu", "nnpa"])
    parser.add_argument("--dtype", default="float32", choices=["float32", "float16"])
    args = parser.parse_args()

    encoder = GraniteEncoder(device=args.device, dtype=args.dtype)
    ml = EmbeddingClassifier(encoder, min_similarity=-1.0)
    t0 = time.perf_counter()
    ml.warm_up()
    if not ml.ready():
        raise SystemExit(f"model did not load: {ml.error}")
    print(f"{encoder.version}; load + prototype encoding {time.perf_counter() - t0:.1f} s"
          + (f"\nNOTE: accelerator not used: {encoder.fallback_reason}" if encoder.fallback_reason else ""))

    keyword = KeywordClassifier()
    rows_a, lat_a = run_set(SET_A, ml, keyword)
    rows_b, lat_b = run_set(SET_B, ml, keyword)
    report_set("A (development)", rows_a)
    report_set("B (held out)", rows_b)
    sweep("top similarity", "sim", SIM_THRESHOLDS, rows_a, rows_b)
    sweep("margin over the runner-up", "margin", MARGIN_THRESHOLDS, rows_a, rows_b)

    lat = sorted(lat_a + lat_b)
    print(f"\nembedding latency per narrative ({encoder.version.split('+')[-1]}): p50 {statistics.median(lat):.1f} ms, "
          f"p95 {lat[int(len(lat) * 0.95) - 1]:.1f} ms, max {lat[-1]:.1f} ms (n={len(lat)})")

    if (encoder._device, encoder._dtype) != ("cpu", "float32"):
        ref = EmbeddingClassifier(GraniteEncoder(device="cpu", dtype="float32"), min_similarity=-1.0)
        ref.warm_up()
        ref_rows = run_set(SET_A, ref, keyword)[0] + run_set(SET_B, ref, keyword)[0]
        got_rows = rows_a + rows_b
        same = sum(a["pred"] == b["pred"] for a, b in zip(ref_rows, got_rows))
        drift = max(abs(a["sim"] - b["sim"]) for a, b in zip(ref_rows, got_rows))
        print(f"\nparity with cpu fp32: {same}/{len(got_rows)} identical suggestions; "
              f"largest difference in top similarity {drift:.4f}")


if __name__ == "__main__":
    main()
