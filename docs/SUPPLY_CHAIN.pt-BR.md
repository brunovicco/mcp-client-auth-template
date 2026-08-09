# Baseline de confiança da supply chain

Este documento define os controles P1.6 para confiança em dependências, CI e inventário de software.
Ele é uma política do projeto e apoio para revisão, não uma certificação. Attestations de artefatos,
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

## Evidências de SBOM e vulnerabilidades (P1.6b)

O workflow `supply-chain-evidence` cria dois inventários CycloneDX JSON com Syft: uma visão do código
a partir do `uv.lock` commitado, incluindo o grafo declarado, e uma visão de runtime da imagem final,
incluindo pacotes Python e do sistema operacional instalados. Grype registra o relatório completo
de vulnerabilidades da imagem e reprova o workflow para achados high ou critical que tenham correção
disponível. Achados sem correção continuam visíveis no artifact completo.

O gate avalia o mesmo relatório salvo contra `security/vulnerability-exceptions.json`. Uma exceção
precisa corresponder exatamente ao namespace do advisory, tipo de pacote e versão instalada;
identificar owner, data de revisão, expiração, justificativa e plano de remoção; e durar no máximo
90 dias. Exceções expiradas, obsoletas, duplicadas ou incompatíveis com uma nova versão falham de
forma fechada. As exceções atuais do CPython existem apenas porque o Grype lista correções fora das
linhas estáveis suportadas, expiram em 2026-09-30 e continuam no relatório completo. A
[ADR-0020](adr/0020-actionable-vulnerability-exceptions.md) registra a decisão e o follow-up.

Syft e Grype são baixados apenas de URLs de releases imutáveis e exatas. Versões e checksums SHA-256
por plataforma ficam commitados em `scripts/install_security_tools.sh`; qualquer divergência impede
a execução dos binários. O artifact é retido por 14 dias e contém os dois SBOMs, o relatório Grype
completo e o resultado minimizado da política. Ele não contém credenciais, conteúdo de código,
dados de requisição, tokens, prompts ou payloads MCP.

Nesta etapa, a evidência permanece como artifact do Actions. Publicar SBOMs em releases, vinculá-los
a digests imutáveis de imagens e adicionar attestations pertence à P1.6c/P1.6d.

## Evidência executável

Execute a validação focada com:

```bash
uv run python scripts/quality_gate.py --check supply-chain
```

A definição completa de pronto continua sendo:

```bash
uv run python scripts/quality_gate.py
```
