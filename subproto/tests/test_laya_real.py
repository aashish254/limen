"""S39: the `laya` backend — real Laya's question shape, mapped back to /score.

Two kinds of witness, deliberately:

* the mapping logic is tested against a **stub runtime** injected into
  `sys.modules`, because the real package needs Python >= 3.10 and torch, and the
  3.9 leg of `run_tests.sh` must still exercise every branch here;
* the *contract* — that the checkpoint answers this shape at all — can only be
  witnessed by the model, so `test_live_checkpoint_*` runs when, and only when,
  `SUBPROTO_LAYA_PYTHON` names an interpreter that can import `laya`. It then
  starts the real server as a subprocess and drives it over HTTP.

The stub is not evidence about Laya. It is evidence that this file's own
translation (options -> criteria -> probabilities) is right, and that is the half
of V2-C that can be gated.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from subproto import laya_server
from subproto.config import Config
from subproto.laya_server import LayaScorer, LayaServer, select_backend
from subproto.systemone import HTTPScoreAdapter, timeout_for

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

# Set to an interpreter that has `pip install "laya[serve]"` (>= 3.10) to run the
# legs that need the real checkpoint — the same opt-in shape as the Postgres leg.
LAYA_PYTHON = os.environ.get("SUBPROTO_LAYA_PYTHON")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class StubRouter:
    """A `laya.Router` stand-in that records what it was asked.

    Answers with a fixed distribution over the criteria it received, so a test
    can assert the server asked one `choice` question (not N), keyed by the
    option names, and read the answer back in the caller's order.
    """

    def __init__(self, device=None, **kw):
        self.device = device
        self.calls = []

    def predict(self, state, questions, **kw):
        name, q = list(questions.items())[0]
        self.calls.append({"state": state, "type": q["type"],
                           "instructions": q["instructions"],
                           "criteria": list(q.get("criteria") or {})})
        crit = list(q.get("criteria") or ["keep", "drop"])
        # A descending distribution: the first criterion is most likely, so a
        # wrong key mapping shows up as the wrong option carrying the top score.
        total = float(len(crit))
        probs = dict((c, round((len(crit) - i) / total, 4))
                     for i, c in enumerate(crit))
        return {"answers": {name: {"type": q["type"],
                                   "choice": crit[0],
                                   "probabilities": probs,
                                   "confidence": probs[crit[0]]}},
                "usage": {"input_tokens": 42, "output_tokens": 0}}


@pytest.fixture
def stub(monkeypatch):
    """Install StubRouter as the runtime, with no weights and no torch."""
    import types

    module = types.ModuleType("laya")
    module.Router = StubRouter
    monkeypatch.setitem(sys.modules, "laya", module)
    monkeypatch.delenv("LAYA_DEVICE", raising=False)
    return StubRouter


def _scorer(stub, **kw):
    """A LayaScorer that skips the warm-up predict (it is one call, tested)."""
    scorer = LayaScorer.__new__(LayaScorer)
    scorer.shape = kw.get("shape", "choice")
    scorer.device = kw.get("device") or "cpu"
    scorer.router = stub(device=scorer.device)
    scorer.load_ms = 0
    scorer.calls = 0
    scorer.last_ms = None
    scorer.last_answer = {}
    return scorer


# ---------------------------------------------------------------- the mapping

def test_one_choice_question_over_the_whole_option_set(stub):
    """The affordable shape: N options must be ONE pass, not N of them."""
    scorer = _scorer(stub)
    probs = scorer.score("post the build status", "keep",
                         ["Read", "mcp__slack__post", "mcp__jira__comment"])
    assert len(scorer.router.calls) == 1, "one decision, one forward pass"
    assert scorer.router.calls[0]["type"] == "choice"
    assert scorer.router.calls[0]["criteria"] == ["Read", "mcp__slack__post",
                                                  "mcp__jira__comment"]
    assert probs["Read"] > probs["mcp__slack__post"] > probs["mcp__jira__comment"]


def test_options_survive_in_the_callers_order_and_case(stub):
    """Keys come back exactly as sent: the slot looks its tool up by name."""
    names = ["mcp__GitHub__PR", "Read", "Bash"]
    got = _scorer(stub).score("open a pull request", "keep", names)
    assert list(got) == names


def test_duplicate_options_are_asked_about_separately(stub):
    """Three options must be three criteria, even when two share a name.

    The collision is the silent kind: Laya keys its answer by the criterion, so
    duplicate names would collapse to one entry and the third option would be
    scored as if it were the second. The vector that comes back can still only
    carry one value per name (last wins) — that is the contract's shape, and the
    suffixes at least stop the *question* from losing an option.
    """
    scorer = _scorer(stub)
    got = scorer.score("q", "keep", ["Read", "Read", "Grep"])
    assert scorer.router.calls[0]["criteria"] == ["Read#0", "Read#1", "Grep"]
    assert len(got) == 2
    assert abs(got["Grep"] - 1.0 / 3.0) < 0.01, \
        "Grep was scored as the second of two criteria, so an option was lost"


def test_empty_option_names_still_get_an_answer(stub):
    got = _scorer(stub).score("q", "keep", ["", None, "Read"])
    assert set(got) == {"", "None", "Read"}
    assert all(0.0 <= v <= 1.0 for v in got.values())


def test_an_empty_option_is_asked_about_as_something(stub):
    """The repair lives in the question, not the reply.

    An empty criterion is a well-formed question with nothing in it: the encoder
    scores the instruction against nothing, and the option comes back with a
    probability that describes the sentence around it. `option 0` is the weakest
    claim the question can make about the caller's name.
    """
    scorer = _scorer(stub)
    scorer.score("q", "keep", ["", None, "Read"])
    assert scorer.router.calls[0]["criteria"] == ["option 0", "None", "Read"]


def test_every_shape_answers_the_callers_names_not_its_own_keys(stub):
    """`keep_drop` builds its reply per option, so it re-keys separately from
    `choice` — and a repaired key leaking into the reply loses the option."""
    for shape in ("choice", "keep_drop"):
        got = _scorer(stub, shape=shape).score("q", "keep", ["", "Read"])
        assert set(got) == {"", "Read"}, shape


def test_no_options_no_calls(stub):
    scorer = _scorer(stub)
    assert scorer.score("q", "keep", []) == {}
    assert scorer.router.calls == []


def test_answer_diagnostics_travel_with_the_probabilities(stub):
    scorer = _scorer(stub)
    scorer.score("q", "keep", ["Read", "Grep"])
    assert scorer.last_answer["input_tokens"] == 42
    assert scorer.last_answer["calibration"] == "choice-distribution"
    assert scorer.last_answer["choice"] == "Read"


def test_keep_drop_shape_asks_once_per_option(stub):
    """The expensive shape stays available, and is expensive on purpose."""
    scorer = _scorer(stub, shape="keep_drop")
    scorer.score("q", "keep", ["Read", "Grep", "Bash"])
    assert len(scorer.router.calls) == 3
    assert all(c["type"] == "choice" for c in scorer.router.calls)
    assert all(set(c["criteria"]) == {"keep", "drop"} for c in scorer.router.calls)


def test_option_names_are_not_read_as_a_format_string(stub):
    """A `%` in a tool name must not be interpolated into the instruction."""
    scorer = _scorer(stub, shape="keep_drop")
    scorer.score("q", "keep", ["100%s_coverage"])
    assert "100%s coverage" in scorer.router.calls[0]["instructions"]


# ------------------------------------------------------------- backend choice

def test_auto_stays_lexical_without_the_runtime(monkeypatch):
    monkeypatch.setattr(laya_server, "laya_importable", lambda: False)
    assert select_backend("auto") == "lexical"
    monkeypatch.setattr(laya_server, "laya_importable", lambda: True)
    assert select_backend("auto") == "laya"


def test_requesting_laya_without_it_names_the_missing_thing(monkeypatch):
    monkeypatch.setattr(laya_server, "laya_importable", lambda: False)
    with pytest.raises(RuntimeError) as exc:
        select_backend("laya")
    assert "laya[serve]" in str(exc.value) and "3.10" in str(exc.value)


def test_mlx_is_refused_with_a_reason_not_a_stub():
    """Apple MLX cannot load this checkpoint, and `import mlx` succeeding proves
    nothing about that — so the branch explains the architecture instead of
    selecting a runtime that would fail later, differently, and more quietly."""
    with pytest.raises(RuntimeError) as exc:
        select_backend("mlx")
    msg = str(exc.value)
    assert "non-autoregressive" in msg and "causal" in msg


def test_lexical_needs_nothing(monkeypatch):
    monkeypatch.setattr(laya_server, "laya_importable", lambda: False)
    scorer = laya_server.Scorer("lexical")
    assert scorer.score("fix the retry", "keep", ["Read", "mcp__slack__post"])["Read"] >= 0.5
    assert scorer.info == {"backend": "lexical", "model": None, "device": None}


def test_cpu_is_the_default_device_because_mps_aborts(stub, monkeypatch):
    """`Router(device=None)` picks MPS and the process dies on a Metal assertion.

    A default that inherits the library's choice would therefore pick the device
    that cannot answer here at all. The real `__init__` is constructed (warm-up
    excised) because `_scorer()` supplies its own device and so proves nothing
    about the source's default.
    """
    monkeypatch.delenv("LAYA_DEVICE", raising=False)
    monkeypatch.setattr(laya_server, "laya_importable", lambda: True)
    monkeypatch.setattr(LayaScorer, "warm", lambda self: self)
    assert LayaScorer().device == "cpu"
    monkeypatch.setenv("LAYA_DEVICE", "mps")
    assert LayaScorer(shape="choice").device == "mps"


def test_the_port_opens_after_the_weights_are_loaded(stub, monkeypatch):
    """`Scorer("laya")` must not accept traffic before the first predict returns.

    The checkpoint's first call costs seconds; taking the port first would let a
    health probe pass while every decision still in flight timed out and fell
    back to the heuristic — a server that reports itself healthy while answering
    nothing.
    """
    order = []

    class Watchful(stub):
        def __init__(self, device=None, **kw):
            stub.__init__(self, device=device)
            order.append("router built")

        def predict(self, *a, **kw):
            order.append("answered")
            return stub.predict(self, *a, **kw)

    import types
    module = types.ModuleType("laya")
    module.Router = Watchful
    monkeypatch.setitem(sys.modules, "laya", module)
    monkeypatch.setattr(laya_server, "laya_importable", lambda: True)
    scorer = laya_server.Scorer("laya")
    assert order == ["router built", "answered"], order
    assert scorer.info["backend"] == "laya"


def test_a_scorer_failure_answers_500_instead_of_dropping_the_socket(lexical_url,
                                                                    monkeypatch):
    """The adapter reads a refused/short response as "no answer"; a 500 with the
    reason in it is the difference between a diagnosable outage and a silent one."""
    def boom(state, question, options):
        raise RuntimeError("checkpoint evicted")

    monkeypatch.setattr(laya_server, "lexical_probabilities", boom)
    req = urllib.request.Request(
        lexical_url + "/score",
        data=json.dumps({"state": "s", "options": ["Read"]}).encode(),
        headers={"content-type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 500
    assert b"checkpoint evicted" in exc.value.read()


# ---------------------------------------------------------------- the budget

def test_the_budget_is_a_number_not_a_flag(monkeypatch):
    """`_env_flag` answers "is this truthy?", so routing a *value* through it
    turned SUBPROTO_MODEL_TIMEOUT_MS=1500 into 0 ms and starved every call."""
    monkeypatch.setenv("SUBPROTO_MODEL_TIMEOUT_MS", "1500")
    assert Config(source={}).model_timeout_ms == 1500
    assert timeout_for(Config(source={})) == 1.5
    monkeypatch.setenv("SUBPROTO_MODEL_TIMEOUT_MS", "not-a-number")
    assert Config(source={}).model_timeout_ms == 350
    monkeypatch.delenv("SUBPROTO_MODEL_TIMEOUT_MS")
    assert timeout_for(Config(source={"model_timeout_ms": 800})) == 0.8
    assert timeout_for(None) == 0.35


def test_a_real_endpoint_gets_the_configured_budget(stub, monkeypatch):
    """The bench and the engine must not disagree about how long to wait."""
    import subproto.systemone.registry as registry

    monkeypatch.setenv("LAYA_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SUBPROTO_MODEL_TIMEOUT_MS", "1200")
    adapter, label = registry.resolve(Config(source={}), slot="tool_gate")
    assert label == "laya" and adapter.timeout == 1.2


# ------------------------------------------------- the contract, over HTTP

@pytest.fixture
def lexical_url():
    srv = LayaServer(port=free_port(), backend="lexical").start()
    try:
        yield "http://127.0.0.1:%d" % srv.port
    finally:
        srv.stop()


def test_health_says_which_backend_and_what_budget(lexical_url):
    with urllib.request.urlopen(lexical_url + "/health", timeout=5) as r:
        data = json.loads(r.read().decode())
    assert data["ok"] is True and data["backend"] == "lexical"
    assert data["model"] is None, "a stand-in must not be described as a checkpoint"


# ------------------------------------------------------ the real checkpoint

live = pytest.mark.skipif(
    not LAYA_PYTHON or not os.path.exists(LAYA_PYTHON),
    reason="needs SUBPROTO_LAYA_PYTHON=<interpreter that can import laya>")

# A wall-clock assertion is a *measurement session*, not a side effect of a test
# run: the same warm checkpoint answered in 136 ms on an idle host and 5231 ms
# while something else on this machine held 10 GB of model weights resident. The
# correctness clause below is meaningful on any host; the budget clause only
# means something when the operator says "certify now".
budget_ok = os.environ.get("SUBPROTO_LAYA_BUDGET", "").strip().lower() in ("1", "true", "yes", "on")


@pytest.fixture(scope="module")
def live_url():
    """One real checkpoint, loaded once, driven over HTTP."""
    port = free_port()
    proc = subprocess.Popen(
        [LAYA_PYTHON, "-m", "subproto.laya_server", "--port", str(port),
         "--backend", "laya"],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = "http://127.0.0.1:%d" % port
    try:
        # Poll the port, do not grep the log: the server's own line is
        # block-buffered when redirected, so it can arrive after it is ready.
        deadline = time.time() + 240
        health = None
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail("server exited %s: %s" % (proc.returncode,
                                                      proc.stdout.read().decode()[-400:]))
            try:
                with urllib.request.urlopen(url + "/health", timeout=3) as r:
                    health = json.loads(r.read().decode())
                break
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(1.0)
        assert health, "the checkpoint never answered /health"
        assert health["backend"] == "laya" and health["device"] in ("cpu", "mps")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


NAMES = ["Read", "Write", "Edit", "Bash", "Grep", "Glob",
         "mcp__slack__post", "mcp__github__pr", "mcp__sentry__list"]


def _ask(url, state, timeout=10.0):
    adapter = HTTPScoreAdapter(url, label="laya", timeout=timeout)
    return adapter, adapter.score(state, "keep", NAMES)


@live
def test_live_checkpoint_answers_the_score_contract(live_url):
    """V2-C1's other half: the shipped translation is the one the encoder wants.

    Everything above proves this file speaks to a `Router`. Only the checkpoint
    can prove a `Router` answers a keep/drop question about tools at all.
    """
    adapter, probs = _ask(live_url, "post the nightly build status to the #release "
                                    "slack channel")
    assert probs and set(probs) == set(NAMES), "every option answered for"
    assert probs["mcp__slack__post"] == max(probs.values()), \
        "a slack task must rank slack first, got %s" % sorted(
            probs.items(), key=lambda kv: -kv[1])[:3]
    # The adapter must carry the server's own timing, or the bench has no latency
    # to report and cannot tell a fast answer from a missing one.
    assert isinstance(adapter.last_latency_ms, (int, float)), "no round-trip ms recorded"


@live
@pytest.mark.skipif(not budget_ok,
                    reason="export SUBPROTO_LAYA_BUDGET=1 on a quiet host to certify "
                           "that a live decision fits the shipped budget")
def test_live_answer_fits_the_shipped_budget(live_url):
    """The engine's own budget, not a test's: if a real decision costs more than
    this, the slot falls back and the model is decoration."""
    adapter, probs = _ask(live_url, "fix the failing test in the auth module")
    assert probs, "no answer to price"
    assert adapter.last_latency_ms <= Config(source={}).model_timeout_ms, \
        "%d ms > the shipped %d ms budget; raise SUBPROTO_MODEL_TIMEOUT_MS" % (
            adapter.last_latency_ms, Config(source={}).model_timeout_ms)

