# FEAT-03 — Streaming voice path and TTFB (templated IVR)

| | |
|---|---|
| **Feature ID** | FEAT-03 |
| **Name** | Low-latency STT/TTS plumbing + Time-To-First-Audio-Byte |
| **Branch** | `feat/ivr-latency-audio-pipeline` |
| **Status** | Implemented (Phases 1–8) + Phase 7b |
| **Target** | After the caller stops talking, canned reply audio starts sending in under ~0.5s |

This is the process/runbook for the latency pipeline. Language selection remains [FEAT-02](FEAT-02.md). Architectural decisions for this feature start at [ADR-011](../adr/ADR-011.md).

---

## Product lock-in (this branch)

- **No LLM** on the call path. Replies are fixed lines or tiny placeholders that simulate tasks. The code should still leave a clear place to plug in generated speech later (5s TTFB budget, not 0.5s).
- **Language ID once** at the start of the call (today’s SpeechBrain / Fixed LID). Do **not** re-detect language on every sentence.
- **Menus and error lines** are pre-built audio, not synthesized on every play.
- **Start free / local or stub engines** so tests and demos need no paid account. Backends sit behind small interfaces so a paid cloud STT/TTS can replace them later without rewriting the call flow.
- **Privacy:** cloud speech is allowed for a later prototype. Keep a written in-house (on-machine) option. Audio still must not be stored (existing IVR rule).
- **Languages:** design for every language the future LLM would understand; **this branch tests English + 1–2 others**. Remaining voices last.
- **No “one moment…” filler** this branch.
- **Proof:** pytest with fake engines (CI) **and** a live Twilio listen-check when the pipeline is wired.
- **Render:** Python 3.12 + `requirements-render.txt` (SpeechBrain CPU). See [deploy-render.md](../deploy-render.md). Windows SAPI STT is not used on Linux.
- **Optimize for a fast demo**, with notes below on what a more private/production path would change.

## What “0.5 seconds” means

| | |
|---|---|
| Stopwatch **starts** | Caller has stopped talking (voice volume dropped → VAD `speech_end`) |
| Stopwatch **stops** | System **starts sending** reply audio outbound |
| Canned replies | **Usually** (median) ≤ 500 ms — one slow outlier does not fail the SLO |
| Future LLM replies | Usually ≤ 5 s (not built here) |

---

## Phases

Each phase is a small slice with its own tests. Do not start the next phase until the current one is green.

### Phase 1 — TTFB timer harness

- **Goal:** One shared stopwatch with a frozen definition of start/stop, budgets, and “typical” (median) canned SLO.
- **Delivered:** `services/ivr/ttfb.py`; [ADR-011](../adr/ADR-011.md).
- **Tests:** `tests/ivr/pytest/test_ttfb.py` (fake clock; no Twilio, no vendors).
- **Done when:** pytest for TTFB passes; harness is not yet wired into live calls.

### Phase 2 — Phrase catalog + instant cache

- **Goal:** Menus, errors, and placeholder-task lines have stable **phrase IDs**. Hot path is “look up ready audio,” not “ask TTS again.”
- **Delivered:** `core/language/phrases.json`, `core/language/phrases.py`, `services/ivr/phrase_cache.py`; [ADR-012](../adr/ADR-012.md). App startup warms English (and French when TTS can speak it).
- **Tests:** `tests/ivr/pytest/test_phrase_cache.py` — cache hit does not call the synthesizer; English + French fixtures; ready lookup TTFB is dict-only.
- **Depends on:** Phase 1 only for the lookup TTFB check.
- **Done when:** pytest for phrases passes. Language selector still uses text TTS for dynamic DTMF menus.

### Phase 3 — Streaming TTS interface + free stub

- **Goal:** TTS can emit μ-law **chunks**. First chunk is `mark_first_audio_byte`. Stub/tone (and later Piper) work without a paid API.
- **Delivered:** `services/ivr/streaming_tts.py`; [ADR-013](../adr/ADR-013.md). `ToneStreamingTextToSpeech` stub; `BatchStreamingTextToSpeech` wraps existing engines; `stream_ready_phrase` for warmed catalog lines; `enqueue_tts_stream` for outbound + TTFB.
- **Tests:** `tests/ivr/pytest/test_streaming_tts.py`.
- **Later swap:** paid streaming TTS (e.g. Cartesia) implements the same protocol. In-house: stream from a local engine on that protocol.
- **Done when:** pytest for streaming TTS passes. Not yet wired into live Twilio playback.

