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


def test_an_extension_written_in_another_case_is_still_an_extension(tmp_path):
    """`SCHEMA.SQL` and `MIGRATIONS.MD` are ordinary files on a case-insensitive
    filesystem, and an index that matched extensions byte for byte left every one
    of them out — silently, which is the worst way for evidence to go missing."""
    root = tmp_path / "repo"
    (root / "db").mkdir(parents=True)
    (root / "db" / "0008_GATEWAY.SQL").write_text(
        "-- why the gateway keeps its own retry budget\n"
        "CREATE TABLE public.gateway_budget (id serial primary key);\n")
    (root / "db" / "NOTES.MD").write_text("# Gateway budget\n\nA 429 is a budget, not a bug.\n")
    g = graph.build(str(root))
    assert g["nodes"]["db/0008_GATEWAY.SQL"]["lang"] == "sql"
    assert "gateway" in g["nodes"]["db/0008_GATEWAY.SQL"]["symbols"]
    assert "budget" in g["nodes"]["db/NOTES.MD"]["symbols"]
    hits = graph.search(g, "why does the gateway budget reject a 429", top_k=2)
    assert [rel for rel, _, _ in hits][0] == "db/0008_GATEWAY.SQL", hits


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


def _data_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "fixtures").mkdir(parents=True)
    (root / "deploy").mkdir()
    (root / "app").mkdir()
    (root / "fixtures" / "refunds.json").write_text(
        '{"refunds": [{"amount_cents": 1200, "gateway_error": "APIConnectionError", '
        '"state": "pending"}], "meta": {"generated_by": "seed_refunds"}}\n')
    (root / "fixtures" / "ledger.csv").write_text(
        "refund_id,amount_cents,captured_at\nr_1,1200,2026-05-01\n")
    (root / "fixtures" / "rates.tsv").write_text("currency\tbasis\nUSD\t1.0\n")
    (root / "deploy" / "gateway.yaml").write_text(
        "service:\n  gateway:\n    retry_policy:\n      max_attempts: 3\n"
        "    health_url: http://127.0.0.1:9/health  # a value with colons\n")
    (root / "app" / "legacy.py").write_text("def old_thing():\n    return 1\n")
    return str(root)


def test_a_data_files_keys_and_columns_are_its_symbols(tmp_path):
    """S31: FR-10 documented that `.json`/`.csv`/`.yaml` had no graph signal at all."""
    g = graph.build(_data_repo(tmp_path))
    assert g["nodes"]["fixtures/refunds.json"]["lang"] == "json"
    syms = g["nodes"]["fixtures/refunds.json"]["symbols"]
    assert "amount" in syms and "cents" in syms, syms
    assert "gateway" in syms and "error" in syms, "a nested key is still a symbol"
    assert "amount" in g["nodes"]["fixtures/ledger.csv"]["symbols"], "a header column"
    assert "basis" in g["nodes"]["fixtures/rates.tsv"]["symbols"], "a tab-separated header"
    assert "retry" in g["nodes"]["deploy/gateway.yaml"]["symbols"], "a nested YAML key"
    doc = g["nodes"]["fixtures/refunds.json"]["doc"]
    assert "refunds[].amount_cents" in doc, "the *path* into the shape survives, not a word soup"


def test_a_data_files_values_are_never_its_symbols(tmp_path):
    """Values are what the agent opens the file to find — indexing them would make
    every fixture rank for every word it happens to contain."""
    g = graph.build(_data_repo(tmp_path))
    node = g["nodes"]["fixtures/refunds.json"]
    assert "pending" not in node["hints"] and "apiconnectionerror" not in node["hints"]
    assert "apiconnectionerror" not in node["symbols"]
    assert "1200" not in node["symbols"]


def test_a_yaml_value_that_contains_a_colon_does_not_become_a_key(tmp_path):
    g = graph.build(_data_repo(tmp_path))
    names = g["nodes"]["deploy/gateway.yaml"]["doc"]
    assert "service.gateway.retry_policy" in names, names
    assert "service.gateway.health_url" in names, names
    assert "service.gateway.retry_policy.health_url" not in names, \
        "a sibling must not nest under the key before it"
    assert "127.0.0.1" not in names and "http" not in names.split("keys:")[-1].split(","), names


def test_a_fixture_question_finds_the_data_file_not_the_legacy_module(tmp_path):
    g = graph.build(_data_repo(tmp_path))
    hits = graph.search(g, "the refund fixture amount_cents disagrees with the "
                           "gateway_error state, which writer is wrong", top_k=5)
    ranked = [rel for rel, _, _ in hits]
    assert ranked[0] == "fixtures/refunds.json", ranked
    assert "app/legacy.py" not in ranked
    _, score, why = hits[0]
    assert "sym:amount" in why and "sym:cents" in why, \
        "a key must earn the 3.0 symbol tier, not 0.6 hints"
    assert score >= 3.0


def test_a_column_question_finds_the_csv_by_its_header(tmp_path):
    g = graph.build(_data_repo(tmp_path))
    hits = graph.search(g, "which captured_at values are in the ledger", top_k=5)
    assert hits[0][0] == "fixtures/ledger.csv", hits
    assert "sym:captured" in hits[0][2] or "sym:at" in hits[0][2], hits[0]


