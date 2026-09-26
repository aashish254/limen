"""`subproto retrain` — the cadence policy, not the compute (TODO S34).

Training a LoRA adapter is one command: `python train/finetune_mlx.py --train …`.
The command is the easy half. The half nobody can answer from memory is *when* to
run it, and running it too early is how a fine-tune gets blamed for noise: a split
that has not changed since the last run trains the same weights again, and a slot
with ten examples cannot tell a regression from a coin flip.

So this module decides, from local state only (I4 — nothing here leaves the
machine), whether a retrain is *worth starting*, and records what it decided:

* **content** — the split fingerprint. `--dry-run` rebuilds the split without
  writing it, so the data that would train is the data that is measured.
* **balance** — every slot must clear a floor, and clear it in both answers.
  Imbalance is not a smaller number, it is a different model.
* **time** — days since the last recorded run. A ceiling on impatience only.
* **provenance** — git sha of the tree that produced the numbers, so a label in
  the history means "this code, this data", not "some model somewhere".

`--run` executes the command it printed and appends a `training_history.json`
record. Records carry the split hash, the counts, and the resulting version label,
which is the key `subproto models --use <label>` looks a version up by — the
versioning ladder (S33) and the cadence policy meet at that file.
"""

import datetime
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time

from . import style

MIN_PER_SLOT = 40       # rows per slot
MIN_ANSWERS_PER_SLOT = 8   # per slot, in *both* answers — the tighter constraint
MIN_DAYS = 7
HISTORY_NAME = "training_history.json"
TRAINER = os.path.join("train", "finetune_mlx.py")
BASE_MODEL = os.path.expanduser("~/.subproto/laya-base")
BASE_ADAPTER = os.path.expanduser("~/.subproto/laya-router-lora")
# What a refused run writes instead of a version. `--use` must not be able to
# activate this, and the report must not be able to group a real checkpoint under
# it — a plan is not evidence that something trained.
PLANNED = "planned"


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def history_path(config):
    return os.path.join(config.data_dir, HISTORY_NAME)


def load_history(config, warn=None):
    """The recorded runs, oldest first.

    A missing file is an empty history. A file that exists and does not parse is
    *not* empty — it is unreadable — so this returns None and `append_history`
    refuses to overwrite an append-only log it cannot read. `warn` collects the
    reason for whoever prints it.
    """
    target = history_path(config)
    try:
        with open(target) as f:
            rows = json.load(f)
    except (IOError, OSError):
        return []
    except ValueError as exc:
        if warn is not None:
            warn.append("%s is not readable JSON (%s); it is left alone and no run "
                        "is recorded" % (os.path.basename(target), str(exc)[:90]))
        return None
    return rows if isinstance(rows, list) else []


def append_history(config, record):
    """Add one record. The file is an append-only log, so the only write that is
    allowed is the whole log with one more row — atomically."""
    warn = []
    rows = load_history(config, warn)
    if rows is None:
        raise ValueError(warn[0] if warn else "the history file is unreadable")
    rows.append(record)
    _write_json(history_path(config), rows)
    return rows


def _write_json(target, payload):
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, target)


