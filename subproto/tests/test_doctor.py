"""``subproto doctor`` — every state word here is witnessed, not asserted.

The page is held to the same style gates test_style.py applies to CLI surfaces,
and driven both ways: through ``check_all``/``render``/``run`` for the rows, and
through ``cli.main`` for the command a stranger actually types. Backends are
probed over real sockets: a lexical ``LayaServer`` for the up case, a refused
port for the dead one.
"""

import json
import os
import platform
import re
import socket
import sys
import time

import pytest

from subproto import __version__, cli, doctor, laya_server, style
from subproto.config import Config
from subproto.systemone import registry
from subproto.systemone.base import timeout_for

BOX = re.compile(u"[┌┐└┘├┤┬┴┼─│═║╔╗╚╝]")
RULE = re.compile(r"^\s*[+\-=_|]{4,}\s*$")
COPYABLE = re.compile(r"\S/|\.json|python|^.*--\w")
MAX_W = 100
HUES = ("31", "32", "33")
ESCAPES = re.compile(r"\033\[([0-9;]*)m([^\033]*)")
STATES = (style.OK, style.UP, style.YES, style.READY, style.PRESENT,
          style.INSTALLED, style.DELIVERED, style.POTENTIAL, style.WARN,
          style.SHORT, style.OFF, style.NOT_READY, style.DOWN, style.MISSING,
          style.BLOCK)
