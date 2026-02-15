# Project Titan (estrutura estilo Compozy)

Arquitetura modular por composição, com um loop central de orquestração e blocos desacoplados por responsabilidade.

## Estrutura principal

- `orchestrator/`: composição e ciclo de execução
  - `engine.py`
  - `registry.py`
- `agents/`: agentes conectados ao orquestrador
  - `zombie_agent.py`
- `workflows/`: fluxos de decisão
  - `poker_hand_workflow.py`
- `tools/`: capacidades reutilizáveis
  - `vision_tool.py`
  - `equity_tool.py`
  - `action_tool.py`
- `memory/`: estado compartilhado
  - `redis_memory.py`

## Estrutura legada mantida

- `core/`, `agent/` e `utils/` foram preservados para facilitar migração incremental.

## Executando

1. Criar e ativar ambiente Python 3.11+
2. Instalar dependências: `pip install -r requirements.txt`
3. Iniciar: `start_squad.bat`

Também é possível executar direto:

- `python -m orchestrator.engine`

## Visão YOLO (já integrada)

`tools/vision_tool.py` já está ligado a `ultralytics` + `mss` com fallback automático.

Se `TITAN_YOLO_MODEL` não for definido, o sistema continua em modo stub (snapshot vazio).

### Padrões de label aceitos

- Hero cards: `hero_Ah`, `hole_Kd`, `hand_Qs`, `player_9c`, `h1_Ah`
- Board cards: `board_7d`, `flop_Kh`, `turn_2c`, `river_As`, `b3_Qd`
- Dead cards: `dead_Ah`, `burn_7c`, `muck_Qd`, `folded_9s`, `dc_2h`
- Card genérico: `Ah`, `card_Ah`, `10h` (normaliza para `Th`)
- Formato por palavras: `ace_hearts`, `ten_spades`, `queen_diamonds`
- Pot/stack numérico no label: `pot_23.5`, `stack_120.0`, `hero_stack_88`
- Oponente atual: `opponent_villain42`, `opp_7`, `villain_rega`
- Showdown para auditor RNG: `showdown_villain42_eq_37_won`, `sd_rega_0.41_lost`, `allin_v7_62p_win`

Quando o label vier genérico (`Ah`, `card_Ah`), o parser separa hero/board pela posição vertical da detecção.

### Variáveis de ambiente

- `TITAN_YOLO_MODEL`: caminho do `.pt` do YOLO
- `TITAN_MONITOR_LEFT`
- `TITAN_MONITOR_TOP`
- `TITAN_MONITOR_WIDTH`
- `TITAN_MONITOR_HEIGHT`
- `TITAN_VISION_DEBUG_LABELS=1`: imprime labels desconhecidos no terminal
- `TITAN_VISION_LABEL_MAP_FILE`: caminho de JSON com aliases de labels do dataset
- `TITAN_VISION_LABEL_MAP_JSON`: JSON inline com aliases de labels
- `TITAN_TABLE_PROFILE`: `tight`, `normal` ou `aggressive`
- `TITAN_TABLE_POSITION`: `utg`, `mp`, `co`, `btn`, `sb`, `bb`
- `TITAN_OPPONENTS`: número de vilões para equity Monte Carlo (`1` a `9`)
- `TITAN_SIMULATIONS`: iterações Monte Carlo por decisão (`100` a `100000`)
- `TITAN_DYNAMIC_SIMULATIONS`: ajusta automaticamente simulações por street (`0`/`1`)
- `TITAN_RNG_EVASION`: ativa protocolo de evasão contra `SUPER_USER` (`0`/`1`, padrão `1`)
- `TITAN_CURRENT_OPPONENT`: id do vilão atual para validação de evasão
- `TITAN_ZMQ_BIND`: bind do servidor HiveBrain (ex: `tcp://0.0.0.0:5555`)
- `TITAN_ZMQ_SERVER`: endpoint para agentes cliente (ex: `tcp://127.0.0.1:5555`)
- `TITAN_AGENT_ID`: id do agente ZMQ (ex: `01`)
- `TITAN_TABLE_ID`: id da mesa para coordenação de squad

Exemplo (PowerShell):

- `$env:TITAN_YOLO_MODEL="C:\\models\\cards_yolov8.pt"`
- `$env:TITAN_MONITOR_LEFT="100"`
- `$env:TITAN_MONITOR_TOP="100"`
- `$env:TITAN_MONITOR_WIDTH="1280"`
- `$env:TITAN_MONITOR_HEIGHT="720"`

## Smoke test rápido (recomendado)

- `python -m orchestrator.healthcheck`

Esse comando inicializa o orquestrador, valida o bootstrap e encerra com código `0`.

## Equity Monte Carlo

`core/math_engine.py` agora usa simulação Monte Carlo com `treys` para estimar `win_rate` e `tie_rate`.

`dead_cards` agora é consolidado entre visão e memória compartilhada (`memory["dead_cards"]`) no workflow.

## Política de ação avançada

`workflows/poker_hand_workflow.py` agora usa política por street com sizing:

- `fold`, `call`, `raise_small`, `raise_big`

A decisão considera `win_rate`, `tie_rate`, `pot_odds` e qualidade da informação observada na mesa.

### RNG Watchdog + Evasão

O workflow agora aceita eventos de showdown em `memory["showdown_events"]`, com itens no formato:

- `{"opponent_id":"villain_42", "equity":0.37, "won":true}`

Esses eventos alimentam o `RngTool`/`RngAuditor`, que calcula Z-Score por vilão e mantém `memory["rng_super_users"]`.

