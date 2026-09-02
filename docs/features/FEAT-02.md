# FEAT-02 — IVR spoken language selection (Twilio Media Streams)

| | |
|---|---|
| **Feature ID** | FEAT-02 |
| **Name** | IVR language selection |
| **Branch** | `feat/ivr-language-detector` |
| **Status** | Implemented (Phases 1–10 + post-live hardening) |
| **Target** | Real-time audio streaming + multi-lingual language routing at call start |

This is the single process/runbook document for the feature. It replaces the former root docs:

- `docs/ivr-language-selection-smoke.md` (Phase 9)
- `docs/ivr-language-id-speechbrain.md` (Phase 10)

Architectural decisions live under [`docs/adr/`](../adr/).

---

## Objective

When a caller dials the Twilio Voice number, the system must select a language before the rest of the IVR continues — primarily by spoken language identification (LID), with DTMF keypad fallback and speech barge-in during the DTMF menu.

Selection runs entirely on the bidirectional Media Stream WebSocket (not Twilio `<Gather>`). The Voice webhook only returns TwiML that starts the stream.

---

## User experience

Every FEAT document should include a section like this: what the caller hears and does, and what the feature does **not** do.

What the **caller** goes through on this feature (before any card tasks):

1. They dial the Twilio number. They do not hear a long “please wait” filler. The call connects to a live audio stream.
2. They are asked (in a language suggested by their phone’s country) to **say the name of the language** they want.
3. If they speak clearly, the system picks that language and continues. They should hear the rest of the IVR in that language when a matching voice exists.
4. If they stay silent for a few seconds, they hear a **keypad menu** (press 1 for English, and so on). They may also **interrupt the menu by speaking**; speech wins over the keypad.
5. If language ID is unsure, they are prompted again or dropped into the keypad path. The call does not yet ask for balance, PIN, or card actions — that is a later feature.

What this feature **does not** do for the caller: identify them as a customer, read card data, or record the call to disk.

---

## ADR correlation

Every decision for this feature maps to an ADR. ADR-001 is the hosting prerequisite; ADR-002–010 were written for this language-selection work.

| ADR | Title | Role | Phases |
|-----|-------|------|--------|
| [ADR-001](../adr/ADR-001.md) | Webhook retrieval and IVR platform (FastAPI + asyncio WebSockets) | Prerequisite platform for media-stream language selection | Foundation (before / alongside Phase 8 wiring) |
| [ADR-002](../adr/ADR-002.md) | Stream-native language selection (not TwiML Gather) | Core architecture: `LanguageSelector` owns prompt → listen → DTMF → done | Phase 6 design; Phase 8 live wiring |
| [ADR-003](../adr/ADR-003.md) | Twilio 8 kHz μ-law as the IVR audio contract | Wire format + PCM helpers; LID resamples to 16 kHz only at classifier | Phase 2 |
| [ADR-004](../adr/ADR-004.md) | Energy-based VAD for utterance boundaries | `speech_start` / `speech_end` → PCM buffer for LID | Phase 3 |
| [ADR-005](../adr/ADR-005.md) | TTS backends and burst playback into Twilio | SAPI/Piper/tone TTS, `.cache/ivr-tts`, `IVR_PLAYBACK_REALTIME=false`, Twilio `clear` | Phase 4; hardened after Phase 9 live |
| [ADR-006](../adr/ADR-006.md) | SpeechBrain LID with Fixed LID fallback | VoxLingua107 preferred; Fixed LID for smoke/CI; log-prob→confidence; warmup | Phase 5 (Fixed); Phase 10 (SpeechBrain) |
| [ADR-007](../adr/ADR-007.md) | DTMF fallback with speech barge-in | Silence → locale DTMF menu; digits or `speech_barge_in` via LID | Phase 6; hardened post Phase 9/10 |
| [ADR-008](../adr/ADR-008.md) | Caller locale from phone country data | `country_languages.json` → menu languages + prompt language | Phase 1 |
| [ADR-009](../adr/ADR-009.md) | Prefer major languages from LID top-k | Remap low-resource lookalikes (e.g. `br`→`fr`); clear on barge-in `speech_start` | Post Phase 10 live barge-in debugging |
| [ADR-010](../adr/ADR-010.md) | Offline harness before live Twilio smoke | Build order: offline → fake stream → Phase 9 fixed smoke → Phase 10 live speech | Process decision across Phases 1–10 |

