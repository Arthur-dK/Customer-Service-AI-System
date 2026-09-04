# FEAT-04 — IVR intent router (caller ID + semantic actions)

| | |
|---|---|
| **Feature ID** | FEAT-04 |
| **Name** | Local semantic intent router, stub caller store, privacy |
| **Branch** | `feat/ivr-intent-router` |
| **Status** | Phase 1–8 done. |
| **Target** | Route spoken requests to stub card actions; identify callers by phone number; never record call audio |

Every FEAT file must include a **User experience** section (what the caller hears and does, and what the feature does not do).

Language selection remains [FEAT-02](FEAT-02.md). Streaming plumbing and canned TTFB remain [FEAT-03](FEAT-03.md). New ADRs for this feature are written **in the phase that needs them**, not up front.

---

## User experience

After the caller already has a language ([FEAT-02](FEAT-02.md)):

1. **Identity.** The system uses the inbound phone number (Twilio `From`) as the only security check. If the number is missing, withheld, or not in the stub allowlist, they hear a short refusal and the call ends. They never reach card actions.
2. **Menu.** An allowlisted caller hears a menu of six voice actions: balance, PIN (sent by SMS — never read aloud), card details, statement, block card, unblock card. Goodbye is not offered.
3. **Speaking.** They may paraphrase (for example “how much is left on my card”) in English, Hebrew, or Arabic. Speech-to-text uses the **language already chosen at the start of the call**; it does not run language ID again.
4. **GET actions** (balance, PIN notice, card, statement) play a **one-shot** stub reply using fake data from the local store. Nothing is charged or issued for real. PIN digits are never spoken.
5. **Block / unblock.** They hear a restatement that includes the stub card last-4, then “are you sure?”. Only after a clear yes does the stub `blocked` flag change and they hear the result. No (or unclear) counts as a failed turn.
6. **Not understood.** Two low-confidence voice turns in a row (including “do both balance and block”) switch to a **DTMF menu** (keys 1–6). If voice confirmation is also unclear twice, confirm with keys (1 yes / 2 no).
7. **Privacy.** They should assume the call is **not recorded**. Utterance audio stays in memory and is dropped after the turn. Logs must not contain their full number, transcript, or PIN.

Demo data is fake. Operators add their own number in a **gitignored** overlay or a Render secret, never in git.

---

## Product lock-in

- No issuer API and no real SMS send. Stub SQLite + example JSON only.
- Embeddings only for the six actions (no keyword fast-path). Two prototype phrases per action per `en` / `he` / `ar`.
- In-process BGE-M3 in demo/Render; pytest uses a fake embedder.
- Local faster-whisper for full sentences; pytest keeps scripted STT.
- LID once per call (FEAT-02). New modules must not classify language.
- Boot may be slow (warm models). Caller-path STT+embed may exceed 500 ms; warmed static phrases still aim for ~500 ms lookup.
- Render must run this stack by merge to main (plan a larger instance than SpeechBrain-only).

---

## Phases

Each phase has its own tests. Do not treat a later phase as done if an earlier pytest file is red. ADRs are added when that phase is implemented.

### Phase 0 — Documentation (this slice)

- **Goal:** UX on every FEAT file; this runbook before behavior changes.
- **Delivered:** UX in FEAT-02 / FEAT-03 / FEAT-04.
- **Tests:** none (markdown).
- **Done when:** those sections exist. No ADRs yet.

### Phase 1 — Stub caller/card store

- **Goal:** Allowlist + fake card rows without PII in git.
- **Delivered:** `core/cards/`; `data/callers.example.json`; gitignore overlay/sqlite; [ADR-020](../adr/ADR-020.md).
- **Tests:** `tests/cards/test_caller_store.py`.
- **Done when:** hit/miss, multi-phone, withheld; example JSON has no `pin` and only reserved test numbers.

### Phase 2 — Semantic router

- **Goal:** Text → one of six actions or reject.
- **Delivered:** `core/intents/`; [ADR-021](../adr/ADR-021.md).
- **Tests:** `tests/intents/test_intent_router.py` (hash embedder). Optional `@slow` real BGE-M3.
- **Done when:** EN/HE/AR paraphrases route; ambiguous two-intent text rejects.

### Phase 3 — Privacy

- **Goal:** No call recording; no secrets in logs.
- **Delivered:** last-4 log helper on the live path; TwiML has no `<Record>`; [ADR-022](../adr/ADR-022.md).
- **Tests:** `tests/ivr/pytest/test_privacy_ivr.py`; `tests/ivr/pytest/test_twiml.py`.
- **Done when:** simulated call creates no `.wav`/`.mp3` under tmp; caplog has no PIN digit runs or full E.164.

### Phase 4 — Local full-sentence STT

- **Goal:** Paraphrases can reach the router.
- **Delivered:** `services/ivr/whisper_stt.py`; `IVR_STT_BACKEND=whisper`; [ADR-023](../adr/ADR-023.md).
- **Tests:** `tests/ivr/pytest/test_whisper_stt.py` (injected recognizer). Optional `@slow`.
- **Done when:** stub tests pass; language comes from `start(language=)`, not a new LID module.

### Phase 5 — Dialogue (offline)

- **Goal:** Allowlist, GET vs two-step confirm, 2-fail DTMF — no Twilio.
- **Delivered:** `services/ivr/intent_turns.py`; [ADR-024](../adr/ADR-024.md).
- **Tests:** `tests/ivr/pytest/test_intent_turns.py`.
- **Done when:** unknown hangup, GET, confirm yes/no, two rejects → DTMF.

