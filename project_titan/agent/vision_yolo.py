"""Vision YOLO — Os Olhos do Titan.

Localiza a janela do emulador Android (LDPlayer9) via ``win32gui``,
calcula a área útil (canvas) removendo as bordas/chrome do emulador,
captura apenas a ROI da mesa de poker via ``mss`` e executa inferência
YOLO para detectar cartas, botões de ação, pot, stack e indicadores de turno.

Arquitetura de coordenadas
--------------------------
O YOLO recebe uma imagem recortada (ROI = Region of Interest) que
corresponde **apenas** à área do jogo, sem a barra de título nem a
toolbar lateral do emulador.  As detecções retornadas são relativas
a essa ROI.  O método :meth:`VisionYolo.to_screen_coords` converte
de volta para coordenadas absolutas do monitor, adicionando o
``offset_x`` / ``offset_y`` (posição da ROI na tela).

Fluxo principal::

    win32gui.FindWindow  →  _calculate_game_area  →  mss.grab(ROI)
    →  YOLO predict  →  DetectionFrame  →  to_screen_coords

Classes públicas
----------------
* :class:`EmulatorWindow` — localiza/rastreia a janela do LDPlayer via win32gui.
* :class:`VisionYolo`     — captura ROI + inferência YOLO → :class:`DetectionFrame`.
* :class:`DetectionFrame` — resultado bruto de uma inferência.

Variáveis de ambiente
---------------------
``TITAN_EMULATOR_TITLE``     Termo de busca no título da janela (default ``LDPlayer``).
``TITAN_YOLO_MODEL``         Caminho do arquivo ``.pt`` de pesos YOLO.
``TITAN_YOLO_CONFIDENCE``    Confiança mínima para detecções (default ``0.35``).
``TITAN_VISION_TARGET_FPS``  FPS alvo para captura (default ``30``).
``TITAN_CHROME_TOP``         Pixels a remover do topo — barra de título (default ``35``).
``TITAN_CHROME_BOTTOM``      Pixels a remover de baixo — toolbar (default ``0``).
``TITAN_CHROME_LEFT``         Pixels a remover da esquerda — sidebar (default ``0``).
``TITAN_CHROME_RIGHT``        Pixels a remover da direita — sidebar (default ``38``).
"""

from __future__ import annotations

import ctypes
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
    import win32gui  # type: ignore[import-untyped]
    import win32con  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    win32gui = None  # type: ignore[assignment]
    win32con = None  # type: ignore[assignment]


# ═══════════════════════════════════════════════════════════════════════════
# Constantes — bordas padrão do LDPlayer9
# ═══════════════════════════════════════════════════════════════════════════

_DEFAULT_CHROME_TOP: int = 35       # Barra de título do LDPlayer
_DEFAULT_CHROME_BOTTOM: int = 0     # Sem barra inferior normalmente
_DEFAULT_CHROME_LEFT: int = 0       # Sem sidebar esquerda
_DEFAULT_CHROME_RIGHT: int = 38     # Toolbar lateral direita do LDPlayer


