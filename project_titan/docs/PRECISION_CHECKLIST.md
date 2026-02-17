# Protocolo de 2 Minutos - Precision Checklist

Objetivo: validar a geometria antes de cada sessão para evitar:

- ruído no OCR,
- cliques falsos nas bordas,
- drift de coordenadas por resolução/janela.

Tempo alvo: <= 120 segundos.

## Preparação (15s)

1. Abra o print de referência no Cockpit:

   - python titan_control.py --image_path table_reference.png
   - Opção 8 (Overlay Standalone).

2. Confirme grid ativo:

   - overlay.show_grid: true
   - overlay.grid_size: 20

## Check 1 - Ponto Zero (0,0) (15s)

Pergunta crítica:

- O canto superior esquerdo da grade (0,0) bate com o canto da área útil da mesa?

Ação:

- Se toda geometria estiver deslocada, ajuste primeiro:

  - vision.chrome_top
  - vision.chrome_bottom
  - vision.chrome_left
  - vision.chrome_right

- Só depois ajuste regiões individuais.

Critério de aprovação:

- Referências globais (HUD/linhas/caixas) alinham no mesmo deslocamento em toda a tela.

## Check 2 - OCR sem ruído (35s)

Pergunta crítica:

- A caixa amarela do Pot cobre apenas os números?

Falhas típicas:

- Captura "R$", "$", textos laterais, brilho do botão.
- Região larga demais (W/H excessivos) trazendo ruído.

Ação:

- Ajuste ocr.pot_region, ocr.stack_region, ocr.call_region no formato x,y,w,h.
- Preferir caixa justa: números + pequena margem interna.

Critério de aprovação:

- Caixa não toca símbolos monetários nem bordas decorativas.
- Leitura permanece estável por 3 frames consecutivos.

## Check 3 - Margem de Segurança dos Botões (35s)

Pergunta crítica:

- A área de clique está menor que o botão visual (~80%)?

Regra prática:

- Não clique em borda.
- Use centro do botão e mantenha margem lateral/superior/inferior.

Ação:

- Verifique pontos em action_buttons (fold/call/raise).
- Opcional: usar action_boxes como referência visual de 80% do botão.

Critério de aprovação:

- O ponto de clique cai no miolo do botão em todos os estados visuais.
- Mesmo com micro-variação da UI, ponto continua dentro da zona segura.

## Check 4 - Teste de Hover/Animação (20s)

Pergunta crítica:

- Se o botão brilhar/crescer com hover, a zona ainda é válida?

Ação:

- Passe o mouse sobre fold/call/raise e observe expansão/brilho.
- Confirme que a zona de clique continua no interior do botão ativo.

Critério de aprovação:

- Estado normal e estado hover continuam válidos sem mover o ponto para a borda.

## Decisão Go/No-Go (10s)

GO se TODOS os itens abaixo forem verdadeiros:

- Ponto zero alinhado.
- OCR sem captar símbolos/ruído.
- Clique no miolo com margem (~80%).
- Hover não invalida a geometria.

NO-GO se qualquer item falhar:

- Corrigir config_calibration.yaml/config.yaml e repetir o protocolo.

---

## Comandos rápidos

Abrir Cockpit com imagem estática:

- python titan_control.py --image_path table_reference.png

Coleta observador (após aprovação):

- .\scripts\start_squad.ps1 -CollectData -Overlay

Dica operacional:

- Faça este checklist antes de cada sessão (leva menos de 2 minutos e evita falhas caras).
