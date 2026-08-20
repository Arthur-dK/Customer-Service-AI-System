"""TwiML builders for Twilio Voice responses."""


def build_media_stream_connect_twiml(
    ws_url: str,
    *,
    caller_from: str | None = None,
    country_code: str | None = None,
) -> str:
    """Return TwiML that opens a bi-directional Media Stream with caller metadata."""
    parameters: list[str] = []
    if caller_from:
        parameters.append(
            f'            <Parameter name="from" value="{_xml_escape(caller_from)}" />'
        )
    if country_code:
        parameters.append(
            f'            <Parameter name="country_code" value="{_xml_escape(country_code)}" />'
        )

    parameter_block = ("\n" + "\n".join(parameters) + "\n") if parameters else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{_xml_escape(ws_url)}">{parameter_block}        </Stream>
    </Connect>
</Response>"""


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
