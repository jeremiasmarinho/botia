"""data_harvester.py — Captura frames reais do MuMu Player para fine-tuning.

Roda em background, captura o frame cru da surface nemuwin a cada N segundos
e salva em training_data/raw/ com timestamp.  Ideal para coletar dataset real
enquanto o PPPoker roda numa mesa.

Uso:
    python data_harvester.py                  # padrão: 5s, sem limite
    python data_harvester.py --interval 3     # a cada 3 segundos
    python data_harvester.py --max 200        # para após 200 frames
    python data_harvester.py --duration 600   # roda por 10 minutos

Ctrl+C para parar a qualquer momento.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Resolução Android-alvo ────────────────────────────────────────────
ANDROID_W = 720
ANDROID_H = 1280

# ── Pasta de saída ────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).resolve().parent / "training_data" / "raw"


def _find_nemuwin_hwnd() -> int | None:
    """Localiza o HWND da surface nemuwin do MuMu Player.

    Procura janelas top-level cujo título contém 'MuMu' (case-insensitive)
    e depois localiza o child com classe 'nemuwin'.  Fallback: maior child
    do MuMu principal.
    """
    user32 = ctypes.windll.user32

    # 1. Encontrar janelas top-level do MuMu
    main_hwnds: list[int] = []
    mumu_classes = {"Qt5156QWindowIcon", "Qt5154QWindowIcon", "Qt5QWindowIcon"}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _enum_top(hwnd: int, _lp: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        # Match por classe
        cname = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cname, 256)
        if cname.value in mumu_classes:
            main_hwnds.append(hwnd)
            return True
        # Match por título
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        if "mumu" in title.value.lower():
            main_hwnds.append(hwnd)
        return True

    user32.EnumWindows(_enum_top, 0)

    if not main_hwnds:
        return None

    # 2. Procurar child 'nemuwin' em cada janela principal
    for main_hwnd in main_hwnds:
        best_child: int | None = None
        best_area: int = 0

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def _enum_child(hwnd: int, _lp: int) -> bool:
            nonlocal best_child, best_area
            cname = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cname, 256)

            # Preferência: classe 'nemuwin'
            if cname.value.lower() == "nemuwin":
                best_child = hwnd
                best_area = 999_999_999  # prioridade máxima
                return True

            # Fallback: maior child visível
            if user32.IsWindowVisible(hwnd):
                rect = wt.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                area = rect.right * rect.bottom
                if area > best_area and best_area < 999_999_999:
                    best_area = area
                    best_child = hwnd
            return True

        user32.EnumChildWindows(main_hwnd, _enum_child, 0)

        if best_child is not None and best_child != 0:
            return best_child

    # 3. Fallback: usar a janela principal (sem child)
    return main_hwnds[0]


def _grab_frame(hwnd: int) -> "np.ndarray | None":
    """Captura o frame da surface nemuwin via mss e redimensiona para 720x1280."""
    import cv2
    import mss
    import numpy as np

    user32 = ctypes.windll.user32

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = _POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    cw, ch = rect.right, rect.bottom

    if cw <= 0 or ch <= 0:
        return None

    with mss.mss() as sct:
        monitor = {"left": pt.x, "top": pt.y, "width": cw, "height": ch}
        raw = np.array(sct.grab(monitor))

    # BGRA → BGR
    frame = raw[:, :, :3].copy()

    # Resize para resolução Android-nativa
    if frame.shape[1] != ANDROID_W or frame.shape[0] != ANDROID_H:
        frame = cv2.resize(frame, (ANDROID_W, ANDROID_H), interpolation=cv2.INTER_LINEAR)

    return frame


def _is_game_frame(frame: "np.ndarray") -> bool:
    """Heurística rápida: rejeita frames pretos/brancos/tela de loading."""
    import numpy as np

    gray_mean = float(np.mean(frame))
    # Frame todo preto ou branco → sem jogo
    if gray_mean < 15 or gray_mean > 245:
        return False

    # Verifica se tem alguma variância (não é cor sólida)
    gray_std = float(np.std(frame[:, :, 1]))  # canal G
    if gray_std < 8:
        return False

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Coleta frames reais do MuMu Player")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Intervalo entre capturas em segundos (default: 5)")
    parser.add_argument("--max", type=int, default=0,
                        help="Número máximo de frames (0 = sem limite)")
    parser.add_argument("--duration", type=float, default=0,
                        help="Duração máxima em segundos (0 = sem limite)")
    parser.add_argument("--no-filter", action="store_true",
                        help="Salvar todos os frames (sem filtro de qualidade)")
    args = parser.parse_args()

    # Imports pesados só quando necessário
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        print(f"[ERRO] Dependência faltando: {e}")
        print("       pip install opencv-python numpy mss")
        sys.exit(1)

    # Criar pasta de saída
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  DATA HARVESTER — Coleta de Frames MuMu Player")
    print("=" * 60)
    print(f"  Intervalo:  {args.interval}s")
    print(f"  Max frames: {args.max or 'ilimitado'}")
    print(f"  Duração:    {args.duration or 'ilimitada'}s")
    print(f"  Saída:      {OUTPUT_DIR}")
    print(f"  Filtro:     {'desligado' if args.no_filter else 'ligado'}")
    print("=" * 60)

    # Encontrar janela MuMu
    print("\n[...] Procurando janela do MuMu Player...")
    hwnd = _find_nemuwin_hwnd()
    if hwnd is None:
        print("[ERRO] MuMu Player não encontrado!")
        print("       Certifique-se de que o emulador está aberto e visível.")
        sys.exit(1)

    # Info da janela
    user32 = ctypes.windll.user32
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    cname = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cname, 256)
    print(f"[OK]  Janela encontrada: hwnd={hwnd}, classe='{cname.value}', "
          f"client={rect.right}x{rect.bottom}")

    # Primeiro frame de teste
    test_frame = _grab_frame(hwnd)
    if test_frame is None:
        print("[ERRO] Falha ao capturar frame de teste!")
        sys.exit(1)
    print(f"[OK]  Frame de teste: {test_frame.shape[1]}x{test_frame.shape[0]} "
          f"mean={np.mean(test_frame):.0f}")

    print(f"\n[>>>] Iniciando coleta — Ctrl+C para parar\n")

    count = 0
    skipped = 0
    start_time = time.time()
    last_hash = None

    try:
        while True:
            # Checar limites
            if args.max > 0 and count >= args.max:
                print(f"\n[FIM] Limite de {args.max} frames atingido.")
                break
            if args.duration > 0 and (time.time() - start_time) >= args.duration:
                print(f"\n[FIM] Duração de {args.duration}s atingida.")
                break

            frame = _grab_frame(hwnd)
            if frame is None:
                print(f"  [{_ts()}] WARN: falha na captura (janela minimizada?)")
                time.sleep(args.interval)
                continue

            # Filtro: rejeitar frames inválidos
            if not args.no_filter and not _is_game_frame(frame):
                skipped += 1
                if skipped % 5 == 1:
                    print(f"  [{_ts()}] skip (tela sem jogo) — {skipped} total")
                time.sleep(args.interval)
                continue

            # Detecção de duplicatas: hash rápido de região central
            # Evita salvar o mesmo frame estático repetidamente
            central = frame[400:500, 200:520]
            frame_hash = hash(central.tobytes()[:4096])
            if frame_hash == last_hash:
                skipped += 1
                time.sleep(args.interval)
                continue
            last_hash = frame_hash

            # Salvar
            count += 1
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"mumu_{ts}.png"
            filepath = OUTPUT_DIR / filename
            cv2.imwrite(str(filepath), frame)

            elapsed = time.time() - start_time
            fps_avg = count / elapsed if elapsed > 0 else 0
            size_kb = filepath.stat().st_size / 1024
            print(f"  [{_ts()}] #{count:04d}  {filename}  "
                  f"{size_kb:.0f}KB  skip={skipped}  avg={fps_avg:.2f}fps")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n\n[STOP] Interrompido pelo usuário.")

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"  RESUMO DA COLETA")
    print(f"{'=' * 60}")
    print(f"  Frames salvos:   {count}")
    print(f"  Frames filtrados:{skipped}")
    print(f"  Tempo total:     {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"  Pasta:           {OUTPUT_DIR}")
    if count > 0:
        total_size = sum(f.stat().st_size for f in OUTPUT_DIR.glob("mumu_*.png"))
        print(f"  Tamanho total:   {total_size / 1024 / 1024:.1f}MB")
    print(f"{'=' * 60}")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


if __name__ == "__main__":
    main()