### Phase 4 — Streaming STT interface + free stub

- **Goal:** Inbound audio can be transcribed through a **StreamingSTT** protocol. CI uses a stub (fixed phrases). Still use energy VAD for “caller stopped talking.”
- **Delivered:** `services/ivr/streaming_stt.py`; [ADR-014](../adr/ADR-014.md). `ScriptedStreamingSpeechToText`; `feed_until_speech_end` ties VAD `speech_end` to TTFB start + `finish()`.
- **Tests:** `tests/ivr/pytest/test_streaming_stt.py` — known transcript after `speech_end`; no network; backend swaps by constructor.
- **Later swap:** paid streaming STT (e.g. Deepgram) or local Whisper-class on the same protocol.
- **Done when:** pytest for streaming STT passes. Live Twilio still uses LID, not this STT.

### Phase 5 — Simulated turn engine (placeholder tasks)

- **Goal:** After language is already known: `speech_end` → stub transcript → canned phrase → stream first audio. Measure TTFB. Placeholder “tasks” (e.g. fake balance) are extra audio prompts, not real card APIs.
- **Delivered:** `services/ivr/placeholder_intents.py`, `services/ivr/turn_engine.py`; [ADR-015](../adr/ADR-015.md).
- **Tests:** `tests/ivr/pytest/test_turn_engine.py` — English + French; unknown → error phrase; median canned TTFB ≤ 500 ms; no TTS on warmed hot path.
- **Done when:** pytest for the turn engine passes. Still not on a live Twilio call.

### Phase 6 — Fake Twilio media-stream wiring

- **Goal:** Same turn engine on inbound/outbound queues like FEAT-02’s fake stream.
- **Delivered:** Handoff in `app/api/ivr.py` after language selection; `services/ivr/turn_store.py`; [ADR-016](../adr/ADR-016.md).
- **Tests:** `tests/ivr/pytest/test_media_stream_turns.py`; optional `manual_verify_media_stream.py --mode placeholder`.
- **Done when:** pytest fake stream shows language select then a canned task reply. Live Twilio now also enters placeholder turns after selection (stub STT → “did not catch that” unless scripted).

### Phase 7 — Live Twilio smoke (English + 1–2 languages)

- **Goal:** Hear canned replies start quickly on a real phone. Official clock remains the harness; the call is a sanity check.
- **Delivered:** [ADR-017](../adr/ADR-017.md); `IVR_STT_SCRIPT`; `placeholder_turn` logs with `ttfb_ms`; `manual_verify_smoke.py --phase 7`.
- **Tests:** pytest stays offline. You verify the checklist below on a handset.

### Phase 7b — TTS voice matches call language

- **Goal:** After language selection, spoken replies use a **voice for that language**. English-only SAPI must not read French (or other) catalog text. Architecture scales to any LLM language: add a Piper model, a Windows speech pack, or later a cloud TTS that takes `language` — do not hard-code en/fr in the engine.
- **Delivered:** [ADR-018](../adr/ADR-018.md); `RoutedTextToSpeech`; SAPI `SelectVoice` from installed cultures; Piper per-language map (`IVR_PIPER_VOICES` / `IVR_PIPER_VOICE_DIR`); phrase cache plays English audio if there is no matching voice.
- **Tests:** `tests/ivr/pytest/test_tts_routing.py`; English-only TTS must not synth French copy (`test_phrase_cache.py`).
- **Depends on:** Phase 7.
- **Done when:** pytest green; live call: pick French → either real French speech **or** clear English (not English-accented French). Startup log `TTS spoken languages=…` lists what this machine can actually speak.
- **How to hear French (or any other language) on this PC:**
  1. **Windows:** Settings → Time & language → Language & region → add the language → speech / TTS voice, **or**
  2. **Piper:** download a voice `.onnx` whose name starts with the locale (`fr_FR-…`), set `IVR_PIPER_VOICE_DIR` or `IVR_PIPER_VOICES`, restart, delete old `.cache/ivr-phrases` files for that language if they were built with the wrong voice.
  3. **Later:** one cloud TTS backend for many locales (same `synthesize(text, language)` interface).

### Phase 8 — Live speech understanding (local grammar STT)

