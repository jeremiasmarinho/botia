"""GTO Mixed-Strategy Engine — frequency-based action randomization.

Replaces the deterministic threshold ladder with **mixed strategies**:
instead of always choosing the same action for a given equity, the engine
computes a probability distribution over actions and samples from it.

Theory
------
In GTO poker, every decision point has an *equilibrium strategy* that is
a probability distribution over actions.  A pure-threshold bot always picks
the same action for equity X, making it trivially exploitable.  This module
introduces controlled randomization so that:

- A hand with equity 0.70 might **call 65%, raise_small 30%, fold 5%**.
- The distribution shifts based on street, position, SPR, board texture,
  and opponent tendencies.
- The bot becomes unexploitable in the limit while still being +EV
  against weaker opponents.

Architecture
------------
``MixedStrategy.select()`` is a **drop-in replacement** for the old
``thresholds.select_action()`` — same inputs, same output signature.

The old function is preserved as ``select_action_deterministic()`` for
testing / comparison.

Environment variables
---------------------
``TITAN_GTO_ENABLED``           ``1`` to enable mixed strategies (default: ``1``).
``TITAN_GTO_RANDOMNESS``        Blend factor [0..1] — 0 = deterministic, 1 = full GTO mix.
``TITAN_GTO_BLUFF_FREQ``        Base bluff frequency [0..1] (default: ``0.12``).
``TITAN_GTO_SLOWPLAY_FREQ``     Base slowplay frequency [0..1] (default: ``0.08``).
``TITAN_GTO_SEED``              RNG seed for reproducible tests (default: None).
"""

from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass, field
from typing import Any

from workflows.thresholds import select_action as select_action_deterministic
from workflows.thresholds import information_quality  # re-export

# Minimum observed hands before exploiting opponent tendencies.
# Below this threshold, opponent classification is unreliable.
_MIN_HANDS_FOR_EXPLOIT: int = 50


# ── Action ranking (for comparisons) ────────────────────────────────────

ACTION_RANK: dict[str, int] = {
    "fold": 0,
    "call": 1,
    "raise_small": 2,
    "raise_big": 3,
    "all_in": 4,
}


# ── Data structures ─────────────────────────────────────────────────────

@dataclass(slots=True)
class ActionDistribution:
    """Probability distribution over poker actions.

    Attributes:
        fold:        P(fold).
        call:        P(call).
        raise_small: P(raise_small).
        raise_big:   P(raise_big).
        chosen:      The action that was sampled.
    """
    fold: float = 0.0
    call: float = 0.0
    raise_small: float = 0.0
    raise_big: float = 0.0
    chosen: str = "fold"

    def as_dict(self) -> dict[str, float]:
        return {
            "fold": round(self.fold, 4),
            "call": round(self.call, 4),
            "raise_small": round(self.raise_small, 4),
            "raise_big": round(self.raise_big, 4),
        }


@dataclass(slots=True)
class OpponentTendencies:
    """Opponent profile for strategy adaptation.

    These stats drive adjustments to the mixed strategy:
    - Against a calling station (high VPIP, low PFR): value-bet wider, bluff less.
    - Against a nit (low VPIP): steal more, fold to 3-bets less.
    - Against an aggro (high aggression): trap more, let them hang themselves.
    """
    vpip: float = 0.50          # Voluntarily Put $ In Pot
    pfr: float = 0.20           # Pre-Flop Raise %
    aggression: float = 1.0     # Aggression Factor (bets+raises / calls)
    fold_to_3bet: float = 0.50  # Fold to 3-bet %
    cbet_freq: float = 0.60     # Continuation bet frequency
    hands_observed: int = 0     # Sample size


# ── Mixed Strategy Engine ───────────────────────────────────────────────