---

## Key code / config map

| Area | Path |
|------|------|
| Webhook + media WS | `app/api/ivr.py` |
| App lifespan warmup | `app/main.py` |
| Deps (TTS / LID / VAD) | `app/deps.py` |
| Language selector | `services/ivr/language_selection.py` |
| μ-law / PCM helpers | `services/ivr/audio.py` |
| VAD | `services/ivr/vad.py` |
| TTS | `services/ivr/tts.py` |
| LID | `services/ivr/lid.py` |
| TwiML | `services/ivr/twiml.py` |
| Metrics | `services/ivr/metrics.py` |
| Selection store | `services/ivr/selection_store.py` |
| Locale core | `core/language/` |
| Config | `core/config.py` |
| Optional LID deps | `requirements-ivr-lid.txt` |
| TTS cache | `.cache/ivr-tts/` |
| Pytest | `tests/ivr/pytest/` |
| Manual verifiers | `tests/ivr/manual/` |
| Fake Twilio harness | `tests/ivr/manual/fake_twilio_stream.py` |

### Important settings

| Setting | Meaning |
|---------|---------|
| `IVR_USE_SPEECHBRAIN_LID` | Prefer SpeechBrain when true (default true if deps available) |
| `IVR_LID_FORCE_LANGUAGE` | If set (e.g. `en`), force Fixed LID — Phase 9 smoke profile |
| `IVR_SPEECHBRAIN_MODEL` | Default `speechbrain/lang-id-voxlingua107-ecapa` |
| `IVR_MIN_LID_CONFIDENCE` | Default `0.15` — reject LID below this |
| `IVR_SILENCE_TIMEOUT_S` | Default `5.0` — silence before DTMF fallback |
| `IVR_PLAYBACK_REALTIME` | Default `false` — burst into Twilio buffer (smooth). `true` ≈ 20ms pacing (often choppy live) |
| `IVR_VAD_RMS_THRESHOLD` | Default `250.0` — energy VAD sensitivity |
| `IVR_PIPER_MODEL_PATH` / `IVR_PIPER_BIN` | If set, use Piper TTS; else Windows SAPI; else tone stub |

---

## Phased implementation process

### Phase 1 — Caller locale / country language data

- **Goal:** Resolve languages and prompt language from the caller’s phone number.
- **Delivered:** `core/language` country tables; `resolve_caller_locale` → `CallerLocale`; DTMF menu builders and digit→language mapping.
- **Tests:** `tests/ivr/pytest/test_countries.py`
- **ADRs:** [ADR-008](../adr/ADR-008.md)
- **Notes:** Unknown numbers fall back to a default English-oriented prompt set.

### Phase 2 — μ-law / PCM audio helpers

- **Goal:** One Twilio-compatible audio contract for VAD, TTS, LID, and tests.
- **Delivered:** `services/ivr/audio.py` — mulaw↔pcm16, resample, chunking, tone generation; 8 kHz mono μ-law wire format.
- **Tests:** `test_audio.py`, `manual_verify_audio.py`
- **ADRs:** [ADR-003](../adr/ADR-003.md)
- **Notes:** SpeechBrain boundary resamples 8 kHz PCM → 16 kHz later (Phase 10).

### Phase 3 — Energy VAD

- **Goal:** Segment spoken utterances (`speech_start` / `speech_end` + PCM buffer).
- **Delivered:** `services/ivr/vad.py` — `EnergyVad` / `VadConfig`; configurable thresholds.
- **Tests:** `test_vad.py`, `manual_verify_vad.py`
- **ADRs:** [ADR-004](../adr/ADR-004.md)
- **Notes:** Simple RMS VAD chosen over neural VAD for speed and offline testability.

