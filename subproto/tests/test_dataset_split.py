"""T21: the training split must be deterministic, leak-free, de-duplicated and
correctly formatted for Laya supervision."""

import json
import os

from subproto import dataset
from subproto.config import Config
from subproto.telemetry import Telemetry


def _row(rid, sha, slot="tool_gate", targets=(("read", True), ("slack", False))):
    decisions = [{"slot": slot, "backend": "heuristic",
                  "candidates": [{"target": t, "keep": k} for t, k in targets]}]
    return {"id": rid, "ts": float(rid), "api": "anthropic", "model": "m", "status": 200,
            "usage": {"model": "m", "input_uncached": 100, "cache_write": 0,
                      "cache_read": 0, "output": 5, "reasoning": 0, "source": "anthropic"},
            "features": {"cat_chars": {}, "tool_names": [t for t, _ in targets]},
            "body_sha": sha, "tool_names": json.dumps([t for t, _ in targets]),
            "decisions": decisions}


def _seed(tel, rows):
    for r in rows:
        rec = dict(r)
        rec.pop("id", None)
        tel.record(rec)


def _tel(tmp_path):
    cfg = Config(source={"data_dir": str(tmp_path)})
    return cfg, Telemetry(cfg.db_path)


def test_split_is_deterministic(tmp_path):
    cfg, tel = _tel(tmp_path)
    _seed(tel, [_row(i, "sha%d" % i) for i in range(10)])
    a = dataset.build_training_split(cfg, tel, val_frac=0.3, seed=7, write=False)
    b = dataset.build_training_split(cfg, tel, val_frac=0.3, seed=7, write=False)
    assert a["train"] == b["train"] and a["val"] == b["val"]
    assert a["n_train"] + a["n_val"] == a["n_examples"]
    tel.close()


def test_split_no_leakage(tmp_path):
    cfg, tel = _tel(tmp_path)
    _seed(tel, [_row(i, "sha%d" % i) for i in range(12)])
    s = dataset.build_training_split(cfg, tel, val_frac=0.25, seed=11, write=False)

    def keys(part):
        return set((e["body_sha"], e["slot"], e["options"][0]) for e in part)
    assert keys(s["train"]).isdisjoint(keys(s["val"])), "train/val overlap"
    assert len(keys(s["train"]) | keys(s["val"])) == s["n_examples"]
    tel.close()


def test_split_deduplicates_by_body_sha(tmp_path):
    cfg, tel = _tel(tmp_path)
    # same body_sha replayed twice -> identical (sha, slot, target) keys
    _seed(tel, [_row(1, "dup"), _row(2, "dup")])
    s = dataset.build_training_split(cfg, tel, write=False)
    assert s["n_examples"] == 2  # read + slack, not 4
    tel.close()


def test_example_format_is_laya_supervision_shape(tmp_path):
    cfg, tel = _tel(tmp_path)
    _seed(tel, [_row(1, "s1", targets=(("read", True),))])
    s = dataset.build_training_split(cfg, tel, val_frac=0.0, write=False)
    ex = s["train"][0]
    assert set(ex) >= {"state", "question", "options", "answer"}
    assert ex["question"] == "keep"
    assert ex["answer"] in ("keep", "drop")
    assert isinstance(ex["options"], list) and len(ex["options"]) == 1
    tel.close()


def test_human_bad_decisions_are_excluded(tmp_path):
    cfg, tel = _tel(tmp_path)
    _seed(tel, [_row(1, "s1"), _row(2, "s2")])
    dataset.save_labels(cfg, {"1": {"tool_gate": "bad"}})
    s = dataset.build_training_split(cfg, tel, write=False)
    shas = set(e["body_sha"] for e in s["train"] + s["val"])
    assert "s1" not in shas and "s2" in shas
    # the good/human provenance is tracked on surviving examples
    assert all(e["label_source"] in ("heuristic", "human") for e in s["train"] + s["val"])
    tel.close()


def test_split_writes_files(tmp_path):
    cfg, tel = _tel(tmp_path)
    cfg.ensure_dirs()
    _seed(tel, [_row(i, "sha%d" % i) for i in range(8)])
    s = dataset.build_training_split(cfg, tel, val_frac=0.25, seed=3, write=True)
    assert os.path.exists(s["train_path"]) and os.path.exists(s["val_path"])
    with open(s["train_path"]) as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert len(lines) == s["n_train"]
    tel.close()


def test_export_slots_none_defaults_to_all_slots(tmp_path):
    """Regression: `subproto export` with no --slots reaches dataset.export(slots=None)
    through the CLI; that crashed on `slot not in None`. None must mean 'every slot'."""
    cfg, tel = _tel(tmp_path)
    _seed(tel, [_row(1, "s1"), _row(2, "s2", slot="compact")])
    res = dataset.export(cfg, tel, path=str(tmp_path / "ds.jsonl"), slots=None)
    assert res["rows"] == 2, "both slots export when slots is None"
    assert res["slots"]["tool_gate"] == 1 and res["slots"]["compact"] == 1
    assert res["slots"]["context"] == 0
    tel.close()


def test_export_is_a_stable_superset_projection(tmp_path):
    """FR-5, honestly stated: the JSONL is re-derived from the telemetry DB in id
    order, so exporting again after new traffic keeps every earlier row identical and
    appends the rest. The DB is the append-only store, not the file."""
    cfg, tel = _tel(tmp_path)
    cfg.ensure_dirs()
    _seed(tel, [_row(1, "s1"), _row(2, "s2")])
    dataset.export(cfg, tel, path=str(tmp_path / "d.jsonl"))
    with open(str(tmp_path / "d.jsonl")) as f:
        first = [json.loads(l) for l in f]
    _seed(tel, [_row(3, "s3")])
    dataset.export(cfg, tel, path=str(tmp_path / "d.jsonl"))
    with open(str(tmp_path / "d.jsonl")) as f:
        again = [json.loads(l) for l in f]
    assert len(first) == 2 and len(again) == 3
    assert again[:2] == first, "a re-export rewrote history"
    assert set(r["schema"] for r in again) == {"subproto/1"}
    assert [r["request_id"] for r in again] == [1, 2, 3]
    tel.close()
