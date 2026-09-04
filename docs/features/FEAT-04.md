# FEAT-04 — IVR intent router (caller ID + semantic actions)

| | |
|---|---|
| **Feature ID** | FEAT-04 |
| **Name** | Local semantic intent router, stub caller store, privacy |
| **Branch** | `feat/ivr-intent-router` |
| **Status** | Phase 1–4 done. Phases 5–8 not started. |
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
- **Delivered:** `phrases.json` `en`/`he`/`ar`; PIN-via-SMS (no digits); **ADR-025**.
- **Tests:** catalog presence; PIN phrases have no digit sequences.
- **Done when:** pytest green without live Twilio.

### Phase 7 — Media-stream wiring

- **Goal:** Live WS path after language select.
- **Delivered:** `app/api/ivr.py` + deps; `render.yaml` not scripted goodbye.
- **Tests:** fake media stream: allowlisted fake number + unknown hangup.
- **Done when:** fake-stream pytest green.

### Phase 8 — Render + live smoke

- **Goal:** Hosting notes and handset checklist.
- **Delivered:** Docker/requirements/RAM notes; boot seed; runbook below.
- **Tests:** pytest still offline.
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
| ADR-025 | HE/AR catalog + Edge TTS warmup | 6 |

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

Commands land as each phase adds tests. After Phase 1:

```powershell
.\venv\Scripts\python.exe -m pytest tests/cards/test_caller_store.py tests/intents/test_intent_router.py tests/ivr/pytest/test_privacy_ivr.py tests/ivr/pytest/test_twiml.py tests/ivr/pytest/test_whisper_stt.py -q
```

---

## Phase 8 live smoke runbook (fill in when that phase ships)

1. Overlay: put your E.164 in `data/callers.local.json` or `CALLER_OVERRIDE_JSON` (never commit it).
2. `IVR_STT_BACKEND=whisper`, `IVR_INTENT_EMBEDDER=bge`.
3. Unknown number: refusal, then hang up. Log `from=` shows **last-4 only**.
4. Allowlisted number: language select, then six-action menu. Ask for balance in a paraphrase; hear stub balance text (no real issuer).
5. Ask to block: hear last-4 restatement; say yes; hear blocked. Unblock the same way.
6. Ask for PIN: hear SMS placeholder; **no digits**.
7. Mumble twice: DTMF menu.
8. Confirm TwiML for `/voice/incoming` has **no** `<Record>`.
9. Hebrew / Arabic: pick that language at start; speak a prototype paraphrase; hear catalog TTS if Edge/Piper/SAPI can speak it.

**STT latency:** a rare slow transcription (for example 1 in 50) is acceptable. If it is slow every 6–7 turns, change `IVR_WHISPER_MODEL` or hardware.

---

## Render notes (Phase 8)

SpeechBrain LID + faster-whisper + BGE-M3 will not fit comfortably on a **2 GB** instance. Use a **larger** Render plan (8 GB class is a reasonable starting point). First boot warms models and may exceed default health-check windows; `/health` must stay off that warmup (existing lifespan pattern).

---

## Out of scope

Issuer APIs, real SMS send, goodbye as a menu/router option, per-turn LID, putting a real number or PIN in git, cloud STT.
