"""
learning/rl_agent.py — Q-Learning Reinforcement Agent
======================================================
Lightweight Q-learning agent that adapts signal dispatch decisions
based on real-time outcomes. No deep learning — pure Python Q-table.

State: discrete tuple of market features
Actions: TAKE_SIGNAL | SKIP_SIGNAL | TIGHTEN_SL | WIDEN_SL | REDUCE_TP1
Reward: based on outcome (TP1/TP2/SL/EXPIRED)
"""
from __future__ import annotations

import json
import random
from typing import Optional

from core.logger import get_logger

log = get_logger(__name__)

# ── Hyperparameters ────────────────────────────────────────────────────────────
ALPHA   = 0.1    # learning rate
GAMMA   = 0.9    # discount factor
EPSILON = 0.15   # initial exploration rate
EPSILON_MIN = 0.02
EPSILON_DECAY = 0.9995

# ── Action space ───────────────────────────────────────────────────────────────
ACTIONS = [
    "TAKE_SIGNAL",
    "SKIP_SIGNAL",
    "TIGHTEN_SL",
    "WIDEN_SL",
    "REDUCE_TP1",
]

# ── State feature discrete mappings ───────────────────────────────────────────
_REGIME_MAP = {
    "LOW_VOL_RANGE":   0,
    "HIGH_VOL_BULL":   1,
    "HIGH_VOL_BEAR":   2,
    "TRENDING_BULL":   1,
    "TRENDING_BEAR":   2,
}
_STRUCT_MAP = {"slope": 0, "partial": 1, "strong": 2}
_POI_MAP    = {"none": 0, "FVG": 1, "OB": 2}
_NARR_MAP   = {"reversal": 0, "unclear": 1, "continuation": 2, "pullback": 3}
_SESS_MAP   = {"Asian": 0, "London": 1, "NewYork": 2}
_PAIR_MAP   = {"crypto": 0, "forex": 1}
_VOL_MAP    = {"low": 0, "medium": 1, "high": 2}


