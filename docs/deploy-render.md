# Deploying the IVR on Render

The phone path needs **Python 3.12**, **SpeechBrain + CPU Torch** (language ID), **Edge TTS**, and **scripted STT** (Windows SAPI does not run on Linux).

## Dashboard (existing service)

1. **Instance:** at least **2 GB RAM** (SpeechBrain on 512 MB often dies).
2. **Environment → PYTHON_VERSION** = `3.12.8` (not 3.14).
3. **Build command:** `export HF_HOME=/opt/render/project/src/.cache/huggingface && pip install -r requirements-render.txt && PYTHONPATH=. python -c "from services.ivr.lid import build_default_lid; print(type(build_default_lid()).__name__)"`
4. **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Env vars:

```env
IVR_USE_SPEECHBRAIN_LID=true
IVR_LID_FORCE_LANGUAGE=
IVR_STT_BACKEND=scripted
IVR_STT_SCRIPT=balance,goodbye
IVR_PLAYBACK_REALTIME=false
DEBUG=true
HF_HOME=/opt/render/project/src/.cache/huggingface
```

Leave `IVR_LID_FORCE_LANGUAGE` **empty**. First build downloads the LID model (can take several minutes). Build log must print `SpeechBrainLanguageIdentifier`, not `FixedLanguageIdentifier`.

After boot, live logs should include `IVR LID warmed backend=SpeechBrainLanguageIdentifier` (may appear a few seconds after `/health` is up). Phrase-cache warmup is also backgrounded: `IVR phrase cache warmed count=` may arrive after health is already green.

Wait until that LID line appears before the first real call if you can (first call during model load can stall).

## After deploy: phone checks

Twilio Voice webhook must stay `https://<your-host>/voice/incoming` (HTTP POST). Do not put `/media-stream` in the number config.

On Render, speech **after** the menu is **scripted**: the app does not hear “balance”. It plays **balance**, then **goodbye**, each time you speak and then pause. (Windows grammar STT is local-only.)

1. Open Render logs. Confirm `TTS spoken languages=` includes `en` and `fr`, STT backend is `ScriptedStreamingSpeechToText`, script is `balance,goodbye`, then `IVR LID warmed backend=SpeechBrainLanguageIdentifier`.
2. Trial Twilio: press any key if asked.
3. **French LID:** at the first prompt, say a few seconds of **French**, then pause. Log should show `language_selected language=fr method=speech`. You should hear the French task menu (Edge `fr` voice), not English-accented French.
4. **English LID:** new call, speak **English**, pause. Expect `language=en` and the English menu.
5. **DTMF fallback:** new call, stay silent ~5s until the keypad menu. Keys follow the **caller’s country list** (not a global “2 = French”). Example: US number often `1` English, `4` French; a UK number’s list may not include French — use speech LID for French instead.
6. After the task menu: **speak anything, pause** → fake-balance line. Speak/pause again → goodbye. Log: `placeholder_turn phrase=placeholder_balance` then `goodbye`, with `ttfb_ms=` (ear delay can be longer than that number).
7. Hang up. Log: Media Stream `STOP`, no crash traceback.

If French at the first prompt still selects English, the LID model did not load (`FixedLanguageIdentifier` in logs) or `IVR_LID_FORCE_LANGUAGE` is set.