def test_malformed_json_still_yields_its_keys(tmp_path):
    """A truncated or streamed fixture is half-useful: the keys already written are
    still the file's shape, so the index must not lose the whole file to one brace."""
    src = '{"refunds": [{"amount_cents": 12, "gateway_error": "x"'
    syms, doc, keys = graph.parse_data(src, "json")
    assert "amount" in syms and "gateway" in syms, syms
    assert keys == ["refunds", "amount_cents", "gateway_error"], keys


def test_an_empty_or_binary_data_file_costs_nothing(tmp_path):
    assert graph.parse_data("", "json") == ([], "keys: ", [])
    syms, _doc, keys = graph.parse_data("\x00\x01not json at all", "json")
    assert keys == [] and syms == []
    assert graph.parse_data("\x00\x01", "csv") == ([], "columns: ", [])


def test_a_data_extension_is_never_truncated_into_another_one():
    """PATH_RE once matched `js` inside `.json`, so `fixtures/refunds.json` was found as
    `fixtures/refunds.js` — no index node ever has that name, so the read could not
    couple to it. The bug was invisible until data files became indexable."""
    from subproto import protocol

    got = protocol.extract_paths("read fixtures/refunds.json, web/src/app.jsx, "
                                 "ui/Button.tsx, config/settings.yaml, "
                                 "fixtures/ledger.csv, notes.tsv, x.py, y.md")
    assert got == ["fixtures/refunds.json", "web/src/app.jsx", "ui/button.tsx",
                   "config/settings.yaml", "fixtures/ledger.csv", "notes.tsv",
                   "x.py", "y.md"], got
    assert protocol.extract_paths("x.pyx y.pyz nothing.json5") == [], \
        "a near-miss extension is not a path either"""


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


def test_load_takes_a_repo_directory_the_way_every_flag_does(tmp_path):
    """`--graph <repo>` is the form people actually write, because `subproto graph`
    and `subproto compile` both take a repository. A loader that accepted only the
    index file answered it with IsADirectoryError."""
    g = _g()
    root = str(tmp_path / "repo")
    graph.save(g, graph.index_path(root))
    assert graph.load(root)["file_count"] == g["file_count"]
    assert graph.index_for(root) == graph.index_path(root)
    file = str(tmp_path / "graph.json")
    assert graph.index_for(file) == file


def test_cli_up_with_an_unreadable_graph_exits_instead_of_tracebacking(tmp_path, capsys):
    from subproto import cli

    bad = tmp_path / "not-a-graph.json"
    bad.write_text(u"this is not json\n")
    rc = cli.main(["up", "--slots", "--graph", str(bad),
                   "--home", str(tmp_path / "home"), "--port", "8867"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "the graph did not load" in out
    # The path the reader handed over has to be on the page, and it has to be the
    # only graph they are told about: a traceback names the frame, not the file.
    assert str(bad) in out
    assert "Traceback" not in out


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


def test_a_multi_line_docstring_never_orphans_a_line_in_where(tmp_path, capsys):
    """The preview under a path is one line; a module docstring is not.

    Truncating the docstring before collapsing its newlines spilled the remainder
    onto the page at column 0 — a two-letter orphan in the middle of a list whose
    whole point is one column of names.
    """
    from subproto import cli

    (tmp_path / "refund_helper.py").write_text(
        '"""refund helper\n\nThe retry path is documented at length below."""\n')
    rc = cli.main(["where", "refund", "--graph", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "refund_helper.py" in out, "the fixture did not reach the page at all"
    orphans = [ln for ln in out.splitlines()
               if ln.strip() and not ln.startswith(" ")
               and "subproto where" not in ln]
    assert not orphans, "line starts at column 0: %r" % orphans


def test_a_rebuilt_index_is_not_scored_with_the_previous_indexs_view():
    """S32: `score_files` caches its lowered/symbol-set view, keyed to the node table.

    The cache is only correct if it cannot outlive the index it came from — so two
    graphs built from two trees must each be scored from their own symbols, in either
    order, and a reload (a fresh dict) counts as a new index too.
    """
    a = graph.build(ROOT)
    b = graph.build(ROOT)
    b["nodes"] = dict(a["nodes"])
    b["nodes"]["app/payments/retry.py"] = dict(a["nodes"]["app/payments/retry.py"],
                                               symbols=["zebra_only_in_b"])
    q = ["zebra_only_in_b"]
    assert [r for r, _s, _w in graph.score_files(a, q, [])] == []
    assert graph.score_files(b, q, [])[0][0] == "app/payments/retry.py"
    # Back to the first index: the second one's view must not have stuck to it.
    assert [r for r, _s, _w in graph.score_files(a, q, [])] == []
    assert graph.score_files(b, q, [])[0][0] == "app/payments/retry.py"
    from tempfile import TemporaryDirectory
    import json
    with TemporaryDirectory() as d:
        path = graph.save(a, os.path.join(d, "g.json"))
        reloaded = graph.load(path)
        assert json.dumps(reloaded)[:2], "the index stays JSON-serialisable with a view live"
        assert graph.score_files(reloaded, ["PaymentGateway"], [])[0][0] == \
            "app/payments/retry.py"
