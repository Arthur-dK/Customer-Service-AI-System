"""TwiML builders for Twilio Voice responses."""


def build_media_stream_connect_twiml(ws_url: str) -> str:
    """Return TwiML that greets the caller and opens a bi-directional Media Stream."""
    # TODO: Maybe implement company name
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Connecting to Customer Support</Say>
    <Connect>
        <Stream url="{ws_url}" />
    </Connect>
</Response>"""
