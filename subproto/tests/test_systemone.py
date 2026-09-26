"""V2 SPI (S01-S07): the System One backend is a config choice, not a code choice.

The load-bearing properties under test are that selection resolves through the
registry to a labelled adapter, that an *arbitrary* label reaches an engine
decision end-to-end, and that a missing or unreachable model degrades to the
heuristics instead of breaking the proxy.
"""

import json
import socket

import pytest

from subproto import systemone
from subproto.config import Config
from subproto.demo import synthetic_request
from subproto.engine import Engine
from subproto.laya import LayaClient
from subproto.laya_server import LayaServer
from subproto.proxy import analyze_request
from subproto.systemone.base import HTTPScoreAdapter


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server():
    srv = LayaServer(port=free_port(), backend="lexical").start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.stop()


def test_no_backend_configured_resolves_to_heuristic(tmp_path, monkeypatch):
    monkeypatch.delenv("SUBPROTO_MODEL", raising=False)
    monkeypatch.delenv("LAYA_URL", raising=False)
    adapter, label = systemone.resolve(Config(source={"data_dir": str(tmp_path)}))
    assert adapter is None and label == "heuristic"


def test_legacy_laya_url_still_binds_the_laya_label(tmp_path, monkeypatch):
    monkeypatch.delenv("SUBPROTO_MODEL", raising=False)
    monkeypatch.setenv("LAYA_URL", "http://127.0.0.1:9")
    adapter, label = systemone.resolve(Config(source={"data_dir": str(tmp_path)}))
    assert label == "laya" and adapter.available is True


def test_named_registry_model_takes_its_own_env_url(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_MODEL", "openjev")
    monkeypatch.setenv("OPENJEV_URL", "http://127.0.0.1:8899")
    adapter, label = systemone.resolve(Config(source={"data_dir": str(tmp_path)}))
    assert label == "openjev"
    assert isinstance(adapter, HTTPScoreAdapter) and adapter.base_url == "http://127.0.0.1:8899"


def test_model_url_is_used_when_no_dedicated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_MODEL", "djev")
    monkeypatch.delenv("DJEV_URL", raising=False)
    cfg = Config(source={"data_dir": str(tmp_path), "model_url": "http://127.0.0.1:8900"})
    adapter, label = systemone.resolve(cfg)
    assert (label, adapter.base_url) == ("djev", "http://127.0.0.1:8900")


def test_raw_url_is_selectable_without_a_registry_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_MODEL", "http://127.0.0.1:9001")
    adapter, label = systemone.resolve(Config(source={"data_dir": str(tmp_path)}))
    assert label == "model" and adapter.base_url == "http://127.0.0.1:9001"


def test_unknown_name_or_disabled_selection_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("LAYA_URL", raising=False)
    cfg = Config(source={"data_dir": str(tmp_path)})
    for value in ("not-a-model", "heuristic", "off"):
        monkeypatch.setenv("SUBPROTO_MODEL", value)
        adapter, label = systemone.resolve(cfg)
        assert adapter is None and label == "heuristic", "%r must degrade cleanly" % value


def test_engine_records_the_active_adapter_label(tmp_path, monkeypatch, server):
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    monkeypatch.setenv("SUBPROTO_MODEL", "openjev")
    monkeypatch.setenv("OPENJEV_URL", server)
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg)
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "openjev", "the label, not the model family, is what slots store"
    assert eng.model_status()["label"] == "openjev"
    assert eng.model_status()["configured"] is True


def test_engine_labels_live_laya_server_as_laya(tmp_path, monkeypatch, server):
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    monkeypatch.delenv("SUBPROTO_MODEL", raising=False)
    cfg = Config(source={"data_dir": str(tmp_path), "laya_url": server})
    eng = Engine(cfg)
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "laya"


def test_engine_falls_back_when_the_named_model_is_down(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    monkeypatch.setenv("SUBPROTO_MODEL", "semif")
    monkeypatch.setenv("SEMIF_URL", "http://127.0.0.1:%d" % free_port())
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg)
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "heuristic"


