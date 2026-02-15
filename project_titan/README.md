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
- Jogadores ativos: `active_players_6`, `player_count_4`, `players_active_3`, `seats_5`
- Botões de ação: `btn_fold`, `btn_call`, `btn_raise`, `btn_allin`, `action_call`, `action_raise`, `button_fold`
- Showdown para auditor RNG: `showdown_villain42_eq_37_won`, `sd_rega_0.41_lost`, `allin_v7_62p_win`

No perfil `TITAN_VISION_LABEL_PROFILE=dataset_v1`, o parser também cobre variantes de dataset para dead cards (`burned`, `mucked`, `discarded`, `folded`) e ordens diferentes de token (ex: `burn_card_1_7c`, `card_burn_7c`).

Quando o label vier genérico (`Ah`, `card_Ah`), o parser separa hero/board pela posição vertical da detecção.

### Variáveis de ambiente

- `TITAN_YOLO_MODEL`: caminho do `.pt` do YOLO
- `TITAN_MONITOR_LEFT`
- `TITAN_MONITOR_TOP`
- `TITAN_MONITOR_WIDTH`
- `TITAN_MONITOR_HEIGHT`
- `TITAN_VISION_DEBUG_LABELS=1`: imprime labels desconhecidos no terminal
- `TITAN_VISION_LABEL_PROFILE`: `generic` (padrão) ou `dataset_v1` para parser específico de dataset
- `TITAN_VISION_WAIT_STATE_CHANGE`: `0|1` ativa polling de mudança de estado no VisionTool
- `TITAN_VISION_CHANGE_TIMEOUT`: timeout em segundos para aguardar mudança (`1.0` padrão)
- `TITAN_VISION_POLL_FPS`: frequência de polling para captura contínua (`30` padrão)
- `TITAN_VISION_WAIT_MY_TURN`: quando `1`, só retorna ao detectar mudança com `is_my_turn=true`
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
- `TITAN_ACTIVE_PLAYERS`: número de jogadores ativos na mão (usado para obfuscação heads-up no HiveBrain)
- `TITAN_AGENT_MAX_CYCLES`: limita ciclos do `agent/poker_agent.py` (útil para teste/CI)
- `TITAN_ACTION_CALIBRATION_CACHE`: `0|1` ativa cache de calibração de botões por mesa (`1` padrão)
- `TITAN_ACTION_CALIBRATION_FILE`: arquivo JSON para persistir cache de calibração (`reports/action_calibration_cache.json` padrão)
- `TITAN_ACTION_CALIBRATION_SESSION`: escopo lógico de sessão para separar perfis no mesmo `table_id` (`default` padrão)
- `TITAN_ACTION_CALIBRATION_MAX_SCOPES`: máximo de scopes (`table_id + session`) mantidos no arquivo (`50` padrão)
- `TITAN_ACTION_SMOOTHING`: `0|1` ativa suavização temporal anti-jitter das coordenadas (`1` padrão)
- `TITAN_ACTION_SMOOTHING_ALPHA`: fator EMA da suavização (`0.35` padrão; menor = mais estável)
- `TITAN_ACTION_SMOOTHING_DEADZONE_PX`: ignora microvariações abaixo deste delta em pixels (`3` padrão)

`TITAN_ACTIVE_PLAYERS` é fallback manual. O `VisionTool` agora tenta inferir automaticamente `active_players` por frame (label explícito > contagem de oponentes detectados > fallback contextual).

O `VisionTool` também tenta calibrar automaticamente coordenadas dos botões (`fold`, `call`, `raise_small`, `raise_big`) a partir dos labels detectados no frame e aplica no `ActionTool` em runtime.

No `PokerAgent`, essa calibração agora fica em cache por `table_id`: quando um frame não traz labels de botão, o agente reutiliza a última calibração válida da mesa. Para desligar, use `TITAN_ACTION_CALIBRATION_CACHE=0`.

Esse cache também é persistido em arquivo local por escopo `table_id + session_id` (via `TITAN_ACTION_CALIBRATION_SESSION`), permitindo restore automático após reinício do processo.

Quando o arquivo ultrapassa o limite de scopes, entradas mais antigas são podadas automaticamente, preservando os scopes mais recentes.

Para reduzir oscilação entre frames, o agente aplica suavização temporal nas coordenadas detectadas (EMA + deadzone em pixels). Ajuste fino por `TITAN_ACTION_SMOOTHING_ALPHA` e `TITAN_ACTION_SMOOTHING_DEADZONE_PX`.

### Operação manual do cache de calibração

