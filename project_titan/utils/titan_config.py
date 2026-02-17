"""Titan Config — Carregador centralizado do config.yaml.

Lê o arquivo ``config.yaml`` na raiz do projeto e disponibiliza todas as
configurações como um dicionário plano.  Variáveis de ambiente ``TITAN_*``
**sempre têm prioridade** sobre o YAML — o arquivo é o fallback amigável.

Uso::

    from utils.titan_config import cfg

    print(cfg.get_float("poker.aggression_level"))  # 0.5
    print(cfg.get_str("vision.emulator_title"))      # "LDPlayer"
    print(cfg.get_bool("overlay.enabled"))            # False

Variável de ambiente equivalente: ``TITAN_POKER_AGGRESSION_LEVEL``
  → a chave YAML ``poker.aggression_level`` vira ``TITAN_POKER_AGGRESSION_LEVEL``.

O carregamento é lazy (na primeira chamada) e thread-safe.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any


def _find_config_path() -> Path:
    """Resolve o caminho do config.yaml subindo até a raiz do projeto."""
    env_path = os.getenv("TITAN_CONFIG_FILE", "").strip()
    if env_path:
        return Path(env_path)

    # Procura config.yaml partindo do diretório deste módulo
    start = Path(__file__).resolve().parent
    for ancestor in [start, start.parent, start.parent.parent]:
        candidate = ancestor / "config.yaml"
        if candidate.exists():
            return candidate

    # Fallback: raiz do projeto (project_titan/config.yaml)
    return start.parent / "config.yaml"


class TitanConfig:
    """Acesso centralizado a configurações com prioridade env > yaml.

    Attributes:
        _data: Dicionário bruto carregado do YAML.
        _loaded: Flag indicando se o YAML já foi lido.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded: bool = False
        self._lock = threading.Lock()

    # ── Carregamento lazy ─────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Carrega o YAML uma única vez de forma thread-safe."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load()
            self._loaded = True

    def _load(self) -> None:
        """Lê e parseia o config.yaml."""
        config_path = _find_config_path()
        if not config_path.exists():
            self._data = {}
            return
        try:
            import yaml  # type: ignore[import-untyped]
            with open(config_path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            if isinstance(raw, dict):
                self._data = raw
            else:
                self._data = {}
        except Exception:
            self._data = {}

    def reload(self) -> None:
        """Força re-leitura do arquivo (útil para hot-reload)."""
        with self._lock:
            self._loaded = False
            self._load()
            self._loaded = True

    # ── Acesso por chave pontilhada ───────────────────────────────

    def _resolve(self, dotted_key: str) -> Any:
        """Resolve ``poker.aggression_level`` → data[poker][aggression_level]."""
        self._ensure_loaded()
        parts = dotted_key.split(".")
        node: Any = self._data
        for part in parts:
            if isinstance(node, dict):
                node = node.get(part)
            else:
                return None
        return node

    @staticmethod
    def _env_key(dotted_key: str) -> str:
        """Converte ``poker.aggression_level`` → ``TITAN_POKER_AGGRESSION_LEVEL``."""
        return "TITAN_" + dotted_key.upper().replace(".", "_")

    # ── Getters tipados ───────────────────────────────────────────

    def get_str(self, key: str, default: str = "") -> str:
        """Retorna string: env > yaml > default."""
        env_val = os.getenv(self._env_key(key), "").strip()
        if env_val:
            return env_val
        yaml_val = self._resolve(key)
        if yaml_val is not None:
            return str(yaml_val)
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        """Retorna inteiro: env > yaml > default."""
        env_val = os.getenv(self._env_key(key), "").strip()
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        yaml_val = self._resolve(key)
        if yaml_val is not None:
            try:
                return int(yaml_val)
            except (ValueError, TypeError):
                pass
        return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Retorna float: env > yaml > default."""
        env_val = os.getenv(self._env_key(key), "").strip()
        if env_val:
            try:
                return float(env_val)
            except ValueError:
                pass
        yaml_val = self._resolve(key)
        if yaml_val is not None:
            try:
                return float(yaml_val)
            except (ValueError, TypeError):
                pass
        return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Retorna booleano: env > yaml > default."""
        env_val = os.getenv(self._env_key(key), "").strip().lower()
        if env_val:
            if env_val in {"1", "true", "yes", "on"}:
                return True
            if env_val in {"0", "false", "no", "off"}:
                return False
        yaml_val = self._resolve(key)
        if yaml_val is not None:
            if isinstance(yaml_val, bool):
                return yaml_val
            raw = str(yaml_val).strip().lower()
            if raw in {"1", "true", "yes", "on"}:
                return True
            if raw in {"0", "false", "no", "off"}:
                return False
        return default

    def get_list(self, key: str, default: list[Any] | None = None) -> list[Any]:
        """Retorna lista: yaml (env não suportado para listas) > default."""
        yaml_val = self._resolve(key)
        if isinstance(yaml_val, list):
            return yaml_val
        return default if default is not None else []

    def get_dict(self, key: str) -> dict[str, Any]:
        """Retorna seção inteira como dicionário."""
        yaml_val = self._resolve(key)
        if isinstance(yaml_val, dict):
            return dict(yaml_val)
        return {}

    def get_raw(self, key: str, default: Any = None) -> Any:
        """Retorna o valor bruto sem conversão de tipo."""
        env_val = os.getenv(self._env_key(key), "").strip()
        if env_val:
            return env_val
        yaml_val = self._resolve(key)
        return yaml_val if yaml_val is not None else default

    # ── repr ──────────────────────────────────────────────────────

    def __repr__(self) -> str:
        self._ensure_loaded()
        sections = list(self._data.keys())
        return f"<TitanConfig sections={sections}>"


# ── Singleton global ─────────────────────────────────────────────
cfg = TitanConfig()