def test_status_lists_every_backend_and_exactly_one_active(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_MODEL", "laya")
    monkeypatch.setenv("LAYA_URL", "http://127.0.0.1:8890")
    monkeypatch.delenv("SUBPROTO_MODEL_URL", raising=False)
    st = systemone.status(Config(source={"data_dir": str(tmp_path)}))
    names = [r["name"] for r in st["adapters"]]
    assert names == ["heuristic"] + list(systemone.ADAPTERS)
    assert st["active"] == "laya"
    active = [r["name"] for r in st["adapters"] if r["active"]]
    assert active == ["laya"]
    assert [r["name"] for r in st["adapters"] if r["configured"]] == ["heuristic", "laya"]


def test_layaclient_is_the_generic_http_adapter_with_a_laya_label(server):
    client = LayaClient(server)
    assert isinstance(client, HTTPScoreAdapter)
    assert client.label == "laya"
    probs = client.score("edit the file", "keep", ["Edit", "WebSearch"])
    assert probs and "Edit" in probs and client.last_error is None
    assert client.health()["label"] == "laya"


def test_cli_models_lists_backends_and_marks_the_active_one(tmp_path, monkeypatch, capsys):
    from subproto import cli

    monkeypatch.setenv("SUBPROTO_MODEL", "mlx_lora")
    monkeypatch.setenv("SUBPROTO_MLX_URL", "http://127.0.0.1:8905")
    assert cli.main(["models", "--json", "--home", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["active"] == "mlx_lora"
    assert [r["name"] for r in payload["adapters"] if r["active"]] == ["mlx_lora"]

    capsys.readouterr()
    assert cli.main(["models", "--home", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "active System One backend: mlx_lora" in text
    assert "* mlx_lora" in text


def test_model_flag_threads_from_the_parser_into_the_engine(tmp_path, monkeypatch):
    """`--model laya` must be the same selection path as SUBPROTO_MODEL."""
    from subproto import cli

    monkeypatch.delenv("SUBPROTO_MODEL", raising=False)
    monkeypatch.delenv("LAYA_URL", raising=False)
    args = cli.build_parser().parse_args(["up", "--model", "openjev", "--slots"])
    assert args.model == "openjev"

    cfg = Config.load(None, data_dir=str(tmp_path), model=args.model,
                      model_url="http://127.0.0.1:8906")
    eng = Engine(cfg)
    assert eng.backend_label == "openjev"
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "heuristic", "an unreachable model must not fake its label"


def test_a_slot_overrides_the_global_selection(tmp_path, monkeypatch, server):
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    monkeypatch.setenv("SUBPROTO_MODEL", "laya")
    monkeypatch.setenv("LAYA_URL", server)
    monkeypatch.setenv("SUBPROTO_MODEL_TOOL_GATE", "openjev")
    monkeypatch.setenv("OPENJEV_URL", server)
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg)
    assert eng.slot_backend("tool_gate")[1] == "openjev"
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "openjev"
    # the un-overridden slot keeps answering with the global model
    assert eng.slot_backend("compact")[1] == "laya"
    assert eng.model_status()["slots"] == {"tool_gate": "openjev", "compact": "laya"}


def test_a_slot_can_run_model_free_while_others_do_not(tmp_path, monkeypatch, server):
    """`heuristic` on one slot is a real choice, not a broken global selection."""
    monkeypatch.setenv("SUBPROTO_APPLY", "tool_gate")
    monkeypatch.setenv("SUBPROTO_MODEL", "laya")
    monkeypatch.setenv("LAYA_URL", server)
    monkeypatch.setenv("SUBPROTO_MODEL_TOOL_GATE", "heuristic")
    cfg = Config(source={"data_dir": str(tmp_path)})
    eng = Engine(cfg)
    body = synthetic_request(0)
    _, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    gate = [d for d in decisions if d["slot"] == "tool_gate"][0]
    assert gate["backend"] == "heuristic"
    assert eng.slot_backend("compact")[1] == "laya"


def test_model_by_slot_comes_from_the_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SUBPROTO_MODEL_TOOL_GATE", raising=False)
    monkeypatch.delenv("SUBPROTO_MODEL_COMPACT", raising=False)
    monkeypatch.delenv("LAYA_URL", raising=False)
    cfg = Config(source={
        "data_dir": str(tmp_path),
        "model": "laya",
        "model_by_slot": {"compact": "semif"},
        "laya_url": "http://127.0.0.1:8907",
        "model_url": "http://127.0.0.1:8908",
    })
    adapter, label = systemone.resolve(cfg, slot="compact")
    assert (label, adapter.base_url) == ("semif", "http://127.0.0.1:8908")
    assert systemone.resolve(cfg, slot="tool_gate")[1] == "laya"


def test_only_model_backed_slots_are_offered_per_slot_selection():
    """context/effort are graph- and shape-driven; pinning a model there would lie."""
    assert systemone.MODEL_SLOTS == ("tool_gate", "compact")
    assert "context" not in systemone.MODEL_SLOTS
    assert systemone.slot_env("compact") == "SUBPROTO_MODEL_COMPACT"


def test_status_reports_which_slot_uses_which_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPROTO_MODEL", "laya")
    monkeypatch.setenv("LAYA_URL", "http://127.0.0.1:8890")
    monkeypatch.setenv("SUBPROTO_MODEL_COMPACT", "djev")
    monkeypatch.setenv("DJEV_URL", "http://127.0.0.1:8891")
    st = systemone.status(Config(source={"data_dir": str(tmp_path)}))
    assert st["slots"] == {"tool_gate": "laya", "compact": "djev"}
    by_name = dict((r["name"], r) for r in st["adapters"])
    assert by_name["laya"]["slots"] == ["tool_gate"]
    assert by_name["djev"]["slots"] == ["compact"]
    assert by_name["semif"]["slots"] is None
