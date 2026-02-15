PROJETO TITAN: Especificação Técnica Completa
Tipo de Projeto: Sistema Distribuído de IA Multi-Agente para Ambientes Estocásticos (Poker PLO6) com Auditoria Forense.

1. Visão Geral e Stack Tecnológica
   O objetivo é criar um sistema autônomo capaz de jogar Poker (Omaha 5 e 6 cartas) em nível super-humano, utilizando múltiplos agentes coordenados (Swarm Intelligence) e auditando a integridade do RNG da plataforma em tempo real.

Tech Stack Obrigatória:

Linguagem: Python 3.11+

Visão Computacional: YOLOv8 (Ultralytics) + MSS (Screen Capture de baixa latência).

Comunicação (IPC): ZeroMQ (pyzmq) para troca de mensagens ultrarrápida (<2ms).

Banco de Dados/Memória: Redis (para estado compartilhado e cache de sessões).

Matemática/Estatística: NumPy, SciPy (para Z-Score) e bibliotecas de avaliação de mãos (ex: treys ou ompeval wrapper).

Automação de Input: PyAutoGUI (com curvas de Bezier customizadas).

2. Arquitetura do Sistema (Cliente-Servidor)
   O sistema deve ser separado em dois módulos distintos para garantir performance e segurança.

Módulo A: O Servidor ("Hive Brain")
Responsabilidade: Centralizar a inteligência, calcular equidade, gerenciar o estado das mesas e detectar colusão amigável.

Componentes:

Servidor ZMQ (REP): Escuta requisições na porta 5555.

Gerenciador de Squads: Identifica quais agentes estão na mesma mesa (baseado no table_id).

Motor Matemático: Calcula a equidade da mão usando "Card Removal" (remove do baralho as cartas conhecidas dos parceiros).

Auditor RNG: Registra resultados de showdown para análise estatística.

Módulo B: O Cliente ("Agente Zumbi")
Responsabilidade: Rodar na máquina do emulador. Apenas "vê" e "age".

Componentes:

Detector Visual (YOLO): Lê as 6 cartas, o board, o pote e os stacks.

Cliente ZMQ (REQ): Envia o estado atual para o servidor e recebe a instrução (Fold/Call/Raise).

Atuador Humano (Ghost Mouse): Executa o clique com curvas naturais.

3. Detalhamento das Funcionalidades (Features)
   Feature 1: Visão Computacional Especializada (PLO6)
   Problema: As cartas no Omaha 6 ficam sobrepostas (amontoadas).

Solução: O modelo YOLO deve ser treinado para identificar Rank e Suit vendo apenas o canto superior esquerdo (15% da carta).

Requisito: O sistema deve capturar a tela a 30FPS e detectar mudanças de estado (ex: "Minha Vez" ativado).

Feature 2: Protocolo "Hive Mind" (Inteligência de Enxame)
Implementar lógica de cooperação dinâmica:

Check-in: A cada nova mão, o Agente envia {'mesa_id': 'X', 'cartas': [...]} para o Redis.

Detecção de Parceiro: O Servidor verifica se há outro agente na mesa_id 'X'.

Modo Solo: Calcula equidade padrão (Monte Carlo).

Modo Squad (God Mode): Se houver parceiros, adiciona as cartas deles na lista de dead_cards (cartas impossíveis do vilão ter). A precisão do cálculo sobe drasticamente.

Fail-Safe: Usar redis.expire de 5 segundos. Se um agente cair, ele é removido do cálculo imediatamente para não corromper a matemática.

Feature 3: Auditoria de Integridade (RNG Watchdog)
O sistema deve auditar se a plataforma é honesta.

Monitoramento de EV (Expected Value):

Calcular a equidade no momento do All-in.

Comparar com o resultado real (Ganhou/Perdeu).

Cálculo Z-Score (Desvio Padrão):

Manter histórico de Sorte para cada vilão.

Se Z-Score > 3.0 (3 Sigmas), marcar vilão como "SUPER USER".

Ação: O bot deve ativar o "Protocolo de Evasão" (Foldar tudo contra esse vilão).

Feature 4: Humanização (Ghost Protocol)
Para evitar detecção por comportamento robótico:

Curvas de Bezier: Jamais mover o mouse em linha reta. Implementar função de ruído e arcos.

Timing Variável:

Decisão Fácil (Pré-flop Fold): 0.8s - 1.5s.

Decisão Difícil (River Bluff): 4.0s - 12.0s (Simular pensamento).

Ofuscação de Colusão: Se dois agentes do sistema ficarem sozinhos na mão (Heads-up), eles devem jogar agressivamente um contra o outro (não dar check-down), para parecerem inimigos.

4. Estrutura de Arquivos Sugerida
   Por favor, gere o código seguindo esta estrutura:

Plaintext
project_titan/
│
├── core/
│ ├── hive_brain.py # Servidor Central (ZMQ + Redis + Logic)
│ ├── math_engine.py # Cálculos de Monte Carlo e Equidade PLO6
│ └── rng_auditor.py # Análise estatística de integridade
│
├── agent/
│ ├── poker_agent.py # Cliente principal (Loop de jogo)
│ ├── vision_yolo.py # Módulo de detecção visual
│ └── ghost_mouse.py # Controle de mouse humanizado
│
├── utils/
│ ├── card_utils.py # Conversores de cartas (ex: "Ah" -> 51)
│ └── config.py # Configurações de IP, Portas e Limites
│
└── start_squad.bat # Script para lançar múltiplos agentes 5. Instruções Finais para o Desenvolvedor (AI)
Priorize a baixa latência. O cálculo da Hive Mind deve ocorrer em menos de 50ms.

O código deve ser robusto a falhas de rede (Reconexão automática do ZMQ).

Inclua logs coloridos no terminal do Servidor para demonstrar a conexão dos agentes ("Agente 01 conectado", "Modo GOD MODE ativado") durante a apresentação.