Utilitário: `./scripts/action_cache_tool.ps1`

- Listar scopes:
  - `./scripts/action_cache_tool.ps1 -Mode list`
  - `./scripts/action_cache_tool.ps1 -Mode list -Json`
- Podar mantendo N scopes mais recentes:
  - `./scripts/action_cache_tool.ps1 -Mode prune -MaxScopes 50`
- Remover scope específico:
  - `./scripts/action_cache_tool.ps1 -Mode delete -Scope table_default::default`
  - `./scripts/action_cache_tool.ps1 -Mode delete -TableId table_default -Session default`
- Limpar tudo:
  - `./scripts/action_cache_tool.ps1 -Mode clear`

Exemplo (PowerShell):

- `$env:TITAN_YOLO_MODEL="C:\\models\\cards_yolov8.pt"`
- `$env:TITAN_MONITOR_LEFT="100"`
- `$env:TITAN_MONITOR_TOP="100"`
- `$env:TITAN_MONITOR_WIDTH="1280"`
- `$env:TITAN_MONITOR_HEIGHT="720"`

## Agente cliente ZMQ (loop completo)

`agent/poker_agent.py` agora executa loop completo no cliente:

1. lê estado da mesa via `VisionTool`
2. faz check-in no `HiveBrain` com `hero_cards` e `active_players`
3. injeta `dead_cards` + `heads_up_obfuscation` retornados pelo servidor em memória compartilhada
4. executa `PokerHandWorkflow` com o mesmo snapshot (decisão consistente)
5. aciona `ActionTool`/`GhostMouse`

O agente agora usa `RedisMemory` como backend de estado (com fallback automático para memória local quando Redis não está disponível). Isso permite:

- Persistência de estado RNG entre reinícios do processo (TTL=0 para `rng_audit_state`)
- Compartilhamento de estado entre agentes na mesma máquina (se Redis estiver rodando)

Variável: `TITAN_REDIS_URL` (padrão: `redis://127.0.0.1:6379/0`)

Execução rápida (PowerShell):

- `$env:TITAN_SIM_SCENARIO="cycle"`
- `$env:TITAN_AGENT_MAX_CYCLES="5"`
- `python -m agent.poker_agent`

Para aguardar mudança de estado em 30 FPS (ex.: evento de "Minha Vez"):

- `$env:TITAN_VISION_WAIT_STATE_CHANGE="1"`
- `$env:TITAN_VISION_WAIT_MY_TURN="1"`
- `$env:TITAN_VISION_POLL_FPS="30"`
- `$env:TITAN_VISION_CHANGE_TIMEOUT="1.5"`
- `python -m agent.poker_agent`

## Lançando o squad completo

### Via BAT (Windows):

- `start_squad.bat`

### Via PowerShell (recomendado):

- `./scripts/start_squad.ps1`
- `./scripts/start_squad.ps1 -Agents 3 -TableId table_beta`
- `./scripts/start_squad.ps1 -Agents 2 -MaxCycles 10 -SimScenario cycle`

O launcher detecta automaticamente o `.venv` do projeto e inicia HiveBrain + N agentes + Orchestrator.

Ctrl+C no terminal PowerShell encerra todos os processos.

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

A calibração de thresholds/sizing considera também:

- perfil de mesa (`tight|normal|aggressive`) por street
- posição (`utg|mp|co|btn|sb|bb`) por street
- efeito multiway (`TITAN_OPPONENTS`)
- contexto de SPR (stack-to-pot ratio) para spots de compromisso no turn/river

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
- `./scripts/run_windows.ps1 -Ticks 10 -LabelMode dataset_v1`
- `./scripts/run_windows.ps1 -Ticks 10 -LabelMode dataset_v1 -LabelMapFile simulator/vision/label_map.example.json`

`-LabelMode` é o nome preferido do parâmetro. `-LabelProfile` continua aceito como alias legado por compatibilidade.

- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -TableProfile aggressive`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -TableProfile normal -TablePosition btn`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -TableProfile normal -TablePosition co -Opponents 4`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -Opponents 3 -Simulations 3000`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -Opponents 3 -Simulations 3000 -DynamicSimulations`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -Opponents 3 -Simulations 3000 -ProfileSweep`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -Opponents 3 -Simulations 3000 -PositionSweep`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -UseBestBaseline`
- `./scripts/run_windows.ps1 -PrintBaseline -UseBestBaseline -ReportDir reports`
- `./scripts/run_windows.ps1 -PrintBaselineJson -UseBestBaseline -ReportDir reports`
- `./scripts/run_windows.ps1 -HealthOnly -UseBestBaseline -SaveBestBaseline -ReportDir reports`

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

