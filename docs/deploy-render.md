# Deploying the IVR on Render

Spoken language ID needs **SpeechBrain + CPU Torch on Python 3.12**. If logs say `FixedLanguageIdentifier` / `fixed-fallback`, every utterance is treated as English.

## Recommended: Docker

In the Render dashboard, set the service **Language / Environment to Docker** (not native Python). Root directory is the repo; Dockerfile is `./Dockerfile`.

The image:

- uses Python 3.12.8
- installs `requirements-render.txt` (Torch CPU + SpeechBrain)
- downloads the VoxLingua107 model at **build** time

Instance size: **at least 2 GB RAM**.

Env vars (delete `IVR_LID_FORCE_LANGUAGE` if it is set to `en`):

```env
IVR_USE_SPEECHBRAIN_LID=true
IVR_STT_BACKEND=scripted
IVR_STT_SCRIPT=balance,goodbye
IVR_PLAYBACK_REALTIME=false
DEBUG=true
TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1
```

Build log must print `lid_backend SpeechBrainLanguageIdentifier`. Live logs: `IVR LID warmed class=SpeechBrainLanguageIdentifier backend=speechbrain`.

Twilio Voice webhook: `https://<your-host>/voice/incoming` (HTTP POST).

## Native Python (only if you cannot use Docker)

1. **PYTHON_VERSION** = `3.12.8` (Torch has no 3.14 wheels).
2. **Build command:** `pip install -r requirements-render.txt`
3. **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. **HF_HOME** = `/opt/render/project/src/.cache/huggingface`
5. Remove `IVR_LID_FORCE_LANGUAGE` (or leave it empty). With SpeechBrain on, a leftover `en` value is ignored, but empty is clearer.

## After deploy: phone checks

VoxLingua107 identifies **many** spoken languages (not only English/French). Canned menus still have full text for English and French; other languages are detected, then the catalog may answer in English until those lines exist.

1. Logs: `class=SpeechBrainLanguageIdentifier`, not `FixedLanguageIdentifier`.
2. Trial Twilio: press any key if asked.
3. New call, speak **French**, pause → `language_selected language=fr method=speech`.
4. New call, speak **English**, pause → `language=en`.
5. After the task menu, speech is still **scripted** on Render (`balance` then `goodbye` on each pause). That is not LID.

If you still see `fixed-fallback`, the build did not install Torch/SpeechBrain or the model download failed — use the Docker environment and a 2 GB instance.
