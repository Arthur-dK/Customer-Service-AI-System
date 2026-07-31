**Current Progress:**   Main program structure set up, no features implemented yet.

---

# Multi-Lingual AI Support Engine

An ultra-low latency (<500ms) multi-channel AI support system handling voice (IVR), SMS, and email support across 50+ languages for humanitarian NGOs.

## High-Level System Architecture
1. **Multi-Lingual IVR**: Voice audio streaming handled using Twilio WebSockets, Deepgram STT, vector semantic routing (locally), and Cartesia TTS.
2. **Multi-Lingual SMS**: Verification for PIN and Balance.
3. **Email Support Engine**: Translation and automated support ticket classification.

## Tech Stack
- **Backend**: Python 3.11+, FastAPI, WebSockets, Pytest
- **AI Core**: Ollama (Llama 3.1 8B), BGE-M3 Multilingual Embeddings, Grammar-Constrained Decoding
- **Infrastructure**: Twilio Programmable Voice/SMS, Redis, Docker