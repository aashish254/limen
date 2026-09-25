"""T22: the finetune script's data contract is testable without MLX; the actual
training step is honest about the external checkpoint it needs."""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import train.finetune_mlx as ft


def _write(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_load_split_validates(tmp_path):
    good = {"state": "fix retry in app/x.py", "question": "keep",
            "options": ["read"], "answer": "keep"}
    p = str(tmp_path / "train.jsonl")
    _write(p, [good, dict(good, answer="drop", options=["slack"])])
    rows = ft.load_split(p)
    assert len(rows) == 2 and rows[0]["answer"] == "keep"


def test_load_split_rejects_bad_rows(tmp_path):
    p = str(tmp_path / "bad.jsonl")
    _write(p, [{"state": "x", "question": "keep", "options": [], "answer": "keep"}])
    try:
        ft.load_split(p)
        assert False, "expected ValueError on empty options"
    except ValueError:
        pass
    _write(p, [{"question": "keep", "options": ["a"], "answer": "keep"}])  # no state
    try:
        ft.load_split(p)
        assert False, "expected ValueError on missing key"
    except ValueError:
        pass


def test_render_prompt_matches_serve_shape():
    ex = {"state": "refactor gateway_client", "question": "keep",
          "options": ["webfetch"], "answer": "drop"}
    text = ft.render_prompt(ex)
    assert "webfetch" in text and text.startswith("context:")


def test_check_only_entrypoint(tmp_path):
    p = str(tmp_path / "train.jsonl")
    v = str(tmp_path / "val.jsonl")
    row = {"state": "s", "question": "keep", "options": ["read"], "answer": "keep"}
    _write(p, [row, row]); _write(v, [row])
    rc = ft.main(["--train", p, "--val", v, "--check-only"])
    assert rc == 0


def test_train_without_mlx_is_honest(tmp_path, monkeypatch):
    # even if mlx were importable, a missing checkpoint must stop cleanly
    monkeypatch.setattr(ft, "mlx_available", lambda: False)
    p = str(tmp_path / "train.jsonl")
    _write(p, [{"state": "s", "question": "keep", "options": ["read"], "answer": "keep"}])
    rc = ft.main(["--train", p])  # no --check-only -> hits train()
    assert rc == 3  # missing deps, not a fake success
