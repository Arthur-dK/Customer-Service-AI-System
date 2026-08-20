from services.ivr.twiml import build_media_stream_connect_twiml


def test_build_media_stream_connect_twiml_includes_stream_url():
    twiml = build_media_stream_connect_twiml("wss://example.com/media-stream")
    assert "<Stream url=" in twiml
    assert "wss://example.com/media-stream" in twiml
    assert "<Connect>" in twiml
    assert "Connecting to Customer Support" not in twiml


def test_build_media_stream_connect_twiml_includes_caller_parameters():
    twiml = build_media_stream_connect_twiml(
        "wss://example.com/media-stream",
        caller_from="+972501234567",
        country_code="IL",
    )
    assert 'name="from" value="+972501234567"' in twiml
    assert 'name="country_code" value="IL"' in twiml


def test_build_media_stream_connect_twiml_escapes_xml_special_chars():
    twiml = build_media_stream_connect_twiml(
        'wss://example.com/media-stream?a="b"',
        caller_from='+1"<>&',
    )
    assert "&quot;" in twiml
    assert "&lt;" in twiml
    assert "&amp;" in twiml