- **Goal:** After language selection, saying **balance**, **PIN**, **block**, or **goodbye** (or French **solde** / **bloquer** / **au revoir**) plays the matching canned line. No `IVR_STT_SCRIPT` required.
- **Delivered:** [ADR-019](../adr/ADR-019.md); `GrammarStreamingSpeechToText` in `services/ivr/sapi_stt.py`; `IVR_STT_BACKEND=sapi`. Default remains the scripted stub for pytest.
- **Tests:** `tests/ivr/pytest/test_sapi_stt.py` (fake recognizer; no PowerShell, no network).
- **Depends on:** Phases 5–7b.
- **Done when:** pytest green; live call with `IVR_STT_BACKEND=sapi` — you say “balance”, pause, hear the fake-balance line. Log `grammar_stt … text='balance'`.
- **Note:** Recognition time sits on the TTFB clock, so live `ttfb_ms` will often be **over 500 ms**. That is expected until a streaming vendor (Deepgram, etc.) is swapped in. Warmed playback after the transcript is still instant.



---

## Notes for a later production / private path (optimize-for-B)

Not in this branch’s demo path, but keep in mind:

| Topic | This branch | Later (Yordex-shaped) |
|--------|-------------|------------------------|
| STT/TTS | Stubs + free local; easy paid cloud plug-in | Prefer on-machine models if voice must not leave the network; or a contracted cloud with DPA |
| Cost | $0 for CI | Paid streaming only if quality/latency needs it |
| LLM | Slot reserved, 5s TTFB | Constrained templates first; LLM only where needed |
| Voices | En + 1–2 for tests | Pre-render phrase cache for each supported language |
| SLO | Median TTFB in-process | Also measure live; keep burst vs realtime playback lessons from ADR-005 |
| Language | LID once per call | Same — do not run LID every sentence |

---

## ADR correlation

| ADR | Title | Role |
|-----|--------|------|
| [ADR-011](../adr/ADR-011.md) | TTFB clock for IVR reply audio | Phase 1 definition |
| [ADR-012](../adr/ADR-012.md) | Phrase IDs and ready audio buffers | Phase 2 catalog + hot-path lookup |
| [ADR-013](../adr/ADR-013.md) | Streaming TTS protocol (chunked μ-law) | Phase 3 stub + batch adapter |
| [ADR-014](../adr/ADR-014.md) | Streaming STT protocol with local VAD utterance bounds | Phase 4 stub + speech_end |
| [ADR-015](../adr/ADR-015.md) | Placeholder turn engine (templated intents) | Phase 5 speech_end → canned reply |
| [ADR-016](../adr/ADR-016.md) | Handoff to placeholder turns on the Media Stream | Phase 6 fake Twilio wiring |
| [ADR-017](../adr/ADR-017.md) | Live smoke for canned TTFB (scripted STT) | Phase 7 handset checklist |
| [ADR-018](../adr/ADR-018.md) | Match TTS voice to call language | Phase 7b; no cross-language voices |
| [ADR-019](../adr/ADR-019.md) | Local grammar STT for placeholder tasks | Phase 8; Windows SAPI commands |
| [ADR-003](../adr/ADR-003.md) | 8 kHz μ-law wire format | Unchanged |
| [ADR-004](../adr/ADR-004.md) | Energy VAD `speech_end` | Clock start |
| [ADR-005](../adr/ADR-005.md) | TTS + burst playback | Playback; phrase cache will extend this |
| [ADR-010](../adr/ADR-010.md) | Offline before live | Same build order |

---

## Key code (Phases 1–8)

| Area | Path |
|------|------|
| TTFB harness | `services/ivr/ttfb.py` |
| Phrase catalog | `core/language/phrases.json`, `core/language/phrases.py` |
| Ready-audio cache | `services/ivr/phrase_cache.py` |
| Streaming TTS | `services/ivr/streaming_tts.py` |
| Streaming STT | `services/ivr/streaming_stt.py` |
| Placeholder turns | `services/ivr/placeholder_intents.py`, `services/ivr/turn_engine.py` |
| Media-stream handoff | `app/api/ivr.py`, `services/ivr/turn_store.py` |
| TTS routing | `services/ivr/tts.py`, `services/ivr/tts_lang.py` |
| Grammar STT | `services/ivr/sapi_stt.py` |
| Live smoke | `tests/ivr/manual/manual_verify_smoke.py --phase 7` / `--phase 7b` / `--phase 8` |
| Pytest | `tests/ivr/pytest/test_ttfb.py`, `test_phrase_cache.py`, `test_streaming_tts.py`, `test_streaming_stt.py`, `test_turn_engine.py`, `test_media_stream_turns.py`, `test_tts_routing.py`, `test_sapi_stt.py` |

