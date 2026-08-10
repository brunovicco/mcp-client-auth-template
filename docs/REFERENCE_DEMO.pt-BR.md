# P1.7a — Demo de referência headless em um comando

O P1.7a transforma a evidência E2E entre repositórios em uma demo executável voltada também para
portfólio. A demo usa o cliente real e o servidor companheiro real, substituindo apenas o
authorization server externo pelo OIDC local e determinístico já usado na suíte E2E.

## Executar

Clone os repositórios lado a lado:

```text
workspace/
├── mcp-client-auth-template/
└── mcp-server-auth-template/
```

No repositório do client, execute apenas:

```bash
./scripts/run_reference_demo.sh
```

Para usar outro checkout do server:

```bash
./scripts/run_reference_demo.sh --server-root /caminho/para/mcp-server-auth-template
```

Para saída legível por máquina:

```bash
./scripts/run_reference_demo.sh --json
```

O wrapper sincroniza o ambiente travado do client, instala o server local nesse ambiente e executa
a demo. Não é necessário cloud account, IdP real, browser, credencial de produção ou Docker daemon.

## O que a demo prova

A demo sobe os dois serviços locais em portas loopback efêmeras e executa:

```text
client MCP real
  -> server/discover
  -> 401 + Protected Resource Metadata
  -> discovery OIDC local
  -> cliente público CIMD-first
  -> Authorization Code + PKCE
  -> validação do issuer da resposta conforme RFC 9207
  -> access token vinculado ao resource conforme RFC 8707
  -> whoami autenticado
  -> health
  -> 403 insufficient_scope
  -> reautorização limitada com scope anterior + health
  -> health com sucesso
  -> whoami elevado
  -> JWT propositalmente emitido para audience errada
  -> rejeição 401
  -> probe com Mcp-Session-Id de aparência legada
  -> request aceito sem criar sessão de protocolo
```

Os contadores do authorization server também são validados. A evidência esperada é:

- zero Dynamic Client Registrations porque CIMD é preferido;
- duas autorizações;
- duas trocas de token;
- scope inicial `mcp:tools:call`;
- scope elevado `mcp:tools:call mcp:tools:health`;
- audience de recurso incorreta rejeitada com HTTP `401`;
- protocolo MCP negociado exatamente `2026-07-28`;
- nenhum `Mcp-Session-Id` retornado.

## Saída

Uma execução bem-sucedida termina com:

```text
P1.7a REFERENCE DEMO PASSED
OAuth:    CIMD-first Authorization Code + PKCE
MCP:      2026-07-28, authenticated whoami + health
Step-up:  mcp:tools:call -> + mcp:tools:health
Audience: wrong-resource JWT rejected with HTTP 401
State:    no protocol-level session minted
```

A demo também produz um resumo JSON. Com `--json`, somente esse resumo é escrito no stdout,
permitindo reutilização futura em CI e automação de portfólio.

## Fronteira de segurança

Esta é uma demo local de referência, não um simulador de IdP para produção:

- o issuer OIDC escuta apenas em `127.0.0.1`;
- chaves e identidades são sintéticas e geradas para o processo local;
- access tokens ficam em memória e nunca são impressos;
- nenhum browser real é aberto;
- nenhuma credencial externa é lida;
- logs dos processos filhos ficam somente em diretório temporário para diagnóstico de falha e são
  removidos ao final;
- o server real continua validando assinatura, issuer, audience, expiração e scopes.

Para a matriz positiva e fail-closed mais ampla, veja [E2E entre repositórios](E2E.md).
