"""Lightweight tracing for model, tool, and validation calls (E03).

Spans are collected on a context-local Tracer (so one pipeline run captures its whole
trajectory) and also emitted as structured `TRACE {...}` log lines, which become
CloudWatch/X-Ray-queryable in the Lambdas. Tool arguments here are codes/substances,
never PHI, so they are safe to record.
"""
from __future__ import annotations

import contextvars
import functools
import json
import time

_current: contextvars.ContextVar = contextvars.ContextVar("tracer", default=None)


class Tracer:
    def __init__(self) -> None:
        self.spans: list[dict] = []

    def add(self, span: dict) -> None:
        self.spans.append(span)

    def __enter__(self) -> "Tracer":
        self._tok = _current.set(self)
        return self

    def __exit__(self, *exc) -> None:
        _current.reset(self._tok)


def _emit(span: dict) -> None:
    try:
        print("TRACE " + json.dumps(span, default=str))
    except Exception:
        pass


def record(kind: str, name: str, **fields) -> None:
    span = {"kind": kind, "name": name, **fields}
    t = _current.get()
    if t is not None:
        t.add(span)
    _emit(span)


def trace(name: str, kind: str = "tool"):
    """Decorator: time a call, capture ok/err and (for list results) row count."""
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*args, **kwargs):
            start = time.perf_counter()
            ok, err, n = True, None, None
            try:
                res = fn(*args, **kwargs)
                if isinstance(res, list):
                    n = len(res)
                return res
            except Exception as e:  # noqa: BLE001
                ok, err = False, type(e).__name__
                raise
            finally:
                record(kind, name, ms=round((time.perf_counter() - start) * 1000, 1),
                       ok=ok, err=err, rows=n)
        return wrap
    return deco
