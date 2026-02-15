from __future__ import annotations

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from random import sample
from typing import Any

from treys import Card, Deck, Evaluator


@dataclass(slots=True)
class EquityResponse:
    win_rate: float
    tie_rate: float
    simulations: int


def parse_card(card: str) -> int:
    rank = card[0].upper()
    suit = card[1].lower()
    return Card.new(f"{rank}{suit}")


def estimate_equity(
    hero_cards: list[str],
    board_cards: list[str],
    dead_cards: list[str],
    simulations: int = 5000,
    opponents: int = 1,
) -> EquityResponse:
    hero = [parse_card(card) for card in hero_cards]
    board = [parse_card(card) for card in board_cards]
    blocked = set(hero + board + [parse_card(card) for card in dead_cards])

    evaluator = Evaluator()
    wins = 0
    ties = 0

    for _ in range(simulations):
        deck = Deck()
        deck.cards = [card for card in deck.cards if card not in blocked]

        needed_board = max(0, 5 - len(board))
        sampled_board = sample(deck.cards, needed_board)
        for card in sampled_board:
            deck.cards.remove(card)

        villain_hands: list[list[int]] = []
        for _ in range(opponents):
            hand = sample(deck.cards, 2)
            for card in hand:
                deck.cards.remove(card)
            villain_hands.append(hand)

        full_board = board + sampled_board
        hero_score = evaluator.evaluate(full_board, hero)
        villain_scores = [evaluator.evaluate(full_board, hand) for hand in villain_hands]
        best_villain = min(villain_scores)

        if hero_score < best_villain:
            wins += 1
        elif hero_score == best_villain:
            ties += 1

    return EquityResponse(
        win_rate=wins / simulations,
        tie_rate=ties / simulations,
        simulations=simulations,
    )


class DecisionHandler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/equity":
            self._send_json(404, {"error": "not_found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)

        try:
            payload = json.loads(raw.decode("utf-8"))
            hero_cards = payload["hero_cards"]
            board_cards = payload.get("board_cards", [])
            dead_cards = payload.get("dead_cards", [])
            simulations = int(payload.get("simulations", 5000))
            opponents = int(payload.get("opponents", 1))

            if len(hero_cards) < 2:
                raise ValueError("hero_cards must contain at least 2 cards")

            result = estimate_equity(
                hero_cards=hero_cards,
                board_cards=board_cards,
                dead_cards=dead_cards,
                simulations=simulations,
                opponents=opponents,
            )
            self._send_json(
                200,
                {
                    "win_rate": result.win_rate,
                    "tie_rate": result.tie_rate,
                    "simulations": result.simulations,
                },
            )
        except Exception as error:
            self._send_json(400, {"error": str(error)})


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), DecisionHandler)
    print(f"[LogicCore] Listening on http://{host}:{port}")
    print("[LogicCore] POST /equity with hero_cards, board_cards, dead_cards")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
