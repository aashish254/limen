"""Laya backend adapter.

Laya answers narrow questions (which option / keep-or-drop / how scoreable)
rather than generating prose, so each slot asks it a schema-shaped question and
gets a probability vector back. When no Laya server is reachable the engine falls
back to the heuristics and records that it did — the dataset then becomes the
supervision signal for the fine-tune that closes the gap.

The HTTP protocol itself lives in :mod:`subproto.systemone.base`; this is the
Laya-named entry point kept for back-compat, since the registry can bind any
``/health`` + ``/score`` server to any label.
"""

from .systemone.base import HTTPScoreAdapter


class LayaClient(HTTPScoreAdapter):
    def __init__(self, base_url, timeout=0.35):
        HTTPScoreAdapter.__init__(self, base_url, label="laya", timeout=timeout)
