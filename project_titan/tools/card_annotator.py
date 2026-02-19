"""
Project Titan — Card Annotator (Anotador de Cartas Interativo)

Ferramenta visual para anotar cartas de poker nos frames capturados.
Mostra cada imagem e permite ao usuário:
  1. Clicar num retângulo ao redor de cada carta
  2. Digitar o label (ex: Ah, Kd, 9s, Tc)
  3. Salvar anotações como labels YOLO (formato txt)

O anotador PRESERVA labels existentes (pot, stack, botões) do auto_labeler
e apenas ADICIONA as labels de cartas (classes 0-51).

Uso:
    python -m tools.card_annotator
    python -m tools.card_annotator --source data/to_annotate --config config_club.yaml
    python -m tools.card_annotator --start-from 50
    python -m tools.card_annotator --hero-only
    python -m tools.card_annotator --board-only

Controles na janela:
    - Clique esquerdo + arraste: desenhar bbox ao redor da carta
    - Após bbox: digite o label (ex: Ah, 9s) no terminal
    - N: próxima imagem (salva labels atuais)
    - P: imagem anterior
    - D: desfazer última anotação na imagem atual
    - S: salvar e continuar
    - Q: sair (salva antes)
    - H: anotar região hero (auto-crop da hero_area)
    - B: anotar região board (auto-crop da board_area)

Formato de label de carta:
    <Rank><Suit>
    Ranks: 2,3,4,5,6,7,8,9,T,J,Q,K,A
    Suits: c(clubs/paus), d(diamonds/ouros), h(hearts/copas), s(spades/espadas)
    Exemplos: Ah (Ás de copas), Kd (Rei de ouros), 9s (9 de espadas)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import cv2  # type: ignore[import-untyped]
    import numpy as np
except ImportError:
    print("[CARD-ANNOTATOR] ERRO: OpenCV não encontrado. Execute: pip install opencv-python")
    sys.exit(1)

# ── Card mapping ──────────────────────────────────────────────
RANKS = "23456789TJQKA"
SUITS = "cdhs"
SUIT_NAMES = {"c": "Paus", "d": "Ouros", "h": "Copas", "s": "Espadas"}
RANK_NAMES = {
    "2": "2", "3": "3", "4": "4", "5": "5", "6": "6",
    "7": "7", "8": "8", "9": "9", "T": "10",
    "J": "Valete", "Q": "Dama", "K": "Rei", "A": "Ás",
}

# Build card label → class_id mapping (must match data.yaml)
CARD_TO_CLASS: dict[str, int] = {}
_idx = 0
for _r in RANKS:
    for _s in SUITS:
        CARD_TO_CLASS[f"{_r}{_s}"] = _idx
        _idx += 1

CLASS_TO_CARD: dict[int, str] = {v: k for k, v in CARD_TO_CLASS.items()}

# Non-card classes (preserved from auto_labeler)
NON_CARD_CLASSES = {52, 53, 54, 55, 56, 57}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# ── Colors ────────────────────────────────────────────────────
COLOR_HERO = (0, 255, 0)       # green
COLOR_BOARD = (255, 200, 0)    # cyan/gold
COLOR_DRAWING = (0, 0, 255)    # red (while drawing)
COLOR_EXISTING = (255, 100, 0) # blue (existing non-card labels)
COLOR_TEXT = (255, 255, 255)


class CardAnnotator:
    """Interactive card annotation tool."""

    def __init__(
        self,
        source_dir: Path,
        label_dir: Path | None = None,
        config: dict | None = None,
        start_from: int = 0,
        hero_area: tuple[int, int, int, int] | None = None,
        board_area: tuple[int, int, int, int] | None = None,
    ) -> None:
        self.source_dir = source_dir
        self.label_dir = label_dir or source_dir
        self.config = config or {}
        self.hero_area = hero_area
        self.board_area = board_area

        # Collect image paths
        self.images = sorted([
            f for f in source_dir.iterdir()
            if f.suffix.lower() in IMAGE_EXTENSIONS
        ])
        if not self.images:
            print(f"[CARD-ANNOTATOR] Nenhuma imagem em {source_dir}")
            sys.exit(1)

        self.current_idx = min(start_from, len(self.images) - 1)
        self.annotations: dict[str, list[tuple[int, float, float, float, float]]] = {}

        # Drawing state
        self._drawing = False
        self._start_x = 0
        self._start_y = 0
        self._end_x = 0
        self._end_y = 0
        self._display_frame: np.ndarray | None = None
        self._base_frame: np.ndarray | None = None

        self.window_name = "TITAN: Card Annotator"
        self.stats = {"images_annotated": 0, "cards_annotated": 0, "skipped": 0}

    def _label_path(self, image_path: Path) -> Path:
        """Get label file path for an image."""
        return self.label_dir / (image_path.stem + ".txt")

    def _load_existing_labels(self, image_path: Path) -> list[tuple[int, float, float, float, float]]:
        """Load existing labels from txt file."""
        label_file = self._label_path(image_path)
        labels: list[tuple[int, float, float, float, float]] = []
        if not label_file.exists():
            return labels
        try:
            with open(label_file, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        cls_id = int(parts[0])
                        cx, cy, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                        labels.append((cls_id, cx, cy, w, h))
        except Exception:
            pass
        return labels

    def _save_labels(self, image_path: Path, labels: list[tuple[int, float, float, float, float]]) -> None:
        """Save labels to txt file."""
        label_file = self._label_path(image_path)
        label_file.parent.mkdir(parents=True, exist_ok=True)
        with open(label_file, "w", encoding="utf-8") as f:
            for cls_id, cx, cy, w, h in labels:
                f.write(f"{cls_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")

    def _get_card_labels(self, labels: list[tuple[int, float, float, float, float]]) -> list[tuple[int, float, float, float, float]]:
        """Filter to only card labels (class 0-51)."""
        return [(c, cx, cy, w, h) for c, cx, cy, w, h in labels if c < 52]

    def _get_noncard_labels(self, labels: list[tuple[int, float, float, float, float]]) -> list[tuple[int, float, float, float, float]]:
        """Filter to only non-card labels (class 52+)."""
        return [(c, cx, cy, w, h) for c, cx, cy, w, h in labels if c >= 52]

    def _draw_annotations(self, frame: np.ndarray, labels: list[tuple[int, float, float, float, float]]) -> np.ndarray:
        """Draw all annotations on frame."""
        display = frame.copy()
        h, w = display.shape[:2]

        for cls_id, cx, cy, bw, bh in labels:
            x1 = int((cx - bw / 2) * w)
            y1 = int((cy - bh / 2) * h)
            x2 = int((cx + bw / 2) * w)
            y2 = int((cy + bh / 2) * h)

            if cls_id < 52:
                # Card label
                card_name = CLASS_TO_CARD.get(cls_id, f"?{cls_id}")
                color = COLOR_HERO
                cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
                # Label text with background
                text = f"{card_name}"
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(display, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
                cv2.putText(display, text, (x1 + 2, y1 - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
            else:
                # Non-card label (pot, stack, buttons)
                _NON_CARD_NAMES = {
                    52: "fold", 53: "check", 54: "raise",
                    55: "raise_2x", 56: "raise_2_5x", 57: "raise_pot",
                    58: "raise_confirm", 59: "allin",
                    60: "pot", 61: "stack",
                }
                name = _NON_CARD_NAMES.get(cls_id, f"cls{cls_id}")
                color = COLOR_EXISTING
                cv2.rectangle(display, (x1, y1), (x2, y2), color, 1)
                cv2.putText(display, name, (x1, y1 - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

        # Draw hero_area and board_area guides
        if self.hero_area:
            hx, hy, hw, hh = self.hero_area
            cv2.rectangle(display, (hx, hy), (hx + hw, hy + hh), (0, 255, 255), 1)
            cv2.putText(display, "HERO", (hx, hy - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

        if self.board_area:
            bx, by, baw, bah = self.board_area
            cv2.rectangle(display, (bx, by), (bx + baw, by + bah), (255, 255, 0), 1)
            cv2.putText(display, "BOARD", (bx, by - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)

        return display

    def _draw_hud(self, display: np.ndarray, labels: list[tuple[int, float, float, float, float]]) -> np.ndarray:
        """Draw HUD info bar at bottom."""
        h, w = display.shape[:2]
        n_cards = len(self._get_card_labels(labels))
        n_other = len(self._get_noncard_labels(labels))
        img_name = self.images[self.current_idx].name

        # Info bar
        bar_h = 40
        cv2.rectangle(display, (0, h - bar_h), (w, h), (30, 30, 30), -1)

        info = (
            f"[{self.current_idx + 1}/{len(self.images)}] {img_name}  |  "
            f"Cartas: {n_cards}  Outros: {n_other}  |  "
            f"N=prox  P=ant  D=desfazer  S=salvar  Q=sair"
        )
        cv2.putText(display, info, (8, h - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, COLOR_TEXT, 1)

        return display

    def _mouse_callback(self, event: int, x: int, y: int, flags: int, param: object) -> None:
        """Handle mouse events for bbox drawing."""
        if event == cv2.EVENT_LBUTTONDOWN:
            self._drawing = True
            self._start_x = x
            self._start_y = y
            self._end_x = x
            self._end_y = y

        elif event == cv2.EVENT_MOUSEMOVE and self._drawing:
            self._end_x = x
            self._end_y = y
            # Draw rectangle preview
            if self._base_frame is not None:
                preview = self._base_frame.copy()
                cv2.rectangle(preview, (self._start_x, self._start_y),
                              (self._end_x, self._end_y), COLOR_DRAWING, 2)
                self._display_frame = preview

        elif event == cv2.EVENT_LBUTTONUP:
            self._drawing = False
            self._end_x = x
            self._end_y = y

    def _ask_card_label(self) -> str | None:
        """Ask user for card label in terminal."""
        print()
        print(f"  Digite o label da carta (ex: Ah, 9s, Kd, Tc)")
        print(f"  Ranks: {RANKS}  |  Suits: c=paus d=ouros h=copas s=espadas")
        print(f"  Enter vazio = cancelar bbox")
        label = input("  > ").strip().upper()

        if not label:
            return None

        # Normalize: first char rank, second char suit (lowercase)
        if len(label) == 2:
            rank = label[0]
            suit = label[1].lower()
            if rank == "1" and suit == "0":
                # user typed "10" — need more input
                print("  Para 10, use T (ex: Th, Ts)")
                return None
            if rank in RANKS and suit in SUITS:
                return f"{rank}{suit}"

        # Try common aliases
        aliases = {"10": "T", "V": "J", "D": "Q", "R": "K"}
        if len(label) >= 2:
            r = aliases.get(label[:-1], label[0])
            s = label[-1].lower()
            if r in RANKS and s in SUITS:
                return f"{r}{s}"

        print(f"  Label inválido: '{label}'. Use formato <Rank><Suit> (ex: Ah, 9s)")
        return None

    def _bbox_to_yolo(self, x1: int, y1: int, x2: int, y2: int, img_w: int, img_h: int) -> tuple[float, float, float, float]:
        """Convert pixel bbox to YOLO normalized format."""
        # Ensure proper ordering
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1

        cx = ((x1 + x2) / 2.0) / img_w
        cy = ((y1 + y2) / 2.0) / img_h
        w = (x2 - x1) / img_w
        h = (y2 - y1) / img_h

        return (
            max(0.0, min(1.0, cx)),
            max(0.0, min(1.0, cy)),
            max(0.001, min(1.0, w)),
            max(0.001, min(1.0, h)),
        )

    def run(self) -> None:
        """Main annotation loop."""
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.window_name, self._mouse_callback)

        print()
        print("=" * 60)
        print("  TITAN Card Annotator — Anotador de Cartas")
        print("=" * 60)
        print(f"  Imagens: {len(self.images)} em {self.source_dir}")
        print(f"  Labels:  {self.label_dir}")
        if self.hero_area:
            print(f"  Hero:    {self.hero_area}")
        if self.board_area:
            print(f"  Board:   {self.board_area}")
        print()
        print("  Fluxo: clique+arraste no canto da carta → solte → digite o label")
        print("  Dica: foque no canto superior-esquerdo (15% visível em PLO6)")
        print("=" * 60)

        while 0 <= self.current_idx < len(self.images):
            image_path = self.images[self.current_idx]
            frame = cv2.imread(str(image_path))
            if frame is None:
                print(f"  Falha ao abrir: {image_path.name}")
                self.current_idx += 1
                continue

            img_h, img_w = frame.shape[:2]

            # Load existing labels
            labels = self._load_existing_labels(image_path)
            session_cards: list[tuple[int, float, float, float, float]] = []

            # Render
            annotated = self._draw_annotations(frame, labels)
            annotated = self._draw_hud(annotated, labels)
            self._base_frame = annotated.copy()
            self._display_frame = annotated.copy()

            print(f"\n  [{self.current_idx + 1}/{len(self.images)}] {image_path.name}")
            existing_cards = self._get_card_labels(labels)
            if existing_cards:
                card_names = [CLASS_TO_CARD.get(c, "?") for c, *_ in existing_cards]
                print(f"  Cartas já anotadas: {', '.join(card_names)}")

            while True:
                if self._display_frame is not None:
                    cv2.imshow(self.window_name, self._display_frame)

                key = cv2.waitKey(50) & 0xFF

                # Check if bbox was just drawn (mouse released)
                if (not self._drawing and
                    self._start_x != self._end_x and
                    self._start_y != self._end_y and
                    abs(self._end_x - self._start_x) > 5 and
                    abs(self._end_y - self._start_y) > 5):

                    card_label = self._ask_card_label()
                    if card_label and card_label in CARD_TO_CLASS:
                        cls_id = CARD_TO_CLASS[card_label]
                        cx, cy, w, h = self._bbox_to_yolo(
                            self._start_x, self._start_y,
                            self._end_x, self._end_y,
                            img_w, img_h,
                        )
                        new_label = (cls_id, cx, cy, w, h)
                        labels.append(new_label)
                        session_cards.append(new_label)
                        self.stats["cards_annotated"] += 1

                        card_full = f"{RANK_NAMES[card_label[0]]} de {SUIT_NAMES[card_label[1]]}"
                        print(f"    ✓ {card_label} ({card_full}) — class {cls_id}")

                        # Re-render
                        annotated = self._draw_annotations(frame, labels)
                        annotated = self._draw_hud(annotated, labels)
                        self._base_frame = annotated.copy()
                        self._display_frame = annotated.copy()

                    # Reset drawing state
                    self._start_x = self._end_x = 0
                    self._start_y = self._end_y = 0

                if key == ord("n") or key == ord("N"):
                    # Next image
                    if session_cards:
                        self._save_labels(image_path, labels)
                        self.stats["images_annotated"] += 1
                        print(f"    Salvo: {len(session_cards)} cartas novas")
                    else:
                        self.stats["skipped"] += 1
                    self.current_idx += 1
                    break

                elif key == ord("p") or key == ord("P"):
                    # Previous image
                    if session_cards:
                        self._save_labels(image_path, labels)
                    self.current_idx = max(0, self.current_idx - 1)
                    break

                elif key == ord("d") or key == ord("D"):
                    # Undo last card annotation
                    if session_cards:
                        removed = session_cards.pop()
                        labels.remove(removed)
                        cls_id_r = removed[0]
                        print(f"    ↩ Desfeito: {CLASS_TO_CARD.get(cls_id_r, '?')}")
                        annotated = self._draw_annotations(frame, labels)
                        annotated = self._draw_hud(annotated, labels)
                        self._base_frame = annotated.copy()
                        self._display_frame = annotated.copy()
                    else:
                        print("    Nada para desfazer.")

                elif key == ord("s") or key == ord("S"):
                    # Save current
                    self._save_labels(image_path, labels)
                    if session_cards:
                        self.stats["images_annotated"] += 1
                    print(f"    Salvo: {len(self._get_card_labels(labels))} cartas total")

                elif key == ord("q") or key == ord("Q"):
                    # Quit
                    if session_cards:
                        self._save_labels(image_path, labels)
                        self.stats["images_annotated"] += 1
                    self.current_idx = len(self.images)  # exit outer loop
                    break

                elif key == 27:  # ESC
                    self.current_idx = len(self.images)
                    break

        cv2.destroyAllWindows()
        self._print_summary()

    def _print_summary(self) -> None:
        """Print annotation summary."""
        print()
        print("=" * 60)
        print("  RESUMO DA ANOTAÇÃO")
        print("=" * 60)
        print(f"  Imagens anotadas: {self.stats['images_annotated']}")
        print(f"  Cartas anotadas:  {self.stats['cards_annotated']}")
        print(f"  Imagens puladas:  {self.stats['skipped']}")
        print()
        print("  Próximos passos:")
        print("    1. Revise as anotações com: python -m tools.card_annotator --start-from <n>")
        print("    2. Recrie o dataset: python -m training.prepare_dataset --include-unlabeled")
        print("    3. Treine: python training/train_yolo.py --epochs 150 --name titan_v2")
        print("=" * 60)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TITAN Card Annotator")
    parser.add_argument("--source", type=str, default="data/to_annotate",
                        help="Diretório com imagens (default: data/to_annotate)")
    parser.add_argument("--labels", type=str, default=None,
                        help="Diretório para salvar labels (default: mesmo que --source)")
    parser.add_argument("--config", type=str, default="config_club.yaml",
                        help="Config YAML para regiões calibradas")
    parser.add_argument("--start-from", type=int, default=0, dest="start_from",
                        help="Índice da imagem para começar")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    source = Path(args.source)
    if not source.is_absolute():
        source = (PROJECT_ROOT / source).resolve()

    label_dir = Path(args.labels) if args.labels else source
    if not label_dir.is_absolute():
        label_dir = (PROJECT_ROOT / label_dir).resolve()

    if not source.exists():
        print(f"[CARD-ANNOTATOR] Diretório não encontrado: {source}")
        sys.exit(1)

    # Load config for hero/board areas
    hero_area = None
    board_area = None
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (PROJECT_ROOT / config_path).resolve()

    config: dict = {}
    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            vision = config.get("vision", {})
            regions = vision.get("regions", {})
            ha = regions.get("hero_area", {})
            if ha and all(k in ha for k in ("x", "y", "w", "h")):
                hero_area = (int(ha["x"]), int(ha["y"]), int(ha["w"]), int(ha["h"]))
            ba = regions.get("board_area", {})
            if ba and all(k in ba for k in ("x", "y", "w", "h")):
                board_area = (int(ba["x"]), int(ba["y"]), int(ba["w"]), int(ba["h"]))
        except Exception as err:
            print(f"[CARD-ANNOTATOR] Aviso config: {err}")

    annotator = CardAnnotator(
        source_dir=source,
        label_dir=label_dir,
        config=config,
        start_from=args.start_from,
        hero_area=hero_area,
        board_area=board_area,
    )
    annotator.run()


if __name__ == "__main__":
    main()
