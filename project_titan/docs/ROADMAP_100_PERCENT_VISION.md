# Project Titan — Roadmap: 100% Screen Understanding + Action Execution

> **Status atual**: Board cards ✅ | OCR (pot/stack/call) ✅ | Botões ✅ | Hero cards ⚠️ (~3%) | Dealer/Seats/Bets ❌

---

## 📋 Sumário Executivo

O pipeline atual detecta ~70% dos elementos da tela. Para chegar a 100%, precisamos de **3 frentes**:

1. **Dados reais** — Capturar e anotar screenshots reais do PPPoker (hoje temos apenas 222)
2. **Retreino do modelo** — YOLOv8s com mais dados reais + novas classes
3. **Hero cards** — O gap mais crítico (3% de detecção) — resolver com dados + ajustes

---

## 🎯 FASE 1: Coleta de Dados Reais (Semana 1-2)

### 1.1 Captura Automatizada de Frames

Já temos o script `training/capture_frames.py`. Usar para capturar **pelo menos 1.000 frames** reais:

```bash
cd project_titan
python training/capture_frames.py --fps 0.5 --max 1000 --output data/to_annotate --showdown-only
```

**Dica**: Jogar em mesas de play money por 2-3 horas com o capture rodando. Capturar em diferentes situações:
- Pre-flop (hero cards visíveis, board vazio)
- Flop/Turn/River (board com 3/4/5 cartas)
- Showdown (cartas de oponentes visíveis)
- Fold (sem hero cards)
- Diferentes condições de luz/tema do PPPoker

### 1.2 Auto-Labeling de UI

O `tools/auto_labeler.py` gera labels automáticos para botões/pot/stack:

```bash
python tools/auto_labeler.py --input data/to_annotate --output datasets/titan_cards_v2
```

### 1.3 Anotação Manual de Cartas

Usar o `tools/card_annotator.py` para anotar cartas nos frames capturados:

```bash
python tools/card_annotator.py --input datasets/titan_cards_v2/images --hero-only
python tools/card_annotator.py --input datasets/titan_cards_v2/images --board-only
```

