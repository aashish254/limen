"""Static model pricing used to turn token counts into money.

Prices are USD per 1e6 tokens: (base input, cached input, output).
A model matches the first pattern whose text appears in the model id.
"""

PRICING = [
    (("claude-opus", "opus-4"), (15.0, 1.50, 75.0)),
    (("claude-sonnet", "sonnet-4"), (3.0, 0.30, 15.0)),
    (("claude-haiku", "haiku-4"), (1.0, 0.10, 5.0)),
    (("claude-3-5-sonnet", "claude-3.7-sonnet", "claude-3.5-sonnet"), (3.0, 0.30, 15.0)),
    (("claude-3-5-haiku", "claude-3.5-haiku"), (0.80, 0.08, 4.0)),
    (("gpt-5-pro",), (15.0, 15.0, 105.0)),
    (("gpt-5-mini", "gpt-5-nano"), (0.25, 0.025, 2.0)),
    (("gpt-5", "chatgpt-5"), (1.25, 0.125, 10.0)),
    (("o3", "o4-mini", "gpt-4.1-mini", "gpt-4o-mini"), (1.10, 0.55, 4.40)),
    (("gpt-4.1", "gpt-4o", "codex"), (2.50, 1.25, 10.0)),
    (("gemini-2.5-pro",), (1.25, 0.3125, 10.0)),
    (("gemini-2.5-flash", "gemini-flash"), (0.30, 0.075, 2.50)),
    (("kimi", "moonshot"), (0.60, 0.10, 2.50)),
    (("deepseek",), (0.27, 0.07, 1.10)),
    (("qwen3-coder", "qwen-coder"), (0.50, 0.10, 2.00)),
]

FALLBACK = (3.0, 0.30, 15.0)


def price_for(model):
    for patterns, price in PRICING:
        for p in patterns:
            if p in (model or "").lower():
                return price
    return FALLBACK


def cost_usd(u):
    pin, pcache, pout = price_for(u.get("model"))
    return (
        u.get("input_uncached", 0) * pin
        + u.get("cache_read", 0) * pcache
        + u.get("cache_write", 0) * pin
        + u.get("output", 0) * pout
    ) / 1e6
