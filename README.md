# Book

Book V1 é uma biblioteca universal, navegável e versionável de experiência operacional. Ela permite que sessões descartáveis de agentes recuperem conhecimento incrementalmente, registrem Tasks e observações, validem resultados e deixem traces reutilizáveis — sem transformar memória histórica em verdade atual.

```text
AUTHOR → TASK → NAVIGATE → RETRIEVE → RELATE → PROBE
       → OBSERVE → VALIDATE → RETAIN → DERIVE PATH
```

Todo conteúdo servido é `UNTRUSTED_DATA`. Fonte, teste, sistema atual e autoridades externas continuam soberanos em seus domínios. O Book nunca executa comandos encontrados em casos, relações, eventos ou percursos.

O manual completo de operação, autoria, importação, navegação, Tasks, retenção,
relações, percursos e manutenção está em
[`docs/user-guide.md`](docs/user-guide.md).

## Requisitos e início

Somente Python 3 e a biblioteca padrão são necessários.

```bash
python3 book.py --version
python3 book.py verify
python3 book.py doctor
python3 book.py list
```

Por padrão, a raiz é o diretório deste launcher. Use `--book DIRETÓRIO` para um corpus isolado. Use `--task T-...` antes do subcomando para associar acessos observáveis a uma Task.

## Autoria

Humano em TTY:

```bash
python3 book.py add-case
```

Agente por stdin ou payload inline:

```bash
printf '%s' '{"title":"...","cues":["..."],"problem":"...","observed_result":"...","guidance":"...","evidence":[{"class":"OBSERVED","description":"...","source":"event:E-..."}]}' \
  | python3 book.py add-case --stdin

python3 book.py add-case --json '{...}'
```

O Book gera ID, schema, status, timestamps e revisão. `import-case arquivo.json` é a interface explícita para representações canônicas externas. `add-case arquivo.json` permanece como compatibilidade V0 e equivale à importação.

## Navegação e recuperação

```bash
python3 book.py list
python3 book.py list Pinker/Runtime
python3 book.py search "runtime obsoleto" --within Pinker
python3 book.py show B-7K4M2Q9R6T3V --metadata
python3 book.py show B-7K4M2Q9R6T3V
python3 book.py references B-7K4M2Q9R6T3V
python3 book.py resolve book:B-7K4M2Q9R6T3V
```

Views são projeções de facetas; alterar uma view com `facet set` não altera o ID do caso.

## Relações e autoridades

```bash
python3 book.py relate B-... affects trama:chave --epistemic ASSERTED
python3 book.py references B-... --depth 2
python3 book.py resolve file:docs/security.md
```

Namespaces do núcleo: `book:`, `file:`, `git:` e `trama:`; `event:` pode ser usado como evidência interna. O adapter da Trama é opcional e somente consulta catálogo configurado — a Trama continua externa.

## Tasks, stop gate e métricas

```bash
python3 book.py task begin --goal "diagnosticar falha" --project repo --domain Pinker
python3 book.py --task T-... search "sintoma"
python3 book.py task missing T-... "onde expected é produzido?"
python3 book.py task next-probe T-... --action "inspecionar produção" \
  --observable "origem identificada ou não" --oracle "source atual" \
  --authorized --bounded --discriminative --no-high-risk-gap
python3 book.py event add --task T-... --kind probe-result --summary "origem identificada"
python3 book.py task validate T-... --oracle "teste X" --outcome PASS --passed
python3 book.py task finish T-... --outcome "corrigido e verificado"
python3 book.py task receipt T-...
```

`receipt` separa conhecimento carregado, lacunas, próximo probe, validação e métricas. `estimated_tokens` é explicitamente estimado; acesso, utilidade e validade são sinais distintos.

Uma Task de outro sistema mantém sua própria identidade. Associe-a sem reutilizar
o ID externo:

```bash
python3 book.py task begin --goal "..." --project repo --domain Pinker \
  --external-task "pinker:#520"
```

O Book gera `T-...` e persiste `external_task_ref=pinker:#520`.

## Retenção, percursos e ladders

```bash
python3 book.py retention assess --stdin
python3 book.py retention record --task T-... --decision no-op --summary "nada novo"
python3 book.py use B-... --as support --task T-...
python3 book.py path search --success "semantic"
python3 book.py ladder add --stdin
```

Percursos são derivados de traces de Tasks finalizadas e validadas. `path != solution`; nada é executado automaticamente. ProbeLadders são procedimentos ramificados explicitamente promovidos, nunca inferidos de um trace único.

## Manutenção

```bash
python3 book.py verify
python3 book.py doctor
python3 book.py reindex
python3 book.py gc candidates
python3 book.py migrate-v0
```

O índice SQLite/FTS é `DERIVED` e pode ser apagado. `gc candidates` somente lista; não remove dados.

## Desenvolvimento

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
python3 book.py verify
git diff --check
```

Detalhes: [arquitetura](docs/architecture.md), [modelo de dados](docs/data-model.md), [protocolo de agentes](docs/agent-protocol.md) e [segurança](docs/security.md).
