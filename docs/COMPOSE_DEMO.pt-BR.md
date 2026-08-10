# P1.7b — Demo de referência com Docker Compose

O P1.7b empacota o cenário OAuth/MCP validado no P1.7a em três containers, preservando a fronteira
de segurança de rede existente.

```text
fake OIDC :9000  <──>  MCP Server :8000  <──>  demo client
     127.0.0.1              127.0.0.1             127.0.0.1
```

Os serviços OIDC e demo entram no namespace de rede do container Server. Isso é proposital:
nomes DNS normais do Compose resolvem para endereços privados da bridge, enquanto estes
repositórios permitem HTTP inseguro somente em desenvolvimento loopback explícito. O P1.7b mantém
tráfego real em `127.0.0.1` em vez de criar um bypass de SSRF/TLS específico para Docker.

Execute:

```bash
./scripts/run_compose_demo.sh
```

O server é consumido por digest imutável:

```text
ghcr.io/brunovicco/mcp-server-auth-template@sha256:39d50ff235df634ef6c4b0d8a4cdef4c4c3be00094fce464eabafea88f216d9a
```

A demo reutiliza exatamente o cenário do P1.7a e prova MCP `2026-07-28`, Authorization Code + PKCE
CIMD-first, catálogo protegido no acesso anônimo, `whoami` autenticado, step-up limitado via
`health`, rejeição de audience incorreta e ausência de sessão MCP.

O resumo JSON identifica a topologia como `docker-compose-shared-loopback`.

Todos os serviços usam filesystem raiz somente leitura, `/tmp` efêmero, Linux capabilities
removidas, `no-new-privileges` e nenhuma porta publicada no host. Não há Docker socket, mount do
source do host, credencial de produção ou volume persistente de token.

Esta é uma topologia local determinística de demonstração, não um modelo recomendado para produção.
