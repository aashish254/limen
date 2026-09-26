"""v4 S33: model *versions* — selectable, health-gated, roll-back-able, and stamped.

The claim under test is not "we have a manifest file". It is that a fine-tune can be
told apart from the model it replaced in the telemetry, that a dead endpoint cannot
quietly become the answer, and that a decision is never attributed to a model that did
not produce it. Those three are the whole reason the version column exists, so each one
gets a test that would fail if the code tried to flatter the operator.
"""

import json
import os
import socket

import pytest

from subproto import cli, dataset, report, systemone
from subproto.config import Config
from subproto.demo import synthetic_request
from subproto.engine import Engine
from subproto.proxy import analyze_request
from subproto.telemetry import Telemetry


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DEAD = "http://127.0.0.1:%d" % 9          # nothing listens here; refusal is instant


@pytest.fixture
def server():
    from subproto.laya_server import LayaServer
    srv = LayaServer(port=free_port(), backend="lexical").start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.stop()


@pytest.fixture
def clean_env(monkeypatch):
    """No inherited selection: the manifest must be the only thing that can choose."""
    for key in ("SUBPROTO_MODEL", "SUBPROTO_MODEL_URL", "SUBPROTO_MODEL_COMPACT",
                "SUBPROTO_MODEL_TOOL_GATE", "LAYA_URL", "OPENJEV_URL", "DJEV_URL",
                "SEMIF_URL", "SUBPROTO_MLX_URL", "SUBPROTO_ROUTER", "SUBPROTO_COMPILE",
                "SUBPROTO_APPLY", "SUBPROTO_ENFORCE", "SUBPROTO_HOME"):
        monkeypatch.delenv(key, raising=False)


def _cfg(tmp_path, **extra):
    cfg = Config.load(None, data_dir=str(tmp_path), port=8899, **extra)
    cfg.ensure_dirs()
    return cfg


def _manifest(tmp_path, *rows, active=None, previous=None):
    m = {"versions": [], "active": active, "previous": previous, "warnings": []}
    for row in rows:
        m, _ = systemone.add(m, **row)
    return m


# --- the manifest itself -------------------------------------------------------

def test_a_missing_manifest_is_an_empty_one_not_an_error(tmp_path):
    target = str(tmp_path / "models.json")
    assert not os.path.exists(target)
    m = systemone.load(target)
    assert m["versions"] == [] and m["active"] is None and m["previous"] is None


def test_a_corrupt_manifest_is_empty_and_says_so(tmp_path):
    target = tmp_path / "models.json"
    target.write_text("{this is not json")
    m = systemone.load(str(target))
    assert m["versions"] == []
    assert m["warnings"], "silently ignoring a broken manifest is how it stays broken"


def test_manifest_round_trips_through_disk(tmp_path):
    target = str(tmp_path / "models.json")
    m = _manifest(tmp_path, {"label": "ckpt-a", "url": DEAD, "tier": "personal",
                             "note": "epoch 3", "version": "lora-v3"},
                  active="ckpt-a")
    systemone.save(target, m)
    back = systemone.load(target)
    assert back["active"] == "ckpt-a"
    assert back["versions"][0]["version"] == "lora-v3"
    assert back["versions"][0]["note"] == "epoch 3"
    # the warnings key is a read-time artifact, never written back
    assert "warnings" not in json.loads((tmp_path / "models.json").read_text())


def test_saving_leaves_no_temp_file_behind(tmp_path):
    target = str(tmp_path / "models.json")
    systemone.save(target, _manifest(tmp_path, {"label": "a", "url": DEAD}))
    assert sorted(os.listdir(str(tmp_path))) == ["models.json"]


def test_an_active_that_names_nothing_registered_is_dropped_with_a_warning(tmp_path):
    """Otherwise resolution hands a label to the lookup table and gets nothing back."""
    target = tmp_path / "models.json"
    target.write_text(json.dumps({"versions": [{"label": "real"}], "active": "ghost",
                                  "previous": "real"}))
    m = systemone.load(str(target))
    assert m["active"] is None and m["previous"] == "real"
    assert any("ghost" in w for w in m["warnings"])


def test_add_upserts_without_losing_the_original_date_or_version(tmp_path):
    m = _manifest(tmp_path, {"label": "a", "url": DEAD, "today": "2026-01-02"})
    first = m["versions"][0]
    m, entry = systemone.add(m, "a")
    assert len(m["versions"]) == 1
    assert entry["added"] == "2026-01-02", "an upsert must not rewrite history"
    assert entry["version"] == first["version"] == "a"
    assert entry["url"] == DEAD, "an omitted url keeps the registered endpoint"


