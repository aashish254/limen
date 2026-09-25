import os

from subproto import graph

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_repo")


def _g():
    return graph.build(ROOT)


def test_build_indexes_python_and_typescript():
    g = _g()
    files = set(g["nodes"])
    assert "app/payments/retry.py" in files
    assert "web/src/refund.ts" in files
    assert g["nodes"]["app/payments/retry.py"]["lang"] == "python"


def test_symbols_and_docstring_captured():
    g = _g()
    node = g["nodes"]["app/services/gateway_client.py"]
    assert "retry" in node["symbols"]
    assert "PaymentGateway" in g["nodes"]["app/payments/retry.py"]["symbols"]


def test_typescript_exports_parsed():
    g = _g()
    syms = g["nodes"]["web/src/refund.ts"]["symbols"]
    assert "retryRefund" in syms and "RetryPolicy" in syms


def test_search_ranks_retry_files_for_refund_query():
    g = _g()
    hits = graph.search(g, "fix the refund retry logic in PaymentGateway", top_k=5)
    assert hits, "expected at least one match"
    top = hits[0][0]
    assert top in ("app/payments/retry.py", "app/services/gateway_client.py")


def test_graph_roundtrips_through_save_load(tmp_path):
    g = _g()
    p = str(tmp_path / "graph.json")
    graph.save(g, p)
    loaded = graph.load(p)
    assert loaded["file_count"] == g["file_count"]
