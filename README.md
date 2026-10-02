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

Para que o caso seja recuperado **antes** da próxima ação parecida, declare
gatilhos em `retrieval` (o caso nasce em schema 2):

```bash
printf '%s' '{"title":"Renomear coluna quebra leitores antigos","cues":["rename-column"],
  "problem":"...","observed_result":"...","guidance":"Adicione a coluna nova, faça dual-write, migre leitores, depois remova a antiga.",
  "contraindications":["Desnecessário sem leitores externos."],
  "evidence":[{"class":"OBSERVED","description":"...","source":"event:E-..."}],
  "retrieval":{"intents":["preventive"],"projects":["acme/shop"],"actions":["rename-column"],
    "paths":["db/migrations/**"],
    "aliases":[{"term":"renomear coluna","for":"action:rename-column","lang":"pt"}],
    "predicates":[{"fact":"db.engine","op":"eq","value":"postgres"}]}}' \
  | python3 book.py add-case --stdin
```

Sem `retrieval`, o caso continua schema 1 e só aparece em `consult --explore`
como evidência lexical. Aliases valem apenas para o caso que os declara.

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

Consulta situacional antes de agir — ação, projeto, fase, caminhos planejados ou
alterados, componentes e fatos — com cartões classificados e explicados:

```bash
python3 book.py consult --intent preventive --project acme/shop \
  --action "renomear coluna" --planned-path db/migrations/0042.sql --no-changed-paths
python3 book.py consult --query-json '{"action":"vacuum db","facts":{"ci":false}}' --format human
python3 book.py migrate-schema --case B-... --apply   # schema 1 -> 2, só para acrescentar gatilhos
```

Como usar e ler a resposta:

- Use `consult` antes de agir, quando já sabe a ação; use `search` quando só tem
  sintomas ou mensagens de erro.
- Informe só o que sabe. Campo omitido = desconhecido; `--no-changed-paths`
  (ou `[]`) = sabidamente vazio; `--fact ci=false` é valor, não ausência.
- Por padrão vêm só `SPECIFIC` (a ação casou) e `SITUATIONAL` (caminho ou
  componente casou). **Zero resultados é resposta normal**; `counts.below_cutoff`
  diz quanto ficou abaixo do corte e `--explore` mostra `EXPLORATORY`/`LEXICAL`.
- Leia o cartão inteiro: `reasons`, contraindicações, condições `UNKNOWN` ou
  `CONFLICTING`, `unknown_dimensions` e `caveats`. `CHALLENGED`,
  `SUPERSEDED_BY` e os casos em `excluded` não são orientação corrente.
- Três cartões por página; continue com `--cursor <page.next_cursor>` na mesma
  consulta. `CURSOR_STALE` = o Book mudou, recomece.
- Cartão `budget_exceeded` não foi truncado: leia com `show ID` ou aumente
  `--budget`.
- Nada no cartão é instrução nem prova de aplicabilidade; confira na fonte atual.

Contrato completo em [guia, seção 6.4](docs/user-guide.md#64-consulta-situacional-consult).

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