def load_trainer():
    """The training script as a module, so its format contract is *this* file's
    contract too. Returns (module, path) or (None, path).

    Deliberately not an import at module scope: `subproto` must run with the
    trainer absent, and it must still say so precisely rather than crash.
    """
    path = os.path.join(repo_root(), TRAINER)
    if not os.path.isfile(path):
        return None, path
    import importlib.util
    spec = importlib.util.spec_from_file_location("subproto_finetune", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, path


def git_sha():
    """The commit that produced these numbers, or None off a git tree."""
    try:
        out = subprocess.run(["git", "-C", repo_root(), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def split_rows(config, **kw):
    """(train, val, trainer_module, trainer_path).

    The split is rebuilt here rather than read off disk: a stale
    `training/train.jsonl` would certify a retrain from data that no longer
    matches the traffic. It is built with `write=False` — deciding whether to
    train must not overwrite the split the next decision reads. The trainer's own
    `load_split` then validates every row, so the format gate here is the one
    training will actually enforce.
    """
    from subproto import dataset
    from subproto.telemetry import Telemetry

    config.ensure_dirs()
    tel = Telemetry(config.db_path)
    try:
        sp = dataset.build_training_split(config, tel, write=False, **kw)
    finally:
        tel.close()
    trainer, trainer_path = load_trainer()
    if trainer is None:
        return sp["train"], sp["val"], None, trainer_path
    for name, rows in (("train", sp["train"]), ("val", sp["val"])):
        tmp = os.path.join(config.data_dir, ".check-%s.jsonl" % name)
        with open(tmp, "w") as f:
            for ex in rows:
                f.write(json.dumps(ex, separators=(",", ":")) + "\n")
        try:
            if trainer.load_split(tmp) is None:
                raise ValueError("the trainer read no rows from %s" % name)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return sp["train"], sp["val"], trainer, trainer_path


def fingerprint(train, val):
    """sha256 over the rows a run would train on.

    The train/val boundary is committed as well as the rows: `train=A, val=B` and
    `train=B, val=A` hold the same text but are different runs, and without the
    separator the fingerprint could not tell them apart. Order matters too — the
    split is shuffled from a seeded RNG, so a changed seed is a changed run.
    """
    h = hashlib.sha256()
    for marker, rows in ((b"#train\n", train), (b"#val\n", val)):
        h.update(marker)
        for ex in rows:
            h.update(json.dumps(ex, sort_keys=True, separators=(",", ":")).encode())
            h.update(b"\n")
    return h.hexdigest()


def per_slot(train, val):
    """{slot: {"keep": n, "drop": n, "total": n}} over the rows that train."""
    out = {}
    for rows in (train, val):
        for ex in rows:
            slot = ex.get("slot") or "?"
            row = out.setdefault(slot, {"keep": 0, "drop": 0, "total": 0})
            ans = ex.get("answer")
            if ans in ("keep", "drop"):
                row[ans] += 1
            row["total"] += 1
    return out


def _days_between(iso_a, iso_b):
    a = datetime.date.fromisoformat(iso_a[:10])
    b = datetime.date.fromisoformat(iso_b[:10])
    return (b - a).days


def last_run(history):
    """The newest recorded run that *finished*, or None.

    Only a clean exit counts. A `refused` row records a decision not to train, and
    a `planned` row records a dry run; neither produced weights, so neither should
    start the cooldown clock. On this machine today the trainer stops at T16 with a
    non-zero exit, so the honest answer is "no completed run on record".
    """
    done = [r for r in history
            if r.get("status") == "ran" and r.get("exit") == 0]
    if not done:
        return None
    return sorted(done, key=lambda r: str(r.get("at") or ""))[-1]


def write_split(config, split_kw=None):
    """Materialise the split `readiness` measured, and return the dataset summary.

    `--run` calls this before it runs anything. The command it prints names
    `training/train.jsonl`, so a run that left that file to whatever an earlier
    `subproto split` wrote would be training on data this decision never saw —
    and the `split_sha` in the record would describe a different corpus than the
    one on disk.
    """
    from subproto import dataset
    from subproto.telemetry import Telemetry

    config.ensure_dirs()
    tel = Telemetry(config.db_path)
    try:
        return dataset.build_training_split(config, tel, write=True,
                                            **(split_kw or {}))
    finally:
        tel.close()


def fingerprint_rows(paths):
    """sha256 over rows already on disk, in the same order as `fingerprint`.

    This is the check that the split the page counted is the split the trainer
    will read: the two are built by different code paths, and only this comparison
    ties them to the same bytes.
    """
    train = _read_jsonl(paths[0]) if len(paths) > 0 else []
    val = _read_jsonl(paths[1]) if len(paths) > 1 else []
    return fingerprint(train, val)


def _read_jsonl(path):
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except (IOError, OSError):
        return []
    return rows


def toolchain(trainer, trainer_path, model_dir=BASE_MODEL):
    """What the machine can do right now, named the way the trainer names it."""
    out = {"trainer": os.path.exists(trainer_path), "mlx": False, "model_dir": model_dir,
           "model_present": os.path.isdir(model_dir)}
    if trainer is not None:
        out["mlx"] = bool(trainer.mlx_available())
    return out


def readiness(config, min_per_slot=MIN_PER_SLOT, min_answers=MIN_ANSWERS_PER_SLOT,
              min_days=MIN_DAYS, today=None, train=None, val=None, history=None,
              trainer=None, trainer_path=None, split_kw=None):
    """The whole decision, as data. Runs nothing and writes nothing.

    `split_kw` forwards `--val-frac` / `--seed` to the split builder, so the rows
    measured here are the rows that run would have written. The defaults are not
    restated: they stay `dataset.build_training_split`'s, or a change there would
    silently make this page describe a different split.
    """
    notes = []
    if train is None or val is None:
        train, val, trainer, trainer_path = split_rows(config, **(split_kw or {}))
    elif trainer is None and trainer_path is None:
        trainer, trainer_path = load_trainer()
    if history is None:
        history = load_history(config, notes)
        unreadable = history is None
        history = [] if unreadable else history
    else:
        unreadable = False
    today = today or time.strftime("%Y-%m-%d")
    sha = fingerprint(train, val)
    slots = per_slot(train, val)

    short_total = {s: min_per_slot - m["total"] for s, m in sorted(slots.items())
                   if m["total"] < min_per_slot}
    short_answers = {s: {a: min_answers - m[a] for a in ("keep", "drop")
                         if m[a] < min_answers}
                     for s, m in sorted(slots.items())}
    short_answers = {s: v for s, v in short_answers.items() if v}
    unbalanced = [s for s, m in sorted(slots.items()) if m["keep"] == 0 or m["drop"] == 0]

    prev = last_run(history)
    reasons = []
    if unreadable:
        # the failure's own text stays in `notes`, which renders above this; saying
        # it twice on one page would only make the reader compare the two copies.
        reasons.append("the cadence cannot be evaluated because the history is "
                       "unreadable (see the note above)")
    if not slots:
        reasons.append("no examples: run `subproto export` and `subproto split` first")
    if short_total:
        reasons.append("under %d examples per slot: %s" % (
            min_per_slot, ", ".join("%s short %d" % (s, n)
                                    for s, n in sorted(short_total.items()))))
    if short_answers:
        reasons.append("under %d in both answers: %s" % (
            min_answers, "; ".join(
                "%s %s" % (s, ", ".join("%s short %d" % (a, n)
                                        for a, n in sorted(v.items())))
                for s, v in sorted(short_answers.items()))))
    if unbalanced:
        reasons.append("a slot with only one answer teaches the answer, not the "
                       "decision: %s" % ", ".join(unbalanced))

    if prev is None:
        days = None
        if not unreadable:
            notes.append("no completed run on record, so the %d-day wait cannot apply"
                         % min_days)
    else:
        days = _days_between(str(prev.get("at")), today)
        if prev.get("split_sha") == sha:
            reasons.append("the split has not changed since the run recorded on %s "
                           "(%s): retraining now would re-derive the same weights"
                           % (prev.get("at"), str(prev.get("version"))))
        elif days < min_days:
            reasons.append("only %d of %d days since the run recorded on %s"
                           % (days, min_days, prev.get("at")))

    return {
        "at": today,
        "split_sha": sha,
        "n_train": len(train), "n_val": len(val),
        "slots": slots, "short_total": short_total,
        "short_answers": short_answers, "unbalanced": unbalanced,
        "last_run": None if prev is None else {
            "at": prev.get("at"), "version": prev.get("version"),
            "split_sha": prev.get("split_sha"), "status": prev.get("status"),
            "days_ago": days},
        "thresholds": {"min_per_slot": min_per_slot, "min_answers": min_answers,
                       "min_days": min_days},
        "toolchain": toolchain(trainer, trainer_path or ""),
        "git_sha": git_sha(),
        "reasons": reasons, "notes": notes,
        "ready": not reasons,
    }


def command(config, train_path=None, val_path=None, model=BASE_MODEL,
            out_dir=BASE_ADAPTER, epochs=3, rank=8, python=None):
    """The exact argv, as one shell string, for the split `readiness` measured.

    Every path is quoted because this project's own directory has a space in it:
    an unquoted command is a paste-able string that breaks on the machine it was
    printed from, and `run_command` would feed `shlex.split` a filename cut in
    half. The one thing this line may not be is an approximation of the run.
    """
    d = os.path.join(config.data_dir, "training")
    argv = [python or sys.executable, os.path.join(repo_root(), TRAINER),
            "--train", train_path or os.path.join(d, "train.jsonl"),
            "--val", val_path or os.path.join(d, "val.jsonl"),
            "--model", model, "--out", out_dir,
            "--epochs", str(epochs), "--rank", str(rank)]
    return " ".join(shlex.quote(a) for a in argv)


def version_for(history):
    """`lora-v<N>` — one past the highest version ever *attempted*.

    Counting refused and planned attempts keeps the numbering honest: it makes
    "v3 was tried" visible in the label rather than reusing v3 for a different
    split, which would let two checkpoints share a name.
    """
    used = []
    for row in history:
        label = str(row.get("version") or "")
        if label.startswith("lora-v"):
            try:
                used.append(int(label[6:]))
            except ValueError:
                pass
    return "lora-v%d" % (max(used) + 1 if used else 1)


def record(state, status, command_line=None, version=None, error=None,
           exit_code=None):
    """One history row. Written for a run, a refusal and a dry run alike — a
    decision *not* to train is the thing an operator most needs to audit later."""
    return {
        "version": version or PLANNED,
        "at": state["at"],
        "status": status,
        "exit": exit_code,
        "split_sha": state["split_sha"],
        "n_train": state["n_train"], "n_val": state["n_val"],
        "per_slot": {s: m["total"] for s, m in sorted(state["slots"].items())},
        "thresholds": dict(state["thresholds"]),
        "git_sha": state.get("git_sha"),
        "toolchain": {"mlx": state["toolchain"]["mlx"],
                      "trainer": state["toolchain"]["trainer"],
                      "model_present": state["toolchain"]["model_present"]},
        "command": command_line,
        "reasons": list(state["reasons"]),
        "notes": list(state["notes"]),
        "error": error,
    }


def run_command(command_line, chatter_to_stderr=False):
    """Hand the terminal to the trainer. Returns its exit code, or 127 with the
    reason if it cannot start at all.

    `chatter_to_stderr` is for `--json`: the trainer prints its own progress line on
    stdout, and gluing it to the front of the document makes the "machine readable"
    surface readable by no machine.
    """
    import shlex
    try:
        return subprocess.call(shlex.split(command_line),
                               stdout=sys.stderr if chatter_to_stderr else None)
    except (OSError, ValueError) as exc:
        sys.stderr.write("could not start the trainer: %s: %s\n"
                         % (type(exc).__name__, str(exc)[:200]))
        return 127


def render_text(state, command_line, ran=None, color=False):
    """Everything the decision was made from, then the decision. The numbers a
    `ready` claim rests on stay on the page beside it.

    One label column (style.METRIC_W) for the whole page, so `split`, `examples`,
    `per slot` and `decision` all hang at the same content column.
    """
    st = style
    t = state["thresholds"]
    lines = [st.header("retrain", "cadence policy", state["at"], color=color), ""]
    lines.append(st.row("split", "%s…" % state["split_sha"][:12], color=color))
    lines.append(st.row("examples", "%d train / %d val" % (
        state["n_train"], state["n_val"]), color=color))
    lines.append(st.note("git %s" % (state.get("git_sha") or "none"), color=color))
    if state["last_run"]:
        lr = state["last_run"]
        lines.append(st.row("last run", "%s  %s" % (lr.get("at"), lr.get("version")),
                            color=color))
        lines.append(st.note("%s days ago, split %s… — %s" % (
            lr.get("days_ago"), str(lr.get("split_sha") or "")[:8],
            lr.get("status")), color=color))
    else:
        lines.append(st.row("last run", "none recorded", color=color))
    lines.append("")
    lines.append(st.section("per slot", "(floor %d rows, %d in both answers)" % (
        t["min_per_slot"], t["min_answers"]), indent=style.INDENT, color=color))
    for slot, m in sorted(state["slots"].items()):
        flags = []
        if m["total"] < t["min_per_slot"]:
            flags.append("%d short" % (t["min_per_slot"] - m["total"]))
        for a in ("keep", "drop"):
            if m[a] < t["min_answers"]:
                flags.append("only %d %s" % (m[a], a))
        # The verdict leads: this table is read as "which slot is holding the
        # decision back", and the counts are the evidence beside it. The specific
        # deficit is spelled out in the `decision` block below.
        verdict = st.state(st.OK, color=color) if not flags else \
            st.state(st.SHORT, color=color)
        lines.append(st.table(
            slot, (verdict, "", -6), (m["total"], "rows", 6), (m["keep"], "keep", 5),
            (m["drop"], "drop", 5), name_w=12, color=color))
    if not state["slots"]:
        lines.append(st.note("nothing to count", color=color))
    tc = state["toolchain"]
    lines.append("")
    lines.append(st.section("toolchain", indent=style.INDENT, color=color))
    lines.append(st.row("trainer", st.state(st.PRESENT if tc["trainer"]
                                            else st.MISSING, color=color),
                        color=color, styled=True))
    lines.append(st.row("mlx + mlx-lm", st.state(st.INSTALLED if tc["mlx"]
                                                 else st.MISSING, color=color),
                        color=color, styled=True))
    lines.append(st.row("base", "%s  %s%s" % (
        tc["model_dir"], st.state(st.PRESENT if tc["model_present"] else st.MISSING,
                                  color=color),
        "" if tc["model_present"] else st.unit("(T16)", color=color)),
        color=color, styled=True))
    for note in state["notes"]:
        lines.append(st.row("note", st.paint(note, st.DIM, color),
                            color=color, styled=True))
    lines.append("")
    if state["ready"]:
        lines.append(st.row("decision", st.state(st.READY, color=color),
                            color=color, styled=True))
    else:
        lines.append(st.row("decision", "%s — %d reason%s" % (
            st.state(st.NOT_READY, color=color), len(state["reasons"]),
            "" if len(state["reasons"]) == 1 else "s"), color=color, styled=True))
        for r in state["reasons"]:
            lines.append(st.reason(r, indent=style.CONTENT, color=color))
    lines.append("")
    lines.append(st.section("the command this would run", indent=style.INDENT,
                            color=color))
    lines.append(st.action(command_line, indent=style.SUB_INDENT, color=color))
    if ran is not None:
        lines.append("")
        lines.append(st.row("ran it", "exit %s" % ran, color=color))
    else:
        lines.append("")
        lines.append(st.prose("nothing was run. --run starts the trainer once ready.",
                              indent=style.INDENT, color=color))
    return "\n".join(lines)