def test_add_refuses_the_reserved_name_and_an_unknown_tier(tmp_path):
    m = _manifest(tmp_path)
    with pytest.raises(ValueError):
        systemone.add(m, "heuristic", url=DEAD)
    with pytest.raises(ValueError):
        systemone.add(m, "a", url=DEAD, tier="shared")
    assert m["versions"] == []


# --- use / rollback ------------------------------------------------------------

def test_use_moves_active_and_remembers_what_it_replaced(tmp_path):
    m = _manifest(tmp_path, {"label": "v1", "url": DEAD}, {"label": "v2", "url": DEAD})
    m, msg = systemone.use(m, "v2")
    assert (m["active"], m["previous"]) == ("v2", None) and "v2" in msg
    m, msg = systemone.use(m, "v1")
    assert (m["active"], m["previous"]) == ("v1", "v2")
    assert "previous: v2" in msg


def test_an_unknown_version_fails_loudly_and_lists_what_exists(tmp_path):
    m = _manifest(tmp_path, {"label": "v1", "url": DEAD})
    with pytest.raises(ValueError) as exc:
        systemone.use(m, "v9")
    assert "v1" in str(exc.value)


def test_rollback_undoes_one_switch_then_honestly_stops(tmp_path):
    m = _manifest(tmp_path, {"label": "v1", "url": DEAD}, {"label": "v2", "url": DEAD})
    m, _ = systemone.use(systemone.use(m, "v1")[0], "v2")
    m, msg = systemone.rollback(m)
    assert (m["active"], m["previous"]) == ("v1", None) and "v1" in msg
    m, _ = systemone.rollback(m)          # v1 was activated over nothing
    assert m["active"] is None
    with pytest.raises(ValueError):
        systemone.rollback(m)


def test_switching_to_no_model_is_still_undoable(tmp_path):
    m = _manifest(tmp_path, {"label": "v1", "url": DEAD}, active="v1")
    m, msg = systemone.use(m, "heuristic")
    assert m["active"] is None and m["previous"] == "v1" and "heuristics" in msg
    m, _ = systemone.rollback(m)
    assert m["active"] == "v1"


# --- the health gate -----------------------------------------------------------

def _health_for(*alive):
    """Injected /health: an adapter is alive only if its url is in `alive`."""
    def check(adapter):
        return {"ok": adapter.base_url in alive, "label": adapter.label,
                "error": None if adapter.base_url in alive else "connection refused"}
    return check


def test_a_dead_active_version_falls_back_to_previous_with_the_reason(tmp_path):
    m = _manifest(tmp_path, {"label": "v1", "url": "http://v1"},
                  {"label": "v2", "url": "http://v2"}, active="v2", previous="v1")
    choice = systemone.select(_cfg(tmp_path), manifest=m,
                              health=_health_for("http://v1"))
    assert choice["label"] == "v1" and choice["source"] == "manifest"
    assert any("v2: /health failed" in n for n in choice["notes"])
    assert any("using v1 instead" in n for n in choice["notes"])


def test_when_everything_is_dead_the_heuristics_answer_and_say_why(tmp_path):
    m = _manifest(tmp_path, {"label": "v1", "url": "http://v1"}, active="v1")
    choice = systemone.select(_cfg(tmp_path), manifest=m, health=_health_for())
    assert choice["adapter"] is None and choice["label"] == "heuristic"
    assert choice["version"] == "heuristic"
    assert any("answering with the heuristics" in n for n in choice["notes"])


def test_a_version_with_no_endpoint_is_not_used(tmp_path):
    m = _manifest(tmp_path, {"label": "v1"}, active="v1")
    choice = systemone.select(_cfg(tmp_path), manifest=m, health=_health_for("x"))
    assert choice["label"] == "heuristic"
    assert any("no endpoint" in n for n in choice["notes"])


