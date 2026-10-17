#!/usr/bin/env bash
# Phase 0 IBM Z / LinuxONE compatibility check. Run ON THE VM and paste output into docs/platform-check.md.
echo "== arch / OS =="; uname -m; cat /etc/os-release | head -3
echo "== cpu =="; lscpu | grep -Ei 'model name|architecture|nnpa|dfltcc|vxe|flags|features' || lscpu
echo "== runtimes =="; python3 --version; docker --version 2>&1 | head -1; podman --version 2>&1 | head -1; psql --version 2>&1 | head -1; node --version 2>&1
echo "== python wheels =="
python3 -m venv /tmp/zcheck && . /tmp/zcheck/bin/activate
for p in numpy torch xgboost sentence-transformers fastapi uvicorn sqlalchemy psycopg2-binary asyncpg ortools; do
  if pip install --only-binary=:all: -q "$p" 2>/tmp/err.txt; then echo "OK    $p"; else echo "FAIL  $p (binary wheel) -> $(tail -1 /tmp/err.txt)"; fi
done
python - <<'P'
import time
try:
    import torch; print("torch", torch.__version__)
    t=time.time(); torch.randn(512,512)@torch.randn(512,512); print("torch matmul ok %.1f ms"%((time.time()-t)*1e3))
except Exception as e: print("torch FAIL", e)
try:
    import xgboost as x, numpy as np; print("xgboost", x.__version__)
    m=x.XGBRegressor(n_estimators=10).fit(np.random.rand(200,4),np.random.rand(200)); print("xgboost train/predict ok")
except Exception as e: print("xgboost FAIL", e)
P
