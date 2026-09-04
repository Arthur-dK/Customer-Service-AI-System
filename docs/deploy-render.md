# Deploying the IVR on Render

Spoken language ID needs **SpeechBrain + CPU Torch on Python 3.12**. Intent routing needs **faster-whisper + BGE-M3** in the same image. If logs say `FixedLanguageIdentifier` / `fixed-fallback`, spoken LID treats every utterance as English (DTMF language select still works).

## Recommended: Docker

In the Render dashboard, set the service **Language / Environment to Docker** (not native Python). Root directory is the repo; Dockerfile is `./Dockerfile`.

The image:

- uses Python 3.12.8
- installs `requirements-render.txt` (Torch CPU, SpeechBrain, faster-whisper, sentence-transformers)
- downloads the VoxLingua107 LID model at **build** time
- downloads Whisper `base` and BGE-M3 on **first boot** (lifespan warmup; `/health` does not wait)

**Instance size: 8 GB RAM class** (not 2 GB). SpeechBrain + Whisper + BGE-M3 will not fit comfortably on a 2 GB instance. First boot may exceed a short health-check window; keep `/health` off that warmup (already the case).

## Env vars

Delete `IVR_LID_FORCE_LANGUAGE` if it is set to `en`. Do **not** put a real phone number in git. On Render, add a **secret** `CALLER_OVERRIDE_JSON` (same shape as `data/callers.local.json`):

```json
{"cards":[{"card_id":"stub-card-demo","phones":["+44YOURNUMBER"]}]}
```

Optional PIN: `"pin": "1234"` only inside that secret, never in `data/callers.example.json`.

Dashboard / `render.yaml` defaults:

```env
IVR_USE_SPEECHBRAIN_LID=true
IVR_STT_BACKEND=whisper
IVR_WHISPER_MODEL=base
INTENT_EMBEDDER=bge
IVR_USE_EDGE_TTS=true
IVR_PLAYBACK_REALTIME=false
DEBUG=true
TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1
```

Build log must print `lid_backend SpeechBrainLanguageIdentifier` and `intent_extras faster_whisper sentence_transformers`. Live logs: `IVR LID warmed class=SpeechBrainLanguageIdentifier`, `IVR STT backend=WhisperStreamingSpeechToText`, `IVR intent router warmed`, `caller store seeded demo_card=stub-card-demo`.

Twilio Voice webhook: `https://<your-host>/voice/incoming` (HTTP POST). Media Stream needs **WSS** on the same host.

## Native Python (only if you cannot use Docker)

1. **PYTHON_VERSION** = `3.12.8` (Torch has no 3.14 wheels).
2. **Build command:** `pip install -r requirements-render.txt`
3. **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. **HF_HOME** = `/opt/render/project/src/.cache/huggingface`
5. Remove `IVR_LID_FORCE_LANGUAGE` (or leave it empty).

## After deploy: phone checks

See the live smoke list in [FEAT-04](features/FEAT-04.md) (Phase 8 runbook). Short version:

1. Logs: SpeechBrain LID, Whisper STT, BGE embedder — not `scripted` / `HashTokenEmbedder` / `fixed-fallback`.
2. Trial Twilio: press any key if asked.
3. Unknown number: refusal, then the dialogue stops. Log `from_last4=` only.
4. Allowlisted number (from `CALLER_OVERRIDE_JSON`): language select, six-action menu, paraphrase for balance, PIN SMS line with **no digits**, block/unblock with last-4 confirm.
5. Mumble twice → DTMF 1–6. Hebrew/Arabic: Edge TTS if `IVR_USE_EDGE_TTS=true`.
6. Webhook TwiML has **no** `<Record>`.

If LID still shows `fixed-fallback`, Torch/SpeechBrain did not install — stay on Docker and 8 GB RAM.

**STT latency:** a rare slow Whisper turn is acceptable. If every 6–7 turns is slow, drop `IVR_WHISPER_MODEL` (for example `tiny`) or raise the instance size.
