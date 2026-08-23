# FEAT-03 — Streaming voice path and TTFB (templated IVR)

| | |
|---|---|
| **Feature ID** | FEAT-03 |
| **Name** | Low-latency STT/TTS plumbing + Time-To-First-Audio-Byte |
| **Branch** | `feat/ivr-latency-audio-pipeline` |
| **Status** | In progress (Phase 1) |
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

### Phase 1 — TTFB timer harness *(this phase)*

- **Goal:** One shared stopwatch with a frozen definition of start/stop, budgets, and “typical” (median) canned SLO.
- **Delivered:** `services/ivr/ttfb.py`; [ADR-011](../adr/ADR-011.md).
- **Tests:** `tests/ivr/pytest/test_ttfb.py` (fake clock; no Twilio, no vendors).
- **Done when:** pytest for TTFB passes; harness is not yet wired into live calls.

### Phase 2 — Phrase catalog + instant cache

- **Goal:** Menus, errors, and placeholder-task lines have stable **phrase IDs**. Hot path is “look up ready audio,” not “ask TTS again.”
- **Tests:** cache hit does not call the synthesizer; English + one other language fixtures.
- **Depends on:** Phase 1 only for later TTFB on cache hits.

### Phase 3 — Streaming TTS interface + free stub

- **Goal:** TTS can emit μ-law **chunks**. First chunk is `mark_first_audio_byte`. Stub/tone (and later Piper) work without a paid API.
- **Tests:** stub yields chunks; first chunk records TTFB; swapping the backend is a constructor choice.
- **Later swap:** paid streaming TTS (e.g. Cartesia) behind the same interface.

### Phase 4 — Streaming STT interface + free stub

- **Goal:** Inbound audio can be transcribed through a **StreamingSTT** protocol. CI uses a stub (fixed phrases). Still use energy VAD for “caller stopped talking.”
- **Tests:** stub returns a known transcript after `speech_end`; no network.
- **Later swap:** paid streaming STT (e.g. Deepgram) behind the same interface. In-house option: local model (Whisper-class) on the same protocol.

### Phase 5 — Simulated turn engine (placeholder tasks)

- **Goal:** After language is already known: `speech_end` → stub transcript → canned phrase → stream first audio. Measure TTFB. Placeholder “tasks” (e.g. fake balance) are extra audio prompts, not real card APIs.
- **Tests:** automated benchmark — median canned TTFB ≤ 500 ms with stubs.

### Phase 6 — Fake Twilio media-stream wiring

- **Goal:** Same turn engine on inbound/outbound queues like FEAT-02’s fake stream.
- **Tests:** pytest + optional `tests/ivr/manual/` script. Language selector unchanged except handing off after selection.

### Phase 7 — Live Twilio smoke (English + 1–2 languages)

- **Goal:** Hear canned replies start quickly on a real phone. Official clock remains the harness; the call is a sanity check.
- **Tests:** smoke checklist in this doc (written in Phase 7).

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
| [ADR-003](../adr/ADR-003.md) | 8 kHz μ-law wire format | Unchanged |
| [ADR-004](../adr/ADR-004.md) | Energy VAD `speech_end` | Clock start |
| [ADR-005](../adr/ADR-005.md) | TTS + burst playback | Playback; phrase cache will extend this |
| [ADR-010](../adr/ADR-010.md) | Offline before live | Same build order |

Later phases will add ADRs for STT/TTS backend choice when those interfaces land.

---

## Key code (Phase 1)

| Area | Path |
|------|------|
| TTFB harness | `services/ivr/ttfb.py` |
| Pytest | `tests/ivr/pytest/test_ttfb.py` |

```powershell
.\venv\Scripts\python.exe -m pytest tests/ivr/pytest/test_ttfb.py -q
```
