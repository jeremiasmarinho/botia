from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from utils.config import ServerConfig
from utils.logger import TitanLogger

try:
    import redis
except Exception:
    redis = None

try:
    import zmq
except Exception:
    zmq = None

_log = TitanLogger("HiveBrain")


@dataclass(slots=True)
class AgentSession:
    agent_id: str
    table_id: str
    updated_at: float
    payload: dict[str, Any]


class HiveBrain:
    def __init__(self, bind_address: str, redis_url: str = "redis://127.0.0.1:6379/0", ttl_seconds: int = 5) -> None:
        self.bind_address = bind_address
        self.redis_url = redis_url
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.sessions: dict[str, AgentSession] = {}
        self._redis_client: Any | None = None
        self._connect_redis()

    def _connect_redis(self) -> None:
        if redis is None:
            self._redis_client = None
            return

        try:
            client = redis.Redis.from_url(self.redis_url, decode_responses=True)
            client.ping()
            self._redis_client = client
        except Exception:
            self._redis_client = None

    def _session_key(self, table_id: str, agent_id: str) -> str:
        return f"titan:squad:{table_id}:{agent_id}"

    @staticmethod
    def _normalize_cards(raw_cards: Any) -> list[str]:
        if not isinstance(raw_cards, list):
            return []

        cards: list[str] = []
        for item in raw_cards:
            if not isinstance(item, str):
                continue
            card = item.strip().upper().replace("10", "T")
            if len(card) != 2:
                continue
            rank = card[0]
            suit = card[1].lower()
            if rank not in "23456789TJQKA" or suit not in "cdhs":
                continue
            normalized = f"{rank}{suit}"
            if normalized not in cards:
                cards.append(normalized)
        return cards

    def _prune_local_sessions(self) -> None:
        now = time.time()
        expired = [
            agent_id
            for agent_id, session in self.sessions.items()
            if (now - session.updated_at) > self.ttl_seconds
        ]
        for agent_id in expired:
            self.sessions.pop(agent_id, None)

    def _upsert_redis_session(self, session: AgentSession) -> None:
        if self._redis_client is None:
            return

        key = self._session_key(session.table_id, session.agent_id)
        payload = {
            "agent_id": session.agent_id,
            "table_id": session.table_id,
            "updated_at": session.updated_at,
            "cards": session.payload.get("cards", []),
        }
        self._redis_client.setex(key, self.ttl_seconds, json.dumps(payload))

    def _partners_from_redis(self, table_id: str, agent_id: str) -> tuple[list[str], list[str]]:
        if self._redis_client is None:
            return [], []

        pattern = self._session_key(table_id, "*")
        partners: list[str] = []
        dead_cards: list[str] = []

        for key in self._redis_client.scan_iter(match=pattern):
            payload_raw = self._redis_client.get(key)
            if payload_raw is None:
                continue
            try:
                payload = json.loads(payload_raw)
            except Exception:
                continue

            current_agent = str(payload.get("agent_id", "")).strip()
            if not current_agent or current_agent == agent_id:
                continue

            partners.append(current_agent)
            cards = self._normalize_cards(payload.get("cards", []))
            for card in cards:
                if card not in dead_cards:
                    dead_cards.append(card)

        return partners, dead_cards

    def _partners_from_memory(self, table_id: str, agent_id: str) -> tuple[list[str], list[str]]:
        self._prune_local_sessions()
        partners: list[str] = []
        dead_cards: list[str] = []

        for other_agent_id, session in self.sessions.items():
            if session.table_id != table_id or other_agent_id == agent_id:
                continue
            partners.append(other_agent_id)
            cards = self._normalize_cards(session.payload.get("cards", []))
            for card in cards:
                if card not in dead_cards:
                    dead_cards.append(card)

        return partners, dead_cards

    def _handle_checkin(self, request: dict[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        agent_id = str(request.get("agent_id", "")).strip() or "unknown"
        table_id = str(request.get("table_id", "")).strip() or "table_default"
        cards = self._normalize_cards(request.get("cards", []))
        active_players = max(int(request.get("active_players", 0)), 0)

        self.register_agent(agent_id=agent_id, table_id=table_id, payload={"cards": cards})
        partners, dead_cards = self._partners_from_redis(table_id=table_id, agent_id=agent_id)

        if not partners and not dead_cards:
            partners, dead_cards = self._partners_from_memory(table_id=table_id, agent_id=agent_id)

        # Collusion obfuscation: if exactly 2 bots at the table and only
        # 2 active players remain (heads-up), signal them to play
        # aggressively against each other so observers see genuine combat.
        heads_up_obfuscation = False
        total_bots_at_table = len(partners) + 1  # current agent + partners
        if total_bots_at_table >= 2 and active_players == 2:
            heads_up_obfuscation = True

        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        mode = "squad" if partners else "solo"
        return {
            "ok": True,
            "mode": mode,
            "agent_id": agent_id,
            "table_id": table_id,
            "partners": partners,
            "dead_cards": dead_cards,
            "heads_up_obfuscation": heads_up_obfuscation,
            "latency_ms": latency_ms,
        }

    def start(self) -> None:
        if zmq is None:
            raise RuntimeError("pyzmq não disponível. Instale dependências com requirements.txt")

        context = zmq.Context.instance()

        def _create_socket() -> Any:
            sock = context.socket(zmq.REP)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.RCVTIMEO, 1000)
            sock.bind(self.bind_address)
            return sock

        socket = _create_socket()

        _log.highlight(f"Listening on {self.bind_address}")
        backend = "redis" if self._redis_client is not None else "memory"
        _log.info(f"Squad backend={backend}  ttl={self.ttl_seconds}s")

        max_reconnects = 10
        reconnect_count = 0

        try:
            while True:
                try:
                    request = socket.recv_json()
                    reconnect_count = 0  # reset on success
                except zmq.error.Again:
                    continue
                except zmq.ZMQError as zmq_err:
                    reconnect_count += 1
                    _log.error(f"ZMQ socket error ({zmq_err}). reconnect attempt {reconnect_count}/{max_reconnects}")
                    if reconnect_count > max_reconnects:
                        _log.error("max reconnect attempts reached. shutting down.")
                        break
                    try:
                        socket.close(0)
                    except Exception:
                        pass
                    import time as _t
                    _t.sleep(min(reconnect_count * 0.5, 5.0))
                    try:
                        socket = _create_socket()
                        _log.success("reconnected successfully")
                    except Exception as rebind_err:
                        _log.error(f"rebind failed: {rebind_err}")
                    continue
                except Exception as error:
                    _log.error(f"invalid_request: {error}")
                    socket.send_json({"ok": False, "error": f"invalid_request: {error}"})
                    continue

                message_type = str(request.get("type", "checkin")).strip().lower()
                if message_type == "health":
                    socket.send_json({"ok": True, "status": "ok"})
                    continue

                if message_type == "checkin":
                    response = self._handle_checkin(request)
                    mode = response.get("mode", "solo")
                    agent_id = response.get("agent_id", "?")
                    latency = response.get("latency_ms", 0)
                    if mode == "squad":
                        partners = response.get("partners", [])
                        _log.highlight(f"Agente {agent_id} conectado -- GOD MODE ativado  partners={partners}  latency={latency}ms")
                    else:
                        _log.success(f"Agente {agent_id} conectado -- modo solo  latency={latency}ms")
                    if response.get("heads_up_obfuscation"):
                        _log.warn(f"Agente {agent_id}: obfuscacao heads-up ativa -- forcando agressividade")
                    socket.send_json(response)
                    continue

                _log.warn(f"unsupported message type: {message_type}")
                socket.send_json({"ok": False, "error": f"unsupported_type: {message_type}"})
        finally:
            socket.close(0)

    def register_agent(self, agent_id: str, table_id: str, payload: dict[str, Any]) -> None:
        session = AgentSession(
            agent_id=agent_id,
            table_id=table_id,
            updated_at=time.time(),
            payload=payload,
        )
        self.sessions[agent_id] = session
        self._upsert_redis_session(session)


if __name__ == "__main__":
    config = ServerConfig()
    server = HiveBrain(bind_address=config.zmq_bind, redis_url=config.redis_url)
    server.start()
