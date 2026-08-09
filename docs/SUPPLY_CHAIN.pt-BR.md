# Baseline de confiança da supply chain

Este documento define os controles P1.6a para confiança em dependências e CI. Ele é uma política
do projeto e apoio para revisão, não uma certificação. Geração de SBOM, attestations de artefatos,
assinatura de releases e provenance de containers ficam, de forma intencional, para os próximos
incrementos da P1.6.

## Threat model e controles

| Ameaça | Controle P1.6a | Risco residual |
| --- | --- | --- |
| Uma GitHub Action mutável ou comprometida executa no CI | Toda action de terceiros usa o SHA completo do commit; o quality gate rejeita referências mutáveis | Um commit confiável e fixado ainda pode conter defeito ou comprometimento |
| O token do workflow tem autoridade maior que a necessária | Todo workflow declara permissões explícitas; o validador P1.6a rejeita escrita | A confiança no runner hospedado e na plataforma permanece |
| Uma dependência vulnerável entra em uma atualização rotineira | Dependency Review bloqueia advisories high/critical novos reconhecidos; `pip-audit` verifica o ambiente travado | A cobertura do ecossistema e a base de advisories podem atrasar uma divulgação recente |
| Dependências ficam desatualizadas | Dependabot verifica Python/uv e GitHub Actions semanalmente com volume limitado de PRs | Maintainers ainda precisam revisar e integrar updates seguros |
| Uma dependência cria obrigações de licença incompatíveis | Dependency Review nega novos pacotes AGPL-3.0-only e GPL-3.0-only; toda licença nova é revisada | A detecção automática de licença pode ser incompleta ou incorreta |
| A automação introduz uma mudança incompatível | Updates minor/patch são agrupados, major ficam isolados e nenhum update recebe auto-merge | O CI não prova todas as integrações downstream |

## Política de aceitação de dependências

Uma dependência direta precisa ter necessidade de produto ou engenharia documentada, upstream
mantido, licença compatível com a distribuição pretendida e não duplicar uma capacidade mais
simples já disponível. A revisão cobre manifest, `uv.lock`, release notes, mudanças transitivas,
resultado do CI e advisories relevantes. Um PR verde do Dependabot é evidência, não aprovação
automática.

A licença do repositório não relicencia dependências. Adições GPL-2.0 e GPL-3.0, incluindo variantes
"or-later", e AGPL-3.0 são negadas por padrão porque suas obrigações recíprocas não combinam com o
modelo de reutilização pretendido para este template. Outras licenças ainda exigem revisão humana;
dados desconhecidos ou ambíguos precisam ser resolvidos antes do merge.

## Política para GitHub Actions

- Fixe actions remotas e workflows reutilizáveis no SHA completo de 40 caracteres, mantenha um
  comentário da release e desabilite a persistência das credenciais do checkout.
- Fixe actions em container pelo digest SHA-256. Actions locais podem usar path relativo.
- Mantenha as permissões dos workflows somente leitura na P1.6a. Escrita futura exige job isolado,
  atualização documentada do threat model e mudança da política executável no mesmo PR.
- Não exponha secrets do repositório a código não confiável de pull requests. Updates de supply
  chain não recebem auto-merge.
- Revise update de action como código executável: valide a release upstream e o intervalo de
  commits antes de aceitar o novo SHA.

## Fluxo de atualização e exceção

O Dependabot verifica os ecossistemas `uv` e `github-actions` toda segunda-feira. Updates minor e
patch são agrupados para controlar ruído; upgrades major ficam separados. Toda atualização precisa
passar pelos gates completos de qualidade, compatibilidade e E2E aplicáveis.

Uma exceção exige ADR revisável com owner, dependência ou workflow afetado, necessidade de negócio,
controles compensatórios, data de expiração ou revisão e plano de remoção. Exceções não podem conter
secrets, tokens, dados pessoais ou detalhes privados de advisories.

Administradores devem manter dependency graph, alertas do Dependabot e security updates do
Dependabot habilitados. Essas configurações complementam os version updates definidos em
`.github/dependabot.yml`.

## Evidência executável

Execute a validação focada com:

```bash
uv run python scripts/quality_gate.py --check supply-chain
```

A definição completa de pronto continua sendo:

```bash
uv run python scripts/quality_gate.py
```
