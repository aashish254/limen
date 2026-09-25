#!/usr/bin/env python3
"""LoRA fine-tune of a Laya base on subproto's routing split (M4 / SPEC §1.2).

This is the training half of the dataset moat: it consumes the JSONL produced by
`subproto split` (train.jsonl / val.jsonl, one narrow keep/drop question per
line) and adapts a small decision model with LoRA on Apple MLX.

It is intentionally honest about its dependencies. The split loader and the
format check run anywhere (and are unit-tested without any heavy install), but
training needs Apple MLX *and* a quantized Laya checkpoint, neither of which is
vendored here — that is the external step tracked as TODO T16. Run without them
and it tells you exactly what is missing instead of silently no-oping.

Usage (when the deps are present):
    pip install mlx mlx-lm
    # place a Laya-compatible model dir at ~/.subproto/laya-base (or --model)
    python train/finetune_mlx.py --train ~/.subproto/training/train.jsonl \\
        --val ~/.subproto/training/val.jsonl --out ~/.subproto/laya-router-lora
"""

import argparse
import importlib.util
import json
import os
import sys

REQUIRED_KEYS = ("state", "question", "options", "answer")


def load_split(path, limit=None):
    """Parse a subproto supervision JSONL split into a list of examples.

    Pure + dependency-free so the data contract is testable without MLX.
    """
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ex = json.loads(line)
            missing = [k for k in REQUIRED_KEYS if k not in ex]
            if missing:
                raise ValueError("line %d missing keys %s: %r"
                                 % (len(out) + 1, missing, ex))
            if not isinstance(ex["options"], list) or not ex["options"]:
                raise ValueError("options must be a non-empty list: %r" % ex)
            if ex["answer"] not in ("keep", "drop"):
                raise ValueError("answer must be keep/drop: %r" % ex["answer"])
            out.append(ex)
            if limit and len(out) >= limit:
                break
    return out


def render_prompt(example):
    """The text the base model is prompted with — one option per call, matching
    the shape laya.py uses at serve time (train/serve parity)."""
    return "context: %s\nquestion: keep %s?\n" % (
        example["state"].strip(), example["options"][0])


def mlx_available():
    return importlib.util.find_spec("mlx") is not None and \
        importlib.util.find_spec("mlx_lm") is not None


def train(args):  # pragma: no cover - requires MLX + a real checkpoint
    if not mlx_available():
        sys.stderr.write(
            "mlx / mlx-lm not installed. `pip install mlx mlx-lm` on Apple "
            "silicon, and point --model at a Laya-compatible checkpoint.\n")
        return 3
    if not os.path.isdir(args.model):
        sys.stderr.write("no model dir at %s (T16: fetch the quantized Laya base)\n"
                         % args.model)
        return 3
    # Guarded import: only reached when the heavy deps are present.
    import mlx.core as mx  # noqa: F401
    from mlx_lm import lora, load, generate  # noqa: F401

    train_rows = load_split(args.train)
    val_rows = load_split(args.val) if args.val else []
    sys.stderr.write("fine-tuning LoRA on %d train / %d val examples\n"
                     % (len(train_rows), len(val_rows)))
    # The adapter is a supervised keep/drop objective over render_prompt(state).
    # Concrete lora.train wiring depends on the checkpoint's chat template and is
    # finalised at T16 once real weights exist; until then we validate the data
    # contract and exit rather than fabricate a training run we cannot honour.
    sys.stderr.write(
        "data contract OK; MLX LoRA step requires the published checkpoint (T16).\n")
    return 4


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", required=True)
    ap.add_argument("--val")
    ap.add_argument("--model", default=os.path.expanduser("~/.subproto/laya-base"))
    ap.add_argument("--out", default=os.path.expanduser("~/.subproto/laya-router-lora"))
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--rank", type=int, default=8)
    ap.add_argument("--check-only", action="store_true",
                    help="validate the split format and exit (no training)")
    args = ap.parse_args(argv)

    n = len(load_split(args.train))
    if args.val:
        v = len(load_split(args.val))
        print("split ok: %d train, %d val" % (n, v))
    else:
        print("split ok: %d train" % n)
    if args.check_only:
        return 0
    return train(args)


if __name__ == "__main__":
    sys.exit(main())