def test_an_empty_active_is_a_choice_not_an_accident(tmp_path):
    """`--use heuristic` leaves a registered `previous` behind; it must stay a spare.

    Resolving that state to `previous` would put a model back on the request path
    behind an operator who deliberately took every model off it.
    """
    calls = []

    def probe(adapter):
        calls.append(adapter.label)
        return {"ok": True, "label": adapter.label}

    m = _manifest(tmp_path, {"label": "v1", "url": "http://v1"}, active=None,
                  previous="v1")
    choice = systemone.select(_cfg(tmp_path), manifest=m, health=probe)
    assert choice["adapter"] is None and choice["label"] == "heuristic"
    assert choice["source"] == "default"
    assert calls == [], "nothing may be probed when no version is active"
    assert choice["notes"] == []


def test_an_explicit_pin_outranks_the_manifest(clean_env, tmp_path, server):
    """I1: nothing recorded may change behaviour a human asked for."""
    m = _manifest(tmp_path, {"label": "v1", "url": server}, active="v1")
    cfg = _cfg(tmp_path, model="openjev", model_url=DEAD)
    choice = systemone.select(cfg, manifest=m, health=_health_for(DEAD))
    assert choice["label"] == "openjev" and choice["source"] == "pin"


def test_a_per_slot_pin_outranks_the_manifest_for_that_slot_only(clean_env, tmp_path,
                                                                 server, monkeypatch):
    monkeypatch.setenv("SUBPROTO_MODEL_COMPACT", "openjev")
    monkeypatch.setenv("OPENJEV_URL", server)
    cfg = _cfg(tmp_path)
    systemone.save(systemone.path(cfg),
                   _manifest(tmp_path, {"label": "v1", "url": server, "version": "a1"},
                             active="v1"))
    eng = Engine(cfg)
    assert eng.slot_backend("compact")[1] == "openjev"       # the pin
    assert eng.slot_backend("tool_gate")[1] == "v1"          # the manifest
    assert eng.slot_versions["compact"] == "openjev"
    assert eng.slot_versions["tool_gate"] == "a1"


def test_a_live_previous_version_is_not_preferred_over_a_live_active(clean_env, tmp_path):
    """The fallback order is the whole point: previous is a spare, not an equal."""
    m = _manifest(tmp_path, {"label": "v1", "url": "http://v1"},
                  {"label": "v2", "url": "http://v2", "version": "v2.1"},
                  active="v2", previous="v1")
    choice = systemone.select(_cfg(tmp_path), manifest=m,
                              health=_health_for("http://v1", "http://v2"))
    assert (choice["label"], choice["version"]) == ("v2", "v2.1")
    assert choice["notes"] == []


def test_a_dead_pin_is_surfaced_instead_of_silently_becoming_heuristic(tmp_path):
    """The pin is honoured (it is the operator's call) but the dead end is said out loud."""
    cfg = _cfg(tmp_path, laya_url=DEAD)
    choice = systemone.select(cfg, manifest=_manifest(tmp_path), health=_health_for())
    assert choice["label"] == "laya"
    assert any("unreachable" in n and "heuristic" in n for n in choice["notes"])


# --- the stamp on every decision ----------------------------------------------

def _decide(cfg, tmp_path, manifest_rows, active, compile_on=False, monkeypatch=None):
    m = _manifest(tmp_path, *manifest_rows, active=active)
    target = systemone.path(cfg)
    systemone.save(target, m)
    if compile_on:
        monkeypatch.setenv("SUBPROTO_COMPILE", "on")
    eng = Engine(cfg)
    body = synthetic_request(0)
    _new, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    return eng, decisions


def test_every_decision_carries_the_version_that_answered(clean_env, tmp_path, server):
    cfg = _cfg(tmp_path)
    eng, decisions = _decide(cfg, tmp_path,
                             [{"label": "ckpt-a", "url": server, "version": "lora-v3"}],
                             "ckpt-a")
    assert decisions, "the synthetic turn must produce decisions"
    stamped = [d for d in decisions if d.get("model_version")]
    assert len(stamped) == len(decisions), "a decision with no version cannot be audited"
    by_slot = dict((d["slot"], d) for d in decisions)
    # the label and the version are different strings on purpose: this is the checkpoint
    assert by_slot["tool_gate"]["backend"] == "ckpt-a"
    assert by_slot["tool_gate"]["model_version"] == "lora-v3"
    assert by_slot["effort"]["model_version"] == "heuristic"
    assert eng.model_version == "lora-v3"


