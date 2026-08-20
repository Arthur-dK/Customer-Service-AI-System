"""Helpers to emulate Twilio Media Stream WebSocket messages in tests."""

from __future__ import annotations

import base64
import json
from typing import Any


STREAM_SID = "MZ_test_stream_sid"


def connected_message() -> str:
    return json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"})


def start_message(
    *,
    stream_sid: str = STREAM_SID,
    call_sid: str = "CA_test",
    from_number: str | None = "+442071838750",
    extra_params: dict[str, str] | None = None,
) -> str:
    custom: dict[str, str] = {}
    if from_number:
        custom["from"] = from_number
    if extra_params:
        custom.update(extra_params)
    return json.dumps(
        {
            "event": "start",
            "sequenceNumber": "1",
            "start": {
                "streamSid": stream_sid,
                "accountSid": "AC_test",
                "callSid": call_sid,
                "tracks": ["inbound"],
                "customParameters": custom,
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1,
                },
            },
            "streamSid": stream_sid,
        }
    )


def media_message(mulaw_chunk: bytes, *, stream_sid: str = STREAM_SID, timestamp: str = "0") -> str:
    return json.dumps(
        {
            "event": "media",
            "sequenceNumber": "2",
            "media": {
                "track": "inbound",
                "chunk": "1",
                "timestamp": timestamp,
                "payload": base64.b64encode(mulaw_chunk).decode("ascii"),
            },
            "streamSid": stream_sid,
        }
    )


def dtmf_message(digit: str, *, stream_sid: str = STREAM_SID) -> str:
    return json.dumps(
        {
            "event": "dtmf",
            "streamSid": stream_sid,
            "sequenceNumber": "3",
            "dtmf": {"track": "inbound_track", "digit": digit},
        }
    )


def stop_message(*, stream_sid: str = STREAM_SID) -> str:
    return json.dumps(
        {
            "event": "stop",
            "sequenceNumber": "4",
            "streamSid": stream_sid,
            "stop": {"accountSid": "AC_test", "callSid": "CA_test"},
        }
    )


def parse_outbound(message: str) -> dict[str, Any]:
    return json.loads(message)
