# Project Titan - Ambiente de Simulação Local (PoC)

Este módulo é uma prova de conceito offline para pesquisa acadêmica e demonstração técnica.

## Módulos

- `vision/abstract_vision.py`
  - Captura uma janela genérica do Windows por título
  - Executa detecção YOLO para classes de cartas
- `logic/decision_server.py`
  - Servidor HTTP local
  - Recebe JSON e retorna equidade por Monte Carlo
- `debug/debug_interface.py`
  - Desenha retângulo de clique simulado
  - Não executa clique físico

## Execução

### 1) Servidor de decisão

`python -m simulator.logic.decision_server`

Teste:

`curl -X POST http://127.0.0.1:8765/equity -H "Content-Type: application/json" -d '{"hero_cards":["Ah","Kd"],"board_cards":["2c","7d","Ts"],"simulations":3000}'`

### 2) Visão abstrata

`python -m simulator.vision.abstract_vision --window-title "Minha Janela" --model "C:/models/cards.pt" --interval 1.0`

### 3) Interface de debug

`python -m simulator.debug.debug_interface --rect-size 90 --interval 0.5`

## Observações

- Uso exclusivo local/offline para demonstração e pesquisa.
- Não interage com apps de terceiros via automação de clique.
