# Multi-Lingual AI Support Engine

AI-assisted customer support system for card holders across **voice (IVR)**, **SMS**, and **email**, with multi-lingual coverage (around 50 languages supported by modern LLMs) and 24/7 automation where appropriate.

The primary customer of this system is Yordex, a card management Fintech that serves global NGOs (for example Red Cross and World Food Programme) operating in 190+ countries. A small support team cannot cover every language and timezone with humans alone. This system extends self-service and assists agents so card holders get help in their language, around the clock, at lower cost.

## Goals

- **Multi-lingual support** - serve card holders in ~50 languages, not just a small selection.
- **24×7 self-service** - automate high-volume, well-scoped requests outside human office hours.
- **Lower support cost** - deflect routine card queries and accelerate email handling for agents.

## Support channels today vs. this project

| Channel | Today | This project |
|--------|--------|----------------|
| **SMS** | Text `Balance` or `PIN` to company numbers (UK + US/Canada). 24×7, **English only**. | Multi-lingual SMS for the same two intents. |
| **IVR** | Call the same numbers for balance, PIN, card/statement info, block/unblock. 24×7, **English + Hebrew**. | Multi-lingual conversational IVR (~50 languages), with keypad fallback. |
| **Email / human** | Email `support@yordex.com` or call UK support (8am–8pm UK, 7 days). **English only**. | Translate <-> English, classify tickets, then automate safe ticket types; remaining mail stays with humans. |

Human phone support remains available for cases that need an agent. The AI channels must hand off cleanly when automation is not enough.

## Delivery order

Work is sequenced by impact and dependency (IVR first because many Red Cross–supported users prefer calling and may be illiterate):

1. **Multi-lingual IVR**
2. **Multi-lingual SMS** (PIN + Balance)
3. **Email translator** (inbound -> English for agents; reply -> card holder’s language)
4. **Email ticket classifier** (map mail to ticket types; prerequisite for step 5)
5. **Automated email responses** (ticket-by-ticket for the easiest types; all others stay with humans)

Each deliverable follows: detailed requirements sign-off -> tool research (prefer cheap/open source over expensive turnkey) -> model choice (prefer **local open-source** models for cost, latency, and security) -> stack agreement -> develop/test/deploy -> UAT.

Ideally steps 3–5 share the same language/AI stack as the voice and SMS work.

---

## Channel 1 - Multi-lingual IVR

### Purpose

Let card holders **call** at any time and complete card self-service by voice (or keypad), in a language appropriate to their country and preference.

### Capabilities

- Retrieve **card balance**, **PIN**, **card / statement** information, and related read-only account prompts (transactions, declines, top-ups) consistent with the existing IVR script catalog.
- **Block** or **unblock** a card (with confirmation).
- **Top up** where allowance rules apply (when the product supports it).
- **Language selection** narrowed by caller country (e.g. Israel: English, Hebrew, and a few others first). If the needed language is not listed, the caller may speak and the system detects language from speech.
- **Intent recognition** over a fixed set of queries (e.g. block card, get PIN) so phrasing can vary without free-form unbounded dialogue.
- **Barge-in** - caller can interrupt prompts when they already know what they want.
- **Human handoff** when requested, or after repeated misunderstanding (about 2–3 failures), **only if** a CS agent is on duty who speaks the right language; otherwise announce availability and schedules.
- **DTMF fallback** when speech is unreliable (noise, accent).
- **Silence handling** - if the caller is silent for more than ~7 seconds after a prompt, repeat the question.

### Identity, security, and privacy

- Caller identity is the **calling phone number** (same security model as today).
- **Do not record or store calls**, especially when PINs or other sensitive data may be spoken.

### Latency

- After the caller speaks, audio responses should begin within **~0.5 seconds**. Prefer templated / constrained replies over long free-form LLM generation.

### Technical notes (implementation direction)

- Twilio Voice webhooks + bi-directional media streams (WebSockets).
- Speech-to-text / text-to-speech in the real-time path; local semantic routing for intents where possible.
- Prompt catalog derived from the existing IVR message keys (welcome, menus, balance/PIN/block/unblock/top-up, errors).

---

## Channel 2 - Multi-lingual SMS

### Purpose

Let card holders **text** a Yordex number at any time and receive **PIN** or **balance** (and only those intents in scope for this phase), in their language, not English-only as today.

### Capabilities

- Accept short inbound messages whose meaning is **balance** or **PIN**, regardless of exact wording or language (intent classification over a tiny fixed intent set).
- Reply with the corresponding card information for the account linked to the sender’s number.
- Support the same multi-lingual ambition as IVR (~50 languages): detect or infer language from the inbound message (and/or number country) and answer in that language.
- Keep responses short and SMS-safe (length, clarity, no unnecessary free-form chat).
- Reject or politely refuse out-of-scope requests (anything beyond PIN/Balance in this phase), with optional pointer to IVR or email/human support where appropriate.

### Identity, security, and privacy