## Profiling da visão (30 FPS)

Para medir latência e throughput do pipeline de visão em carga controlada:

- `./scripts/vision_profile.ps1 -Frames 300 -TargetFps 30 -ReportDir reports`
- `./scripts/vision_profile.ps1 -Frames 300 -TargetFps 30 -NoSamples -Json`

O utilitário executa leituras do `VisionTool`, calcula métricas de latência (`avg`, `p50`, `p95`, `max`), FPS alcançado e taxa de `state_changed` / `is_my_turn`.

Também salva relatório em `reports/vision_profile_*.json` para comparação histórica.

Comparar automaticamente os 2 últimos perfis com alerta de regressão de `p95`:

- `./scripts/vision_profile_compare.ps1 -ReportDir reports`
- `./scripts/vision_profile_compare.ps1 -ReportDir reports -P95RegressionThresholdPct 20 -FailOnRegression`
- `./scripts/vision_profile_compare.ps1 -ReportDir reports -Json -SaveCompare -SaveLatest`

Payload CI-ready do comparativo inclui:

- `status`: `pass|fail`
- `ci.exit_code`: código de saída efetivo considerando `-FailOnRegression`
- `ci.should_fail`: booleano para consumo por pipelines
- `latest_file`: caminho do arquivo estável (quando `-SaveLatest`)

Para ajuste automático por street (preflop/flop/turn/river), use `-DynamicSimulations`.

Para benchmark rápido A/B/C por perfil (`tight`, `normal`, `aggressive`), use `-ProfileSweep`.

Para benchmark rápido por posição (`utg`, `mp`, `co`, `btn`, `sb`, `bb`), use `-PositionSweep`.

Use apenas um sweep por execução: `-ProfileSweep` ou `-PositionSweep`.

### Recomendação inicial (baseline de benchmark)

No benchmark simulado curto (`cycle`, `12 ticks`, `opponents=3`, `simulations=3000`, `dynamic=on`), o ranking mais recente apontou:

- **Melhor profile:** `normal`
- **Pior profile:** `aggressive`
- **Melhor posição:** `bb`
- **Pior posição:** `utg`

Sugestão prática inicial de operação para testes controlados:

- `TITAN_TABLE_PROFILE=normal`
- `TITAN_TABLE_POSITION=bb`

Para aplicar automaticamente o último baseline salvo nos sweeps, use:

- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 10 -UseBestBaseline -ReportDir reports`

Para apenas consultar no terminal o baseline efetivo (sem iniciar healthcheck/engine), use:

- `./scripts/run_windows.ps1 -PrintBaseline -UseBestBaseline -ReportDir reports`

Para saída JSON (útil para automação/CI), use:

- `./scripts/run_windows.ps1 -PrintBaselineJson -UseBestBaseline -ReportDir reports`

Também existe utilitário dedicado para consulta de baseline (sem depender do fluxo do runner):

- `./scripts/print_baseline.ps1 -ReportDir reports`
- `./scripts/print_baseline.ps1 -ReportDir reports -Json`

Para validar rapidamente regressão de baseline (LabelMode + alias legado + JSON), use:

- `./scripts/smoke_baseline.ps1 -ReportDir reports`

Para validar rapidamente regressão de sweep (ProfileSweep + PositionSweep + geração de summaries), use:

- `./scripts/smoke_sweep.ps1 -ReportDir reports`

Para validar profiling de visão + comparação histórica (`vision_profile` + `vision_profile_compare`), use:

- `./scripts/smoke_vision_profile.ps1 -ReportDir reports`

Para gerar resumo consolidado único dos checks de smoke (sem reexecutar tudo), use:

- `./scripts/smoke_health_summary.ps1 -ReportDir reports`
- `./scripts/smoke_health_summary.ps1 -ReportDir reports -Json`

Para rodar baseline + sweep em uma única execução (status único para CI), use:

- `./scripts/smoke_all.ps1 -ReportDir reports`

Ao final, o `smoke_all.ps1` também gera resumo consolidado em:

- `reports/smoke_health_latest.json` (arquivo estável para CI/dashboard)
- `reports/smoke_health_*.json` (histórico timestampado)

Para validar integração multi-agente (HiveBrain + 2 agentes + protocolo squad), use:

- `./scripts/smoke_squad.ps1 -ReportDir reports`

O `smoke_all.ps1` agora inclui automaticamente o `smoke_squad.ps1`.

## CI (GitHub Actions)

Workflow pronto em [../.github/workflows/project_titan_smoke.yml](../.github/workflows/project_titan_smoke.yml).

Ele roda no `windows-latest`, instala dependências de `requirements.txt`, executa `smoke_all.ps1` e publica `project_titan/reports` como artifact.

O job `smoke` também exporta outputs para automação:

- `overall_status`
- `vision_compare_status`
- `health_file`

Além disso, escreve um resumo no `GITHUB_STEP_SUMMARY` com status consolidado do smoke health.

O job `gate` bloqueia merge quando `overall_status != pass`.

No `workflow_dispatch`, é possível ativar modo estrito de visão com input `strict_vision_gate=true`, que também falha o gate quando `vision_compare_status != pass`.

Quando o passo de smoke falha, o workflow também gera automaticamente um `ci_debug_bundle_*.zip` dentro de `reports` para facilitar troubleshooting no artifact.

Para montar um pacote local de troubleshooting (scripts + docs + governança + reports), use:

- `./scripts/collect_ci_debug.ps1 -ReportDir reports -OutputDir reports`

O comando gera `reports/ci_debug_bundle_*.zip` e imprime o caminho final no terminal.

### Proteção de branch (recomendado)

Para exigir o smoke no merge para `main`:

1. GitHub → `Settings` → `Branches`.
2. Em `Branch protection rules`, crie/edite a regra para `main`.
3. Ative `Require a pull request before merging`.
4. Ative `Require status checks to pass before merging`.
5. Em checks obrigatórios, selecione os jobs `smoke` e `gate` do workflow `Project Titan Smoke`.

Opcional (mais rígido):

- `Require branches to be up to date before merging`.
- `Require conversation resolution before merging`.
- `Do not allow bypassing the above settings` (apenas se o time já estiver pronto para política estrita).

## Definition of Done (DoD)

Antes de abrir/mesclar PR em `main`, use este checklist:

- [ ] `./scripts/smoke_baseline.ps1 -ReportDir reports` executou com sucesso.
- [ ] `./scripts/smoke_sweep.ps1 -ReportDir reports` executou com sucesso.
- [ ] `./scripts/smoke_vision_profile.ps1 -ReportDir reports` executou com sucesso.
- [ ] `./scripts/smoke_squad.ps1 -ReportDir reports` executou com sucesso.
- [ ] `./scripts/smoke_all.ps1 -ReportDir reports` executou com sucesso.
- [ ] Workflow `Project Titan Smoke` passou no PR.
- [ ] Job `gate` do workflow `Project Titan Smoke` passou no PR.
- [ ] Arquivos de documentação afetados foram atualizados (`README`, scripts).
- [ ] Regra de branch protection com checks obrigatórios `smoke` e `gate` está ativa em `main`.

Template de PR disponível em [../.github/PULL_REQUEST_TEMPLATE.md](../.github/PULL_REQUEST_TEMPLATE.md).

Owners de revisão definidos em [../.github/CODEOWNERS](../.github/CODEOWNERS).

Guia de contribuição em [../CONTRIBUTING.md](../CONTRIBUTING.md).

Ordem de resolução do baseline com `-UseBestBaseline`:

1. `baseline_best.json` (se existir e estiver válido)
2. último `sweep_summary_profile_*.json` + último `sweep_summary_position_*.json`
3. fallback para `-TableProfile` e `-TablePosition`

Para persistir o baseline atual em arquivo único (`baseline_best.json`), use:

- `./scripts/run_windows.ps1 -HealthOnly -UseBestBaseline -SaveBestBaseline -ReportDir reports`

Comandos usados no benchmark:

- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 12 -TickSeconds 0.1 -Opponents 3 -Simulations 3000 -DynamicSimulations -ProfileSweep -ReportDir reports`
- `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 12 -TickSeconds 0.1 -Opponents 3 -Simulations 3000 -DynamicSimulations -PositionSweep -ReportDir reports`

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

## Ghost Mouse (Ghost Protocol)

`agent/ghost_mouse.py` implementa o protocolo de humanizacao de input:

- **Curvas de Bezier cubicas**: o cursor nunca move em linha reta. Dois control points aleatorios geram um arco natural.
- **Ruido Gaussiano**: cada ponto intermediario recebe perturbacao `gauss(0, noise_amplitude)` para simular tremor humano.
- **Timing variavel por dificuldade da decisao**:
  - Easy (preflop fold): 0.8 - 1.5s
  - Medium (raise no flop, fold no turn/river): 2.0 - 4.0s
  - Hard (raise no turn/river): 4.0 - 12.0s
