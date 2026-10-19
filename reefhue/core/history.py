"""Per-file undo/redo of the edit state, in memory only (SPEC §12). No Qt.

Drag events are coalesced: a change with the same ``merge_key`` replaces the
top entry while that entry is still *open*; ``close()`` ends the step (mouse
release, key release, Enter).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

MAX_STEPS = 200


@dataclass
class _Step:
    state: dict
    merge_key: str | None = None
    open: bool = False


class EditHistory:
    def __init__(self, initial: dict) -> None:
        self._steps: list[_Step] = [_Step(copy.deepcopy(initial))]
        self._index = 0

    @property
    def current(self) -> dict:
        return copy.deepcopy(self._steps[self._index].state)

    def record(self, state: dict, merge_key: str | None = None) -> None:
        top = self._steps[self._index]
        if merge_key is not None and top.open and top.merge_key == merge_key and self._index > 0:
            top.state = copy.deepcopy(state)
            return
        top.open = False
        del self._steps[self._index + 1 :]
        self._steps.append(_Step(copy.deepcopy(state), merge_key, open=merge_key is not None))
        if len(self._steps) > MAX_STEPS:
            self._steps.pop(0)
        self._index = len(self._steps) - 1

    def close(self) -> None:
        self._steps[self._index].open = False

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._steps) - 1

    def undo(self) -> dict | None:
        if not self.can_undo:
            return None
        self.close()
        self._index -= 1
        return self.current

    def redo(self) -> dict | None:
        if not self.can_redo:
            return None
        self.close()
        self._index += 1
        return self.current

    def __len__(self) -> int:
        return len(self._steps)
