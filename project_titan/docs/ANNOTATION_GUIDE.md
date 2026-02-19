# Guia de Anotação — Project Titan (YOLO Dataset)

## Objetivo

Criar um dataset anotado com bounding boxes para treinar o YOLOv8 a detectar
cartas de poker (PLO6), botões de ação, pot e stack em capturas de emulador.

## 1. Preparar imagens para anotação

```powershell
# Copiar frames de data/raw/ para data/to_annotate/ (com dedup)
python -m tools.label_assist

# Com resize para 640x640 (opcional, para treino mais rápido)
python -m tools.label_assist --resize 640

# Verificar progresso
python -m tools.label_assist --stats

# Exportar classes.txt avulso
python -m tools.label_assist --export-classes classes.txt
```

O script cria:

- `data/to_annotate/` com as imagens + labels `.txt` vazios
- `data/to_annotate/classes.txt` com as 62 classes na ordem correta
- `data/to_annotate/manifest.csv` para controlo de progresso

## 2. Auto-Labeler v0 (acelerador inicial)

Antes de anotar manualmente, corra o auto-labeler para gerar labels aproximados
das regiões calibradas (hero, board, pot, botões):

```powershell
# Gerar labels automáticos baseados nas regiões do config_club.yaml
python -m tools.auto_labeler

# Com config diferente
python -m tools.auto_labeler --config config.yaml

# Apenas verificar o que seria gerado (dry-run)
python -m tools.auto_labeler --dry-run
```

O auto-labeler gera bounding boxes para:

- **pot** (classe 60) — da região `ocr.pot_region` ou `ocr.pot_box`
- **stack** (classe 61) — da região `ocr.stack_region`
- **fold** (classe 52) — do centro `action_coordinates.fold`
- **check** (classe 53) — do centro `action_coordinates.call`
- **raise** (classe 54) — do centro `action_coordinates.raise`

> As cartas (classes 0-51) **não são geradas** automaticamente —
> precisam de anotação manual porque dependem de reconhecimento visual.

## 3. Ferramenta de anotação recomendada

### Opção A: Roboflow (recomendado para velocidade)