- **Click hold aleatorio**: entre 40ms e 120ms para simular pressao do botao do mouse.
- **Backend real via PyAutoGUI** (quando `TITAN_GHOST_MOUSE=1`) ou modo simulado seguro para CI/teste.

### Variaveis de ambiente do Ghost Mouse

- `TITAN_GHOST_MOUSE`: `0` (padrao, simulado) ou `1` (ativa PyAutoGUI real)
- `TITAN_BTN_FOLD`: coordenadas do botao fold, ex: `600,700`
- `TITAN_BTN_CALL`: coordenadas do botao call, ex: `800,700`
- `TITAN_BTN_RAISE_SMALL`: coordenadas do botao raise small, ex: `1000,700`
- `TITAN_BTN_RAISE_BIG`: coordenadas do botao raise big, ex: `1000,700`

`tools/action_tool.py` agora delega para o `GhostMouse` automaticamente, usando `classify_difficulty(action, street)` para determinar o timing adequado.

## Obfuscacao de colusao (Heads-Up)

Quando dois agentes do sistema ficam sozinhos na mesma mesa (heads-up), o `HiveBrain` seta `heads_up_obfuscation=true` no check-in.

O workflow detecta essa flag e:

- Converte `call` em `raise_small`
- Converte `raise_small` em `raise_big` (quando score >= 0.55)

Isso garante que observadores vejam combate real entre os agentes, nunca check-down.

## Equity Monte Carlo PLO (Villains)

Viloes agora sao avaliados no mesmo formato Omaha do hero (PLO4/PLO5/PLO6), nao mais como Hold'em (2 cartas). O numero de cartas por vilao e igual ao do hero (`len(hero_cards)`), e cada vilao usa `_evaluate_omaha_like()` com `combinations(hand, 2) x combinations(board, 3)`.

## Logs coloridos (terminal)

O terminal usa ANSI colors para demo/apresentacao via `utils/logger.py`:

- `[HiveBrain]` em magenta
- `[Orchestrator]` em ciano
- `[Agent]` em verde
- Niveis: `>` info (verde), `+` success (verde brilhante), `!` warn (amarelo), `X` error (vermelho), `*` highlight (negrito)
- Desativa com `TITAN_NO_COLOR=1` ou `NO_COLOR=1`
- Compativel com Windows Terminal, VS Code, e conhost (Windows 10+)

O `PokerAgent` agora usa `TitanLogger` para output colorido em vez de `print()` cru. Modo squad aparece com highlight.

## Auto-reconnect ZMQ (HiveBrain)

O servidor ZMQ agora reconecta automaticamente em caso de erro de socket:

- Ate 10 tentativas com backoff incremental (0.5s, 1.0s, ... ate 5.0s)
- Log colorido de cada tentativa e sucesso/falha
- Se exceder o limite, encerra graciosamente

## Observacao sobre exit code

- `python -m orchestrator.engine` e um loop continuo (nao encerra sozinho).
- Se interrompido por timeout/stop externo, pode aparecer codigo de saida diferente de `0` sem indicar crash.

## Proximos passos sugeridos

1. Treinar modelo YOLO com dataset de cartas PLO6 (canto superior esquerdo)
2. Calibrar GhostMouse com coordenadas reais do emulador
3. Teste end-to-end com emulador real + modelo YOLO treinado
4. Dashboard web para monitoramento em tempo real (mesas, agentes, RNG flags)
5. Telemetria contínua de produção (histórico de profiling + alertas de regressão)

### Funcionalidades já implementadas

- [x] Visão YOLO com 30 FPS polling + detecção de mudança de estado + is_my_turn
- [x] Detecção automática de jogadores ativos por frame
- [x] Auto-calibração de botões de ação a partir de YOLO
- [x] Cache de calibração por mesa (memória + arquivo + pruning + smoothing)
- [x] Protocolo Hive Mind (ZMQ + Redis + check-in + God Mode + dead_cards)
- [x] RNG Watchdog (Z-Score + SUPER_USER + Protocolo de Evasão)
- [x] Ghost Protocol (Bézier + timing variável + obfuscação heads-up)
- [x] Memória compartilhada via RedisMemory (com fallback in-memory)
- [x] Logs coloridos (TitanLogger) em HiveBrain, Orchestrator e Agent
- [x] Lançador de squad (bat + PowerShell) com auto-detecção de venv
- [x] Smoke test de squad (multi-agente + HiveBrain)
- [x] CI/CD com GitHub Actions
- [x] Utilitário operacional de cache de calibração
- [x] Profiling de performance do pipeline de visão a 30 FPS