class RLAgent:
    """
    Q-Learning agent for live signal dispatch decisions.

    Persists Q-table to MemoryStore (data/learning.json).
    Thread-safe action selection; Q-table updates happen in the
    outcome callback thread.
    """

    def __init__(self, memory) -> None:
        self._memory = memory
        self._epsilon = EPSILON
        self._q: dict[str, dict[str, float]] = {}
        self._load_q_table()
        self._step_count = 0

    # ──────────────────────────────────────────────────────────────────────────
    # Public
    # ──────────────────────────────────────────────────────────────────────────

    def choose_action(self, state: tuple) -> str:
        """
        Epsilon-greedy action selection.
        Returns one of the ACTIONS strings.
        """
        try:
            # Exploration
            if random.random() < self._epsilon:
                return random.choice(ACTIONS)

            # Exploitation
            state_key = _state_key(state)
            q_row = self._q.get(state_key, {})
            if not q_row:
                return "TAKE_SIGNAL"  # default when no data

            return max(q_row, key=q_row.get)
        except Exception as exc:
            log.debug("[RLAgent] choose_action error: %s", exc)
            return "TAKE_SIGNAL"

    def apply_action(self, action: str, signal) -> object:
        """
        Modify signal based on chosen action.
        Returns the (possibly modified) signal object.
        """
        try:
            if action == "TIGHTEN_SL":
                risk = abs(signal.entry - signal.stop_loss)
                if signal.direction == "BUY":
                    signal.stop_loss = signal.entry - risk * 0.8
                else:
                    signal.stop_loss = signal.entry + risk * 0.8

            elif action == "WIDEN_SL":
                risk = abs(signal.entry - signal.stop_loss)
                if signal.direction == "BUY":
                    signal.stop_loss = signal.entry - risk * 1.2
                else:
                    signal.stop_loss = signal.entry + risk * 1.2

            elif action == "REDUCE_TP1":
                risk = abs(signal.entry - signal.stop_loss)
                if signal.direction == "BUY":
                    signal.tp1 = signal.entry + risk * 1.5
                else:
                    signal.tp1 = signal.entry - risk * 1.5
        except Exception as exc:
            log.debug("[RLAgent] apply_action error: %s", exc)

        return signal

    def update(
        self,
        state: tuple,
        action: str,
        reward_val: float,
        next_state: tuple,
    ) -> None:
        """
        Q-learning update rule:
        Q(s,a) ← Q(s,a) + α [r + γ max_a' Q(s',a') - Q(s,a)]
        """
        try:
            sk  = _state_key(state)
            nsk = _state_key(next_state)

            if sk not in self._q:
                self._q[sk] = {a: 0.0 for a in ACTIONS}
            if nsk not in self._q:
                self._q[nsk] = {a: 0.0 for a in ACTIONS}

            current_q  = self._q[sk].get(action, 0.0)
            max_next_q = max(self._q[nsk].values())

            new_q = current_q + ALPHA * (reward_val + GAMMA * max_next_q - current_q)
            self._q[sk][action] = new_q

            # Decay epsilon
            self._step_count += 1
            self._epsilon = max(EPSILON_MIN, self._epsilon * EPSILON_DECAY)

            # Persist periodically
            if self._step_count % 20 == 0:
                self._save_q_table()

        except Exception as exc:
            log.debug("[RLAgent] update error: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────────────────────────────────

    def _load_q_table(self) -> None:
        """Load Q-table from MemoryStore."""
        try:
            raw = self._memory.get_q_table()
            if raw:
                self._q = raw
                log.info("[RLAgent] Loaded Q-table with %d states", len(self._q))
        except Exception as exc:
            log.debug("[RLAgent] Q-table load error: %s", exc)

    def _save_q_table(self) -> None:
        """Persist Q-table to MemoryStore."""
        try:
            self._memory.set_q_table(self._q)
            self._memory.save()
        except Exception as exc:
            log.debug("[RLAgent] Q-table save error: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Q-table summary for dashboard
    # ──────────────────────────────────────────────────────────────────────────

    def get_top_state_actions(self, n: int = 5) -> list[dict]:
        """
        Return the top N state→best_action mappings sorted by max Q-value.
        For dashboard display.
        """
        try:
            items = []
            for state_key, actions in self._q.items():
                if not actions:
                    continue
                best_action = max(actions, key=actions.get)
                best_q      = actions[best_action]
                items.append({
                    "state": state_key,
                    "best_action": best_action,
                    "q_value": round(best_q, 4),
                })
            items.sort(key=lambda x: x["q_value"], reverse=True)
            return items[:n]
        except Exception:
            return []


# ── Helpers ────────────────────────────────────────────────────────────────────

def state_from_signal(signal, gate_audit: dict, regime: str) -> tuple:
    """
    Convert a signal + gate audit + regime into a discrete state tuple.
    """
    try:
        regime_d   = _REGIME_MAP.get(regime, 0)
        struct_d   = _STRUCT_MAP.get(gate_audit.get("G2_quality", "slope"), 0)
        poi_d      = _POI_MAP.get(gate_audit.get("G11_poi_type") or "none", 0)
        narr_d     = _NARR_MAP.get(gate_audit.get("G7_narrative", "unclear"), 1)
        sess_d     = _SESS_MAP.get(gate_audit.get("G1_session", "Asian"), 0)
        pair_d     = _PAIR_MAP.get(signal.market_type, 0)

        # Volatility bucket from gate audit
        vol_ok  = gate_audit.get("G4_volatility", False)
        vol_d   = 2 if vol_ok else 0

        return (regime_d, struct_d, poi_d, narr_d, sess_d, pair_d, vol_d)
    except Exception:
        return (0, 0, 0, 1, 0, 0, 1)


def reward(action_taken: str, outcome: str, rr_achieved: float) -> float:
    """Compute reward from outcome."""
    if outcome == "TP1_HIT":
        return +rr_achieved
    if outcome == "TP2_HIT":
        return +rr_achieved * 1.5
    if outcome == "SL_HIT":
        return -1.0
    if outcome == "EXPIRED":
        return -0.2
    return 0.0


def _state_key(state: tuple) -> str:
    """Convert state tuple to JSON-serializable string key."""
    return json.dumps(state)
