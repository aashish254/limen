import json

from subproto import protocol, usage


def test_normalize_openai_and_anthropic_blocks():
    body = {"system": "be safe", "messages": [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]},
    ]}
    norm = protocol.normalize_messages(body)
    kinds = [k for _, _, k in norm]
    assert kinds == ["system", "user", "assistant_tool_call", "tool_result"]


def test_openai_tool_role_maps_to_tool_result():
    body = {"messages": [{"role": "tool", "content": "result text"}]}
    assert protocol.normalize_messages(body)[0][2] == "tool_result"


def test_anthropic_stream_usage_reconstructed():
    acc = usage.UsageAccumulator("anthropic")
    acc.feed_obj({"type": "message_start", "message": {
        "model": "claude-x", "usage": {"input_tokens": 100,
        "cache_creation_input_tokens": 50, "cache_read_input_tokens": 4000,
        "output_tokens": 4}}})
    acc.feed_obj({"type": "message_delta", "usage": {"output_tokens": 320}})
    u = acc.usage()
    assert u["input_uncached"] == 100
    assert u["cache_write"] == 50
    assert u["cache_read"] == 4000
    assert u["output"] == 320
    assert u["model"] == "claude-x"


def test_openai_stream_usage_and_cache_split():
    acc = usage.UsageAccumulator("openai")
    acc.feed_obj({"model": "gpt-5", "choices": [{"delta": {"content": "x"}}]})
    acc.feed_obj({"model": "gpt-5", "usage": {
        "prompt_tokens": 5000, "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 4800},
        "completion_tokens_details": {"reasoning_tokens": 50}}})
    u = acc.usage()
    assert u["input_uncached"] == 200
    assert u["cache_read"] == 4800
    assert u["output"] == 200
    assert u["reasoning"] == 50


def test_byte_feeder_splits_partial_sse_frames():
    acc = usage.UsageAccumulator("anthropic")
    feeder = usage.ByteFeeder(acc, "text/event-stream")
    full = ("event: message_start\ndata: " +
            json.dumps({"type": "message_start", "message": {
                "model": "m", "usage": {"input_tokens": 10, "output_tokens": 1}}}) +
            "\n\nevent: message_delta\ndata: " +
            json.dumps({"type": "message_delta", "usage": {"output_tokens": 99}}) + "\n\n")
    data = full.encode()
    feeder.feed(data[:40])
    feeder.feed(data[40:])
    feeder.finish()
    assert acc.usage()["output"] == 99
    assert acc.usage()["input_uncached"] == 10


def test_lexical_tokens_and_paths():
    toks = protocol.lexical_tokens("fix the PaymentGateway refund in app/retry.py")
    assert "payment" in toks and "refund" in toks
    assert protocol.extract_paths("edit app/payments/retry.py now") == ["app/payments/retry.py"]