class MixedStrategy:
    """GTO-inspired mixed-strategy action selector.

    Instead of deterministic thresholds, computes a probability distribution
    over actions and samples from it.  The distribution is shaped by:

    1. **Base equity mapping** — sigmoid-like curves that smoothly transition
       between fold/call/raise regions instead of hard cutoffs.
    2. **Bluff injection** — even with low equity, maintain a non-zero raise
       frequency to prevent exploitation.
    3. **Slowplay injection** — with very high equity, sometimes just call to
       trap aggressive opponents.
    4. **Board texture** — wet boards increase check/call frequency; dry boards
       increase bet frequency.
    5. **Opponent adaptation** — adjust frequencies based on opponent profile.
    """

    def __init__(self, seed: int | None = None) -> None:
        seed_env = os.getenv("TITAN_GTO_SEED", "").strip()
        if seed is not None:
            self._rng = random.Random(seed)
        elif seed_env:
            self._rng = random.Random(int(seed_env))
        else:
            self._rng = random.Random()

        self._enabled = os.getenv(
            "TITAN_GTO_ENABLED", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}

        self._randomness = self._env_float("TITAN_GTO_RANDOMNESS", 0.85)
        self._bluff_freq = self._env_float("TITAN_GTO_BLUFF_FREQ", 0.12)
        self._slowplay_freq = self._env_float("TITAN_GTO_SLOWPLAY_FREQ", 0.08)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Public API ──────────────────────────────────────────────────

    def select(
        self,
        win_rate: float,
        tie_rate: float,
        street: str,
        pot: float,
        stack: float,
        info_quality: float,
        table_profile: str,
        table_position: str,
        opponents_count: int,
        opponent: OpponentTendencies | None = None,
    ) -> tuple[str, float, float, ActionDistribution]:
        """Select an action using mixed-strategy logic.

        Returns:
            ``(action, score, pot_odds, distribution)`` — same first 3
            elements as ``thresholds.select_action()``, plus the full
            probability distribution for logging.
        """
        # Always compute the deterministic baseline
        det_action, score, pot_odds = select_action_deterministic(
            win_rate=win_rate,
            tie_rate=tie_rate,
            street=street,
            pot=pot,
            stack=stack,
            info_quality=info_quality,
            table_profile=table_profile,
            table_position=table_position,
            opponents_count=opponents_count,
        )

        if not self._enabled:
            dist = ActionDistribution(chosen=det_action)
            setattr(dist, det_action, 1.0)
            return det_action, score, pot_odds, dist

        # Build the mixed-strategy distribution
        dist = self._build_distribution(
            score=score,
            det_action=det_action,
            street=street,
            pot=pot,
            stack=stack,
            table_position=table_position,
            opponents_count=opponents_count,
            opponent=opponent,
        )

        # Blend between deterministic and mixed based on randomness factor
        # At randomness=0 → always deterministic; at 1.0 → full mix
        if self._rng.random() > self._randomness:
            dist.chosen = det_action
            return det_action, score, pot_odds, dist

        # Sample from the distribution
        chosen = self._sample(dist)
        dist.chosen = chosen
        return chosen, score, pot_odds, dist

    # ── Distribution building ───────────────────────────────────────

    def _build_distribution(
        self,
        score: float,
        det_action: str,
        street: str,
        pot: float,
        stack: float,
        table_position: str,
        opponents_count: int,
        opponent: OpponentTendencies | None,
    ) -> ActionDistribution:
        """Compute the action probability distribution.

        The distribution is built in layers:
        1. Start from a soft-threshold baseline (sigmoid curves).
        2. Inject bluff frequency.
        3. Inject slowplay frequency.
        4. Apply position/street multipliers.
        5. Adjust for opponent tendencies (if available).
        6. Normalize to sum to 1.0.
        """
        # Start with sigmoid-based soft thresholds
        p_fold, p_call, p_raise_s, p_raise_b = self._sigmoid_baseline(score, street)

        # ── Consistency floor: prevent GTO from overriding clear +EV decisions ──
        # When the deterministic engine (which accounts for all contextual
        # adjustments) says to continue (call/raise), the sigmoid baseline
        # must NOT assign excessive fold probability.  Capping p_fold
        # ensures the mixed strategy stays consistent with the EV signal.
        from workflows.gto_engine import ACTION_RANK  # local ref for clarity
        if ACTION_RANK.get(det_action, 0) >= ACTION_RANK["call"]:
            max_fold = 0.08  # at most 8% fold when deterministic says continue
            if p_fold > max_fold:
                excess = p_fold - max_fold
                p_fold = max_fold
                # Redistribute excess proportionally into call + raises
                p_call += excess * 0.60
                p_raise_s += excess * 0.25
                p_raise_b += excess * 0.15
        elif ACTION_RANK.get(det_action, 0) >= ACTION_RANK["raise_small"]:
            max_fold = 0.03
            if p_fold > max_fold:
                excess = p_fold - max_fold
                p_fold = max_fold
                p_raise_s += excess * 0.55
                p_raise_b += excess * 0.45

        # Bluff injection: even with weak hands, maintain some aggression
        # BUT: against a Fish / calling station with sufficient sample,
        # bluffing is -EV → reduce to near zero.
        bluff_freq = self._bluff_frequency(
            score, street, table_position, opponents_count, opponent,
        )
        if score < 0.40:
            # Redistribute some fold probability into raises (bluffs)
            bluff_amount = min(bluff_freq, p_fold * 0.5)
            p_fold -= bluff_amount
            p_raise_s += bluff_amount * 0.65
            p_raise_b += bluff_amount * 0.35

        # Slowplay injection: with very strong hands, sometimes just call
        if score > 0.75:
            slowplay = self._slowplay_frequency(score, street, opponents_count)
            trap_amount = min(slowplay, (p_raise_s + p_raise_b) * 0.4)
            if p_raise_b > p_raise_s:
                p_raise_b -= trap_amount * 0.6
                p_raise_s -= trap_amount * 0.4
            else:
                p_raise_s -= trap_amount
            p_call += trap_amount

        # Position multipliers: IP (in position) → more aggression
        p_fold, p_call, p_raise_s, p_raise_b = self._position_adjust(
            p_fold, p_call, p_raise_s, p_raise_b,
            table_position, street,
        )

        # Opponent adaptation — requires minimum sample size
        # Below the threshold, opponent classification is unreliable and the
        # bot plays pure GTO to avoid being counter-exploited.
        if opponent is not None and opponent.hands_observed >= _MIN_HANDS_FOR_EXPLOIT:
            p_fold, p_call, p_raise_s, p_raise_b = self._opponent_adjust(
                p_fold, p_call, p_raise_s, p_raise_b,
                opponent, score,
            )

        # SPR adjustments: low SPR → flatten distribution toward call/raise
        spr = stack / max(pot, 1e-6) if pot > 0 else 99.0
        if spr < 3.0 and score > 0.35:
            commit_shift = min(0.15, p_fold * 0.5)
            p_fold -= commit_shift
            p_call += commit_shift * 0.4
            p_raise_s += commit_shift * 0.3
            p_raise_b += commit_shift * 0.3

        # Normalize
        return self._normalize(p_fold, p_call, p_raise_s, p_raise_b)

    def _sigmoid_baseline(
        self, score: float, street: str,
    ) -> tuple[float, float, float, float]:
        """Compute soft-threshold baseline using sigmoid-like curves.

        Instead of hard cutoffs at equity thresholds, each action has a
        smooth transition curve.  Equal equity values get spread across
        nearby actions, creating implicit mixed strategies.
        """
        # Street-dependent inflection points (where each action peaks)
        inflections: dict[str, tuple[float, float, float]] = {
            "preflop": (0.38, 0.55, 0.72),  # call, raise_small, raise_big
            "flop":    (0.42, 0.58, 0.75),
            "turn":    (0.45, 0.62, 0.78),
            "river":   (0.48, 0.65, 0.82),
        }
        call_inf, raise_s_inf, raise_b_inf = inflections.get(
            street, (0.42, 0.58, 0.75)
        )

        # Sigmoid steepness (lower = wider mixing zone)
        k = 12.0  # moderate steepness — creates ~10% mix zone around each threshold

        # Raw sigmoid outputs
        s_call = 1.0 / (1.0 + math.exp(-k * (score - call_inf)))
        s_raise_s = 1.0 / (1.0 + math.exp(-k * (score - raise_s_inf)))
        s_raise_b = 1.0 / (1.0 + math.exp(-k * (score - raise_b_inf)))

        # Convert to probabilities via differences
        p_raise_b = s_raise_b
        p_raise_s = max(0.0, s_raise_s - s_raise_b)
        p_call = max(0.0, s_call - s_raise_s)
        p_fold = max(0.0, 1.0 - s_call)

        return p_fold, p_call, p_raise_s, p_raise_b

    def _bluff_frequency(
        self,
        score: float,
        street: str,
        position: str,
        opponents: int,
        opponent: OpponentTendencies | None = None,
    ) -> float:
        """Compute bluff frequency based on context.

        Bluff more:
        - In late position (IP advantage)
        - On later streets (more credible bluffs)
        - Against fewer opponents (less likely someone has it)

        Bluff less:
        - When equity is very low (< 0.15 — no backdoor equity)
        - Against many opponents (multiway)
        - **Against a Fish / calling station** → near zero (they never fold)
        """
        base = self._bluff_freq

        # ── Fish / Calling Station kill switch ──────────────────────
        # A Fish (VPIP > 55%, aggression < 1.2) calls too much to bluff.
        # With ≥ 50 hands of data we are confident in the classification.
        # Reduce bluff to 2% (minimal uncertainty hedge only).
        if (
            opponent is not None
            and opponent.hands_observed >= _MIN_HANDS_FOR_EXPLOIT
            and opponent.vpip > 0.55
            and opponent.aggression < 1.2
        ):
            return 0.02  # near-zero: don't bluff a calling station

        # Position multiplier
        pos_mult = {
            "btn": 1.6, "co": 1.3, "sb": 0.7, "bb": 0.9,
            "mp": 0.8, "utg": 0.5,
        }.get(position, 1.0)

        # Street multiplier — bluff more on later streets (more fold equity)
        street_mult = {
            "preflop": 0.6, "flop": 0.9, "turn": 1.2, "river": 1.5,
        }.get(street, 1.0)

        # Multiway discount
        multi_mult = max(0.3, 1.0 - (opponents - 1) * 0.25)

        # Equity floor — don't bluff with complete air (no backdoor draws)
        if score < 0.10:
            equity_mult = 0.3
        elif score < 0.20:
            equity_mult = 0.7
        else:
            equity_mult = 1.0

        freq = base * pos_mult * street_mult * multi_mult * equity_mult
        return min(freq, 0.30)  # cap at 30%

    def _slowplay_frequency(
        self,
        score: float,
        street: str,
        opponents: int,
    ) -> float:
        """Compute slowplay (trap) frequency for strong hands.

        Slowplay more:
        - On dry boards (opponent unlikely to catch up)
        - With the nuts (no risk of being outdrawn)
        - Heads-up (trap one opponent)

        Slowplay less:
        - Multiway (too many draws available)
        - On wet boards (protect equity)
        """
        base = self._slowplay_freq

        # Stronger hand = more slowplay
        strength_mult = min(2.0, max(0.0, (score - 0.75) / 0.15))

        # Street multiplier — slowplay more on flop (let them catch up)
        street_mult = {
            "preflop": 0.3, "flop": 1.5, "turn": 1.0, "river": 0.6,
        }.get(street, 1.0)

        # Multiway discount
        multi_mult = max(0.2, 1.0 - (opponents - 1) * 0.3)

        freq = base * strength_mult * street_mult * multi_mult
        return min(freq, 0.25)  # cap at 25%

    def _position_adjust(
        self,
        p_fold: float,
        p_call: float,
        p_raise_s: float,
        p_raise_b: float,
        position: str,
        street: str,
    ) -> tuple[float, float, float, float]:
        """Shift distribution based on position (IP/OOP).

        In Position (BTN, CO): shift toward more aggression.
        Out of Position (UTG, SB): shift toward more passive play.
        """
        # Aggression shift — positive = more aggression
        pos_shift: dict[str, float] = {
            "btn": 0.06, "co": 0.03, "bb": -0.01,
            "sb": -0.03, "mp": 0.0, "utg": -0.04,
        }
        shift = pos_shift.get(position, 0.0)

        if shift > 0:
            # More aggressive: steal from fold/call → raises
            steal = min(shift, p_fold * 0.3 + p_call * 0.2)
            p_fold -= steal * 0.5
            p_call -= steal * 0.5
            p_raise_s += steal * 0.6
            p_raise_b += steal * 0.4
        elif shift < 0:
            # More passive: steal from raises → call/fold
            give = min(-shift, (p_raise_s + p_raise_b) * 0.3)
            p_raise_s -= give * 0.5
            p_raise_b -= give * 0.5
            p_call += give * 0.7
            p_fold += give * 0.3

        return (
            max(0.0, p_fold),
            max(0.0, p_call),
            max(0.0, p_raise_s),
            max(0.0, p_raise_b),
        )

    def _opponent_adjust(
        self,
        p_fold: float,
        p_call: float,
        p_raise_s: float,
        p_raise_b: float,
        opp: OpponentTendencies,
        score: float,
    ) -> tuple[float, float, float, float]:
        """Adjust frequencies based on opponent tendencies.

        - vs. Fish (high VPIP, low aggression): value-bet wider, bluff less.
        - vs. Nit (low VPIP): steal more preflop, give up postflop when resisted.
        - vs. LAG (high VPIP, high aggression): trap more, call down lighter.
        - vs. TAG (moderate VPIP, high aggression): play tighter, exploit 3-bet fold.
        """
        # Fish detection: VPIP > 0.55 and aggression < 1.2
        if opp.vpip > 0.55 and opp.aggression < 1.2:
            # Value-bet wider (they call too much), bluff less (they don't fold)
            if score > 0.45:
                shift = 0.05
                p_call -= shift * 0.5
                p_raise_s += shift * 0.5
            else:
                # Don't bluff fish — they call everything
                bluff_reduction = min(0.08, p_raise_s * 0.3)
                p_raise_s -= bluff_reduction
                p_fold += bluff_reduction

        # Nit detection: VPIP < 0.22
        elif opp.vpip < 0.22:
            # Steal more (they fold too much)
            steal = 0.06
            p_fold -= min(steal, p_fold * 0.3)
            p_raise_s += steal * 0.7
            p_raise_b += steal * 0.3
            # But fold to their aggression (they have it when they bet)
            if score < 0.50 and opp.aggression > 2.0:
                give = min(0.08, p_call * 0.3)
                p_call -= give
                p_fold += give

        # LAG detection: VPIP > 0.40 and aggression > 2.5
        elif opp.vpip > 0.40 and opp.aggression > 2.5:
            # Trap more, call down lighter (they bluff a lot)
            if score > 0.55:
                trap = 0.06
                p_raise_s -= min(trap, p_raise_s * 0.3)
                p_call += trap
            elif score > 0.40:
                # Call lighter
                shift = 0.04
                p_fold -= min(shift, p_fold * 0.3)
                p_call += shift

        return (
            max(0.0, p_fold),
            max(0.0, p_call),
            max(0.0, p_raise_s),
            max(0.0, p_raise_b),
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _normalize(
        self,
        p_fold: float,
        p_call: float,
        p_raise_s: float,
        p_raise_b: float,
    ) -> ActionDistribution:
        """Clamp, normalize to sum=1, and return an ActionDistribution."""
        p_fold = max(0.0, p_fold)
        p_call = max(0.0, p_call)
        p_raise_s = max(0.0, p_raise_s)
        p_raise_b = max(0.0, p_raise_b)

        total = p_fold + p_call + p_raise_s + p_raise_b
        if total <= 0:
            return ActionDistribution(fold=1.0, chosen="fold")

        return ActionDistribution(
            fold=p_fold / total,
            call=p_call / total,
            raise_small=p_raise_s / total,
            raise_big=p_raise_b / total,
        )

    def _sample(self, dist: ActionDistribution) -> str:
        """Sample an action from the distribution."""
        r = self._rng.random()
        cumulative = 0.0

        for action, prob in [
            ("fold", dist.fold),
            ("call", dist.call),
            ("raise_small", dist.raise_small),
            ("raise_big", dist.raise_big),
        ]:
            cumulative += prob
            if r < cumulative:
                return action

        return "fold"  # fallback (should not reach here)

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return min(max(float(raw), 0.0), 1.0)
        except ValueError:
            return default
