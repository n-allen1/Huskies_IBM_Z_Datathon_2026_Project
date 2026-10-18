# DispatchZ backend on IBM's LinuxONE ML image (s390x).
#
# The base image already carries the ML stack (torch, torch_nnpa, xgboost, scikit-learn, ...) in
# /opt/venv, owned by the non-root `ibm-user`. This file only adds the API's own dependencies on top.
#
# Build context is the REPO ROOT:
#   docker build -f backend/Dockerfile -t dispatchz-api .
# Run the test suite inside the image (the same code, on the VM):
#   docker build -f backend/Dockerfile --target test -t dispatchz-test . && docker run --rm dispatchz-test
# Run the API (bound to localhost on the host; reach it through an SSH tunnel):
#   docker run -d --name dispatchz-api -p 127.0.0.1:8000:8000 -e DEMO_MODE=true --restart unless-stopped dispatchz-api

ARG BASE_IMAGE=registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest
FROM ${BASE_IMAGE} AS base

ARG PYTHON=/opt/venv/bin/python
ENV PYTHON=${PYTHON} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The base image starts Jupyter; this image must start only what we tell it to.
ENTRYPOINT []

WORKDIR /app/backend
COPY backend/requirements.txt ./

# Pin every package that is already installed so pip cannot upgrade or replace IBM's ML libraries.
# If our requirements conflict with them the build fails loudly instead of silently changing torch.
RUN ${PYTHON} -m pip list --format=freeze > /tmp/constraints.txt \
 && ${PYTHON} -m pip install --no-cache-dir -c /tmp/constraints.txt -r requirements.txt \
 && rm /tmp/constraints.txt \
 && ${PYTHON} -c "import torch, xgboost, fastapi, pydantic_core; print('torch', torch.__version__, '| xgboost', xgboost.__version__, '| fastapi', fastapi.__version__)"

COPY backend/app ./app


# ---- test image: test dependencies, the tests, and the contract they validate against ----
FROM base AS test
COPY backend/requirements-test.txt ./
RUN ${PYTHON} -m pip list --format=freeze > /tmp/constraints.txt \
 && ${PYTHON} -m pip install --no-cache-dir -c /tmp/constraints.txt -r requirements-test.txt \
 && rm /tmp/constraints.txt
COPY backend/tests ./tests
COPY contracts/openapi.json /app/contracts/openapi.json
CMD exec $PYTHON -m pytest -q -p no:cacheprovider


# ---- runtime image (default target: last stage) ----
FROM base AS runtime
COPY backend/tools ./tools
# Use the pretrained embedding classifier (the keyword rules remain the automatic fallback).
ENV CLASSIFIER=embedding
# Run the embedding model on the Telum NNPA accelerator in fp16 (the dtype it accelerates). Measured on the VM:
# ~3 ms vs ~130 ms per narrative on the CPU build of PyTorch in this image, same suggestions. If the accelerator
# cannot load, the encoder falls back to CPU fp32 automatically and the version string says so.
ENV EMBEDDING_DEVICE=nnpa EMBEDDING_DTYPE=float16
# Train the XGBoost ETA model at start-up, on this machine (the straight-line estimate is the automatic fallback).
ENV ETA_MODEL=xgboost
# Bake the pretrained model into the image so the demo does not depend on the network at start-up.
# If the download fails the build still succeeds and the model is fetched at first start instead.
RUN ${PYTHON} -c "from app.embedding_classifier import MODEL_ID, MODEL_REVISION; from transformers import AutoModel, AutoTokenizer; AutoTokenizer.from_pretrained(MODEL_ID, revision=MODEL_REVISION); AutoModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION); print('model cached')"  || echo "WARNING: model preload failed; it will be downloaded at first start"
EXPOSE 8000
# Single worker: the SSE ring buffer and idempotency cache are in-process (see docs/mvp-plan.md).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD $PYTHON -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status == 200 else 1)"
CMD exec $PYTHON -m uvicorn app.main:app --host 0.0.0.0 --port 8000