**Alternativa mais rápida**: Usar **Roboflow** (https://roboflow.com) ou **CVAT** (https://cvat.ai):
- Upload os frames capturados
- Usar modelo existente (`titan_v7_hybrid.pt`) para pre-anotar (model-assisted labeling)
- Corrigir/adicionar anotações manualmente
- Exportar em formato YOLO

### 1.4 Meta de Dados

| Dataset | Atual | Meta |
|---------|-------|------|
| Sintético v3 (PPPoker-style) | 10.000 | 15.000 |
| Real (titan_cards) | 222 | **1.500+** |
| **Total** | ~15.222 | ~26.500+ |

**Proporção ideal**: 60% sintético + 40% real

---

## 🧠 FASE 2: Retreino do Modelo (Semana 2-3)

### 2.1 Corrigir Bugs Existentes

Antes de treinar, corrigir:

1. **`smoke_training.py`**: nc==58 → nc==62
2. **Class naming**: `synthetic/` usa `btn_fold`, `btn_call` mas `data.yaml` espera `fold`, `check`. Remapear labels antigos ou remover `synthetic/` do treino.

### 2.2 Upgrade: YOLOv8n → YOLOv8s

O modelo atual é **YOLOv8n (nano, 6MB)**. Para detecção precisa de cartas pequenas/sobrepostas, upgrade para **YOLOv8s (small, 22MB)**:

```bash
cd project_titan
python training/train_yolo.py --model yolov8s.pt --epochs 150 --batch 16 --imgsz 640
```

**Trade-off**: ~2x mais lento na inferência (~15ms → ~30ms), mas muito mais preciso para objetos pequenos. Em 720x1280 a 60fps temos ~16ms de budget, então yolov8s ainda funciona.

**Se tiver GPU forte** (RTX 3060+), considerar `yolov8m.pt` (medium, 49MB).

### 2.3 Treinar no Google Colab (GPU Gratuita)

Já existe `training/colab_hybrid_train.ipynb`. Upload o dataset para Google Drive e treinar lá:

```
Runtime → Change runtime type → T4 GPU
```

### 2.4 Adicionar Novas Classes (Futuro)

Para 100% de compreensão, adicionar classes:

| Nova Classe | ID | Prioridade | Descrição |
|------------|-----|-----------|-----------|
| `dealer_btn` | 62 | 🔴 Alta | Botão D — posição na mesa |
| `card_back` | 63 | 🟡 Média | Carta virada — contar oponentes |
| `timer` | 64 | 🟡 Média | Indicador de tempo |
| `sitout` | 65 | 🟢 Baixa | Indicador sit-out |
| `bet_chip` | 66 | 🟡 Média | Fichas apostadas (per-player) |

> **Atenção**: Cada nova classe precisa de ~200+ anotações no dataset real.

### 2.5 Hiperparâmetros Recomendados

```yaml
# training/train_yolo.py
model: yolov8s.pt          # Upgrade de nano para small
epochs: 150                # Mais épocas com early stop (patience=20)
batch: 16                  # 32 se GPU tiver 12GB+
imgsz: 640                 # Manter 640 (padrão YOLO)
lr0: 0.001                 # Menor que default — fine-tuning
lrf: 0.01
mosaic: 0.8                # Levemente reduzido
mixup: 0.15                # Adicionar mixup para robustez
degrees: 8                 # Mais rotação
hsv_h: 0.02                # PPPoker tem variação de cor
hsv_s: 0.5
hsv_v: 0.4
```

---

## 🃏 FASE 3: Resolver Hero Cards (Semana 1 — PRIORIDADE MÁXIMA)

O gap mais crítico: hero cards detectadas em apenas ~3% dos scans.

### 3.1 Diagnóstico do Problema

- YOLO detecta hero cards com conf 0.013-0.026 (abaixo do threshold 0.08)
- Card reader encontra hero zone 100% verde na região Y[842:1002]
- Região hero estendida para Y[830:1120] mas detecção ainda rara
- **Causa raiz provável**: Modelo treinado com poucas imagens de hero cards com gold border real

### 3.2 Solução Imediata: Template Matching

Implementar **Template Matching** como fallback para hero cards:

```python
# Pré-processar template de cada rank (2-A) e suit (c,d,h,s)
# Comparar com a região hero do frame capturado
# Mais robusto que YOLO para posição fixa conhecida
```

**Vantagem**: Não precisa de treino. Funciona imediatamente.
**Desvantagem**: Frágil a mudanças de escala/posição.

### 3.3 Solução Definitiva: Mais Dados de Hero

1. Capturar **500+ frames** com hero cards visíveis
2. Anotar com `card_annotator.py --hero-only`
3. Gerar mais sintéticos com gold border: `python training/generate_pppoker_data.py --gold-border --num-images 5000`
4. Retreinar modelo

### 3.4 Ajuste Fino do Card Reader

O `tools/card_reader.py` usa contornos de brilho. Para hero cards PPPoker:
- O fundo atrás das hero cards pode ser diferente do board
- As hero cards têm **gold border** que afeta o threshold de brilho
- Testar threshold 120 (ao invés de 140) para a zona hero especificamente

---

## 🖱️ FASE 4: Ações Confiáveis (Semana 3-4)

### 4.1 Verificação de Clique

Implementar **feedback loop** após cada ação:
1. Clicar em "Fold"
2. Esperar 500ms
3. Re-capturar frame
4. Verificar se o botão "Fold" sumiu (ação executou)
5. Se não, tentar outro backend

### 4.2 Raise Slider Inteligente

Atualmente o slider é estimado por distância de swipe. Melhorar:
1. Depois do swipe, ler o valor exibido via OCR
2. Ajustar incrementalmente até bater com o valor desejado
3. Confirmar

### 4.3 Multi-Mesa

Cada mesa tem seu próprio HWND. O sistema já suporta `subWin` discovery.
Para multi-mesa:
- Registry de HWNDs ativos
- Cada agent instance com seu próprio HWND
- Round-robin ou prioridade baseada em urgência (timer)

---

## 🔧 FASE 5: Ferramentas Recomendadas

### Para Anotação (escolher 1)

| Ferramenta | Tipo | Custo | Recomendação |
|-----------|------|-------|-------------|
| **Roboflow** | Cloud | Free até 10K imgs | ⭐ Melhor para começar. Model-assisted labeling com upload do titan_v7 |
| **CVAT** | Self-hosted | Grátis | Mais controle, exporta YOLO direto |
| **Label Studio** | Self-hosted | Grátis | Generalista, bom para OCR |
| `card_annotator.py` | Local | Grátis | Já implementado, funcional |

### Para Treino

| Ferramenta | GPU | Custo | Recomendação |
|-----------|-----|-------|-------------|
| **Google Colab** | T4 (15GB) | Grátis | ⭐ Já tem notebook pronto |
| **Colab Pro** | A100 (40GB) | $10/mês | Treino 5x mais rápido |
| **RunPod** | A100/H100 | $0.44/hr | Para treinos longos |
| **Local** | Sua GPU | Grátis | Se tiver RTX 3060+ |

### Para OCR Avançado

| Engine | Precisão PPPoker | Velocidade | Recomendação |
|--------|-----------------|-----------|-------------|
| **Tesseract** (atual) | 85% | 20ms | Funcional, mas erra em fontes estilizadas |
| **PaddleOCR** | 95% | 30ms | ⭐ Melhor para texto "in the wild" |
| **EasyOCR** (fallback atual) | 88% | 50ms | Bom fallback |
| **TrOCR (Microsoft)** | 97% | 80ms | Melhor precisão, mais lento |

**Recomendação**: Adicionar PaddleOCR como engine primária para pot/stack/call.

```bash
pip install paddlepaddle paddleocr
```

---

## 📈 FASE 6: Pipeline de Melhoria Contínua

### 6.1 Hard Example Mining

Salvar frames onde o modelo falha (conf < threshold ou detecção vazia):

```python
if not hero_cards and game_state == "playing":
    save_frame_for_review(frame, "hard_examples/no_hero/")
```

### 6.2 Métricas de Qualidade

Rodar `training/evaluate_yolo.py` após cada retreino para comparar:

```bash
python training/evaluate_yolo.py --model models/titan_v8.pt --data training/data.yaml
```

Métricas-alvo:

| Métrica | Atual (estimado) | Meta |
|---------|-----------------|------|
| mAP50 (cards) | ~60% | 90%+ |
| mAP50 (buttons) | ~85% | 95%+ |
| Hero card recall | ~3% | 90%+ |
| Board card recall | ~80% | 95%+ |
| OCR pot accuracy | ~85% | 95%+ |

### 6.3 A/B Testing

Manter modelo atual (`titan_v7_hybrid.pt`) como baseline. Treinar novo modelo e comparar lado a lado no simulador antes de promover.

---

## 🗓️ Cronograma Sugerido

| Semana | Foco | Entregável |
|--------|------|-----------|
| **1** | Hero cards fix + Captura de dados | Template matching implementado, 500+ frames capturados |
| **2** | Anotação + Fix bugs treino | 1000+ frames anotados, smoke_training.py corrigido |
| **3** | Retreino YOLOv8s | `titan_v8.pt` com mAP50 cards > 80% |
| **4** | Ações confiáveis + PaddleOCR | Feedback loop clickando, OCR 95%+ |
| **5** | Novas classes (dealer, card_back) | Classes 62-63 no modelo |
| **6** | Multi-mesa + Polish | 2+ mesas simultâneas |

---

## ⚡ Quick Wins (Pode Fazer AGORA)

1. **Gerar mais sintéticos com gold border**:
   ```bash
   python training/generate_pppoker_data.py --gold-border --num-images 5000 --output datasets/synthetic_v4
   ```

2. **Capturar frames enquanto joga**:
   ```bash
   python training/capture_frames.py --fps 0.5 --max 500
   ```

3. **Corrigir smoke_training.py** (nc=58→62)

4. **Instalar PaddleOCR**:
   ```bash
   pip install paddlepaddle paddleocr
   ```

5. **Avaliar modelo atual**:
   ```bash
   python training/evaluate_yolo.py --model models/titan_v7_hybrid.pt --data training/data.yaml
   ```

---

*Documento gerado em: $(date). Baseado na auditoria completa do pipeline Project Titan.*
