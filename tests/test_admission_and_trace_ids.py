"""Run admission serialization and monotonic trace ids (no models)."""
import threading
import time
from unittest.mock import patch

from app import chat_run_state


def test_root_turn_admission_is_serialized():
    active = {"now": 0, "peak": 0}
    guard = threading.Lock()

    def slow_turn(*_a, **_k):
        with guard:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(0.05)
        with guard:
            active["now"] -= 1
        return {}

    with patch.object(chat_run_state, "_start_turn", slow_turn):
        threads = [threading.Thread(target=chat_run_state.start_turn, args=(1, 1, "x", None, None)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    # Count-then-insert must not interleave, or two turns pass a full cap.
    assert active["peak"] == 1
