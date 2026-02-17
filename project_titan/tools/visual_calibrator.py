"""Calibrador visual interativo para geometria de clube.

Fluxo principal:
1) Selecionar retângulos (Hero, Board, Pot OCR) com clique-e-arraste.
2) Selecionar centros dos botões (FOLD, CALL, RAISE) com clique simples.
3) Persistir no ``config_club.yaml`` atualizando apenas campos de geometria.

Uso:
    python -m tools.visual_calibrator --image club_table_reference.png --config config_club.yaml

Fallbacks:
    - imagem: ``--image`` -> ``TITAN_IMAGE_PATH`` -> ``club_table_reference.png``
    - config: ``--config`` -> ``TITAN_CONFIG_FILE`` -> ``config_club.yaml``
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import cv2  # type: ignore[import-untyped]
except ImportError:
    cv2 = None  # type: ignore[assignment]

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    yaml = None  # type: ignore[assignment]


@dataclass(slots=True)
class CalibrationTarget:
    """Definição de um alvo calibrável na imagem."""

    key: str
    prompt: str
    mode: str  # "rect" | "point"


@dataclass(slots=True)
class RectXYWH:
    """Retângulo em coordenadas absolutas da imagem."""

    x: int
    y: int
    w: int
    h: int


@dataclass(slots=True)
class PointXY:
    """Ponto em coordenadas absolutas da imagem."""

    x: int
    y: int


class MouseRectSelector:
    """Selecionador de retângulo por clique-e-arraste com OpenCV."""

    def __init__(self) -> None:
        self.start_x = 0
        self.start_y = 0
        self.end_x = 0
        self.end_y = 0
        self.mouse_x = 0
        self.mouse_y = 0
        self.drawing = False
        self.has_rect = False
        self.last_click: PointXY | None = None

    def callback(self, event: int, x: int, y: int, _flags: int, _param: Any) -> None:
        """Mouse callback usado por ``cv2.setMouseCallback``."""
        self.mouse_x, self.mouse_y = x, y

        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing = True
            self.start_x, self.start_y = x, y
            self.end_x, self.end_y = x, y
            self.has_rect = False
            self.last_click = PointXY(x=x, y=y)
            return

        if event == cv2.EVENT_MOUSEMOVE and self.drawing:
            self.end_x, self.end_y = x, y
            return

        if event == cv2.EVENT_LBUTTONUP and self.drawing:
            self.drawing = False
            self.end_x, self.end_y = x, y
            self.has_rect = True
            self.last_click = PointXY(x=x, y=y)

    def get_point(self) -> PointXY | None:
        """Retorna o último ponto clicado."""
        return self.last_click

    def get_rect(self) -> RectXYWH | None:
        """Retorna retângulo normalizado (x,y,w,h) quando disponível."""
        if not self.has_rect:
            return None
        x1 = min(self.start_x, self.end_x)
        y1 = min(self.start_y, self.end_y)
        x2 = max(self.start_x, self.end_x)
        y2 = max(self.start_y, self.end_y)
        w = max(0, x2 - x1)
        h = max(0, y2 - y1)
        if w <= 0 or h <= 0:
            return None
        return RectXYWH(x=x1, y=y1, w=w, h=h)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project Titan Visual Calibrator")
    parser.add_argument("--image", default="", help="Imagem de referência para calibrar")
    parser.add_argument("--config", default="", help="Arquivo YAML alvo (default: config_club.yaml)")
    return parser.parse_args()


def _resolve_project_root() -> Path:
    """Resolve a raiz do projeto a partir deste módulo."""
    return Path(__file__).resolve().parent.parent


def _resolve_image_path(project_root: Path, image_arg: str) -> Path:
    """Resolve imagem via --image, TITAN_IMAGE_PATH ou fallback de clube."""
    candidate = (
        (image_arg or "").strip()
        or os.getenv("TITAN_IMAGE_PATH", "").strip()
        or "club_table_reference.png"
    )
    if not candidate:
        raise FileNotFoundError("Informe --image ou TITAN_IMAGE_PATH")
    path = Path(candidate)
    if not path.is_absolute():
        path = (project_root / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Imagem não encontrada: {path}")
    return path


def _resolve_config_path(project_root: Path, config_arg: str) -> Path:
    """Resolve config via --config ou TITAN_CONFIG_FILE ou config_club.yaml."""
    candidate = (
        (config_arg or "").strip()
        or os.getenv("TITAN_CONFIG_FILE", "").strip()
        or "config_club.yaml"
    )
    path = Path(candidate)
    if not path.is_absolute():
        path = (project_root / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config não encontrada: {path}")
    return path


def _default_targets() -> list[CalibrationTarget]:
    """Ordem de calibração solicitada ao operador."""
    return [
        CalibrationTarget("vision.hero_area", "Selecione a área das cartas do Hero", "rect"),
        CalibrationTarget("vision.board_area", "Selecione a área do Board (cartas comunitárias)", "rect"),
        CalibrationTarget("ocr.pot_region", "Selecione a área do Pote (OCR, somente números)", "rect"),
        CalibrationTarget("action.fold", "Clique no centro do botão FOLD", "point"),
        CalibrationTarget("action.call", "Clique no centro do botão CALL", "point"),
        CalibrationTarget("action.raise", "Clique no centro do botão RAISE", "point"),
    ]


def _draw_magnifier(frame: Any, source: Any, mouse_x: int, mouse_y: int) -> None:
    """Desenha uma lupa (zoom) no canto superior direito."""
    h, w = source.shape[:2]
    zoom_factor = 4
    lens_half = 18
    lens_x1 = max(0, mouse_x - lens_half)
    lens_y1 = max(0, mouse_y - lens_half)
    lens_x2 = min(w, mouse_x + lens_half)
    lens_y2 = min(h, mouse_y + lens_half)

    patch = source[lens_y1:lens_y2, lens_x1:lens_x2]
    if patch.size == 0:
        return

    zoom_w = 180
    zoom_h = 180
    inset = cv2.resize(patch, (zoom_w, zoom_h), interpolation=cv2.INTER_NEAREST)

    x2 = w - 10
    x1 = x2 - zoom_w
    y1 = 70
    y2 = y1 + zoom_h

    if x1 < 0 or y2 > frame.shape[0]:
        return

    frame[y1:y2, x1:x2] = inset
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)

    cx = x1 + zoom_w // 2
    cy = y1 + zoom_h // 2
    cv2.line(frame, (cx - 12, cy), (cx + 12, cy), (0, 0, 255), 1)
    cv2.line(frame, (cx, cy - 12), (cx, cy + 12), (0, 0, 255), 1)
    cv2.putText(
        frame,
        f"ZOOM x{zoom_factor} ({mouse_x},{mouse_y})",
        (x1, y1 - 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 255, 255),
        1,
        cv2.LINE_AA,
    )


def _draw_overlay(
    base_frame: Any,
    selector: MouseRectSelector,
    target: CalibrationTarget,
    accepted_rects: dict[str, RectXYWH],
    accepted_points: dict[str, PointXY],
) -> Any:
    """Desenha instruções e retângulos salvos/em edição no frame."""
    frame = base_frame.copy()
    h, w = frame.shape[:2]

    # Faixa superior de instruções
    cv2.rectangle(frame, (0, 0), (w, 60), (0, 0, 0), -1)
    cv2.addWeighted(frame, 0.85, base_frame.copy(), 0.15, 0, frame)

    text = f"{target.prompt}"
    controls = "ENTER=confirmar  R=resetar  S=pular  Q/ESC=sair"
    cv2.putText(frame, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, controls, (10, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1, cv2.LINE_AA)

    # Retângulos já aceitos
    for key, rect in accepted_rects.items():
        x, y, rw, rh = rect.x, rect.y, rect.w, rect.h
        cv2.rectangle(frame, (x, y), (x + rw, y + rh), (0, 200, 0), 2)
        cv2.putText(frame, key, (x, max(16, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 0), 1, cv2.LINE_AA)

    # Pontos já aceitos
    for key, point in accepted_points.items():
        cv2.circle(frame, (point.x, point.y), 7, (255, 120, 0), 2)
        cv2.putText(
            frame,
            f"{key} ({point.x},{point.y})",
            (point.x + 10, max(16, point.y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 120, 0),
            1,
            cv2.LINE_AA,
        )

    if target.mode == "rect":
        current_rect = selector.get_rect()
        if current_rect is not None:
            x, y, rw, rh = current_rect.x, current_rect.y, current_rect.w, current_rect.h
            cv2.rectangle(frame, (x, y), (x + rw, y + rh), (0, 140, 255), 2)
            cv2.putText(
                frame,
                f"x={x} y={y} w={rw} h={rh}",
                (x, min(h - 8, y + rh + 18)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 140, 255),
                1,
                cv2.LINE_AA,
            )
    else:
        point = selector.get_point()
        if point is not None:
            cv2.circle(frame, (point.x, point.y), 8, (0, 140, 255), 2)
            cv2.putText(
                frame,
                f"x={point.x} y={point.y}",
                (point.x + 10, min(h - 8, point.y + 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 140, 255),
                1,
                cv2.LINE_AA,
            )

    _draw_magnifier(frame, base_frame, selector.mouse_x, selector.mouse_y)

    return frame


def _run_calibration(
    image_path: Path,
    targets: list[CalibrationTarget],
) -> tuple[dict[str, RectXYWH], dict[str, PointXY]]:
    """Executa o loop de calibração por alvo (retângulo e ponto)."""
    frame = cv2.imread(str(image_path))
    if frame is None:
        raise RuntimeError(f"Falha ao abrir imagem: {image_path}")

    window_name = "Titan Visual Calibrator"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    selector = MouseRectSelector()
    cv2.setMouseCallback(window_name, selector.callback)

    accepted_rects: dict[str, RectXYWH] = {}
    accepted_points: dict[str, PointXY] = {}

    try:
        for target in targets:
            selector.has_rect = False
            selector.drawing = False
            selector.last_click = None
            print(f"[CALIB] {target.prompt}")

            while True:
                preview = _draw_overlay(frame, selector, target, accepted_rects, accepted_points)
                cv2.imshow(window_name, preview)
                key = cv2.waitKey(16) & 0xFF

                if key in (ord("q"), 27):
                    raise KeyboardInterrupt("Calibração interrompida pelo usuário")

                if key in (ord("r"),):
                    selector.has_rect = False
                    selector.drawing = False
                    continue

                if key in (ord("s"),):
                    print(f"[CALIB] Pulado: {target.key}")
                    break

                if key in (13, 10):  # Enter
                    if target.mode == "rect":
                        rect = selector.get_rect()
                        if rect is None:
                            print("[CALIB] Desenhe um retângulo antes de confirmar.")
                            continue
                        accepted_rects[target.key] = rect
                        print(f"[CALIB] OK {target.key} -> x={rect.x} y={rect.y} w={rect.w} h={rect.h}")
                    else:
                        point = selector.get_point()
                        if point is None:
                            print("[CALIB] Clique no ponto antes de confirmar.")
                            continue
                        accepted_points[target.key] = point
                        print(f"[CALIB] OK {target.key} -> x={point.x} y={point.y}")
                    break
    finally:
        cv2.destroyAllWindows()

    return accepted_rects, accepted_points


def _apply_updates(
    config_data: dict[str, Any],
    rect_updates: dict[str, RectXYWH],
    point_updates: dict[str, PointXY],
) -> dict[str, Any]:
    """Atualiza somente coordenadas das seções calibradas."""
    vision = config_data.setdefault("vision", {})
    vision_regions = vision.setdefault("regions", {})

    hero_rect = rect_updates.get("vision.hero_area")
    if hero_rect is not None:
        vision_regions["hero_area"] = {
            "x": hero_rect.x,
            "y": hero_rect.y,
            "w": hero_rect.w,
            "h": hero_rect.h,
        }

    board_rect = rect_updates.get("vision.board_area")
    if board_rect is not None:
        vision_regions["board_area"] = {
            "x": board_rect.x,
            "y": board_rect.y,
            "w": board_rect.w,
            "h": board_rect.h,
        }

    # OCR regions em formato string x,y,w,h
    ocr_section = config_data.setdefault("ocr", {})
    pot_rect = rect_updates.get("ocr.pot_region")
    if pot_rect is not None:
        ocr_section["pot_region"] = f"{pot_rect.x},{pot_rect.y},{pot_rect.w},{pot_rect.h}"
        ocr_section["pot_box"] = {
            "x": pot_rect.x,
            "y": pot_rect.y,
            "w": pot_rect.w,
            "h": pot_rect.h,
        }

    action_buttons = config_data.setdefault("action_buttons", {})
    action_coordinates = config_data.setdefault("action_coordinates", {})

    fold_point = point_updates.get("action.fold")
    if fold_point is not None:
        action_buttons["fold"] = [fold_point.x, fold_point.y]
        action_coordinates["fold"] = {"x": fold_point.x, "y": fold_point.y}

    call_point = point_updates.get("action.call")
    if call_point is not None:
        action_buttons["call"] = [call_point.x, call_point.y]
        action_coordinates["call"] = {"x": call_point.x, "y": call_point.y}

    raise_point = point_updates.get("action.raise")
    if raise_point is not None:
        action_buttons["raise_small"] = [raise_point.x, raise_point.y]
        action_buttons["raise_big"] = [raise_point.x, raise_point.y]
        action_coordinates["raise"] = {"x": raise_point.x, "y": raise_point.y}

    return config_data


def main() -> None:
    if cv2 is None:
        raise SystemExit("OpenCV não disponível. Instale opencv-python.")
    if yaml is None:
        raise SystemExit("PyYAML não disponível. Instale pyyaml.")

    args = _parse_args()
    project_root = _resolve_project_root()

    try:
        image_path = _resolve_image_path(project_root, args.image)
        config_path = _resolve_config_path(project_root, args.config)
    except FileNotFoundError as err:
        raise SystemExit(str(err)) from err

    with open(config_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Config inválida: {config_path}")

    print(f"[CALIB] Imagem: {image_path}")
    print(f"[CALIB] Config alvo: {config_path}")

    targets = _default_targets()
    try:
        rect_updates, point_updates = _run_calibration(image_path, targets)
    except KeyboardInterrupt:
        print("[CALIB] Cancelado sem salvar.")
        raise SystemExit(1)

    if not rect_updates and not point_updates:
        print("[CALIB] Nenhuma região confirmada. Nada a salvar.")
        raise SystemExit(0)

    data = _apply_updates(data, rect_updates, point_updates)

    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)

    print("[CALIB] Salvamento concluído.")
    print("[CALIB] Campos atualizados:")
    for key in sorted(rect_updates.keys()):
        rect = rect_updates[key]
        print(f"  - {key}: x={rect.x} y={rect.y} w={rect.w} h={rect.h}")
    for key in sorted(point_updates.keys()):
        point = point_updates[key]
        print(f"  - {key}: x={point.x} y={point.y}")


if __name__ == "__main__":
    main()
