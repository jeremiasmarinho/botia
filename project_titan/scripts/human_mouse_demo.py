"""
Script de Demonstração — Movimento Humano (GhostMouse)
=====================================================

Este script demonstra as capacidades de humanização do mouse:
- Curvas Bézier (não é linha reta)
- Aceleração e desaceleração (ease-in/out)
- Micro-overshoots (errar o alvo e corrigir)
- Click hold log-normal (tempo de clique variável)
- Delays de "pensamento" (Poisson)

Este é um show visual para confirmar que o bot não se move como uma máquina.
"""

import sys
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def main():
    try:
        from agent.ghost_mouse import GhostMouse, ClickPoint, GhostMouseConfig
        from agent.vision_yolo import VisionYolo
    except ImportError as e:
        print(f"Erro ao importar: {e}")
        return

    print("\n" + "="*60)
    print("  PROJECT TITAN - GHOSTMOUSE HUMAN DEMO")
    print("="*60 + "\n")

    # 1. Localizar emulador
    vision = VisionYolo()
    print("[INFO] Procurando janela 'LDPlayer'...")
    if not vision.find_window():
        print("[ERRO] Janela 'LDPlayer' não encontrada! Por favor, abra o emulador.")
        return
    
    emu = vision.emulator
    print(f"[OK] Janela encontrada em ({emu.left}, {emu.top})")
    
    # 2. Configurar GhostMouse
    os.environ["TITAN_GHOST_MOUSE"] = "1"  # Forçar ativação
    config = GhostMouseConfig()
    config.overshoot_probability = 1.0  # Forçar overshoots para a demo
    config.idle_jitter_enabled = True
    gm = GhostMouse(config=config)

    # 3. Pontos de interesse fictícios no canvas do jogo (botões do PPPoker)
    # Coordenadas relativas ao internal canvas
    points = [
        ("FOLD (Button)", ClickPoint(emu.canvas_width * 0.15, emu.canvas_height * 0.85)),
        ("CALL (Button)", ClickPoint(emu.canvas_width * 0.50, emu.canvas_height * 0.85)),
        ("RAISE (Button)", ClickPoint(emu.canvas_width * 0.85, emu.canvas_height * 0.85)),
        ("CENTRO DA MESA", ClickPoint(emu.canvas_width * 0.50, emu.canvas_height * 0.50)),
    ]

    print("[INFO] Iniciando sequência de movimentos humanos...")
    print("[INFO] OBSERVE O MOUSE NO EMULADOR!\n")

    try:
        for name, pt in points:
            # Converter relativa -> tela
            screen_x = emu.left + emu.offset_x + pt.x
            screen_y = emu.top + emu.offset_y + pt.y
            
            target = ClickPoint(screen_x, screen_y)
            
            print(f"-> Movendo para {name}...")
            
            # Executar movimento e click
            gm.move_and_click_sequence([target], relative=False, inter_click_delay=(0.2, 0.4))
            
            # Jitter de repouso
            print("   (Repouso - Jitter de micromovimento...)")
            gm.idle_jitter()
            time.sleep(1)

        print("\n[OK] Demonstração concluída com sucesso!")
        print("[INFO] Viu as curvas e os pequenos erros (overshoots)? É assim que escapamos da detecção.")

    except KeyboardInterrupt:
        print("\n[INFO] Demo interrompida pelo usuário.")

if __name__ == "__main__":
    main()