def test_a_heuristic_answer_never_carries_the_model_s_version(clean_env, tmp_path):
    """The mislabel guard: after degradation the stamp has to move with the answer."""
    cfg = _cfg(tmp_path)
    systemone.save(systemone.path(cfg),
                   _manifest(tmp_path, {"label": "v1", "url": DEAD}, active="v1"))
    eng = Engine(cfg)                    # v1 is dead: a real /health against a refused port
    body = synthetic_request(0)
    _new, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    assert eng.backend_label == "heuristic" and eng.model_version == "heuristic"
    assert all(d["model_version"] == "heuristic" for d in decisions)
    assert any("v1: /health failed" in n for n in eng.model_notes)


def test_the_compiled_arm_stamps_itself_not_a_model(clean_env, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _eng, decisions = _decide(cfg, tmp_path, [], None, compile_on=True,
                              monkeypatch=monkeypatch)
    assert decisions, "the compiled arm must still be legible in the telemetry"
    assert all("model_version" in d for d in decisions), \
        "an unstamped compiled decision cannot be compared with a per-slot one"
    versions = {d["model_version"] for d in decisions}
    assert versions <= {"compiled", "compiled+graph", "heuristic"}, versions


def test_a_registered_version_can_be_routed_to_on_evidence(clean_env, tmp_path, server,
                                                            monkeypatch):
    """No new selection surface: a version joins the Router's pool as just another label."""
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    cfg = _cfg(tmp_path)
    m = _manifest(tmp_path, {"label": "live-a", "url": server, "version": "a1"})
    router = systemone.Router(cfg, evidence={"live-a": {"precision": 0.99,
                                                        "latency_ms": 1.0},
                                             "heuristic": {"precision": 0.50,
                                                           "latency_ms": 0.0}},
                              manifest=m)
    assert "live-a" in router.adapters, "the manifest versions must be routable"
    adapter, label, reason = router.select("tool_gate")
    assert label == "live-a" and adapter.base_url == server
    plan = router.plan()["tool_gate"]
    assert plan["mode"] == "routed" and plan["version"] == "a1", plan


def test_a_routed_slot_carries_its_own_version(clean_env, tmp_path, server, monkeypatch):
    """Two versions, one live and one dead: routing cannot blur which answered."""
    monkeypatch.setenv("SUBPROTO_ROUTER", "on")
    cfg = _cfg(tmp_path)
    m = _manifest(tmp_path, {"label": "live-a", "url": server, "version": "a1"},
                  {"label": "dead-b", "url": DEAD, "version": "b1"})
    systemone.save(systemone.path(cfg), m)
    eng = Engine(cfg)
    assert eng.slot_versions["tool_gate"] in ("a1", "heuristic")
    body = synthetic_request(0)
    _new, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    for d in decisions:
        assert d["model_version"] == systemone.version_of(m, d["backend"])


def test_model_status_reports_the_ladder(clean_env, tmp_path):
    cfg = _cfg(tmp_path)
    m = _manifest(tmp_path, {"label": "v1", "url": DEAD}, active="v1")
    systemone.save(systemone.path(cfg), m)
    status = Engine(cfg).model_status()
    assert status["version"] == "heuristic"
    assert status["manifest"] == {"active": "v1", "previous": None, "registered": 1}
    assert any("/health failed" in n for n in status["notes"])


# --- the comparison has to be readable ----------------------------------------

def _corpus(tmp_path, versions):
    tel = Telemetry(str(tmp_path / "t.db"))
    for i, version in enumerate(versions):
        tel.record({"ts": 1.0 + i, "api": "anthropic", "model": "claude", "status": 200,
                    "usage": {"input_uncached": 1000, "output": 10},
                    "engine_ms": 1.0,
                    "decisions": [{"slot": "tool_gate", "backend": version,
                                   "model_version": version, "savings_est_tok": 100,
                                   "decision_ms": 0.5}]})
    return tel


def test_the_report_groups_decisions_by_version(tmp_path):
    tel = _corpus(tmp_path, ["lora-v1", "lora-v1", "heuristic"])
    roll = report.version_rollup(tel)
    assert roll["lora-v1"]["decisions"] == 2 and roll["lora-v1"]["requests"] == 2
    assert roll["lora-v1"]["saved_tok"] == 200
    assert roll["heuristic"]["decisions"] == 1
    tel.close()


def test_decisions_recorded_before_the_stamp_stay_visible(tmp_path):
    """A version table that quietly dropped un-stamped rows would flatter the new model."""
    tel = Telemetry(str(tmp_path / "t.db"))
    tel.record({"ts": 1.0, "api": "anthropic", "status": 200, "decisions":
                [{"slot": "compact", "backend": "heuristic"}]})
    assert "unrecorded" in report.version_rollup(tel)
    tel.close()


def test_approval_rate_is_grouped_per_version(tmp_path):
    tel = _corpus(tmp_path, ["lora-v1", "lora-v1"])
    labels = {"1": {"tool_gate": "good"}, "2": {"tool_gate": "bad"}}
    roll = report.version_rollup(tel, labels)
    assert roll["lora-v1"]["approval_rate"] == 0.5
    tel.close()


def test_render_text_prints_the_version_section(tmp_path):
    tel = _corpus(tmp_path, ["lora-v2"])
    text = report.render_text(report.summarize(tel))
    assert "by model version" in text and "lora-v2" in text
    tel.close()


def test_export_carries_the_version_per_row(clean_env, tmp_path, server):
    cfg = _cfg(tmp_path)
    tel = Telemetry(cfg.db_path)
    m = _manifest(tmp_path, {"label": "ckpt-a", "url": server, "version": "lora-v3"},
                  active="ckpt-a")
    systemone.save(systemone.path(cfg), m)
    eng = Engine(cfg)
    body = synthetic_request(0)
    _new, decisions = eng.decide("anthropic", body, analyze_request(body), cfg)
    tel.record({"ts": 1.0, "api": "anthropic", "status": 200, "decisions": decisions})
    out = dataset.export(cfg, tel, str(tmp_path / "ds.jsonl"))
    row = json.loads(open(str(tmp_path / "ds.jsonl")).readline())
    assert row["model_version"] in ("lora-v3", "heuristic")
    assert out["rows"] == len(decisions)
    tel.close()


# --- the CLI surface -----------------------------------------------------------

def _models(tmp_path, *argv):
    return cli.main(["models", "--home", str(tmp_path)] + list(argv))


def test_cli_registers_switches_and_rolls_back(tmp_path, capsys):
    assert _models(tmp_path, "--add", "v1", "--url", DEAD, "--tier", "personal",
                   "--note", "first") == 0
    assert _models(tmp_path, "--use", "v1") == 0
    assert _models(tmp_path, "--add", "v2", "--url", DEAD) == 0
    assert _models(tmp_path, "--use", "v2") == 0
    out = capsys.readouterr().out
    assert "activated v2 (previous: v1)" in out
    assert _models(tmp_path, "--rollback") == 0
    body = open(str(tmp_path / "models.json")).read()
    assert json.loads(body)["active"] == "v1"
    out = capsys.readouterr().out
    assert "rolled back to v1" in out


def test_cli_fails_without_writing_on_a_bad_selection(tmp_path, capsys):
    _models(tmp_path, "--add", "v1", "--url", DEAD)
    capsys.readouterr()
    before = open(str(tmp_path / "models.json")).read()
    assert _models(tmp_path, "--use", "nope") == 1
    assert "no version 'nope' registered" in capsys.readouterr().err
    assert open(str(tmp_path / "models.json")).read() == before


def test_cli_warns_when_an_env_pin_will_outrank_the_switch(tmp_path, capsys, server):
    os.environ["LAYA_URL"] = server
    try:
        _models(tmp_path, "--add", "v1", "--url", server)
        out = capsys.readouterr().out
        assert "SUBPROTO_MODEL/LAYA_URL is set" in out
    finally:
        del os.environ["LAYA_URL"]


def test_cli_prints_the_manifest_with_health(tmp_path, capsys, server):
    _models(tmp_path, "--add", "live", "--url", server, "--tier", "foundation")
    _models(tmp_path, "--add", "dead", "--url", DEAD)
    capsys.readouterr()
    _models(tmp_path)
    out = capsys.readouterr().out
    assert "versions in" in out
    assert "health ok" in out and "unreachable" in out
    assert "--use" in out and "--rollback" in out


def test_cli_json_carries_the_manifest_and_selection(tmp_path, capsys, server):
    _models(tmp_path, "--add", "live", "--url", server)
    _models(tmp_path, "--use", "live")
    capsys.readouterr()
    _models(tmp_path, "--json")
    st = json.loads(capsys.readouterr().out)
    assert st["manifest"]["active"] == "live"
    assert st["manifest"]["versions"][0]["health"] == "health ok"
    assert st["selection"]["source"] == "manifest"
    assert st["selection"]["version"] == "live"
