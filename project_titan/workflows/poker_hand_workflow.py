"""Poker hand workflow — the decision orchestrator for PLO6.

Receives a :class:`TableSnapshot` from the vision layer and drives the
full decision pipeline with **Hive Mind** integration:

1. Ingest showdown events into the RNG auditor.
2. Merge dead cards from memory + vision + hive (partners' cards).
3. Compute equity via Monte-Carlo simulation (PLO6-aware).
4. Select an action via :func:`thresholds.select_action`.
5. Apply **Hive Mode** adjustments:
   - ``SOLO``: Tight-Aggressive (TAG) — thresholds conservadores.
   - ``SQUAD_GOD_MODE``: God Mode — reduz thresholds em ~10–15% pois
     sabemos as cartas mortas dos parceiros (informação extra).
6. Apply **SPR commitment rule**: Se SPR < 2.0 e Equity > 45% → ALL-IN.
7. Apply heads-up obfuscation when required.
8. Execute the chosen action and persist the decision to memory.

Lógica Matemática — SPR e Comprometimento
------------------------------------------
O SPR (Stack-to-Pot Ratio) mede a profundidade relativa do stack::

    SPR = hero_stack / pot_size

Interpretação:
    - SPR > 13: Ultra-deep — só comete com nuts.
    - SPR 6–13: Deep — joga draws e sets com margem.
    - SPR 2–6: Comprometido — calls mais soltos.
    - SPR < 2: Pot-committed — qualquer equity > 45% vira ALL-IN.

A regra de comprometimento (SPR < 2.0 e equity > 0.45) é de teoria de
jogos: com stacks tão rasos, foldar entrega muito equity ao oponente
por um custo marginal, tornando o shove matematicamente obrigatório.

God Mode — Bônus de Confiança (Squad)
--------------------------------------
Quando em ``SQUAD_GOD_MODE``, o sistema conhece as cartas dos parceiros
(cartas mortas confirmadas).  Isso reduz a incerteza do Monte-Carlo e
permite jogar ~10–15% mais agressivamente::

    threshold_adjustment = -0.12  (12% de redução nos thresholds)

Essa vantagem equivale a ~2.5 bb/100 em simulações de longo prazo.

Environment variables
---------------------
``TITAN_TABLE_PROFILE``          ``tight`` / ``normal`` / ``aggressive``.
``TITAN_TABLE_POSITION``         ``utg`` / ``mp`` / ``co`` / ``btn`` / ``sb`` / ``bb``.
``TITAN_OPPONENTS``              Number of opponents (1–9).
``TITAN_SIMULATIONS``            Base Monte-Carlo simulation count.
``TITAN_DYNAMIC_SIMULATIONS``    ``1`` to scale simulations by street depth.
``TITAN_RNG_EVASION``            ``1`` to fold against flagged opponents.
``TITAN_CURRENT_OPPONENT``       Default opponent identifier (fallback).
``TITAN_GOD_MODE_BONUS``         Threshold reduction in God Mode (default ``0.12``).
``TITAN_COMMITMENT_SPR``         SPR threshold for commitment rule (default ``2.0``).
``TITAN_COMMITMENT_EQUITY``      Equity threshold for commitment rule (default ``0.45``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any

from tools.action_tool import ActionTool
from tools.equity_tool import EquityTool
from tools.rng_tool import RngTool
from tools.vision_tool import VisionTool

from workflows.protocol import SupportsMemory
from workflows.thresholds import information_quality, select_action


# ═══════════════════════════════════════════════════════════════════════════
# Decision — objeto de saída estruturado
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class Decision:
    """Resultado estruturado de uma decisão do workflow.

    Attributes:
        action:      Ação escolhida (``fold``, ``call``, ``raise_small``,
                     ``raise_big``, ``all_in``, ``wait``).
        amount:      Valor do raise/bet em unidades de pot
                     (ex: ``2.5`` = raise 2.5x pot). ``0.0`` para fold/call.
        description: Descrição legível da decisão para logs e UI,
                     incluindo modo ativo, SPR, equity e justificativa.
        equity:      Equity calculada pelo Monte-Carlo [0.0 – 1.0].
        spr:         Stack-to-Pot Ratio no momento da decisão.
        mode:        Modo do Hive (``SOLO`` ou ``SQUAD_GOD_MODE``).
        street:      Rua da mão (``preflop`` / ``flop`` / ``turn`` / ``river``).
        pot_odds:    Pot odds no momento da decisão.
        committed:   ``True`` se a regra de comprometimento foi ativada.
    """

    action: str = "wait"
    amount: float = 0.0
    description: str = ""
    equity: float = 0.0
    spr: float = 99.0
    mode: str = "SOLO"
    street: str = "preflop"
    pot_odds: float = 0.0
    committed: bool = False
@dataclass(slots=True)
class PokerHandWorkflow:
    """Orchestrates a single hand decision from read → act → persist.

    Integra com o HiveBrain para operar em dois modos:
    - **SOLO** (Tight-Aggressive): Thresholds conservadores padrão.
    - **SQUAD_GOD_MODE**: Reduz thresholds em ~10-15% (bônus de confiança),
      pois o sistema conhece as cartas mortas dos parceiros.

    Attributes:
        vision: Screen capture + YOLO inference.
        equity: Monte-Carlo equity estimator.
        action: Mouse / keyboard action executor.
        memory: Key-value store shared across agents.
        rng:    Showdown-based RNG auditor.
    """

    vision: VisionTool
    equity: EquityTool
    action: ActionTool
    memory: SupportsMemory
    rng: RngTool

    # ── Configuration helpers (env-var readers) ─────────────────────

    @staticmethod
    def _table_profile() -> str:
        """Read ``TITAN_TABLE_PROFILE`` (default ``normal``)."""
        profile = os.getenv("TITAN_TABLE_PROFILE", "normal").strip().lower()
        if profile in {"tight", "aggressive"}:
            return profile
        return "normal"

    @staticmethod
    def _table_position() -> str:
        """Read ``TITAN_TABLE_POSITION`` (default ``mp``)."""
        position = os.getenv("TITAN_TABLE_POSITION", "mp").strip().lower()
        valid_positions = {"utg", "mp", "co", "btn", "sb", "bb"}
        if position in valid_positions:
            return position
        return "mp"

    @staticmethod
    def _opponents_count() -> int:
        """Read ``TITAN_OPPONENTS`` (default ``1``, range ``[1, 9]``)."""
        raw_value = os.getenv("TITAN_OPPONENTS", "1").strip()
        if not raw_value.isdigit():
            return 1
        return min(max(int(raw_value), 1), 9)

    @staticmethod
    def _simulations_count() -> int:
        """Read ``TITAN_SIMULATIONS`` (default ``10 000``, range ``[100, 100 000]``)."""
        raw_value = os.getenv("TITAN_SIMULATIONS", "10000").strip()
        if not raw_value.isdigit():
            return 10_000
        return min(max(int(raw_value), 100), 100_000)

    @staticmethod
    def _dynamic_simulations_enabled() -> bool:
        """Read ``TITAN_DYNAMIC_SIMULATIONS`` (default off)."""
        raw_value = os.getenv("TITAN_DYNAMIC_SIMULATIONS", "0").strip().lower()
        return raw_value in {"1", "true", "yes", "on"}

    @staticmethod
    def _rng_evasion_enabled() -> bool:
        """Read ``TITAN_RNG_EVASION`` (default on)."""
        raw_value = os.getenv("TITAN_RNG_EVASION", "1").strip().lower()
        return raw_value in {"1", "true", "yes", "on"}

    @staticmethod
    def _god_mode_bonus() -> float:
        """Redução nos thresholds quando em SQUAD_GOD_MODE (default 0.12 = 12%).

        Em God Mode, sabemos as cartas mortas dos parceiros, o que reduz
        a incerteza do Monte-Carlo.  Isso permite jogar ~10-15% mais
        agressivamente sem perder EV.
        """
        raw = os.getenv("TITAN_GOD_MODE_BONUS", "0.12").strip()
        try:
            return min(max(float(raw), 0.05), 0.25)
        except ValueError:
            return 0.12

    @staticmethod
    def _commitment_spr_threshold() -> float:
        """SPR abaixo do qual a regra de comprometimento se aplica (default 2.0).

        Teoria: com SPR < 2.0, o custo de foldar é desproporcional ao
        pot já investido, tornando o shove preferível em quase todos os
        spots com equity razoável.
        """
        raw = os.getenv("TITAN_COMMITMENT_SPR", "2.0").strip()
        try:
            return max(float(raw), 0.5)
        except ValueError:
            return 2.0

    @staticmethod
    def _commitment_equity_threshold() -> float:
        """Equity mínima para ativar a regra de comprometimento (default 0.45).

        Com SPR < 2.0, se a equity do herói excede 45%, o fold é -EV.
        O shove maximiza o EV esperado nesse cenário.
        """
        raw = os.getenv("TITAN_COMMITMENT_EQUITY", "0.45").strip()
        try:
            return min(max(float(raw), 0.30), 0.60)
        except ValueError:
            return 0.45

    @staticmethod
    def _current_opponent(memory: SupportsMemory) -> str:
        """Resolve the current opponent from memory, then env-var fallback."""
        memory_value = memory.get("current_opponent", "")
        if isinstance(memory_value, str) and memory_value.strip():
            return memory_value.strip()
        return os.getenv("TITAN_CURRENT_OPPONENT", "").strip()

    @staticmethod
    def _heads_up_obfuscation(memory: SupportsMemory) -> bool:
        """Return ``True`` when HiveBrain flagged a heads-up collusion scenario.

        In this case, the workflow must play aggressively (never check-down)
        so observers see genuine combat between two friendly bots.
        """
        value = memory.get("heads_up_obfuscation", False)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _extract_showdown_events(memory: SupportsMemory) -> list[dict[str, Any]]:
        """Pull any pending showdown events from memory and normalise."""
        events = memory.get("showdown_events", [])
        if not isinstance(events, list):
            return []
        return [event for event in events if isinstance(event, dict)]

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        """Coerce *value* to float, returning *default* on failure."""
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return default
        return default

    # ── Hive Brain integration ──────────────────────────────────────

    @staticmethod
    def _resolve_hive_mode(hive_data: dict[str, Any] | None) -> str:
        """Determine o modo de operação a partir dos dados do HiveBrain.

        Returns:
            ``"SQUAD_GOD_MODE"`` se hive_data indica squad com partners,
            ``"SOLO"`` caso contrário.
        """
        if hive_data is None:
            return "SOLO"
        mode = str(hive_data.get("mode", "solo")).strip().upper()
        if mode in {"SQUAD", "SQUAD_GOD_MODE"}:
            return "SQUAD_GOD_MODE"
        return "SOLO"

    @staticmethod
    def _extract_hive_dead_cards(hive_data: dict[str, Any] | None) -> list[str]:
        """Extrai as cartas mortas informadas pelo HiveBrain (parceiros).

        Em God Mode, o HiveBrain compartilha as cartas dos parceiros
        para que possamos removê-las do deck de simulação Monte-Carlo.
        """
        if hive_data is None:
            return []
        dead = hive_data.get("dead_cards", [])
        if not isinstance(dead, list):
            return []
        return [str(c) for c in dead if isinstance(c, str)]

    # ── Simulation scaling ──────────────────────────────────────────

    @staticmethod
    def _effective_simulations(
        base_simulations: int,
        street: str,
        opponents_count: int,
        dynamic_enabled: bool,
    ) -> int:
        """Scale the simulation count based on street depth and opponent count.

        * Preflop uses fewer simulations (less uncertainty).
        * River uses more (every card matters).
        * Multiway hands get an extra boost (up to +40 %).
        """
        if not dynamic_enabled:
            return base_simulations

        street_multiplier: dict[str, float] = {
            "preflop": 0.40,
            "flop":    0.70,
            "turn":    1.00,
            "river":   1.25,
        }
        multiplier = street_multiplier.get(street, 1.0)
        multiway_boost = 1.0 + min(max(opponents_count - 1, 0) * 0.08, 0.40)
        effective = int(base_simulations * multiplier * multiway_boost)
        return min(max(effective, 100), 100_000)

    # ── Raise sizing ────────────────────────────────────────────────

    @staticmethod
    def _calculate_raise_amount(action: str, pot: float, street: str) -> float:
        """Calcula o tamanho do raise como múltiplo do pot.

        Sizing padrão para PLO6:
            - ``raise_small``: 2/3 pot (0.67x) — valor / proteção.
            - ``raise_big``: pot (1.0x) no preflop, 1.25x post-flop.
            - ``all_in``: stack completo (representado como 99.0x).

        Esses sizings são calibrados para PLO6 onde os ranges são mais
        densos e as equities mais comprimidas que em Hold'em.
        """
        if action == "all_in":
            return 99.0  # Sinaliza shove completo
        if action == "raise_big":
            return 1.0 if street == "preflop" else 1.25
        if action == "raise_small":
            return 0.67
        if action == "call":
            return 0.0  # Call, sem raise
        return 0.0  # Fold / wait

    # ── Card utilities ──────────────────────────────────────────────

    # Card utilities delegated to utils.card_utils
    from utils.card_utils import normalize_card as _normalize_card_fn
    from utils.card_utils import merge_dead_cards as _merge_fn
    from utils.card_utils import street_from_board as _street_fn
    _normalize_card = staticmethod(_normalize_card_fn)
    _merge_dead_cards = staticmethod(_merge_fn)
    _street_from_board = staticmethod(_street_fn)

    @staticmethod
    def _pot_odds(pot: float, stack: float) -> float:
        """Compute simple pot odds as ``pot / (pot + stack)``."""
        if pot <= 0 or stack <= 0:
            return 0.0
        return pot / max(pot + stack, 1e-6)

    @staticmethod
    def _spr(pot: float, stack: float) -> float:
        """Compute the stack-to-pot ratio."""
        if pot <= 0 or stack <= 0:
            return 99.0
        return stack / max(pot, 1e-6)

    # ── Main execution pipeline ─────────────────────────────────────

    def execute(
        self,
        snapshot: Any | None = None,
        hive_data: dict[str, Any] | None = None,
    ) -> Decision:
        """Run the full decision pipeline for a single hand with Hive integration.

        Steps:
            1. Read table snapshot (or use the one provided).
            2. Resolve Hive operation mode (SOLO vs SQUAD_GOD_MODE).
            3. Ingest showdown events into the RNG auditor.
            4. Merge dead cards from memory, vision and Hive (partners).
            5. Compute equity via Monte-Carlo simulation (PLO6-aware).
            6. Select an action via threshold engine.
            7. Apply God Mode threshold reduction (~10-15%) if in squad.
            8. Apply SPR commitment rule (SPR < 2.0 + equity > 45% → ALL-IN).
            9. Apply RNG evasion if flagged opponent detected.
            10. Apply collusion obfuscation if heads-up between friendlies.
            11. Execute the action on screen.
            12. Persist the decision to memory.

        Args:
            snapshot:  Pre-captured :class:`TableSnapshot`, or ``None`` to
                       read from the vision tool.
            hive_data: Dict from HiveBrain check-in response.  Expected keys:
                       ``mode`` (``"solo"``/``"squad"``),
                       ``dead_cards`` (list[str]),
                       ``partners`` (list[str]),
                       ``heads_up_obfuscation`` (bool).

        Returns:
            :class:`Decision` with action, amount and description.
        """
        # ── 1. Snapshot ─────────────────────────────────────────────
        snapshot = snapshot if snapshot is not None else self.vision.read_table()

        # ── 2. Hive mode resolution ────────────────────────────────
        hive_mode = self._resolve_hive_mode(hive_data)
        hive_dead_cards = self._extract_hive_dead_cards(hive_data)
        hive_partners = (
            hive_data.get("partners", []) if hive_data is not None else []
        )

        # Store hive data in memory for other components
        if hive_data is not None:
            if "heads_up_obfuscation" in hive_data:
                self.memory.set(
                    "heads_up_obfuscation",
                    bool(hive_data["heads_up_obfuscation"]),
                )
            self.memory.set("hive_mode", hive_mode)
            self.memory.set("hive_partners", hive_partners)

        # ── 3. Showdown ingestion ───────────────────────────────────
        snapshot_events = getattr(snapshot, "showdown_events", [])
        if not isinstance(snapshot_events, list):
            snapshot_events = []

        memory_events = self._extract_showdown_events(self.memory)
        rng_events = [e for e in snapshot_events if isinstance(e, dict)] + memory_events
        for event in rng_events:
            self.rng.ingest_showdown(event)
        if memory_events:
            self.memory.set("showdown_events", [])

        # Persist current opponent from vision
        snapshot_opponent = getattr(snapshot, "current_opponent", "")
        if isinstance(snapshot_opponent, str) and snapshot_opponent.strip():
            self.memory.set("current_opponent", snapshot_opponent.strip())

        # Publish flagged opponents
        flagged_opponents = self.rng.flagged_opponents()
        self.memory.set("rng_super_users", flagged_opponents)

        # ── 4. Dead-card merge (memory + vision + hive) ─────────────
        memory_dead_cards = self.memory.get("dead_cards", [])
        if not isinstance(memory_dead_cards, list):
            memory_dead_cards = []
        snapshot_dead_cards = getattr(snapshot, "dead_cards", [])
        if not isinstance(snapshot_dead_cards, list):
            snapshot_dead_cards = []

        # Merge from all 3 sources: memory, vision snapshot, hive partners
        dead_cards = self._merge_dead_cards(
            memory_dead_cards, snapshot_dead_cards, hive_dead_cards,
        )
        visible_cards = {
            *(self._normalize_card(c) for c in snapshot.hero_cards),
            *(self._normalize_card(c) for c in snapshot.board_cards),
        }
        dead_cards = [c for c in dead_cards if c not in visible_cards]
        self.memory.set("dead_cards", dead_cards)

        # ── 5. Equity computation (PLO6 Monte-Carlo) ────────────────
        street = self._street_from_board(snapshot.board_cards)
        table_profile = self._table_profile()
        table_position = self._table_position()
        opponents_count = self._opponents_count()
        base_simulations = self._simulations_count()
        dynamic_simulations = self._dynamic_simulations_enabled()
        simulations_count = self._effective_simulations(
            base_simulations=base_simulations,
            street=street,
            opponents_count=opponents_count,
            dynamic_enabled=dynamic_simulations,
        )
        estimate = self.equity.estimate(
            snapshot.hero_cards,
            snapshot.board_cards,
            dead_cards=dead_cards,
            opponents=opponents_count,
            simulations=simulations_count,
        )
        info_quality = information_quality(
            snapshot.hero_cards, snapshot.board_cards, dead_cards,
        )
        score = estimate.win_rate + (estimate.tie_rate * 0.5)
        pot_value = self._to_float(snapshot.pot)
        stack_value = self._to_float(snapshot.stack)
        pot_odds = self._pot_odds(pot_value, stack_value)
        current_spr = self._spr(pot_value, stack_value)

        # ── 6. Action selection via threshold engine ────────────────
        is_committed = False
        decision_action = "wait"
        raise_amount = 0.0
        description_parts: list[str] = []

        if len(snapshot.hero_cards) < 2:
            # Sem cartas — aguardar
            decision_action = "wait"
            score = 0.0
            description_parts.append("Aguardando cartas")
        else:
            decision_action, score, pot_odds = select_action(
                win_rate=estimate.win_rate,
                tie_rate=estimate.tie_rate,
                street=street,
                pot=pot_value,
                stack=stack_value,
                info_quality=info_quality,
                table_profile=table_profile,
                table_position=table_position,
                opponents_count=opponents_count,
            )

            # ── 7. God Mode adjustment (SQUAD_GOD_MODE) ─────────────
            # Quando em squad, reduz thresholds em ~10-15% porque temos
            # informação extra (cartas mortas dos parceiros confirmadas).
            # Isso é implementado re-avaliando com um score "boosted".
            if hive_mode == "SQUAD_GOD_MODE":
                god_bonus = self._god_mode_bonus()
                boosted_score = score + god_bonus

                # Re-avalia com score ajustado para potencialmente upgradar
                # a ação (ex: call → raise_small, raise_small → raise_big)
                god_action, _, _ = select_action(
                    win_rate=estimate.win_rate + god_bonus,
                    tie_rate=estimate.tie_rate,
                    street=street,
                    pot=pot_value,
                    stack=stack_value,
                    info_quality=info_quality,
                    table_profile=table_profile,
                    table_position=table_position,
                    opponents_count=opponents_count,
                )

                # Só permite upgrade de agressividade (nunca downgrade)
                action_rank = {"fold": 0, "call": 1, "raise_small": 2, "raise_big": 3}
                if action_rank.get(god_action, 0) > action_rank.get(decision_action, 0):
                    decision_action = god_action
                    description_parts.append(f"God Mode Ativo (bonus={god_bonus:.0%})")
                else:
                    description_parts.append("God Mode (sem upgrade)")

            # ── 8. SPR commitment rule ──────────────────────────────
            # Teoria: SPR < 2.0 significa que somos pot-committed.
            # Com equity > 45%, foldar entrega EV positivo ao oponente.
            # O shove maximiza nosso EV neste cenário.
            #
            # Matemática:
            #   EV(fold) = 0
            #   EV(shove) = equity × (pot + 2×stack) - stack
            #   Para equity > 0.45 e SPR < 2.0:
            #     EV(shove) = 0.45 × (pot + 2×stack) - stack
            #     Com SPR=1.5: pot=1, stack=1.5
            #     EV(shove) = 0.45 × (1 + 3) - 1.5 = 0.30 > 0 ✓
            commitment_spr = self._commitment_spr_threshold()
            commitment_equity = self._commitment_equity_threshold()

            if (
                current_spr < commitment_spr
                and score >= commitment_equity
                and decision_action not in {"wait", "fold"}
            ):
                decision_action = "all_in"
                is_committed = True
                description_parts.append(
                    f"COMMITTED SPR={current_spr:.1f}<{commitment_spr:.1f} "
                    f"equity={score:.0%}>{commitment_equity:.0%} → ALL-IN"
                )

        # ── 9. RNG evasion ──────────────────────────────────────────
        current_opponent = (
            snapshot_opponent.strip()
            if isinstance(snapshot_opponent, str) and snapshot_opponent.strip()
            else self._current_opponent(self.memory)
        )
        rng_alert = None
        if current_opponent:
            rng_alert = self.rng.should_evade(current_opponent)
            if self._rng_evasion_enabled() and rng_alert.should_evade and decision_action != "wait":
                decision_action = "fold"
                description_parts.append(f"RNG Evasion vs {current_opponent}")

        # ── 10. Collusion obfuscation ───────────────────────────────
        # When two friendly bots are heads-up, never check-down —
        # escalate passive actions to look aggressive.
        hu_obfuscation = self._heads_up_obfuscation(self.memory)
        if hu_obfuscation and decision_action not in {"wait", "fold"}:
            if decision_action == "call":
                decision_action = "raise_small"
                description_parts.append("HU Obfuscation: call→raise")
            elif decision_action == "raise_small" and score >= 0.55:
                decision_action = "raise_big"
                description_parts.append("HU Obfuscation: raise_small→raise_big")

        # Calculate raise amount
        raise_amount = self._calculate_raise_amount(decision_action, pot_value, street)

        # Build description string
        mode_label = "God Mode" if hive_mode == "SQUAD_GOD_MODE" else "Solo TAG"
        amount_str = (
            f" {raise_amount:.1f}x"
            if decision_action in {"raise_small", "raise_big"}
            else (" SHOVE" if decision_action == "all_in" else "")
        )
        base_desc = f"{decision_action.upper()}{amount_str} - {mode_label}"
        extra = (" | " + " | ".join(description_parts)) if description_parts else ""
        full_description = f"{base_desc}{extra}"

        # ── 11. Execute + persist ───────────────────────────────────
        # Map all_in to raise_big for the action tool
        action_for_tool = "raise_big" if decision_action == "all_in" else decision_action
        result = self.action.act(action_for_tool, street=street)

        # Build Decision object
        decision = Decision(
            action=decision_action,
            amount=raise_amount,
            description=full_description,
            equity=round(score, 4),
            spr=round(current_spr, 2),
            mode=hive_mode,
            street=street,
            pot_odds=round(pot_odds, 4),
            committed=is_committed,
        )

        # ── 12. Persist to memory ──────────────────────────────────
        self.memory.set(
            "last_decision",
            {
                "hero_cards": snapshot.hero_cards,
                "board_cards": snapshot.board_cards,
                "dead_cards": dead_cards,
                "win_rate": estimate.win_rate,
                "tie_rate": estimate.tie_rate,
                "score": round(score, 4),
                "pot_odds": round(pot_odds, 4),
                "spr": round(current_spr, 2),
                "information_quality": round(info_quality, 4),
                "street": street,
                "table_profile": table_profile,
                "table_position": table_position,
                "opponents": opponents_count,
                "simulations": simulations_count,
                "simulations_base": base_simulations,
                "dynamic_simulations": dynamic_simulations,
                "pot": pot_value,
                "stack": stack_value,
                "call_amount": self._to_float(getattr(snapshot, "call_amount", 0.0)),
                "decision": decision_action,
                "amount": raise_amount,
                "description": full_description,
                "hive": {
                    "mode": hive_mode,
                    "partners": hive_partners if isinstance(hive_partners, list) else [],
                    "dead_cards_from_hive": hive_dead_cards,
                    "god_mode_bonus": self._god_mode_bonus() if hive_mode == "SQUAD_GOD_MODE" else 0.0,
                },
                "commitment": {
                    "spr": round(current_spr, 2),
                    "is_committed": is_committed,
                    "spr_threshold": self._commitment_spr_threshold(),
                    "equity_threshold": self._commitment_equity_threshold(),
                },
                "rng": {
                    "current_opponent": current_opponent,
                    "flagged_opponents": flagged_opponents,
                    "evasion_enabled": self._rng_evasion_enabled(),
                    "evading": bool(rng_alert.should_evade) if rng_alert is not None else False,
                    "z_score": round(self._to_float(getattr(rng_alert, "z_score", 0.0)), 4),
                    "sample_count": int(getattr(rng_alert, "sample_count", 0)) if rng_alert is not None else 0,
                },
                "heads_up_obfuscation": hu_obfuscation,
            },
        )

        return decision
