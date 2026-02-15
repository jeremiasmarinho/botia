## Resumo

- O que foi alterado neste PR:
- Por que essa mudança é necessária:

## Escopo

- [ ] Alteração focada (sem mudanças fora de escopo)
- [ ] Compatível com o fluxo atual (`run_windows.ps1`, `smoke_*`)

## Validação

- [ ] Testado localmente
- [ ] Evidências anexadas (logs/comandos/prints, quando aplicável)

Comandos executados:

```powershell
# exemplo
./scripts/smoke_all.ps1 -ReportDir reports
```

## Definition of Done (DoD)

- [ ] `./scripts/smoke_baseline.ps1 -ReportDir reports` executou com sucesso
- [ ] `./scripts/smoke_sweep.ps1 -ReportDir reports` executou com sucesso
- [ ] `./scripts/smoke_all.ps1 -ReportDir reports` executou com sucesso
- [ ] Workflow `Project Titan Smoke` passou no PR
- [ ] Documentação afetada foi atualizada (`README`, scripts)
- [ ] Branch protection com check obrigatório `smoke` está ativa em `main`

## Checklist final

- [ ] Não introduz segredos/credenciais
- [ ] Não quebra compatibilidade de parâmetros legados (ex.: `-LabelProfile` alias)
- [ ] Pronto para revisão