### Phase 6 — Catalog + HE/AR

- **Goal:** Spoken copy for menus, confirms, GET templates.
- **Delivered:** `phrases.json` `en`/`he`/`ar`; PIN-via-SMS (no digits); [ADR-025](../adr/ADR-025.md).
- **Tests:** `tests/ivr/pytest/test_catalog_he_ar.py`; PIN phrases have no digit sequences.
- **Done when:** pytest green without live Twilio.

### Phase 7 — Media-stream wiring

- **Goal:** Live WS path after language select.
- **Delivered:** `app/api/ivr.py` + deps; `render.yaml` not scripted goodbye.
- **Tests:** `tests/ivr/pytest/test_media_stream_turns.py` (allowlisted fake number + unknown hangup).
- **Done when:** fake-stream pytest green.

### Phase 8 — Render + live smoke

- **Goal:** Hosting notes and handset checklist.
- **Delivered:** Docker/requirements/RAM notes; boot seed; runbook below.
- **Tests:** `tests/ivr/pytest/test_render_stack.py` (offline).
- **Done when:** this checklist is accurate; image includes whisper + embedding extras.

---

## Planned ADRs (write with the matching phase)

Do not add these files until the phase that needs them.

| ADR | Title | Phase |
|-----|--------|-------|
| [ADR-020](../adr/ADR-020.md) | Stub caller/card SQLite + gitignore overlay | 1 |
| [ADR-021](../adr/ADR-021.md) | BGE-M3 prototype router | 2 |
| [ADR-022](../adr/ADR-022.md) | Privacy: in-memory audio, log redaction, no TwiML Record | 3 |
| [ADR-023](../adr/ADR-023.md) | faster-whisper on selected language, thread offload | 4 |
| [ADR-024](../adr/ADR-024.md) | GET vs two-step confirm + 2-fail DTMF | 5 |
| [ADR-025](../adr/ADR-025.md) | HE/AR catalog + Edge TTS warmup | 6 |

---

## Stub data vs git (Phase 1+)

```text
data/callers.example.json   # committed: fake numbers only, no real PII, no real PINs
data/callers.local.json     # gitignored: you add your real From + stub card/PIN locally
data/callers.sqlite         # gitignored: built at boot from example + local overlay
CALLER_OVERRIDE_JSON        # Render secret env: same overlay without committing files
```

Schema (one card, many phones): card last-4, fake balance, currency, blocked flag, short statement blurb; PIN **only** from local overlay. Tests use tmp SQLite + fake `+15555550100`-style numbers.

---

## Pytest (offline)

```powershell
.\venv\Scripts\python.exe -m pytest -q -m "not slow"
```

---

## Phase 8 live smoke runbook

Prove the **intent** IVR on a real phone. Overlay your number without committing it. Official TTFB for canned lines is still the server log (`intent_turn`), not ear delay.

### Overlay (never git)

Local file `data/callers.local.json` (gitignored), **or** Render secret `CALLER_OVERRIDE_JSON`:

```json
{"cards":[{"card_id":"stub-card-demo","phones":["+44YOURNUMBER"]}]}
```

Optional `"pin"` only in that overlay. Example seed last-4 is `4242`; stub balance is “one hundred US dollars”.

### Env

```env
IVR_STT_BACKEND=whisper
IVR_WHISPER_MODEL=base
INTENT_EMBEDDER=bge
IVR_USE_EDGE_TTS=true
IVR_PLAYBACK_REALTIME=false
IVR_USE_SPEECHBRAIN_LID=true
```

Local Windows without SpeechBrain still starts (fixed English LID + DTMF). Render Docker must show SpeechBrain. Do **not** set `IVR_STT_SCRIPT`.

### Calls

1. **Unknown `From`:** language select, then refusal, no menu. Log `from_last4=` only (not full E.164). Dialogue stops.
2. **Allowlisted:** six-action menu (goodbye is not offered). Paraphrase balance (“how much is left on the card”) → stub balance text.
3. **Block:** restatement with last-4 `4242`; say yes → blocked. Unblock the same way. No must not mutate.
4. **PIN:** SMS placeholder; **no digits** spoken or logged.
5. **Mumble twice** → DTMF 1–6. Unclear confirm twice → 1 yes / 2 no.
6. **Ambiguous** (“block the card and what is my balance”) counts as a failed turn.
7. Webhook XML: **no** `<Record>`.
8. **Hebrew / Arabic:** pick that language at start; speak a prototype from `core/intents/prototypes.json`; hear catalog TTS if Edge/Piper/SAPI can speak it.

**STT latency:** a rare slow transcription (for example 1 in 50) is acceptable. If it is slow every 6–7 turns, change `IVR_WHISPER_MODEL` or hardware.

Useful logs: `incoming_call from_last4=`, `language_selected`, `intent_turn phrase= action= hung_up=`, `caller store seeded`, `IVR STT warmed`, `IVR intent router warmed`. Never `transcript=`.

---

## Render notes (Phase 8)

Details: [deploy-render.md](../deploy-render.md). **8 GB RAM** class (not 2 GB). Image installs faster-whisper + sentence-transformers; Whisper and BGE weights download on first boot. `/health` does not wait on that warmup. Allowlist your handset with secret `CALLER_OVERRIDE_JSON`, never git.

---

## Out of scope

Issuer APIs, real SMS send, goodbye as a menu/router option, per-turn LID, putting a real number or PIN in git, cloud STT.
