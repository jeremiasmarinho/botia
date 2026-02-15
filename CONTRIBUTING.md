# Contributing

Este repositório usa um fluxo de PR com smoke tests e checks obrigatórios.

## Fluxo recomendado

1. Faça alterações focadas (sem expandir escopo sem necessidade).
2. Rode os smokes locais antes de abrir PR:

```powershell
cd project_titan
./scripts/smoke_baseline.ps1 -ReportDir reports
./scripts/smoke_sweep.ps1 -ReportDir reports
./scripts/smoke_all.ps1 -ReportDir reports
```

3. Abra PR usando o template em `.github/PULL_REQUEST_TEMPLATE.md`.
4. Aguarde o workflow `Project Titan Smoke` passar.
5. Só faça merge quando todos os checks obrigatórios estiverem verdes.

## Baseline e sweeps

- Baseline rápido (texto):

```powershell
cd project_titan
./scripts/print_baseline.ps1 -ReportDir reports
```

- Baseline rápido (JSON):

```powershell
cd project_titan
./scripts/print_baseline.ps1 -ReportDir reports -Json
```

## Convenções

- Preferir `-LabelMode` (`-LabelProfile` é alias legado).
- Atualizar documentação quando alterar scripts ou fluxo.
- Evitar commits com artefatos temporários não necessários.

## Troubleshooting rápido

- **`Python não encontrado` no `run_windows.ps1`**
  - Ative o ambiente virtual em `C:\botia\.venv` ou garanta `python` no `PATH`.

- **`smoke_sweep.ps1` não gera `sweep_summary_*.json`**
  - Confirme que o comando está rodando em `project_titan` e com `-ReportDir` válido.
  - Rode novamente com:
    - `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 6 -ProfileSweep -ReportDir reports`
    - `./scripts/run_windows.ps1 -SimScenario cycle -Ticks 6 -PositionSweep -ReportDir reports`

- **`print_baseline.ps1` retorna baseline manual/fallback**
  - Gere baseline via sweep e salve baseline fixo:
    - `./scripts/run_windows.ps1 -HealthOnly -UseBestBaseline -SaveBestBaseline -ReportDir reports`

- **Alias legado de label**
  - Preferir `-LabelMode dataset_v1`.
  - `-LabelProfile dataset_v1` continua aceito por compatibilidade.

## Troubleshooting CI (GitHub Actions)

- **Workflow `Project Titan Smoke` falhou no PR**
  - Abra a execução no GitHub Actions.
  - Baixe o artifact `project-titan-reports`.
  - Reproduza localmente com:
    - `cd project_titan`
    - `./scripts/smoke_all.ps1 -ReportDir reports`

- **Falha no passo `Install dependencies`**
  - Valide `requirements.txt` localmente com:
    - `python -m pip install -r project_titan/requirements.txt`

- **Falha no passo `Run smoke_all`**
  - Rode os smokes de forma isolada para localizar causa:
    - `./scripts/smoke_baseline.ps1 -ReportDir reports`
    - `./scripts/smoke_sweep.ps1 -ReportDir reports`

- **Precisa compartilhar contexto completo para debug de CI**
  - Gere um pacote local com scripts, docs, governança e reports:
    - `cd project_titan`
    - `./scripts/collect_ci_debug.ps1 -ReportDir reports -OutputDir reports`
  - O comando imprime o caminho do ZIP (`ci_debug_bundle_*.zip`) para anexar no PR/issue.

- **Checagem obrigatória não aparece em Branch Protection**
  - Execute o workflow pelo menos uma vez na branch padrão.
  - Depois selecione o job `smoke` em `Settings > Branches > Require status checks`.
