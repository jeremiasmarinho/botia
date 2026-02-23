"""Sanity Guard para blindagem de OCR durante animações e ruído visual."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class OCRSample:
    pot: float
    stack: float
    call: float


class SanityGuard:
    """Valida estabilidade temporal e regras de negócio dos valores OCR."""

    def __init__(
        self,
        history_size: int = 5,
        stable_frames: int = 3,
        repeat_decimals: int = 2,
        pot_drop_tolerance: float = 10.0,
        new_hand_pot_threshold: float = 10.0,
    ) -> None:
        self.history_size = max(3, int(history_size))
        self.stable_frames = max(2, int(stable_frames))
        self.repeat_decimals = max(0, int(repeat_decimals))
        self.pot_drop_tolerance = max(0.0, float(pot_drop_tolerance))
        self.new_hand_pot_threshold = max(0.0, float(new_hand_pot_threshold))
        self._samples: deque[OCRSample] = deque(maxlen=self.history_size)
        self._last_valid_pot: float = 0.0
        self.last_reason: str = "boot"

    def reset(self) -> None:
        self._samples.clear()
        self._last_valid_pot = 0.0
        self.last_reason = "reset"

    def _normalized(self, value: float) -> float:
        safe = max(0.0, float(value))
        return round(safe, self.repeat_decimals)

    def _is_tail_stable(self) -> bool:
        if len(self._samples) < self.stable_frames:
            return False
        tail = list(self._samples)[-self.stable_frames:]
        first = tail[0]
        return all(
            item.pot == first.pot and item.stack == first.stack and item.call == first.call
            for item in tail
        )

    def validate(self, pot: float, stack: float, call: float) -> bool:
        pot_value = self._normalized(pot)
        stack_value = self._normalized(stack)
        call_value = self._normalized(call)

        if call_value > (stack_value + 0.01):
            self.last_reason = "call_gt_stack"
            return False

        if (
            self._last_valid_pot > 0
            and pot_value + self.pot_drop_tolerance < self._last_valid_pot
        ):
            if pot_value <= self.new_hand_pot_threshold and call_value <= 0.01:
                self._samples.clear()
                self._last_valid_pot = 0.0
            else:
                self.last_reason = "pot_decreased"
                return False

        self._samples.append(OCRSample(pot=pot_value, stack=stack_value, call=call_value))

        if not self._is_tail_stable():
            self.last_reason = "unstable_tail"
            return False

        self._last_valid_pot = max(self._last_valid_pot, pot_value)
        self.last_reason = "ok"
        return True
