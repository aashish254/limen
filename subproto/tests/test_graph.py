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


def _docs_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "db" / "migrations").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "app").mkdir(parents=True)
    (root / "db" / "migrations" / "0007_refund_ledger.sql").write_text(
        "-- bring the refund ledger online; the swallow of APIConnectionError\n"
        "CREATE TABLE public.refund_ledger (id serial primary key);\n"
        "ALTER TABLE public.refund_ledger ADD COLUMN state text;\n")
    (root / "docs" / "refund-flow.md").write_text(
        "# Refund flow\n\nThe ledger is written after the gateway answers.\n\n"
        "## Retry semantics\n\nA connection error must surface, never retry silently.\n")
    (root / "docs" / "runbook.txt").write_text(
        "Escalation runbook\n-------------------\nPage the payments owner on ledger drift.\n")
    (root / "app" / "legacy.py").write_text("def old_thing():\n    return 1\n")
    return str(root)


def test_the_index_covers_the_docs_and_sql_a_task_asks_about(tmp_path):
    """S27: a `.sql` or `.md` the agent read used to be invisible to the graph, which
    left the compiler with no evidence about it. Headings and DDL objects are that
    file's symbols."""
    g = graph.build(_docs_repo(tmp_path))
    assert g["nodes"]["db/migrations/0007_refund_ledger.sql"]["lang"] == "sql"
    syms = g["nodes"]["db/migrations/0007_refund_ledger.sql"]["symbols"]
    assert ["refund", "ledger"] == [s for s in syms if s in ("refund", "ledger")], syms
    assert "retry" in g["nodes"]["docs/refund-flow.md"]["symbols"]
    assert g["nodes"]["docs/runbook.txt"]["symbols"], "a plain-text title still counts"


def test_a_schema_question_finds_the_migration_not_the_legacy_module(tmp_path):
    g = graph.build(_docs_repo(tmp_path))
    hits = graph.search(g, "why is the refund_ledger migration swallowing errors", top_k=5)
    ranked = [rel for rel, _, _ in hits]
    assert ranked[0] == "db/migrations/0007_refund_ledger.sql", ranked
    assert "app/legacy.py" not in ranked
    _, score, why = hits[0]
    assert "sym:refund" in why and "sym:ledger" in why, \
        "a DDL object must earn the symbol tier, not 0.6 hints"
    assert score >= 3.0


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


def test_cli_where_takes_a_repo_not_just_an_index(tmp_path, capsys):
    """`subproto where --graph <dir>` used to raise IsADirectoryError: `compile` accepts
    a directory and everyone reads --graph as "the repo"."""
    from subproto import cli

    rc = cli.main(["where", "fix the payment retry refund", "--graph", ROOT])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "app/payments/retry.py" in captured.out
    assert "Traceback" not in captured.err
    # ... and the note about building goes to stderr, so --json stays parseable.
    rc = cli.main(["where", "refund", "--graph", ROOT, "--json"])
    captured = capsys.readouterr()
    import json as _json
    assert rc == 0 and isinstance(_json.loads(captured.out), list)