1. Criar conta em [roboflow.com](https://roboflow.com)
2. Novo projecto → Object Detection
3. Upload das imagens de `data/to_annotate/`
4. Upload do `classes.txt` como lista de classes
5. Anotar com a ferramenta visual (drag bounding boxes)
6. Export → formato **YOLOv8** → download ZIP
7. Extrair para `datasets/titan_cards/`

**Vantagem:** interface web rápida, smart label assist, augmentation automática.

### Opção B: LabelImg (local, offline)

```powershell
pip install labelimg
labelimg data/to_annotate/ data/to_annotate/classes.txt
```

1. Definir formato de saída: **YOLO** (menu View → YOLO)
2. Carregar directório de imagens
3. Anotar cada imagem (W para criar bbox, Ctrl+S para guardar)

**Vantagem:** 100% local, sem upload de dados.

### Opção C: CVAT (self-hosted, equipas)

Indicado para equipas com muitas imagens. Setup Docker disponível em
[github.com/cvat-ai/cvat](https://github.com/cvat-ai/cvat).

## 4. Lista completa das 62 classes

A ordem **deve** coincidir exactamente com `training/data.yaml`.

| ID | Classe | Descrição |
|----|--------|-----------|
| 0 | `2c` | 2 de Paus |
| 1 | `2d` | 2 de Ouros |
| 2 | `2h` | 2 de Copas |
| 3 | `2s` | 2 de Espadas |
| 4 | `3c` | 3 de Paus |
| 5 | `3d` | 3 de Ouros |
| 6 | `3h` | 3 de Copas |
| 7 | `3s` | 3 de Espadas |
| 8 | `4c` | 4 de Paus |
| 9 | `4d` | 4 de Ouros |
| 10 | `4h` | 4 de Copas |
| 11 | `4s` | 4 de Espadas |
| 12 | `5c` | 5 de Paus |
| 13 | `5d` | 5 de Ouros |
| 14 | `5h` | 5 de Copas |
| 15 | `5s` | 5 de Espadas |
| 16 | `6c` | 6 de Paus |
| 17 | `6d` | 6 de Ouros |
| 18 | `6h` | 6 de Copas |
| 19 | `6s` | 6 de Espadas |
| 20 | `7c` | 7 de Paus |
| 21 | `7d` | 7 de Ouros |
| 22 | `7h` | 7 de Copas |
| 23 | `7s` | 7 de Espadas |
| 24 | `8c` | 8 de Paus |
| 25 | `8d` | 8 de Ouros |
| 26 | `8h` | 8 de Copas |
| 27 | `8s` | 8 de Espadas |
| 28 | `9c` | 9 de Paus |
| 29 | `9d` | 9 de Ouros |
| 30 | `9h` | 9 de Copas |
| 31 | `9s` | 9 de Espadas |
| 32 | `Tc` | 10 de Paus |
| 33 | `Td` | 10 de Ouros |
| 34 | `Th` | 10 de Copas |
| 35 | `Ts` | 10 de Espadas |
| 36 | `Jc` | J de Paus |
| 37 | `Jd` | J de Ouros |
| 38 | `Jh` | J de Copas |
| 39 | `Js` | J de Espadas |
| 40 | `Qc` | Q de Paus |
| 41 | `Qd` | Q de Ouros |
| 42 | `Qh` | Q de Copas |
| 43 | `Qs` | Q de Espadas |
| 44 | `Kc` | K de Paus |
| 45 | `Kd` | K de Ouros |
| 46 | `Kh` | K de Copas |
| 47 | `Ks` | K de Espadas |
| 48 | `Ac` | A de Paus |
| 49 | `Ad` | A de Ouros |
| 50 | `Ah` | A de Copas |
| 51 | `As` | A de Espadas |
| 52 | `fold` | Botão Fold |
| 53 | `check` | Botão Check / Call |
| 54 | `raise` | Botão Raise (abrir modal) |
| 55 | `raise_2x` | Preset Raise 2× |
| 56 | `raise_2_5x` | Preset Raise 2.5× |
| 57 | `raise_pot` | Preset Raise Pot |
| 58 | `raise_confirm` | Confirmar Raise |
| 59 | `allin` | Botão All-In |
| 60 | `pot` | Valor do pote |
| 61 | `stack` | Stack do jogador |

## 5. Dicas de anotação para PLO6

### Cartas (classes 0-51)

- Anotar apenas o **canto superior esquerdo** da carta (≈15% da área visível)
- Em PLO6 as cartas ficam sobrepostas — o modelo precisa aprender a ler cantos
- A bbox deve conter o rank + suit do canto (ex: `A♥`)
- Se a carta está completamente tapada, **não anotar**

### Botões (classes 52-59)

- Anotar o botão inteiro (texto + fundo colorido)
- `raise` (54) = botão que abre o modal de raise
- `raise_2x` (55) = preset 2× dentro do modal
- `raise_2_5x` (56) = preset 2.5× dentro do modal
- `raise_pot` (57) = preset Pot dentro do modal
- `raise_confirm` (58) = botão de confirmação dentro do modal
- `allin` (59) = botão All-In
- `check` (53) representa tanto Check como Call (mesmo botão no PPPoker)

### Pot e Stack (classes 60-61)

- Anotar a região do texto numérico
- Incluir apenas os dígitos e separadores, não o fundo decorativo

## 6. Pipeline completo (passo a passo)

```
data/raw/                     ← frames capturados (start_squad -CollectData)
    │
    ▼  python -m tools.label_assist
data/to_annotate/             ← imagens + labels vazios + classes.txt
    │
    ▼  python -m tools.auto_labeler
data/to_annotate/             ← labels parciais (pot, stack, botões)
    │
    ▼  Roboflow / LabelImg    ← anotação manual das cartas
data/to_annotate/             ← labels completos (62 classes)
    │
    ▼  python training/prepare_dataset.py --source data/to_annotate
datasets/titan_cards/         ← train/val/test splits
    │
    ▼  python training/train_yolo.py
runs/detect/titan_v1/         ← modelo treinado (best.pt)
```

## 7. Validação pré-treino

```powershell
# Validar labels antes de treinar
python training/prepare_dataset.py --source data/to_annotate --validate-only

# Ver estatísticas do dataset organizado
python training/prepare_dataset.py --output datasets/titan_cards --stats-only
```

## 8. Quantidade mínima recomendada

| Cenário | Imagens | Qualidade esperada |
|---------|---------|-------------------|
| Smoke test | 50-100 | Overfit controlado (prova de conceito) |
| MVP | 300-500 | Detecta cartas em condições similares ao treino |
| Produção | 1000-2000 | Generaliza para variações de luz/posição/mesa |

Para PLO6 com 52 classes de cartas, o ideal é ter pelo menos **5-10 exemplos
de cada carta** no dataset. As classes de botão/pot/stack são mais fáceis
(1-2 exemplos por frame é suficiente).