O estado do auditor RNG agora é persistido em memória compartilhada (`RedisMemory`) na chave `rng_audit_state` (ou `TITAN_RNG_STATE_KEY`), preservando histórico entre reinícios do processo.

Se `TITAN_RNG_EVASION=1` e `TITAN_CURRENT_OPPONENT` estiver marcado como `SUPER_USER`, o workflow força `fold` (quando não estiver em estado `wait`).

## Modo simulado (sem YOLO, para teste rápido)

Para ver decisões variando no Windows sem visão real, use:

- `./scripts/run_windows.ps1 -SimScenario cycle`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -TickSeconds 0.1`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -ReportDir reports`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -ReportDir reports -OpenLastReport`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -ReportDir reports -PrintLastReport`
- `./scripts/run_windows.ps1 -Ticks 10 -LabelMapFile simulator/vision/label_map.example.json`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -TableProfile aggressive`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -TableProfile normal -TablePosition btn`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -TableProfile normal -TablePosition co -Opponents 4`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -Opponents 3 -Simulations 3000`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -Opponents 3 -Simulations 3000 -DynamicSimulations`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -Opponents 3 -Simulations 3000 -ProfileSweep`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -Opponents 3 -Simulations 3000 -PositionSweep`

Também é possível forçar um cenário específico:

- `./scripts/run_windows.ps1 -HealthOnly -SimScenario wait`
- `./scripts/run_windows.ps1 -HealthOnly -SimScenario fold`
- `./scripts/run_windows.ps1 -HealthOnly -SimScenario call`
- `./scripts/run_windows.ps1 -HealthOnly -SimScenario raise`

Para execução finita (encerra sozinha), use `-Ticks`.

Para controlar a velocidade do loop, use `-TickSeconds` (padrão `0.2`).

Para calibrar agressividade da política, use `-TableProfile` (`tight|normal|aggressive`).

Para calibrar por posição de mesa, use `-TablePosition` (`utg|mp|co|btn|sb|bb`).

Para calibrar por pote multiway, use `-Opponents` (`1..9`).

Para balancear precisão vs velocidade do equity, use `-Simulations` (`100..100000`).

Para ajuste automático por street (preflop/flop/turn/river), use `-DynamicSimulations`.

Para benchmark rápido A/B/C por perfil (`tight`, `normal`, `aggressive`), use `-ProfileSweep`.

Para benchmark rápido por posição (`utg`, `mp`, `co`, `btn`, `sb`, `bb`), use `-PositionSweep`.

Use apenas um sweep por execução: `-ProfileSweep` ou `-PositionSweep`.

Os modos de sweep agora exibem ranking automático (`Best`/`Worst`) por `score` composto (`average_win_rate` + bônus de `raises` - penalidade de `folds`), com desempate por `average_win_rate`, `raises` e `folds`.

Além do output no terminal, cada sweep salva um arquivo consolidado `sweep_summary_profile_*.json` ou `sweep_summary_position_*.json` no `ReportDir`.

Para comparar a última execução com a anterior, use `-CompareSweepHistory` (e opcionalmente `-HistoryDepth`, padrão `5`).

Para consultar histórico sem rodar o engine, use `-OnlySweepHistory -SweepHistoryMode profile|position`.

Para salvar o comparativo em JSON, adicione `-SaveHistoryCompare` (gera `history_compare_profile_*.json` ou `history_compare_position_*.json`).

Para um dashboard rápido dos últimos sweeps (`profile` + `position`), use `-SweepDashboard` (usa `-HistoryDepth` como quantidade por modo).

Exemplo: `./scripts/run_windows.ps1 -OnlySweepHistory -SweepHistoryMode position -HistoryDepth 5 -ReportDir reports`.

Ao finalizar a execução, o engine imprime um relatório JSON em uma linha:

- `[Orchestrator] run_report={...}`

Campos atuais: `ticks`, `outcomes`, `average_win_rate`, `action_counts`, `simulation_usage`, `rng_watchdog`, `duration_seconds`.

`simulation_usage` inclui: `count`, `average`, `min`, `max`, `dynamic_enabled_decisions`.

`rng_watchdog` inclui: `players_audited`, `players_flagged`, `flagged_opponents`, `top_zscores`.

Para persistir em arquivo `.json`, informe `-ReportDir` no script Windows (ou defina `TITAN_REPORT_DIR`).

Para abrir automaticamente o último relatório gerado ao final da execução, use `-OpenLastReport`.

Para imprimir o último relatório JSON no terminal ao final da execução, use `-PrintLastReport`.

### Alias de labels por dataset (YOLO)

Você pode mapear labels do dataset para labels canônicos aceitos pelo parser.

Exemplo de mapeamento JSON:

```json
{
  "hero-card-1-ah": "hero_Ah",
  "table-flop-kd": "board_Kd",
  "burn-card-1": "dead_7c",
  "pot_value_120": "pot_120"
}
```

## APK Android (PoC)

- Estrutura mobile pronta em `mobile/`.
- Guia de build em `mobile/README.md`.
- Arquivo de build Android: `mobile/buildozer.spec`.

Build recomendado via WSL/Linux com Buildozer.

## Observação sobre exit code

- `python -m orchestrator.engine` é um loop contínuo (não encerra sozinho).
- Se interrompido por timeout/stop externo, pode aparecer código de saída diferente de `0` sem indicar crash.

## Próximos passos sugeridos

1. Ajustar regras/benchmark de Monte Carlo para PLO em produção
2. Aumentar cobertura de labels dead/burn/muck no dataset YOLO
3. Implementar parser de labels YOLO específico do dataset
4. Calibrar thresholds/sizing por perfil de mesa e posição