# ═══════════════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class DetectionItem:
    """Um único objeto detectado pelo modelo YOLO.

    Coordenadas são **relativas à ROI capturada** (área do jogo, sem
    chrome do emulador — não são relativas à tela inteira).

    Attributes:
        label:      Classe predita pelo YOLO (ex: ``Ah``, ``fold``, ``pot_120``).
        confidence: Confiança da detecção [0.0 – 1.0].
        cx:         Centro X (pixels, relativo à ROI do jogo).
        cy:         Centro Y (pixels, relativo à ROI do jogo).
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
        frame_width:    Largura da ROI capturada (pixels).
        frame_height:   Altura da ROI capturada (pixels).
        inference_ms:   Tempo de inferência YOLO (milissegundos).
        timestamp:      ``time.perf_counter()`` do momento da captura.
        window_left:    Posição X absoluta da ROI na tela (offset_x).
        window_top:     Posição Y absoluta da ROI na tela (offset_y).
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
        """Converte coordenadas relativas à ROI → absolutas na tela.

        Soma o offset da janela (window_left, window_top) que já incorpora
        a remoção do chrome, retornando a posição absoluta no monitor
        necessária para o GhostMouse clicar no ponto correto.
        """
        return (self.window_left + cx, self.window_top + cy)


# ═══════════════════════════════════════════════════════════════════════════
# EmulatorWindow — localização e rastreamento da janela via win32gui
# ═══════════════════════════════════════════════════════════════════════════

class EmulatorWindow:
    """Encontra e rastreia a janela do emulador Android (LDPlayer9) no Windows.

    Usa ``win32gui.EnumWindows`` para buscar pelo título (substring match).
    Calcula automaticamente a **área útil** (canvas) removendo as bordas
    do emulador (chrome) para que o YOLO receba apenas a imagem da mesa.

    Dimensões do chrome (valores padrão para LDPlayer9):
        - Topo: 35px (barra de título)
        - Direita: 38px (toolbar lateral)
        - Esquerda: 0px
        - Inferior: 0px

    Args:
        title_pattern:  Substring a procurar no título da janela
                        (ex: ``"LDPlayer"``, ``"MEmu"``).
        chrome_top:     Pixels do chrome no topo (barra de título).
        chrome_bottom:  Pixels do chrome na parte inferior.
        chrome_left:    Pixels do chrome à esquerda.
        chrome_right:   Pixels do chrome à direita (toolbar).
    """

    def __init__(
        self,
        title_pattern: str | None = None,
        chrome_top: int | None = None,
        chrome_bottom: int | None = None,
        chrome_left: int | None = None,
        chrome_right: int | None = None,
    ) -> None:
        self.title_pattern = (
            title_pattern
            or os.getenv("TITAN_EMULATOR_TITLE", "LDPlayer").strip()
            or "LDPlayer"
        )

        # Chrome do emulador (bordas a remover)
        self._chrome_top = chrome_top if chrome_top is not None else self._env_int("TITAN_CHROME_TOP", _DEFAULT_CHROME_TOP)
        self._chrome_bottom = chrome_bottom if chrome_bottom is not None else self._env_int("TITAN_CHROME_BOTTOM", _DEFAULT_CHROME_BOTTOM)
        self._chrome_left = chrome_left if chrome_left is not None else self._env_int("TITAN_CHROME_LEFT", _DEFAULT_CHROME_LEFT)
        self._chrome_right = chrome_right if chrome_right is not None else self._env_int("TITAN_CHROME_RIGHT", _DEFAULT_CHROME_RIGHT)

        # Coordenadas absolutas da janela inteira
        self._win_left: int = 0
        self._win_top: int = 0
        self._win_width: int = 0
        self._win_height: int = 0

        # Coordenadas absolutas da ROI (canvas do jogo, sem chrome)
        self._offset_x: int = 0   # left absoluto da ROI na tela
        self._offset_y: int = 0   # top absoluto da ROI na tela
        self._canvas_w: int = 0   # largura da ROI
        self._canvas_h: int = 0   # altura da ROI

        self._hwnd: int = 0
        self._last_find_ok: bool = False

    # -- Localização via win32gui -------------------------------------------

    def find(self) -> bool:
        """Localiza a janela do emulador pelo título parcial via win32gui.

        Usa ``win32gui.EnumWindows`` para iterar todas as janelas visíveis
        e encontrar a primeira cujo título contém ``title_pattern``.
        Atualiza posição, tamanho e calcula a ROI automaticamente.

        Returns:
            ``True`` se a janela foi encontrada e a ROI é válida.
        """
        if win32gui is None:
            self._last_find_ok = False
            return False

        found_hwnd: int = 0
        pattern_lower = self.title_pattern.lower()

        def _enum_callback(hwnd: int, _extra: Any) -> bool:
            """Callback para EnumWindows — para na primeira janela válida."""
            nonlocal found_hwnd
            if not win32gui.IsWindowVisible(hwnd):
                return True  # continua iterando
            title = win32gui.GetWindowText(hwnd)
            if pattern_lower in title.lower():
                found_hwnd = hwnd
                return False  # para a enumeração
            return True

        try:
            win32gui.EnumWindows(_enum_callback, None)
        except Exception:
            # EnumWindows levanta exceção quando o callback retorna False
            # (é o comportamento normal para "parar" a enumeração).
            pass

        if found_hwnd == 0:
            self._last_find_ok = False
            return False

        self._hwnd = found_hwnd

        # Obtém o retângulo da janela (coordenadas absolutas da tela)
        try:
            rect = win32gui.GetWindowRect(self._hwnd)
            self._win_left = int(rect[0])
            self._win_top = int(rect[1])
            self._win_width = int(rect[2] - rect[0])
            self._win_height = int(rect[3] - rect[1])
        except Exception:
            self._last_find_ok = False
            return False

        if self._win_width <= 0 or self._win_height <= 0:
            self._last_find_ok = False
            return False

        # Calcula a ROI (área útil do jogo) removendo o chrome
        self._calculate_game_area()
        self._last_find_ok = self._canvas_w > 0 and self._canvas_h > 0
        return self._last_find_ok

    def find_window(self) -> bool:
        """Alias para :meth:`find` — mantém compatibilidade com run_titan."""
        return self.find()

    # -- Cálculo da Área Útil (Canvas) --------------------------------------

    def _calculate_game_area(self) -> None:
        """Calcula a região de interesse (ROI) removendo o chrome do emulador.

        A partir das coordenadas absolutas da janela e dos valores de chrome
        configurados, determina:

        - ``offset_x`` / ``offset_y``: posição absoluta na tela onde começa
          o canvas do jogo (sem a barra de título nem a toolbar).
        - ``canvas_w`` / ``canvas_h``: dimensões da ROI em pixels.

        Matemática::

            offset_x = win_left + chrome_left
            offset_y = win_top  + chrome_top
            canvas_w = win_width  - chrome_left - chrome_right
            canvas_h = win_height - chrome_top  - chrome_bottom

        Exemplo para LDPlayer9 (window 900×1600, chrome top=35, right=38):

            offset_x = win_left + 0   = win_left
            offset_y = win_top  + 35
            canvas_w = 900 - 0 - 38   = 862
            canvas_h = 1600 - 35 - 0  = 1565
        """
        self._offset_x = self._win_left + self._chrome_left
        self._offset_y = self._win_top + self._chrome_top
        self._canvas_w = max(0, self._win_width - self._chrome_left - self._chrome_right)
        self._canvas_h = max(0, self._win_height - self._chrome_top - self._chrome_bottom)

    # -- Propriedades -------------------------------------------------------

    @property
    def is_visible(self) -> bool:
        """Verdadeiro se a última chamada a :meth:`find` localizou a janela."""
        return self._last_find_ok

    @property
    def hwnd(self) -> int:
        """Handle nativo da janela (HWND) para operações win32."""
        return self._hwnd

    @property
    def left(self) -> int:
        """Posição X absoluta da janela inteira."""
        return self._win_left

    @property
    def top(self) -> int:
        """Posição Y absoluta da janela inteira."""
        return self._win_top

    @property
    def width(self) -> int:
        """Largura total da janela (incluindo chrome)."""
        return self._win_width

    @property
    def height(self) -> int:
        """Altura total da janela (incluindo chrome)."""
        return self._win_height

    @property
    def offset_x(self) -> int:
        """Posição X absoluta da ROI (canvas) na tela."""
        return self._offset_x

    @property
    def offset_y(self) -> int:
        """Posição Y absoluta da ROI (canvas) na tela."""
        return self._offset_y

    @property
    def canvas_width(self) -> int:
        """Largura da ROI (área do jogo, sem chrome)."""
        return self._canvas_w

    @property
    def canvas_height(self) -> int:
        """Altura da ROI (área do jogo, sem chrome)."""
        return self._canvas_h

    @property
    def region(self) -> dict[str, int] | None:
        """Retorna dict compatível com ``mss`` para a **ROI** (sem chrome).

        Formato: ``{"left": offset_x, "top": offset_y, "width": canvas_w, "height": canvas_h}``.
        Retorna ``None`` se a janela não foi encontrada ou a ROI é inválida.
        """
        if not self._last_find_ok or self._canvas_w <= 0 or self._canvas_h <= 0:
            return None
        return {
            "left": self._offset_x,
            "top": self._offset_y,
            "width": self._canvas_w,
            "height": self._canvas_h,
        }

    @property
    def full_window_region(self) -> dict[str, int] | None:
        """Retorna dict compatível com ``mss`` para a janela inteira (com chrome).

        Útil para debug / screenshots completos.
        """
        if not self._last_find_ok:
            return None
        return {
            "left": self._win_left,
            "top": self._win_top,
            "width": self._win_width,
            "height": self._win_height,
        }

    def to_screen_coords(self, x_rel: int, y_rel: int) -> tuple[int, int]:
        """Converte coordenada relativa à ROI para absoluta na tela.

        O YOLO retorna posições relativas à imagem capturada (ROI).
        Este método soma o offset da ROI (``offset_x``, ``offset_y``)
        para retornar a coordenada absoluta do monitor, necessária
        para o GhostMouse clicar na posição correta.

        Args:
            x_rel: Posição X dentro da ROI (retornada pelo YOLO).
            y_rel: Posição Y dentro da ROI (retornada pelo YOLO).

        Returns:
            Tupla ``(x_abs, y_abs)`` em coordenadas absolutas do monitor.
        """
        return (self._offset_x + x_rel, self._offset_y + y_rel)

    def bring_to_front(self) -> bool:
        """Traz a janela do emulador para o primeiro plano.

        Útil antes de começar a captura para garantir que o emulador
        não está sobreposto por outra janela.

        Returns:
            ``True`` se a operação foi bem-sucedida.
        """
        if win32gui is None or self._hwnd == 0:
            return False
        try:
            win32gui.SetForegroundWindow(self._hwnd)
            return True
        except Exception:
            return False

    def __repr__(self) -> str:
        state = "visible" if self._last_find_ok else "not_found"
        return (
            f"EmulatorWindow(title={self.title_pattern!r}, {state}, "
            f"window=({self._win_left},{self._win_top},{self._win_width}x{self._win_height}), "
            f"canvas=({self._offset_x},{self._offset_y},{self._canvas_w}x{self._canvas_h}), "
            f"chrome=top:{self._chrome_top} bottom:{self._chrome_bottom} "
            f"left:{self._chrome_left} right:{self._chrome_right})"
        )

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        """Lê uma variável de ambiente como int, com fallback."""
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default


# ═══════════════════════════════════════════════════════════════════════════
# VisionYolo — captura ROI + inferência YOLO
# ═══════════════════════════════════════════════════════════════════════════

class VisionYolo:
    """Captura a ROI do jogo no emulador e executa inferência YOLO.

    Ciclo principal::

        yolo = VisionYolo(model_path="best.pt", emulator=emu)
        frame = yolo.detect()
        # frame.detections[i].cx/cy são relativos à ROI
        abs_x, abs_y = yolo.to_screen_coords(det.cx, det.cy)
        # abs_x, abs_y = coordenada do monitor para clique real

    A captura usa ``mss`` focada **apenas** na ROI do jogo (sem chrome),
    calculada pelo :class:`EmulatorWindow._calculate_game_area`.

    Assets:
        offset_x, offset_y: Posição absoluta da ROI na tela.
        Usados por :meth:`to_screen_coords` para converter coordenadas
        YOLO → coordenadas do monitor.

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

        # Offsets absolutos da ROI no monitor (atualizados a cada captura)
        self.offset_x: int = 0
        self.offset_y: int = 0

        # Modelo YOLO (lazy-load na primeira chamada a detect())
        self._model: Any = None
        self._model_loaded: bool = False
        self._model_error: str = ""

    # -- Localização da janela (delegada ao EmulatorWindow) -----------------

    def find_window(self) -> bool:
        """Localiza a janela do emulador e atualiza os offsets.

        Wrapper sobre :meth:`EmulatorWindow.find` seguido da atualização
        dos offsets internos ``offset_x`` e ``offset_y``.

        Returns:
            ``True`` se a janela foi localizada com ROI válida.
        """
        found = self.emulator.find()
        if found:
            self.offset_x = self.emulator.offset_x
            self.offset_y = self.emulator.offset_y
        return found

    # -- Conversão de coordenadas -------------------------------------------

    def to_screen_coords(self, relative_x: int, relative_y: int) -> tuple[int, int]:
        """Converte coordenadas relativas à ROI para absolutas no monitor.

        O YOLO retorna posições (cx, cy) relativas à imagem capturada,
        que é a ROI do jogo (sem chrome do emulador).  Este método soma
        o offset da ROI para obter a coordenada absoluta do monitor,
        necessária para o GhostMouse realizar o clique.

        Matemática::

            screen_x = self.offset_x + relative_x
            screen_y = self.offset_y + relative_y

        Onde:
            - offset_x = window_left + chrome_left
            - offset_y = window_top  + chrome_top

        Args:
            relative_x: Posição X na imagem capturada (retornada pelo YOLO).
            relative_y: Posição Y na imagem capturada (retornada pelo YOLO).

        Returns:
            Tupla ``(screen_x, screen_y)`` em coordenadas absolutas do monitor.
        """
        return (self.offset_x + relative_x, self.offset_y + relative_y)

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

    # -- Captura de tela (ROI apenas) ---------------------------------------

    def capture_frame(self) -> Any:
        """Captura a ROI (área do jogo) do emulador via ``mss``.

        Usa o retângulo calculado por :meth:`EmulatorWindow._calculate_game_area`
        que exclui o chrome do emulador (barra de título, toolbar lateral).
        O YOLO recebe apenas a imagem limpa da mesa de poker.

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

        # Atualiza posição da janela e ROI a cada frame
        if not self.emulator.find():
            return None

        # Atualiza offsets internos para to_screen_coords
        self.offset_x = self.emulator.offset_x
        self.offset_y = self.emulator.offset_y

        # Captura apenas a ROI (area do jogo, sem chrome)
        roi_region = self.emulator.region
        if roi_region is None:
            return None

        try:
            with _mss_module.mss() as sct:
                raw = sct.grab(roi_region)
                # mss retorna BGRA; remove canal alpha → BGR para YOLO
                frame = np.array(raw)[:, :, :3]
                return frame
        except Exception:
            return None

    # -- Inferência YOLO ----------------------------------------------------

    def detect(self) -> DetectionFrame:
        """Pipeline completo: captura ROI → YOLO → :class:`DetectionFrame`.

        Se a janela não for encontrada ou o modelo não estiver carregado,
        retorna um ``DetectionFrame`` vazio.

        As coordenadas em ``DetectionFrame.detections`` são relativas à ROI.
        Use ``DetectionFrame.to_screen_coords(det.cx, det.cy)`` ou
        ``self.to_screen_coords(det.cx, det.cy)`` para converter para
        coordenadas absolutas do monitor.
        """
        # Garante que o modelo está carregado
        if not self._load_model():
            return DetectionFrame(timestamp=time.perf_counter())

        # Captura frame da ROI do jogo
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
                window_left=self.offset_x,
                window_top=self.offset_y,
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
            window_left=self.offset_x,
            window_top=self.offset_y,
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
