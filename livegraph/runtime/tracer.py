"""Capture real call edges via sys.monitoring (PEP 669)."""
from __future__ import annotations

import sys
import types
from collections import Counter

from livegraph.models import RuntimeCall
from livegraph.runtime.observations import qid_from_code


class CallTracer:
    """Records caller->callee edges during a traced run.

    Uses a dedicated ``sys.monitoring`` tool id and listens for ``CALL``
    events. Each edge is attributed to the test currently set via
    ``set_current_test``. Only calls where both sides resolve to a
    project qualified_name are recorded.
    """

    def __init__(self, root: str, tool_id: int = 3) -> None:
        self._root = root
        self._tool_id = tool_id
        self._current_test: str | None = None
        # (caller_qn, callee_qn, test_qn) -> observed count
        self._counts: Counter[tuple[str, str, str]] = Counter()

    def set_current_test(self, test_qn: str | None) -> None:
        """Attribute subsequently observed calls to ``test_qn``."""
        self._current_test = test_qn

    def start(self) -> None:
        """Register the monitoring tool and begin listening for calls."""
        mon = sys.monitoring
        mon.use_tool_id(self._tool_id, "livegraph")
        mon.register_callback(self._tool_id, mon.events.CALL, self._on_call)
        mon.set_events(self._tool_id, mon.events.CALL)

    def stop(self) -> None:
        """Stop listening and release the monitoring tool id."""
        mon = sys.monitoring
        mon.set_events(self._tool_id, 0)
        mon.register_callback(self._tool_id, mon.events.CALL, None)
        mon.free_tool_id(self._tool_id)

    def runtime_calls(self) -> list[RuntimeCall]:
        """Return the distinct observed call edges with counts."""
        out: list[RuntimeCall] = []
        for (caller, callee, test_qn), _count in self._counts.items():
            out.append(RuntimeCall(caller_qn=caller, callee_qn=callee,
                                   test_qn=test_qn, call_site_line=0))
        return out

    def counts(self) -> dict[tuple[str, str, str], int]:
        """Expose raw observation counts (used by the merge step)."""
        return dict(self._counts)

    # -- monitoring callback ---------------------------------------------

    def _on_call(
        self, code: types.CodeType, instruction_offset: int,
        callable_obj: object, arg0: object,
    ) -> object:
        """sys.monitoring CALL callback: code is the caller frame."""
        if self._current_test is None:
            return None
        callee_code = _code_of(callable_obj)
        if callee_code is None:
            return None
        caller_qn = qid_from_code(code, self._root)
        callee_qn = qid_from_code(callee_code, self._root)
        if caller_qn is None or callee_qn is None:
            return None
        self._counts[(caller_qn, callee_qn, self._current_test)] += 1
        return None


def _code_of(obj: object) -> types.CodeType | None:
    """Best-effort extraction of a code object from a callable."""
    code = getattr(obj, "__code__", None)
    if isinstance(code, types.CodeType):
        return code
    func = getattr(obj, "__func__", None)
    if func is not None:
        inner = getattr(func, "__code__", None)
        if isinstance(inner, types.CodeType):
            return inner
    return None
