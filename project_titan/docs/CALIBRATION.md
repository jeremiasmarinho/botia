# Calibração Rápida — Project Titan

Este guia é para alinhar visualmente coordenadas **X, Y, W, H** antes da coleta de dados/treino YOLO.

## 1) Setup da imagem de referência

1. Abra o emulador/cliente de poker na mesa alvo (preferência: Play Money).
2. Tire um print da mesa.
3. Salve como `table_reference.png` na raiz de `project_titan`.

## 2) Abrir overlay estático (sem abrir o jogo)

Use o Cockpit com imagem fixa:

```bash
python titan_control.py --image_path table_reference.png
```

No menu, escolha **8. Overlay Standalone (Testar Visão)**.

A janela de calibração mostra:

- pontos dos botões (`action_buttons`),
- regiões OCR (`ocr`),
- HUD lateral,
- grade opcional de pixels (`overlay.show_grid`).

Feche com **Q**.

## 3) Chaves de coordenadas no `config.yaml`

## Botões de ação (X, Y)

```yaml
action_buttons:
  fold: [600, 700]
  call: [800, 700]
  raise_small: [1000, 700]
  raise_big: [1000, 700]
```

- Cada item é `[x, y]`.
- Ajuste até os círculos laranja ficarem exatamente sobre os botões reais.

## OCR (X, Y, W, H)

```yaml
ocr:
  pot_region: "360,255,180,54"
  stack_region: "330,610,220,56"
  call_region: "450,690,180,54"
```

- Formato: `"x,y,w,h"`.
- Ajuste até os retângulos tracejados cobrirem corretamente os textos de pot/stack/call.

## Janela útil do emulador (recorte/canvas)

```yaml
vision:
  chrome_top: 35
  chrome_bottom: 0
  chrome_left: 0
  chrome_right: 38
```

- Esses valores removem bordas/toolbars do emulador.
- Se toda a geometria estiver deslocada, ajuste estes campos primeiro.

## ROI de referência (calibração)

```yaml
vision:
  roi:
    left: 0
    top: 0
    width: 0
    height: 0
```

- Preset de referência para documentação/calibração visual.
- Útil para registrar a área alvo da mesa em cada resolução.

## Grid de pixels (opcional)

```yaml
overlay:
  show_grid: true
  grid_size: 50
```

- `show_grid`: liga grade no Terminator Vision.
- `grid_size`: distância entre linhas (pixels).

## 4) Loop de calibração recomendado

1. Abra opção 8 com `table_reference.png`.
2. Ajuste `config.yaml`.
3. Salve.
4. Reabra opção 8.
5. Repita até alinhamento cirúrgico.

## 5) Coleta de dados (após alinhar)

No Cockpit:

- opção **2. Iniciar Coleta de Dados (Observar Mesa)**

Ou direto:

```powershell
.\scripts\start_squad.ps1 -CollectData -Overlay
```

Rode em modo espectador por ~10 minutos para iniciar o dataset.