- Sender identity is the **from phone number** (aligned with IVR).
- PIN and balance are sensitive: minimise logging of message bodies; avoid retaining SMS content longer than operationally required.
- Use the same card/account lookup rules as voice where possible so IVR and SMS stay consistent.

### Availability and numbers

- Target **24×7** automated replies (matching today’s SMS availability).
- Integrate with existing Twilio SMS numbers (UK production/test numbers and US/Canada locals as configured by Yordex).

### Latency and reliability

- End-to-end reply should feel immediate for a text channel (typically well under a few seconds including carrier lag).
- Prefer template replies filled with live card data over generative prose.
- On failure (unknown number, no cards, upstream error), send a clear short failure SMS, do not silently drop.

### Fallback and handoff

- If intent is unclear after a small number of attempts, send a clarifying prompt listing supported keywords/actions.
- Do not invent new product actions over SMS in this phase; escalate complex needs toward email/human or IVR.

### Why this channel stays “thin”

The product scope is deliberately two intents so multi-lingual SMS can reuse language, identity, and card services built for IVR without a full conversational stack.

---

## Channel 3 - Email support engine

### Purpose

Help the human support team handle mail from card holders worldwide: understand non-English mail, triage it, and automate only the safest, highest-volume ticket types, without replacing agents for everything.

Human coverage today is roughly **8am–8pm UK, 7 days**, English only. Email AI improves quality and speed inside that process and can prepare work outside hours for agents.

### Phase A - Translator

- Detect the card holder’s language on inbound email.
- Translate the thread (or latest message) **into English** for agents.
- When an agent replies in English, translate the response **back into the card holder’s language** before send.
- Preserve meaning for support content (card issues, compliance, actionable instructions), not casual paraphrase that drops details.
- Keep original text available to agents so translation errors can be checked.

### Phase B - Ticket classifier

- Classify inbound mail into **support ticket types** used by the team.
- Run after (or with) translation so classification can use a consistent English representation when helpful.
- Output should be structured and reviewable (type + confidence); low-confidence mail should not be forced into automation.

### Phase C - Automated responses (ticket-by-ticket)

- Automate **only** ticket types chosen as safe and easy (agreed with product/support).
- All other types continue to **humans unchanged**.
- Automated replies must respect the same language path as the translator (reply in the card holder’s language).
- Where automation cannot complete the request, create/assign a normal human ticket rather than guessing.

### Identity, security, and privacy

- Tie mail to accounts using existing identity practices (email address, account references in the message), not phone-number auth as on IVR/SMS.
- Treat mail as sensitive (PII, card/account details): encrypt in transit, restrict storage, and avoid sending unnecessary card secrets in automated replies.
- Do not auto-send irreversible account actions without the same confirmations policy humans use.

### Latency and operations

- Translation and classification should complete quickly enough for agent workflows (seconds, not minutes).
- Automation may run asynchronously; failed automation must surface to the human queue.
- Prefer the **same local/open models** and pipelines as IVR/SMS where practical so one AI stack serves all five deliverables.

### Fallback and handoff

- Translation-only and classify-only modes must always be able to leave the ticket with a human.
- Automation is opt-in per ticket type; default is human handling.
- Unclear language detection or classification confidence → human review.

---

## Shared product principles

Across IVR, SMS, and email:

- **Fixed action sets** where possible (known intents / ticket types), not open-ended agentic behaviour.
- **Prefer local open-source models** (cost, speed when self-hosted, security) over closed cloud APIs unless agreed otherwise.
- **Reuse one implementation stack** across deliverables when feasible (language, hosting, CI/CD, test approach).
- **Human remains in the loop** for ambiguity, language mismatch for live handoff, and non-automated email types.

## Tech stack (current direction)

| Layer | Choice |
|--------|--------|
| Backend | Python 3.11+, FastAPI, WebSockets, Pytest |
| AI | Ollama (e.g. Llama 3.1 8B), BGE-M3 multilingual embeddings, grammar-constrained decoding where useful |
| Telephony / messaging | Twilio Programmable Voice & SMS |
| Infra | Redis, Docker (hosting flexible, not required to be AWS) |

## Repository layout

```text
app/                 # FastAPI composition root + HTTP/WebSocket routers
  main.py            # App factory; mounts channel routers
  api/
    health.py
    ivr.py           # Voice webhook + media stream
    sms.py           # SMS webhooks (planned)
    email.py         # Email/ticket hooks (planned)
services/
  ivr/               # Voice session, TwiML, language, intents
  sms/               # SMS handlers (planned)
  email/             # Translate / classify / automate (planned)
  cards/             # Shared card account operations (planned)
core/                # Config, AI clients, telephony helpers, shared language utilities
tests/               # Channel-aligned tests (ivr/, sms/, email/)
docs/adr/            # Architecture decision records
```

`app/main.py` stays thin: channel behaviour lives under `app/api/` and `services/<channel>/`.

## Development status

Scaffolding and Twilio Voice media-stream plumbing are in progress. Multi-lingual IVR language selection, SMS, and email features follow the delivery order above.

## Running tests

```bash
python -m pytest tests/ -v
```

(Use the project virtualenv if present.)