ALL_ENVS = doctor.ENV_VARS + doctor.KEY_VARS + ("SUBPROTO_MODEL",
                                                "SUBPROTO_MODEL_URL",
                                                "SUBPROTO_HOME", "SUBPROTO_PORT")


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A stranger's machine: no backend, no key, data dir under pytest's tmp."""
    for var in ALL_ENVS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SUBPROTO_HOME", str(tmp_path / "home"))
    return Config.load()


def _row(rows, label):
    return next(r for r in rows if r["label"] == label)


# ------------------------------------------------------------------ the page


def test_the_page_survives_the_style_gates(env, monkeypatch):
    """doctor is held to test_style.py's grid here rather than in its PAGES table on
    purpose: every other page must render byte-identically twice, and this one names
    the machine — a real interpreter path, a port the kernel just chose, whatever keys
    happen to be exported. The grid rules apply; the determinism rule cannot."""
    monkeypatch.setattr(laya_server, "laya_importable", lambda: False)
    rows = doctor.check_all(env, port=_free_port())
    plain = doctor.render(rows)
    painted = doctor.render(rows, color=True)
    assert plain.strip()
    assert style.strip(painted) == plain, "hue must be additive"
    first = plain.splitlines()[0]
    assert first.startswith("subproto doctor —") and first.count(" — ") == 1
    for line in plain.splitlines():
        assert not BOX.search(line), line
        assert not RULE.match(line), line
        assert len(line) <= MAX_W or COPYABLE.search(line), line
    for code, payload in ESCAPES.findall(painted):
        if any(h in code.split(";") for h in HUES):
            assert payload.strip() in STATES, repr(payload)


def test_every_row_has_the_documented_shape(env):
    for row in doctor.check_all(env, port=_free_port()):
        assert set(row) == {"section", "label", "state", "value", "note", "fix"}


# ------------------------------------------------------------------ python


def test_the_floor_is_read_from_pyproject_not_hardcoded():
    assert doctor._py_floor() == ((3, 9), "pyproject.toml")


def test_the_floor_the_page_prints_is_the_one_the_file_says(tmp_path, monkeypatch,
                                                            env):
    """This checkout says `3.9` and so does the shipped default, so the test above
    cannot tell reading from quoting a constant. A file that says otherwise can: the
    row must move with it, or a release that raises the floor would keep telling
    readers the old one."""
    path = str(tmp_path / "pyproject.toml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('[project]\nrequires-python = ">=3.12"\n')
    monkeypatch.setattr(doctor, "PYPROJECT", path)
    assert doctor._py_floor() == ((3, 12), "pyproject.toml")
    # which of the two rows this is depends on the interpreter running the suite, so
    # the claim under test is the one both branches carry: the number came from a file
    assert "3.12" in json.dumps(_row(doctor.check_all(env, port=_free_port()),
                                     "python"))


def test_an_installed_build_says_that_its_floor_is_the_default(env, monkeypatch):
    """`pip install subproto` puts no `pyproject.toml` beside the package, so a page
    that reads "3.9" there is quoting a constant, not a file — and has to say so.

    The row is the whole difference between a floor a reader can check and one they
    have to take on trust, and this is the branch no runner here ever hits by accident.
    """
    monkeypatch.setattr(doctor, "PYPROJECT",
                        os.path.join(env.data_dir, "absent.toml"))
    floor, seen_at = doctor._py_floor()
    assert floor == doctor.FLOOR and seen_at == "the shipped default"
    row = _row(doctor.check_all(env, port=_free_port()), "python")
    assert "the shipped default" in row["note"]


def test_a_python_below_the_floor_is_red_and_names_the_path(env, monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 8, 10, "final", 0))
    row = _row(doctor.check_all(env, port=_free_port()), "python")
    assert row["state"] == style.MISSING and "3.9" in row["fix"]
    assert "pyproject.toml" in row["fix"], "the fix names where the requirement lives"


def test_a_supported_python_names_the_interpreter_that_ran_it(env):
    row = _row(doctor.check_all(env, port=_free_port()), "python")
    assert row["state"] == style.OK
    assert sys.executable in row["note"]


# ------------------------------------------------------------------ data dir


def test_the_witness_is_a_real_write_and_removal(env):
    row = _row(doctor.check_all(env, port=_free_port()), "data dir")
    assert row["state"] == style.OK
    assert os.path.isdir(env.data_dir)
    assert not os.path.exists(os.path.join(env.data_dir, doctor.PROBE_NAME))


def test_an_unwritable_data_dir_is_called_missing(tmp_path, monkeypatch, env):
    """The guard is inside the test, not on a `skipif`, because `os.geteuid` and
    `os.chmod` are both missing or inert on some platforms — and an import-time
    `os.geteuid()` in a marker would take the whole file down with it there."""
    if not hasattr(os, "geteuid") or not hasattr(os, "chmod"):
        pytest.skip("no POSIX permission model here")
    if os.geteuid() == 0:
        pytest.skip("root writes anywhere")
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(str(locked), 0o500)
    monkeypatch.setenv("SUBPROTO_HOME", str(locked))
    try:
        # The precondition is checked, not assumed: a filesystem that ignores mode
        # bits (a mounted share, Windows developer mode) makes the assertion below
        # a measurement of nothing.
        assert not os.access(str(locked), os.W_OK), \
            "chmod did not make the directory unwritable"
        row = _row(doctor.check_all(env, port=_free_port()), "data dir")
        assert row["state"] == style.MISSING
        assert "SUBPROTO_HOME" in row["fix"]
    finally:
        os.chmod(str(locked), 0o700)


# ------------------------------------------------------------------ port


def test_the_port_is_asked_of_the_kernel(env):
    port = _free_port()
    assert _row(doctor.check_all(env, port=port), "port")["state"] == style.OK
    holder = socket.socket()
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:
        row = _row(doctor.check_all(env, port=port), "port")
        assert row["state"] == style.DOWN
        assert "subproto up --port" in row["fix"]
    finally:
        holder.close()


# ------------------------------------------------------------------ backends


def test_no_backend_configured_is_not_the_same_as_broken(env):
    rows = doctor.check_all(env, port=_free_port())
    row = _row(rows, "backend")
    assert row["state"] is None and "not configured" in row["value"]
    assert not [r for r in rows if r["state"] in doctor.RED]


def test_a_configured_backend_is_probed_over_real_http(env, monkeypatch):
    srv = laya_server.LayaServer(port=0, backend="lexical").start()
    port = srv.server_address[1]
    monkeypatch.setenv("OPENJEV_URL", "http://127.0.0.1:%d" % port)
    try:
        row = _row(doctor.check_all(env, port=_free_port()), "openjev")
        assert row["state"] == style.UP and row["fix"] is None
    finally:
        srv.stop()
    row = _row(doctor.check_all(env, port=_free_port()), "openjev")
    assert row["state"] == style.DOWN
    assert "OPENJEV_URL" in row["fix"] and "heuristics" in row["fix"]


def test_all_dead_endpoints_still_answer_the_page_fast(env, monkeypatch):
    for var in doctor.ENV_VARS:
        monkeypatch.setenv(var, "http://127.0.0.1:1")
    started = time.time()
    rows = doctor.check_all(env, port=_free_port())
    # Five names on one dead URL is *one* probe, so the floor is one `timeout_for`
    # budget and some slack. The slack is deliberately wide: what this catches is a
    # missing timeout, and an unanswered SYN costs ~75 s, while a loaded runner
    # costs a second or two. A tight bound here would flake and not inform.
    assert time.time() - started < max(5.0, 20 * timeout_for(env))
    down = [r for r in rows if r["state"] == style.DOWN]
    assert down, "five names on one dead url must not read as healthy"
    assert "," in down[0]["label"], "shared urls collapse into one probe row"


def test_the_probe_budget_is_the_shipped_timeout_for(env, monkeypatch):
    monkeypatch.setenv("OPENJEV_URL", "http://127.0.0.1:1")
    assert timeout_for(env) <= 0.35
    for adapter in registry.configured_adapters(env).values():
        assert adapter.timeout == timeout_for(env)


def test_a_raw_url_selection_is_reachable_and_named_by_nothing(env, monkeypatch):
    monkeypatch.setenv("SUBPROTO_MODEL", "http://127.0.0.1:1")
    row = _row(doctor.check_all(env, port=_free_port()), "model")
    assert row["state"] == style.DOWN and "SUBPROTO_MODEL" in row["fix"]


# ------------------------------------------------------------------ laya


def test_laya_absent_says_what_to_install_without_a_private_path(env, monkeypatch):
    monkeypatch.setattr(laya_server, "laya_importable", lambda: False)
    row = _row(doctor.check_all(env, port=_free_port()), "laya runtime")
    assert row["state"] == style.NOT_READY
    assert 'pip install "laya[serve]"' in row["fix"] and "3.10" in row["fix"]
    assert "laya-venv" not in row["fix"], "the fix must name no maintainer path"


def test_laya_present_is_reported_installed(env, monkeypatch):
    monkeypatch.setattr(laya_server, "laya_importable", lambda: True)
    assert _row(doctor.check_all(env, port=_free_port()),
                "laya runtime")["state"] == style.INSTALLED


# ------------------------------------------------------------------ keys


def test_keys_report_presence_and_never_the_material(env, monkeypatch):
    row = _row(doctor.check_all(env, port=_free_port()), "provider keys")
    assert row["state"] == style.WARN and not row["value"].startswith("sk")
    secret = "sk-test-DO-NOT-PRINT-0123456789"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    rows = doctor.check_all(env, port=_free_port())
    row = _row(rows, "provider keys")
    assert row["state"] == style.PRESENT and row["value"] == "OPENAI_API_KEY"
    page = doctor.render(rows, color=True)
    assert secret not in page and "DO-NOT-PRINT" not in page


# ------------------------------------------------------------------ run


def test_run_prints_a_page_and_exits_zero_when_nothing_is_red(env, capsys):
    port = _free_port()
    assert doctor.run(["--port", str(port), "--no-color"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("subproto doctor —") and str(port) in out


def test_run_exits_one_when_a_check_is_actually_red(env, capsys):
    port = _free_port()
    holder = socket.socket()
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:
        assert doctor.run(["--port", str(port), "--no-color"]) == 1
    finally:
        holder.close()
    assert "not available" in capsys.readouterr().out


def test_run_exits_one_when_a_port_is_taken(env, capsys):
    port = _free_port()
    holder = socket.socket()
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:
        assert doctor.run(["--port", str(port), "--no-color"]) == 1
    finally:
        holder.close()


def test_port_zero_reports_the_port_it_took_not_zero(env, capsys):
    """`--port 0` is a question ("find any free one"), so the page must name the
    answer the kernel gave. Printing `0  127.0.0.1` would be a true sentence about
    nothing: no listener ever uses port 0."""
    rows = doctor.check_all(env, port=0)
    chosen = int(_row(rows, "port")["value"].split()[0])
    assert chosen > 0 and doctor.port_free(chosen)
    assert doctor.run(["--port", "0", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "any free port" in out, out


# ------------------------------------------------------------------ the seams


def test_json_is_the_same_verdict_as_the_page(env, capsys):
    port = _free_port()
    rows = doctor.check_all(env, port=port)
    assert doctor.run(["--port", str(port), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True
    assert len(doc["checks"]) == len(rows)
    for got, row in zip(doc["checks"], rows):
        assert got == {k: v for k, v in row.items() if v is not None}


def test_json_says_what_the_page_says_when_a_check_fails(env, capsys):
    port = _free_port()
    holder = socket.socket()
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:
        assert doctor.run(["--port", str(port), "--json"]) == 1
    finally:
        holder.close()
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False
    red = [c for c in doc["checks"] if c.get("state") in doctor.RED]
    assert [c["label"] for c in red] == ["port"], red
    assert red[0]["fix"], "a red row without a remediation is a warning, not a fix"


def test_the_cli_answers_for_doctor(env, capsys, monkeypatch):
    """The documented command, through the real parser — not the module call."""
    monkeypatch.setattr(laya_server, "laya_importable", lambda: False)
    assert cli.main(["doctor", "--home", env.data_dir, "--port", "0"]) == 0
    assert capsys.readouterr().out.startswith("subproto doctor —")


def test_the_cli_carries_a_red_check_out_to_its_exit(env, capsys):
    port = _free_port()
    holder = socket.socket()
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:
        assert cli.main(["doctor", "--home", env.data_dir,
                         "--port", str(port)]) == 1
    finally:
        holder.close()


def test_the_version_line_names_the_build_the_interpreter_and_the_os(capsys):
    """A bug report needs all four, and `doctor` needs the same interpreter path,
    so the two must agree about which build is running."""
    assert cli.main(["--version"]) == 0
    line = capsys.readouterr().out.strip()
    assert line == "subproto %s (python %s, %s %s)" % (
        __version__, ".".join(str(n) for n in sys.version_info[:3]),
        platform.system().lower(), platform.machine().lower())
    assert line.startswith("subproto " + __version__)