### Phase 4 — Text-to-speech

- **Goal:** Speak language-selection and DTMF-menu prompts on the call.
- **Delivered:** Piper (optional), Windows SAPI, tone stub; `CachedTextToSpeech` under `.cache/ivr-tts/`; startup warmup.
- **Tests:** `test_tts.py`, `manual_verify_tts.py`
- **ADRs:** [ADR-005](../adr/ADR-005.md)
- **Notes:** Cold Windows SAPI via PowerShell is ~2–3s; cache/warmup brings warm prompt start to a few hundred ms after stream connect. Production should use Piper or pre-rendered prompts — not SAPI+PowerShell.

### Phase 5 — Language ID (Fixed backend)

- **Goal:** Prove the selection path with a deterministic LID before SpeechBrain.
- **Delivered:** `FixedLanguageIdentifier`; `build_default_lid()` with force-language override.
- **Tests:** `test_lid.py` (fixed + contracts)
- **ADRs:** [ADR-006](../adr/ADR-006.md)
- **Notes:** Fixed LID remains the hard fallback and Phase 9 smoke profile.

### Phase 6 — Language selection state machine

- **Goal:** Stream-native selector: prompt → listen → (silence) → DTMF menu → done.
- **Delivered:** `LanguageSelector`; speech via VAD+LID; DTMF fallback; barge-in; metrics; methods `speech` \| `dtmf` \| `speech_barge_in`.
- **Tests:** `test_language_selection.py`, `manual_verify_language_selection.py`
- **ADRs:** [ADR-002](../adr/ADR-002.md), [ADR-007](../adr/ADR-007.md)
- **Notes:** No TwiML Gather — queues only (inbound audio, outbound media, DTMF).

### Phase 7 — Fake Twilio media-stream harness

- **Goal:** End-to-end offline: fake start → prompt audio out → DTMF/speech in → selected.
- **Delivered:** `fake_twilio_stream.py`, `manual_verify_media_stream.py`, pytest media-stream coverage.
- **Tests:** `test_media_stream_language.py`, `manual_verify_media_stream.py`
- **ADRs:** [ADR-010](../adr/ADR-010.md)
- **Notes:** Cannot fully reproduce acoustic echo or live network jitter.

### Phase 8 — TwiML + webhook + WebSocket wiring

- **Goal:** Real FastAPI endpoints: incoming Voice → Stream TwiML; WS media events.
- **Delivered:** `twiml.py`; `app/api/ivr.py` (media/DTMF/start/stop; send media + clear); webhook/WS unit tests.
- **Tests:** `test_twiml.py`, `test_webhook.py`, `test_websocket.py`, `manual_verify_webhook.py`
- **ADRs:** [ADR-001](../adr/ADR-001.md), [ADR-002](../adr/ADR-002.md)
- **Notes:** `CLEAR_AUDIO_SENTINEL` → Twilio Media Stream event `clear` (flush buffer).

### Phase 9 — Live Twilio smoke (Fixed LID + DTMF)

