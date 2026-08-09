from services.ivr.twiml import build_media_stream_connect_twiml


def test_build_media_stream_connect_twiml():
    twiml = build_media_stream_connect_twiml("wss://example.com/media-stream")
    assert "<Stream url=" in twiml
    assert "wss://example.com/media-stream" in twiml
    assert "Connecting to Customer Support" in twiml
