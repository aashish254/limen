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


def test_save_creates_missing_parent_dirs(tmp_path):
    # `subproto graph <repo> --out some/fresh/path.json` is how the docs tell people
    # to park a graph; it must not die on the directory that isn't there yet.
    g = _g()
    p = str(tmp_path / "deep" / "nested" / "graph.json")
    graph.save(g, p)
    assert os.path.exists(p)
    assert graph.load(p)["file_count"] == g["file_count"]


def test_cli_graph_missing_root_exits_cleanly(tmp_path, capsys):
    from subproto import cli

    rc = cli.main(["graph", str(tmp_path / "nope")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no such directory" in captured.out
    assert "Traceback" not in captured.err


def test_cli_graph_writes_to_a_fresh_out_dir(tmp_path, capsys):
    from subproto import cli

    target = str(tmp_path / "state" / "g.json")
    rc = cli.main(["graph", ROOT, "--out", target])
    assert rc == 0
    assert os.path.exists(target)
    assert "import edges" in capsys.readouterr().out
