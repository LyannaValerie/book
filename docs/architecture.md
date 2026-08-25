# Arquitetura do Book V1

## Fronteira

Book é experiência, evidência e orientação histórica. Não é autoridade factual atual. Casos, relações, eventos, Tasks e ladders são `UNTRUSTED_DATA`; código-fonte, testes, estado do sistema e autoridades federadas permanecem soberanos.

## Módulos

- `book.py`: launcher fino.
- `booklib/cli.py`: parsing, composição e saída JSON.
- `authoring.py`: payload semântico, TTY e mecânica de criação.
- `v0.py`: Case Store schema 1 preservado, CAS, validação e escrita atômica.
- `views.py` e `search.py`: navegação por facetas e busca lexical determinística.
- `relations.py` e `references.py`: grafo tipado e resolvers por namespace.
- `tasks.py`, `events.py` e `metrics.py`: contexto operacional curto, fatos append-only e projeções de custo.
- `retention.py`: decisão explícita entre criar, revisar, desafiar ou não escrever.
- `paths.py`: projeções derivadas de traces; nunca execução.
- `ladders.py`: procedimentos ramificados explicitamente promovidos.
- `index.py`: SQLite/FTS descartável.
- `maintenance.py`: verify, doctor, migração overlay e GC somente consultivo.

## Persistência

Canônico e Git-friendly:

```text
cases/*.json       OperationalCase
catalog/*.json     facetas/view por ID
relations/*.json   arestas tipadas
tasks/*.json       estado operacional curto
events/events.jsonl eventos factuais append-only
ladders/*.json     ProbeLadders explícitas
```

Derivado:

```text
.book/index.sqlite3
percursos calculados de Tasks + eventos
métricas calculadas do log
```

Apagar `.book/` não perde conhecimento. Reindex usa arquivo temporário e troca atômica; leituras canônicas não dependem do índice.

## Concorrência

Casos usam lock mínimo e compare-and-swap de revision hash. Eventos usam um lock dedicado, sequência monotônica, IDs idempotentes opcionais e hash chain. Tasks têm locks por ID. Reindex possui lock próprio e `os.replace`; leituras não exigem lock global.

## Recuperação progressiva

`list → search → show --metadata → show → references → resolve` é um repertório, não uma ordem obrigatória. O Book mede bytes/chars servidos para que um arquivo completo possa ser mais econômico do que fragmentação excessiva.
