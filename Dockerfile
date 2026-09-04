# Render Docker image: Python 3.12 + CPU Torch + SpeechBrain LID + Whisper + BGE-M3.
FROM python:3.12.8-slim-bookworm

WORKDIR /opt/render/project/src

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/render/project/src \
    HF_HOME=/opt/render/project/src/.cache/huggingface \
    IVR_USE_SPEECHBRAIN_LID=true \
    IVR_LID_FORCE_LANGUAGE= \
    IVR_STT_BACKEND=whisper \
    INTENT_EMBEDDER=bge \
    TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ivr-lid.txt requirements-ivr-intent.txt requirements-render.txt ./
RUN pip install --no-cache-dir -r requirements-render.txt
# PyPI only: Torch's extra-index must not be the only place pip looks for Whisper.
RUN pip install --no-cache-dir --index-url https://pypi.org/simple --upgrade \
    "faster-whisper>=1.0.0" \
    "sentence-transformers>=3.0.0"

COPY . .
RUN PYTHONPATH=. python -c "from services.ivr.lid import SpeechBrainLanguageIdentifier, build_default_lid; lid = SpeechBrainLanguageIdentifier(); print('lid_backend', type(build_default_lid()).__name__); import faster_whisper; import sentence_transformers; print('intent_extras', faster_whisper.__name__, sentence_transformers.__name__)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
