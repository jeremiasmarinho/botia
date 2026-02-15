"""Vision YOLO — Os Olhos do Titan.

Localiza a janela do emulador Android (LDPlayer / Memu / BlueStacks),
captura apenas a região dela via ``mss`` e executa inferência YOLO para
detectar cartas, botões de ação, pot, stack e indicadores de turno.

Fluxo principal::

    EmulatorWindow.find()  →  captura mss  →  YOLO predict  →  DetectionFrame

Classes públicas
----------------
* :class:`EmulatorWindow` — localiza/rastreia a janela do emulador.
* :class:`VisionYolo`     — captura + inferência YOLO → :class:`DetectionFrame`.
* :class:`DetectionFrame` — resultado bruto de uma inferência.

Variáveis de ambiente
---------------------
``TITAN_EMULATOR_TITLE``     Termo de busca no título da janela (default ``LDPlayer``).
``TITAN_YOLO_MODEL``         Caminho do arquivo ``.pt`` de pesos YOLO.
``TITAN_YOLO_CONFIDENCE``    Confiança mínima para detecções (default ``0.35``).
``TITAN_VISION_TARGET_FPS``  FPS alvo para captura (default ``30``).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Imports opcionais — falham graciosamente para ambientes sem GUI / CI.
# ---------------------------------------------------------------------------
try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    import mss as _mss_module
except Exception:  # pragma: no cover
    _mss_module = None  # type: ignore[assignment]

try:
    import pygetwindow as gw  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    gw = None  # type: ignore[assignment]


# ═══════════════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class DetectionItem:
    """Um único objeto detectado pelo modelo YOLO.

    Coordenadas são **relativas à janela do emulador** (não à tela inteira).

    Attributes:
        label:      Classe predita pelo YOLO (ex: ``Ah``, ``fold``, ``pot_120``).
        confidence: Confiança da detecção [0.0 – 1.0].
        cx:         Centro X (pixels, relativo à janela).
        cy:         Centro Y (pixels, relativo à janela).
        w:          Largura da bounding box.
        h:          Altura da bounding box.
    """

    label: str
    confidence: float
    cx: int
    cy: int
    w: int
    h: int


@dataclass(slots=True)
class DetectionFrame:
    """Resultado completo de uma captura + inferência YOLO.

    Attributes:
        detections:     Lista de todos os objetos detectados.
        frame_width:    Largura do frame capturado (pixels).
        frame_height:   Altura do frame capturado (pixels).
        inference_ms:   Tempo de inferência YOLO (milissegundos).
        timestamp:      ``time.perf_counter()`` do momento da captura.
        window_left:    Posição X absoluta da janela na tela.
        window_top:     Posição Y absoluta da janela na tela.
    """

    detections: list[DetectionItem] = field(default_factory=list)
    frame_width: int = 0
    frame_height: int = 0
    inference_ms: float = 0.0
    timestamp: float = 0.0
    window_left: int = 0
    window_top: int = 0

    # -- Helpers de consulta rápida -----------------------------------------

    def labels_by_prefix(self, prefix: str) -> list[DetectionItem]:
        """Retorna detecções cujo label começa com *prefix* (case-insensitive)."""
        prefix_lower = prefix.lower()
        return [d for d in self.detections if d.label.lower().startswith(prefix_lower)]

    def best_by_label(self, label: str) -> DetectionItem | None:
        """Retorna a detecção de maior confiança para *label* exato."""
        best: DetectionItem | None = None
        for d in self.detections:
            if d.label.lower() == label.lower():
                if best is None or d.confidence > best.confidence:
                    best = d
        return best

    def to_screen_coords(self, cx: int, cy: int) -> tuple[int, int]:
        """Converte coordenadas relativas à janela → absolutas na tela."""
        return (self.window_left + cx, self.window_top + cy)


# ═══════════════════════════════════════════════════════════════════════════
# EmulatorWindow — localização e rastreamento da janela do emulador
# ═══════════════════════════════════════════════════════════════════════════

class EmulatorWindow:
    """Encontra e rastreia a janela do emulador Android no Windows.

    Usa ``pygetwindow`` para buscar pelo título (substring match).
    Mantém cache da última posição/dimensão para re-uso entre frames.

    Args:
        title_pattern: Substring a procurar no título da janela
                       (ex: ``"LDPlayer"``, ``"MEmu"``, ``"BlueStacks"``).
    """

    def __init__(self, title_pattern: str | None = None) -> None:
        self.title_pattern = (
            title_pattern
            or os.getenv("TITAN_EMULATOR_TITLE", "LDPlayer").strip()
            or "LDPlayer"
        )
        self._left: int = 0
        self._top: int = 0
        self._width: int = 0
        self._height: int = 0
        self._handle: Any = None
        self._last_find_ok: bool = False

    # -- Localização --------------------------------------------------------

    def find(self) -> bool:
        """Tenta localizar a janela pelo título.

        Atualiza as propriedades internas (posição, tamanho).
        Retorna ``True`` se a janela foi encontrada e está visível.
        """
        if gw is None:
            self._last_find_ok = False
            return False

        try:
            windows = gw.getWindowsWithTitle(self.title_pattern)
        except Exception:
            self._last_find_ok = False
            return False

        if not windows:
            self._last_find_ok = False
            return False

        # Pega a primeira janela que bater com o título.
        win = windows[0]
        try:
            self._left = int(win.left)
            self._top = int(win.top)
            self._width = int(win.width)
            self._height = int(win.height)
            self._handle = win
            self._last_find_ok = self._width > 0 and self._height > 0
        except Exception:
            self._last_find_ok = False

        return self._last_find_ok

    # -- Propriedades -------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        """Verdadeiro se a última chamada a :meth:`find` localizou a janela."""
        return self._last_find_ok

    @property
    def left(self) -> int:
        return self._left

    @property
    def top(self) -> int:
        return self._top

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def region(self) -> dict[str, int] | None:
        """Retorna dict compatível com ``mss`` ou ``None`` se janela não encontrada.

        Formato: ``{"left": X, "top": Y, "width": W, "height": H}``.
        """
        if not self._last_find_ok:
            return None
        return {
            "left": self._left,
            "top": self._top,
            "width": self._width,
            "height": self._height,
        }

    def to_screen_coords(self, x_rel: int, y_rel: int) -> tuple[int, int]:
        """Converte coordenada relativa à janela para absoluta na tela.

        Args:
            x_rel: Posição X dentro da janela.
            y_rel: Posição Y dentro da janela.

        Returns:
            Tupla ``(x_abs, y_abs)`` na tela.
        """
        return (self._left + x_rel, self._top + y_rel)

    def __repr__(self) -> str:
        state = "visible" if self._last_find_ok else "not_found"
        return (
            f"EmulatorWindow(title={self.title_pattern!r}, {state}, "
            f"region=({self._left},{self._top},{self._width}x{self._height}))"
        )


# ═══════════════════════════════════════════════════════════════════════════
# VisionYolo — captura + inferência YOLO
# ═══════════════════════════════════════════════════════════════════════════

class VisionYolo:
    """Captura a janela do emulador e executa inferência YOLO.

    Ciclo típico::

        yolo = VisionYolo(model_path="best.pt", emulator=emu)
        frame = yolo.detect()          # DetectionFrame com coordenadas relativas
        abs_x, abs_y = frame.to_screen_coords(det.cx, det.cy)

    Args:
        model_path:  Caminho do arquivo ``.pt`` de pesos YOLO.
        emulator:    Instância de :class:`EmulatorWindow` já configurada.
        confidence:  Confiança mínima para aceitar detecções (default ``0.35``).
        target_fps:  FPS alvo; usado para rate-limit entre capturas (default ``30``).
    """

    def __init__(
        self,
        model_path: str = "",
        emulator: EmulatorWindow | None = None,
        confidence: float | None = None,
        target_fps: float | None = None,
    ) -> None:
        self.model_path = model_path or os.getenv("TITAN_YOLO_MODEL", "")
        self.emulator = emulator or EmulatorWindow()
        self.confidence = confidence or self._env_float("TITAN_YOLO_CONFIDENCE", 0.35)
        self.target_fps = target_fps or self._env_float("TITAN_VISION_TARGET_FPS", 30.0)
        self._frame_interval = 1.0 / max(self.target_fps, 1.0)
        self._last_capture_time: float = 0.0

        # Modelo YOLO (lazy-load na primeira chamada a detect())
        self._model: Any = None
        self._model_loaded: bool = False
        self._model_error: str = ""

    # -- Carregamento do modelo (lazy) --------------------------------------

    def _load_model(self) -> bool:
        """Carrega o modelo YOLO. Retorna True em caso de sucesso."""
        if self._model_loaded:
            return self._model is not None

        self._model_loaded = True
        if not self.model_path:
            self._model_error = "TITAN_YOLO_MODEL não definido"
            return False

        try:
            from ultralytics import YOLO  # type: ignore[import-untyped]

            self._model = YOLO(self.model_path)
            return True
        except Exception as err:
            self._model_error = str(err)
            self._model = None
            return False

    # -- Captura de tela ----------------------------------------------------

    def capture_frame(self) -> Any:
        """Captura a região do emulador via ``mss``.

        Retorna um array numpy BGR (H×W×3) ou ``None`` se falhar.
        Respeita o rate-limit de ``target_fps``.
        """
        # Rate-limit para não sobrecarregar a CPU
        now = time.perf_counter()
        elapsed = now - self._last_capture_time
        if elapsed < self._frame_interval:
            time.sleep(self._frame_interval - elapsed)
        self._last_capture_time = time.perf_counter()

        if _mss_module is None or np is None:
            return None

        # Atualiza posição da janela do emulador a cada frame
        if not self.emulator.find():
            return None

        region = self.emulator.region
        if region is None:
            return None

        try:
            with _mss_module.mss() as sct:
                raw = sct.grab(region)
                # mss retorna BGRA; remove canal alpha → BGR para YOLO
                frame = np.array(raw)[:, :, :3]
                return frame
        except Exception:
            return None

    # -- Inferência YOLO ----------------------------------------------------

    def detect(self) -> DetectionFrame:
        """Pipeline completo: captura → YOLO → :class:`DetectionFrame`.

        Se a janela não for encontrada ou o modelo não estiver carregado,
        retorna um ``DetectionFrame`` vazio.
        """
        # Garante que o modelo está carregado
        if not self._load_model():
            return DetectionFrame(timestamp=time.perf_counter())

        # Captura frame da janela do emulador
        frame = self.capture_frame()
        if frame is None:
            return DetectionFrame(timestamp=time.perf_counter())

        height, width = frame.shape[:2]
        t_start = time.perf_counter()

        # Inferência YOLO (verbose=False para não poluir stdout)
        try:
            results = self._model.predict(
                source=frame,
                conf=self.confidence,
                verbose=False,
            )
        except Exception:
            return DetectionFrame(
                frame_width=width,
                frame_height=height,
                timestamp=t_start,
                window_left=self.emulator.left,
                window_top=self.emulator.top,
            )

        inference_ms = (time.perf_counter() - t_start) * 1000.0

        # Extrai detecções do primeiro resultado
        detections: list[DetectionItem] = []
        if results and len(results) > 0:
            result = results[0]
            names: dict[int, str] = getattr(result, "names", {})
            boxes = getattr(result, "boxes", None)

            if boxes is not None:
                cls_list = boxes.cls.tolist() if boxes.cls is not None else []
                xyxy_list = boxes.xyxy.tolist() if boxes.xyxy is not None else []
                conf_list = boxes.conf.tolist() if boxes.conf is not None else []

                for idx, (cls_idx, xyxy) in enumerate(zip(cls_list, xyxy_list)):
                    label = names.get(int(cls_idx), "")
                    conf = float(conf_list[idx]) if idx < len(conf_list) else 0.0

                    x1, y1, x2, y2 = (float(v) for v in xyxy)
                    cx = int((x1 + x2) / 2.0)
                    cy = int((y1 + y2) / 2.0)
                    w = int(x2 - x1)
                    h = int(y2 - y1)

                    detections.append(DetectionItem(
                        label=label,
                        confidence=conf,
                        cx=cx,
                        cy=cy,
                        w=w,
                        h=h,
                    ))

        return DetectionFrame(
            detections=detections,
            frame_width=width,
            frame_height=height,
            inference_ms=inference_ms,
            timestamp=t_start,
            window_left=self.emulator.left,
            window_top=self.emulator.top,
        )

    # -- Utilitários --------------------------------------------------------

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        """Lê uma variável de ambiente como float, com fallback."""
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default
