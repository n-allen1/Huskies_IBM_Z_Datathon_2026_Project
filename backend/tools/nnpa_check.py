"""What does the NNPA PyTorch backend (torch_nnpa) actually do on this VM? Reports facts, claims nothing.

Run inside the image on the VM:
    docker run --rm dispatchz-api sh -c 'exec $PYTHON -m tools.nnpa_check'
    docker run --rm dispatchz-api sh -c 'exec $PYTHON -m tools.nnpa_check --embedding'

Every step is attempted independently and prints PASS/FAIL with the exception, because the backend is
experimental and we do not know in advance which ops/dtypes it supports. We may only say "the model ran
on the NNPA device" if the relevant lines below say so.
"""

import argparse
import statistics
import time

import torch

try:
    import torch_nnpa  # registers the 'nnpa' device and torch.nnpa
except Exception as exc:  # noqa: BLE001
    raise SystemExit(f"torch_nnpa is not importable here: {exc!r}")


def timed(fn, n=100):
    for _ in range(5):
        fn()
    samples = []
    for _ in range(n):
        a = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - a) * 1e3)
    return statistics.median(samples)


def facts() -> None:
    print(f"torch {torch.__version__} | torch_nnpa {torch_nnpa.__version__}")
    mod = torch_nnpa.NNPAModule
    print(f"nnpa available: {mod.is_available()} | telum2 available: {mod.is_telum2_available()}")
    print(f"max dims (from right): {mod.max_dim1_size()}, {mod.max_dim2_size()}, {mod.max_dim3_size()}, {mod.max_dim4_size()}")
    print(f"autocast dtype: {mod.get_autocast_dtype()}")


def linear_test() -> None:
    torch.manual_seed(0)
    lin = torch.nn.Linear(384, 384).eval()
    x = torch.randn(8, 384)
    with torch.inference_mode():
        ref = lin(x)
        cpu_ms = timed(lambda: lin(x))
    print(f"\nlinear 8x384 @ 384x384 on cpu fp32: {cpu_ms:.3f} ms")
    for label, dtype in (("fp32", torch.float32), ("fp16", torch.float16)):
        try:
            m = torch.nn.Linear(384, 384).eval()
            m.load_state_dict(lin.state_dict())
            m = m.to(dtype).to("nnpa")
            xi = x.to(dtype).to("nnpa")
            with torch.inference_mode():
                out = m(xi).to("cpu").float()
                ms = timed(lambda: m(xi))
                ms_back = timed(lambda: m(xi).to("cpu"))  # includes copying the result back to the host
            print(
                f"PASS nnpa {label}: max abs diff vs cpu {float((out - ref).abs().max()):.5f}, "
                f"{ms:.3f} ms ({ms_back:.3f} ms including copy back to host)"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL nnpa {label}: {type(exc).__name__}: {str(exc)[:200]}")


def embedding_test() -> None:
    from app.embedding_classifier import GraniteEncoder

    texts = [
        "Smoke showing from a two story house fire on Elm Street",
        "My husband just dropped to the floor and will not wake up",
        "Barrels tipped over at the loading dock and something is leaking",
        "Two vehicle collision at 5th and Main, one rollover",
    ]
    cpu = GraniteEncoder(device="cpu")
    ref = cpu.encode(texts)
    cpu_ms = timed(lambda: cpu.encode(texts[:1]), n=30)
    print(f"\nGranite embedding on cpu: {cpu_ms:.1f} ms per narrative")
    try:
        dev = GraniteEncoder(device="nnpa")
        got = dev.encode(texts)
        cos = [sum(a * b for a, b in zip(r, g)) for r, g in zip(ref, got)]
        ms = timed(lambda: dev.encode(texts[:1]), n=30)
        print(f"PASS nnpa embedding: min cosine vs cpu {min(cos):.4f}, {ms:.1f} ms per narrative")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL nnpa embedding: {type(exc).__name__}: {str(exc)[:300]}")


def sweep_test() -> None:
    """Does batching or fp16 change the picture? Per-narrative latency for cpu / nnpa fp32 / nnpa fp16."""
    from app.embedding_classifier import GraniteEncoder
    from tools.classifier_eval import SET_A, SET_B

    pool = [t for _, t in SET_A + SET_B]  # 52 narratives
    print(f"\n{'config':<12} {'batch':>5} {'ms/narrative':>13} {'narratives/s':>13}")
    encoders = {}
    for label, device, dtype in (("cpu fp32", "cpu", "float32"), ("nnpa fp32", "nnpa", "float32"), ("nnpa fp16", "nnpa", "float16")):
        try:
            enc = GraniteEncoder(device=device, dtype=dtype)
            enc.encode(pool[:1])  # load + warm
            encoders[label] = enc
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {label}: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        for batch in (1, 8, 32):
            texts = pool[:batch]
            ms = timed(lambda: enc.encode(texts), n=10 if batch > 1 else 30)
            print(f"{label:<12} {batch:>5} {ms / batch:>13.1f} {1000 * batch / ms:>13.1f}")
    if "cpu fp32" in encoders and "nnpa fp16" in encoders:
        ref, got = encoders["cpu fp32"].encode(pool[:16]), encoders["nnpa fp16"].encode(pool[:16])
        print(f"\nnnpa fp16 vs cpu fp32 embeddings: min cosine {min(sum(a * b for a, b in zip(r, g)) for r, g in zip(ref, got)):.4f}")


def profile_test() -> None:
    """Which ops dominate one embedding on nnpa? Shows whether work stays on the CPU."""
    from torch.profiler import ProfilerActivity, profile

    from app.embedding_classifier import GraniteEncoder

    enc = GraniteEncoder(device="nnpa")
    enc.encode(["warm up"])
    with profile(activities=[ProfilerActivity.CPU]) as prof:
        enc.encode(["Smoke showing from a two story house fire on Elm Street"])
    print("\nTop ops during one nnpa embedding (by self time):")
    print(prof.key_averages().table(sort_by="self_cpu_time_total", row_limit=12, max_name_column_width=48))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedding", action="store_true", help="also try the full Granite model (downloads/loads it)")
    ap.add_argument("--sweep", action="store_true", help="batch size x dtype x device latency sweep (implies the model)")
    ap.add_argument("--profile", action="store_true", help="op-level profile of one embedding on nnpa")
    args = ap.parse_args()
    facts()
    linear_test()
    if args.embedding:
        embedding_test()
    for flag, fn in ((args.sweep, sweep_test), (args.profile, profile_test)):
        if flag:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {fn.__name__}: {type(exc).__name__}: {str(exc)[:300]}")