```powershell
.\venv\Scripts\python.exe -m pytest tests/ivr/pytest/test_ttfb.py tests/ivr/pytest/test_phrase_cache.py tests/ivr/pytest/test_streaming_tts.py tests/ivr/pytest/test_streaming_stt.py tests/ivr/pytest/test_turn_engine.py tests/ivr/pytest/test_media_stream_turns.py tests/ivr/pytest/test_media_stream_language.py tests/ivr/pytest/test_tts_routing.py tests/ivr/pytest/test_tts.py tests/ivr/pytest/test_sapi_stt.py -q
.\venv\Scripts\python.exe tests\ivr\manual\manual_verify_smoke.py --phase 8
```

---

## Phase 7 live smoke runbook

Prove canned replies on a **real phone**. The official 0.5s clock is the server log (`placeholder_turn … ttfb_ms=`), not how long the network takes to reach your ear.

The speech-to-text stub still **does not understand** what you say. Set `IVR_STT_SCRIPT` so each pause after speech plays the next canned line.

### Env (`.env`)

```env
DEBUG=true
IVR_PLAYBACK_REALTIME=false
IVR_STT_SCRIPT=balance,goodbye
```

Keep your existing language-selection settings (Phase 9 fixed English **or** Phase 10 SpeechBrain).

After Phase 7b, Windows SAPI only speaks languages that have an installed speech pack. Without a French voice, a French selection plays **English** lines (intelligible), not French words in an English accent.

### Run

1. `.\venv\Scripts\python.exe tests\ivr\manual\manual_verify_smoke.py --phase 7`
2. `.\venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000`
3. Expose HTTPS/WSS (ngrok); Twilio Voice webhook → `https://<public-host>/voice/incoming`
4. Call the number.

### Pass checklist (all must be true)

1. Trial “press any key” gate (if trial) — expected
2. Hear language-selection prompt; select by **DTMF** or speech
3. Log: `language_selected` — then you **hear** the task menu
4. Speak, then pause. Hear a canned reply:
   - with `IVR_STT_SCRIPT=balance` → fake-balance line
   - with empty script → “did not catch that”
5. Log: `placeholder_turn phrase=… ttfb_ms=…` (typical `ttfb_ms` ≤ 500)
6. Optional second call: French catalog if TTS can speak `fr`; otherwise English is enough for this branch
7. Hang up → `STOP` / WebSocket closed, no crash traceback spam

**Useful logs:** `incoming_call`, `Playing prompt`, `language_selected`, `TTS spoken languages`, `IVR phrase cache warmed`, `placeholder_turn`, `Twilio Media Stream STOP`.

---

## Phase 7b live listen-check

1. Restart the app after `.env` TTS changes. Delete `.cache/ivr-phrases` if you previously heard mangled French (old files were English-voiced French).
2. Startup log `TTS spoken languages=` — if `fr` is missing, you will not hear French yet.
3. Select French on the call:
   - **No French voice:** menu and replies are clear English.
   - **French SAPI pack or Piper `fr_*.onnx`:** menu and replies are French, spoken as French.
4. Add another LLM language the same way: a Piper file named `{locale}-….onnx` or a Windows voice for that language, then add catalog text later.

---

## Phase 8 live listen-check (the phone hears you)

In **`.env`**:

```env
IVR_STT_BACKEND=sapi
```

Restart the server. Startup log should show `IVR STT backend=GrammarStreamingSpeechToText`.

1. Call, pick a language, wait for the task menu.
2. Say **balance** (or **solde** if the call language is French), then pause.
3. Hear the fake-balance line. Log: `grammar_stt … text='balance'` then `placeholder_turn phrase=placeholder_balance`.
4. `ttfb_ms` may be **above 500** — that includes Windows recognition time. Expected this phase.
5. Try **goodbye** to hear the goodbye line.

Windows speech recognition must be available (it usually is for English). French commands need a French recognizer pack, same idea as TTS voices.


