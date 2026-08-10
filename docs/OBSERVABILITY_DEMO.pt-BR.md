# P1.7c — Demo MCP observável

O P1.7c adiciona um overlay opcional de observabilidade à topologia Docker Compose do P1.7b.
P1.7a e P1.7b continuam utilizáveis sem exportação de telemetria.

```text
MCP client ──contexto W3C──▶ MCP server
    │                           │
    └────── a2a-otel-kit ───────┘
                │
           OTLP/HTTP :4318
                ▼
     OpenTelemetry Collector
          │             │
          │ receipt     └──▶ Tempo :4319
          │                      │
          └──────────────────────┤
                                 ▼
                            Grafana :3000
```

Execute com limpeza automática:

```bash
./scripts/run_observability_demo.sh
```

Mantenha o Grafana ativo após a verificação:

```bash
./scripts/run_observability_demo.sh --keep
```

Acesse `http://127.0.0.1:3000`. Para parar:

```bash
./scripts/stop_observability_demo.sh
```

## Prova positiva

O client one-shot agora possui um único trace raiz `demo.reference_flow` e permanece vivo
até o receipt do Collector conter os spans MCP client e server desse trace. Isso segue a
ordem receipt-positivo-antes-do-shutdown usada pelo próprio `a2a-otel-kit`.

A execução falha se não comprovar:

- readiness de Collector, Tempo e Grafana;
- receipt positivo do Collector;
- spans `mcp.client.streamable_http` e `mcp.server.streamable_http` no mesmo `trace_id`;
- resources dos serviços client e server;
- consulta do mesmo trace no Tempo pelo trace ID;
- datasource Tempo provisionado no Grafana;
- ausência de valores conhecidos dos fixtures OAuth/MCP na telemetria.

Isso é mais forte do que validar apenas flush do exporter ou reachability.

O wrapper encerra qualquer Collector anterior antes de recriar `traces.jsonl`. Assim, o receipt nunca é truncado enquanto um Collector pode manter o arquivo aberto. O verifier final seleciona exatamente o trace `demo.reference_flow`.

O file exporter do Collector usa explicitamente `append: true`. Sem esse modo, o exporter pode truncar o arquivo entre batches, o que é incompatível com um receipt que precisa acumular spans de client e server na mesma execução distribuída.

O Tempo 2.9 é ajustado intencionalmente para esta prova local com `max_block_duration` de 5 segundos, checks de idle/flush de 1 segundo e retenção de bloco completo por 1 minuto no ingester. Os defaults de produção são muito maiores; esses valores curtos existem apenas para a demo comprovar a recuperação do trace no backend dentro da janela de verificação de 30 segundos.

O backend local do Tempo também usa `storage.trace.blocklist_poll: 1s`. O Tempo mantém uma blocklist em memória e não descobre blocos recém-flushados consultando o backend a cada request. O intervalo de 1 segundo conecta o flush de 5 segundos da demo ao `complete_block_timeout` de 1 minuto e elimina a janela em que o trace já saiu do ingester, mas ainda não está visível ao querier.

Todos os comandos one-shot após o startup usam `docker compose run --no-deps`. O wrapper também captura os IDs dos containers de server, Collector, Tempo e Grafana logo após a subida e rejeita a verificação se qualquer ID mudar. Assim, receipt, ingestão no backend e consulta do trace pertencem sempre à mesma geração de containers.

## Fronteira de rede

Todos os serviços de observabilidade entram no namespace do Server. Collector usa
`127.0.0.1:4318`, Tempo recebe OTLP em `127.0.0.1:4319` e Grafana consulta Tempo em
`127.0.0.1:3200`. Somente Grafana é publicado no host, em `127.0.0.1:3000`.

Anonymous Admin no Grafana é exclusivo desta demo local e não deve ser copiado para produção.
