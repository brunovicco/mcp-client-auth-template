# Baseline de confiança da supply chain

Este documento define os controles P1.6 para confiança em dependências, CI, inventário de software,
provenance de artifacts e integridade de releases. Ele é uma política do projeto e apoio para
revisão, não uma certificação.

## Threat model e controles

| Ameaça | Controle P1.6 | Risco residual |
| --- | --- | --- |
| Uma GitHub Action mutável ou comprometida executa no CI | Toda action de terceiros usa o SHA completo do commit; o quality gate rejeita referências mutáveis | Um commit confiável e fixado ainda pode conter defeito ou comprometimento |
| O token do workflow tem autoridade maior que a necessária | Todo workflow declara permissões explícitas; escritas de release, registry e attestation ficam isoladas por job | A confiança no runner, no OIDC do GitHub e na plataforma permanece |
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
- Mantenha os workflows somente leitura por padrão. No workflow de release acionado por tag, o
  build Python pode escrever attestations, o job do container pode escrever no GHCR e em
  attestations, e o job final pode escrever contents da GitHub Release. Nenhum job recebe as três
  autoridades. Qualquer nova escrita exige atualização do threat model e da política executável.
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

A evidência P1.6b de pull request/`main` permanece como artifact do Actions. A publicação por tag
regenera e revalida a mesma evidência antes de publicar a imagem da release.

## Artifacts de release reproduzíveis e provenance (P1.6c)

O workflow `secure-release-publication` executa somente após o push de uma tag `v*`. Ele exige que
a tag corresponda à versão em `pyproject.toml`, cria wheel e source distribution duas vezes com
`SOURCE_DATE_EPOCH` derivado do commit e exige igualdade byte a byte entre os dois conjuntos. O
validador de release verifica paths dos archives, metadata do pacote, nomes esperados e uma fronteira
estrita de conteúdo antes de copiar os artifacts e gerar `SHA256SUMS`.

O backend Hatchling fica fixado de forma exata em `pyproject.toml`; seu ambiente transitivo e
isolado de build fica fixado em `build-constraints.txt` e é passado às duas execuções de
`uv build`. Assim, uma resolução futura do backend/dependências não altera silenciosamente os
bytes da mesma tag.

A seleção padrão de sdist do Hatch pode incluir qualquer arquivo não ignorado pelo VCS local. A
configuração explícita `only-include` impede que configuração local de assistants, worktrees,
credenciais e automação não relacionada entrem no source release. O validador do archive aplica a
mesma fronteira de forma independente, portanto drift de configuração falha de forma fechada.

Depois da validação, os subjects SHA-256 recebem attestations de build provenance GitHub/Sigstore
por uma `actions/attest` fixada por SHA. O job Python pode emitir identidade OIDC e escrever
attestations e metadata de artifacts, mas não pode escrever em contents, packages, releases ou
registries.

```bash
sha256sum --check SHA256SUMS
gh attestation verify mcp_client_auth_template-<versao>-py3-none-any.whl \
  --repo brunovicco/mcp-client-auth-template
```

## Publicação segura de release e provenance de container (P1.6d)

O mesmo workflow de tag agora separa três autoridades. `build-python-artifacts` cria e atesta os
pacotes reproduzíveis. `publish-container` constrói a imagem de produção localmente, gera SBOMs
CycloneDX de source/imagem, registra o relatório Grype completo e aplica a política fail-closed de
exceções antes de receber um token do GHCR. `publish-github-release` recebe apenas
`contents: write` e só executa depois do sucesso dos outros jobs.

A partir da v0.6.0, a fronteira do container é multi-platform. O job constrói
`linux/amd64` e `linux/arm64` localmente, gera evidências CycloneDX/Grype/política independentes para
cada arquitetura e só autentica no GHCR depois que ambas as políticas passam. As imagens locais
exatas que foram escaneadas são publicadas sob tags imutáveis de versão/commit específicas por
arquitetura; não há rebuild após o scan. O workflow então cria os índices OCI de versão e commit a
partir dos dois digests canônicos e exige que ambas as tags de índice resolvam para o mesmo digest.
`image-platforms.json` registra e valida esse mapeamento.

O índice OCI final recebe build provenance. Cada digest de plataforma recebe sua própria attestation
de SBOM CycloneDX. A GitHub Release contém wheel, sdist, manifesto de checksums dos pacotes, SBOM de
source, SBOMs de imagem por plataforma, relatórios Grype completos, resultados de política por
plataforma, subject final da imagem, `image-platforms.json`, `release-manifest.json` e
`RELEASE_SHA256SUMS`. O validador aceita somente esse conjunto e vincula tag, commit, repositório,
digest final do índice, conjunto exato de plataformas e digests das plataformas antes de
`gh release create --verify-tag`. A publicação no PyPI continua fora de escopo.

A visibilidade do package no GHCR é uma configuração administrativa, não uma permissão do workflow.
Depois da primeira publicação, torne o package de container público caso o consumo anônimo seja
desejado; até lá, `docker pull` e a verificação OCI exigem autenticação no GHCR. O workflow não
altera visibilidade de packages de forma deliberada.

Verifique uma release antes do uso:

```bash
gh release download v<versao> \
  --repo brunovicco/mcp-client-auth-template \
  --dir release
(cd release && sha256sum --check RELEASE_SHA256SUMS)
gh attestation verify release/mcp_client_auth_template-<versao>-py3-none-any.whl \
  --repo brunovicco/mcp-client-auth-template
image="$(cat release/image-digest.txt)"
gh attestation verify "oci://${image}" \
  --repo brunovicco/mcp-client-auth-template
docker pull "$image"
```

Para uma versão coordenada server/client, publique e verifique primeiro o server. Publique a tag do
client somente depois que os assets e o digest do server forem aprovados. Essa ordem evita anunciar
um client cujo companion server esteja incompleto e não cria dependência de CI entre repositórios.
A [ADR-0022](adr/0022-secure-release-publication.md) registra a separação de autoridade e a cerimônia de release.
A [ADR-0023](adr/0023-multi-platform-release-publication.md) registra a decisão multi-platform de scan antes da publicação.

## Evidência executável

Execute a validação focada com:

```bash
uv run python scripts/quality_gate.py --check supply-chain
```

A definição completa de pronto continua sendo:

```bash
uv run python scripts/quality_gate.py
```