- **Goal:** First real handset call: hear prompt, select via speech(en)/DTMF, clean `STOP`.
- **Delivered:** Dev smoke profile; `manual_verify_smoke.py --phase 9`; burst playback default; TTS cache/warmup.
- **Tests:** Live call + readiness script
- **ADRs:** [ADR-005](../adr/ADR-005.md), [ADR-006](../adr/ADR-006.md), [ADR-010](../adr/ADR-010.md)
- **Notes:** Do not enable SpeechBrain until Phase 10. See [Phase 9 live smoke](#phase-9-live-smoke-runbook) below.

### Phase 10 — SpeechBrain LID (live multilingual)

- **Goal:** Real audio language-ID on the same port; Fixed LID remains fallback.
- **Delivered:** `SpeechBrainLanguageIdentifier` (VoxLingua107 ECAPA); `requirements-ivr-lid.txt`; Windows `inspect.py` path patch; log-prob→confidence; LID warmup; live handset speech as definitive test.
- **Tests:** `test_lid.py`, `manual_verify_lid.py --try-speechbrain`, live call
- **ADRs:** [ADR-006](../adr/ADR-006.md), [ADR-010](../adr/ADR-010.md)
- **Notes:** English SAPI reading French text is **not** a French LID test — speak French on the handset. See [Phase 10 live](#phase-10-live-speechbrain-runbook) below.

---

## Post-phase hardening (live calls)

| Issue | Fix | ADRs |
|-------|-----|------|
| Choppy / stuttering TTS with realtime frame pacing | Default `IVR_PLAYBACK_REALTIME=false` — burst enqueue into Twilio buffer | ADR-005 |
| Cold SAPI ~2–3s before first prompt | Disk/memory TTS cache + startup warmup | ADR-005 |
| DTMF registered but menu kept repeating / did not stop | Listen for `(mulaw_bytes/8000)+silence_timeout` after burst; Twilio `clear` on digit/selection | ADR-005, ADR-007 |
| Barge-in LID wrong (French → `br`; earlier English → `lb`) | Clear on VAD `speech_start` during barge-in; `prefer_major_language_from_topk` | ADR-009 |
| SpeechBrain load failed on Windows (lazy import / k2) | Patch `ensure_module` to treat `*inspect.py` basename as non-import | ADR-006 |
| Fake-classifier unit test returned language `'1'` after top-k remap | Without label encoder, keep `classify_batch` text label as top-1 | ADR-009 |

---

## Runtime call flow

1. `POST /voice/incoming` → TwiML `<Connect><Stream>` to media WebSocket (`from` / `country_code` query params attached).
2. WS `start` → `LanguageSelector.run(phone_number, inbound/outbound/dtmf queues)`.
3. Resolve `CallerLocale` from phone country.
4. **PROMPT:** TTS language-selection prompt; enqueue μ-law to Twilio.
5. **LISTEN:** VAD on inbound μ-law; on `speech_end` → `LID.identify(pcm16)`.
   - confidence ≥ `IVR_MIN_LID_CONFIDENCE` → selected (`method=speech`)
   - DTMF digit during listen → map digit to locale language (`method=dtmf`)
   - silence timeout → increment; after enough silences → `DTMF_MENU`
6. **DTMF_MENU:** speak locale keypad menu; listen in parallel (barge-in).
   - valid digit → clear playback → selected (`method=dtmf`)
   - speech → clear on `speech_start` → LID → selected (`method=speech_barge_in`) or retry menu if LID rejects
7. Persist last selection (`selection_store`); log `language_selected` + metrics.
8. WS `stop` / hangup → cancel tasks, close socket.

**Selection methods:** `speech` | `dtmf` | `speech_barge_in`

---

## Phase 9 live smoke runbook

First **live** Twilio call after offline Phases 1–8. Use the **dev smoke profile** only: English TTS + Fixed LID + DTMF. Do **not** enable SpeechBrain until Phase 10.

### Env (`.env`) minimum

```env
DEBUG=true
IVR_USE_SPEECHBRAIN_LID=false
IVR_LID_FORCE_LANGUAGE=en
IVR_PLAYBACK_REALTIME=false
```

**Notes:**

- `IVR_PLAYBACK_REALTIME=false` bursts TTS into Twilio’s buffer (smooth). `true` paces ~20ms frames and often sounds like regular micro-silences live.
- On Windows, leave Piper unset → SAPI (spoken English, not tones).
- Cold SAPI ~2–3s; after uvicorn warmup/cache, first prompt should start within a few hundred ms of media-stream connect.

### Run

1. Copy `.env.example` → `.env`; set Twilio credentials.
2. `.\venv\Scripts\python.exe tests\ivr\manual\manual_verify_smoke.py --phase 9`
3. `.\venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload`
4. Expose HTTPS/WSS (ngrok); Twilio Voice webhook → `https://<public-host>/voice/incoming`
5. Call from a phone whose country is in the locale table (e.g. UK `+44…` or IL `+972…`).

### Pass checklist (all must be true)

1. Trial “press any key” gate (if trial account) — expected
2. Server log: `Playing prompt … bytes=…` (bytes > 0)
3. You **hear** a spoken prompt (not silence; not only beeps unless tone stub intentional)
4. Speak **or** press a listed digit → `language_selected` / metrics `outcome=selected`
5. Hang up cleanly → `STOP` / WebSocket closed, no crash traceback spam

**Useful logs:** `incoming_call`, `Twilio Media Stream START`, `Playing prompt`, `DTMF digit` / `VAD speech_*`, `language_selected`, `Twilio Media Stream STOP`.

### Failure hints

| Symptom | Likely cause |
|---------|----------------|
| Only beeps | Tone TTS stub (non-Windows or SAPI failed) |
| Silence after connect | Prompt not played / zero bytes |
| Speech ignored, DTMF works | Expected with `force=en` if you speak another language |
| WebSocket never starts | Webhook URL / ngrok / `wss` host mismatch |
| Trial message only | Twilio trial — press a key, then continue |

---

## Phase 10 live SpeechBrain runbook

Upgrade from Fixed/dev LID to **real** audio language-ID. Fixed LID remains fallback when SpeechBrain is unavailable or forced off.

**Definitive test:** speak the language **yourself** on the phone. Do **not** use multilingual SAPI / downloaded voices / French text read by English TTS — those test the synthesizer accent, not LID.

### Env (`.env`)

```env
IVR_USE_SPEECHBRAIN_LID=true
IVR_LID_FORCE_LANGUAGE=
IVR_MIN_LID_CONFIDENCE=0.15
IVR_SPEECHBRAIN_MODEL=speechbrain/lang-id-voxlingua107-ecapa
```

### Dependencies

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-ivr-lid.txt
```

On Windows, `services/ivr/lid.py` patches SpeechBrain’s lazy-import `inspect.py` path guard so the model loads without optional `k2`. Model downloads on first load.

### Readiness + call

```powershell
.\venv\Scripts\python.exe tests\ivr\manual\manual_verify_smoke.py --phase 10
```

Then: uvicorn + public webhook (ngrok), dial the number. After the prompt, speak French (or another language) in a normal sentence.

**Pass when logs show:**

- LID backend is SpeechBrain (not `fixed`)
- `language_selected language=<spoken>` (e.g. `fr`)
- metrics `outcome=selected`
- clean hangup (`STOP`)

DTMF remains fallback if speech LID is unclear. Prompt may still be English SAPI — that only affects what you hear, not what you speak.

### Offline wiring only (not multilingual proof)

```powershell
.\venv\Scripts\python.exe -m pytest tests/ivr/pytest/test_lid.py -q
.\venv\Scripts\python.exe tests\ivr\manual\manual_verify_lid.py --try-speechbrain
```

Contract tests may use a fake classifier. Multilingual acceptance = live handset speech.

---

## Empirical notes (live debugging)

Not a formal load suite — observed on handset:

| Observation | Detail |
|-------------|--------|
| Warm prompt start | Cached SAPI → Twilio: ~few hundred ms after stream start (vs cold SAPI 2–3s) |
| Clear English barge-in | LID `en` ~0.96 confidence — accepted |
| French barge-in (pre-remap) | Raw top `br` ~0.52, `fr` #2 ~0.29 → remapped to `fr` ([ADR-009](../adr/ADR-009.md)) |
| DTMF menu repeat | Digits registered but menu repeated until listen-duration + Twilio `clear` fixed |
| Menu contamination | Twilio `clear` must run on barge-in `speech_start`, not only after utterance end |

---

## Testing inventory

| Kind | Location |
|------|----------|
| Pytest | `tests/ivr/pytest/` |
| Manual | `tests/ivr/manual/` |
| Readiness | `tests/ivr/manual/manual_verify_smoke.py --phase 9\|10` |

```powershell
.\venv\Scripts\python.exe -m pytest tests/ivr/pytest/ -q
.\venv\Scripts\python.exe tests\ivr\manual\manual_verify_smoke.py --phase 10
```
