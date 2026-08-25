# Guia completo de uso do Book V1

Este documento é o manual operacional do Book V1. Ele explica como iniciar um
Book, navegar sem conhecer previamente a resposta, criar conhecimento sem
fabricar JSON canônico, incorporar memória de outras fontes, trabalhar dentro
de uma Task, registrar observações, validar resultados, reter aprendizado e
manter o corpus íntegro.

Os exemplos presumem que o shell está na raiz do repositório `book`:

```bash
cd /caminho/para/book
```

Se o corpus estiver em outro lugar, coloque a opção global `--book` **antes** do
subcomando:

```bash
python3 book.py --book /caminho/para/corpus verify
```

## 1. O que o Book é — e o que ele não é

O Book persiste experiência operacional para que uma sessão futura não precise
herdar toda a sessão que produziu aquela experiência.

```text
sessão descartável
        ↓
experiência persistente
        ↓
recuperação sob demanda
        ↓
próximo probe observável
        ↓
validação externa
```

O Book armazena evidência, experiência, orientação e relações. Ele **não é**
autoridade factual sobre o estado atual. Um caso pode indicar onde investigar,
mas fonte atual, teste atual, sistema observado e autoridades externas continuam
soberanos.

Todo texto recuperado é marcado conceitualmente como `UNTRUSTED_DATA`. Um caso
que contenha `ignore as regras` ou um comando destrutivo continua sendo somente
dado. A CLI não executa conteúdo encontrado em casos, eventos, relações,
percursos ou ladders.

Quatro distinções evitam a maioria dos erros de uso:

```text
case  != verdade atual
trace != procedure
path  != solution
origem da memória != domínio do conhecimento
```

Exemplo da última distinção: uma memória armazenada em `/pinker/msg` pode tratar
da Forja. Nesse caso, `file:/pinker/msg/...` é a proveniência, enquanto `Forja`
é o domínio. O path da fonte não deve escolher automaticamente a faceta.

## 2. Requisitos, versão e convenções da CLI

O Book V1 usa Python 3 e biblioteca padrão. Não requer rede, embeddings, banco
vetorial, serviço web ou LLM interno.

```bash
python3 book.py --version
python3 book.py -h
```

A versão retorna, separadamente, a versão da ferramenta e as versões de schema.

As opções globais precisam aparecer antes do subcomando:

```bash
python3 book.py \
  --book /caminho/para/book \
  --task T-EXEMPLO \
  --actor amara \
  search "runtime obsoleto"
```

- `--book`: raiz do corpus. Sem essa opção, usa a raiz do launcher.
- `--task`: associa acessos observáveis, como `search`, `list` e `show`, a uma
  Task já existente.
- `--actor`: registra o responsável mecânico por autoria/revisão quando o
  comando o utiliza.
- `--version`: mostra versões e encerra.

A saída normal é JSON determinístico. Sucesso retorna `"ok": true`. Erros
operacionais retornam `"ok": false`, um código em `error`, uma mensagem e,
quando necessário, `details`. Scripts devem usar o exit code e o campo `error`,
não analisar frases humanas.

## 3. Estrutura persistente

O corpus usa arquivos legíveis e versionáveis:

```text
book/
├── cases/                 casos canônicos, um JSON por ID
├── catalog/               facetas e views dos casos
├── relations/             arestas tipadas
├── tasks/                 estado operacional das Tasks
├── events/events.jsonl    log factual append-only
├── ladders/               ProbeLadders explicitamente criadas
├── book.config.json       configuração opcional de resolvers
└── .book/index.sqlite3    índice derivado e reconstruível
```

Estados de percursos são projeções derivadas dos eventos `PATH_STATUS`; não
existe um segundo arquivo canônico de percurso ou um diretório `path-status/`.

O diretório `.book` é derivado. O índice SQLite/FTS5 pode desaparecer ou ser
reconstruído sem perda do conhecimento canônico. Casos, catálogo, relações,
Tasks, ladders e eventos são persistência; não trate o índice como autoridade.

O Git continua sendo o histórico recuperável das revisões dos arquivos. O Book
acrescenta CAS por hash para impedir sobrescrita silenciosa, mas não substitui
um fluxo normal de revisão e commit.

## 4. Primeira inicialização e diagnóstico

Em um corpus novo ou clonado:

```bash
python3 book.py doctor
python3 book.py verify
python3 book.py reindex
python3 book.py list
```

- `doctor` verifica diretórios, permissões, índice e resolvers configurados.
- `verify` valida toda a persistência que o Book consegue provar
  mecanicamente.
- `reindex` recria o índice derivado a partir dos arquivos canônicos.
- `list` mostra os territórios navegáveis da raiz.

Book vazio é um estado normal:

```text
search → nenhum resultado
Task   → continua por investigação normal
```

Ausência de memória não prova ausência do problema e nunca deve bloquear uma
investigação.

## 5. Fluxo recomendado de uma Task

O fluxo completo não precisa ser rígido, mas esta sequência produz boa
telemetria e um `ContextReceipt` útil:

```text
begin
→ known / hypothesis / missing
→ list / search / show
→ references / resolve
→ next-probe
→ ação fora do Book
→ event add
→ validate
→ retention assess
→ add / revise / challenge / no-op
→ retention record
→ finish
→ receipt
```

### 5.1 Começar

`project` é onde a Task acontece; `domain` é o assunto principal. Eles podem ser
diferentes.

```bash
python3 book.py --actor amara task begin \
  --goal "diagnosticar falha de link do runtime" \
  --project LyannaValerie/pinker-v0 \
  --domain Pinker \
  --external-task "pinker:#520"
```

Guarde o `task_id` retornado, por exemplo `T-...`.

`--external-task` associa uma Task soberana de outro sistema sem reutilizar sua
identidade. O Book gera um `T-...` independente e persiste, neste exemplo,
`external_task_ref: "pinker:#520"`. Essa referência é vínculo e proveniência,
não autoridade: a Task Pinker continua governando ownership, autorização,
checkpoint, execução e finalização. A V1 aceita somente o namespace explícito
`pinker:` e rejeita referências sem esse formato.

Também é possível fornecer `--id` para integração idempotente quando o chamador
já possui uma identidade estável **do próprio Book**:

```bash
python3 book.py task begin \
  --id T-MINHAIDENTIDADE \
  --goal "..." --project projeto --domain domínio
```

Não passe o ID Pinker em `--id`. Para sistemas externos, use
`--external-task`; duas máquinas não devem afirmar posse da mesma identidade.

### 5.2 Registrar estado operacional curto

Não coloque raciocínio privado ou transcript. Registre apenas fatos,
interpretações e lacunas operacionais curtas:

```bash
python3 book.py task known T-... \
  "o erro ocorre somente no backend nativo"

python3 book.py task hypothesis T-... \
  "um artefato estático pode estar obsoleto"

python3 book.py task missing T-... \
  "onde o archive consumido pelo teste é produzido?"

python3 book.py task status T-...
```

`known`, `hypotheses` e `missing` pertencem à Task. Eles não promovem essas
frases a conhecimento canônico do Book.

### 5.3 Associar recuperação à Task

Use a opção global `--task` antes de `list`, `search`, `show` e outras operações
observáveis:

```bash
python3 book.py --task T-... list Pinker
python3 book.py --task T-... search "archive obsoleto" --within Pinker
python3 book.py --task T-... show B-...
python3 book.py --task T-... references B-...
```

Isso registra acesso, bytes e caracteres servidos e os IDs carregados. Acesso
não é utilidade e não é prova de causalidade.

### 5.4 Context stop gate e próximo probe

Quando houver contexto suficiente para agir, registre uma ação limitada e um
oráculo observável:

```bash
python3 book.py task next-probe T-... \
  --action "reconstruir somente o archive material" \
  --observable "hash do archive muda ou permanece igual" \
  --oracle "file:build/runtime.a" \
  --authorized \
  --bounded \
  --discriminative \
  --no-high-risk-gap
```

As quatro flags representam a condição mecânica de prontidão:

```text
authorized
and bounded
and observable
and discriminative
and no_known_high_risk_gap
```

O Book registra essas declarações; não consegue provar sozinho que a ação é
realmente autorizada ou segura. A autoridade da Task continua externa.

### 5.5 Registrar o que aconteceu fora do Book

O Book observa a própria CLI automaticamente, mas não vê comandos, testes ou
leituras feitos fora dela. Registre uma síntese allowlisted:

```bash
python3 book.py --actor amara event add \
  --task T-... \
  --kind probe-result \
  --event-key runtime-archive-probe-v1 \
  --summary "o rebuild alterou o hash e o teste passou" \
  --tool-calls 2 \
  --source-reads 1 \
  --full-file-reads 0 \
  --wall-time 12.4 \
  --reverts 0
```

`--event-key` torna a operação idempotente para a mesma Task e o mesmo conteúdo.
Reutilizar a chave com conteúdo diferente falha fechado.

O evento é append-only e recebe `E-...`, sequência, timestamp, hash e
`previous_hash`. O resumo é limitado e passa por redaction. Não envie stdout
gigante, secrets, ambiente integral, transcript ou chain-of-thought.

Kinds factuais suportados incluem `probe-result`, `source-read-reported` e
`tool-calls-reported`, além dos kinds internos usados automaticamente pela CLI.

### 5.6 Validar

Validação exige um oráculo explícito. `--passed` marca sucesso; sem a flag, o
registro é uma validação que não passou.

```bash
python3 book.py task validate T-... \
  --oracle "file:tests/runtime-regression.txt" \
  --outcome "teste de regressão passou" \
  --summary "archive reconstruído foi consumido pelo teste" \
  --passed
```

Isso não prova que um caso lido causou o sucesso. O Book separa:

- `ACCESS`: algo foi servido;
- `UTILITY`: o usuário declarou que algo apoiou ou resolveu;
- `VALIDITY`: uma afirmação foi confrontada com evidência/oráculo.

Utilidade é registrada explicitamente:

```bash
python3 book.py use B-... --as support --task T-...
python3 book.py use B-... --as resolved --task T-...
```

### 5.7 Finalizar e emitir recibo

```bash
python3 book.py task finish T-... \
  --outcome "causa discriminada, correção aplicada e regressão verde"

python3 book.py task receipt T-...
```

O receipt resume Task, conhecimento carregado, refs seguidas, missing,
next-probe, validação, outcome e métricas observáveis. Ele não contém contexto
interno do modelo.

Limitação conhecida da V1: não há `task revise`/`task amend` para corrigir
metadados de uma Task encerrada. Escolha `project` e `domain` com cuidado no
`begin`; não edite o JSON canônico manualmente para esconder um erro histórico.

Também não há `task missing resolve/remove`: uma pergunta adicionada a
`missing` continua no receipt mesmo quando a validação passa e a Task termina.
Ao ler um receipt V1, confronte a lista histórica com `validation` e `outcome`;
não presuma que toda entrada ainda representa um blocker atual.

## 6. Navegar sem conhecer a resposta

### 6.1 Explorar views

Views são projeções editoriais, não identidade canônica:

```bash
python3 book.py list
python3 book.py list Forja
python3 book.py list Forja/Toolchains
python3 book.py list Pinker/Runtime --limit 20
```

A raiz mostra domínios. Uma view mostra subviews e itens associados. O mesmo caso
pode aparecer em várias views sem ser duplicado, e seu ID não muda quando uma
faceta muda.

Uma view vazia é normal. Não converta ausência em aplicabilidade universal.

### 6.2 Busca lexical

```bash
python3 book.py search "runtime obsoleto"
python3 book.py search "libxml2.so.2" --within Forja
python3 book.py search "stderr CI" --within Pinker/Tooling --limit 10
```

A busca é lexical e determinística. Ela consulta título, cues, scope, domínio,
componentes, problema, guidance e relações relevantes. Se o índice FTS5 estiver
ausente, corrompido ou divergente, o Book pode usar o corpus canônico; `reindex`
restaura o índice derivado.

Resultados são candidatos compactos. Eles retornam
`applicability_claimed: false`. Similaridade de palavras não prova que um caso se
aplica à Task atual.

Escolha queries vindas do sintoma, diagnóstico, erro, componente ou ambiente —
não somente do título que o autor escolheu. Para testar recuperabilidade, tente
reencontrar o caso sem usar seu ID.

### 6.3 Inspeção progressiva

```bash
python3 book.py show B-... --metadata
python3 book.py show B-...
```

`--metadata` é a leitura barata. O `show` completo inclui status, scope,
environment, problem, probe, resultado, guidance, contraindicações, evidência,
referências, challenges e revisão.

`show` não interpreta conteúdo e não afirma aplicabilidade.

## 7. Criar conhecimento com `add-case`

`add-case` é autoria. O usuário fornece significado; o Book gera estrutura
mecânica.

### 7.1 Fluxo interativo humano

```bash
python3 book.py --actor amara add-case
```

O fluxo pergunta:

```text
Title
Problem
Cue
Probe                    opcional
Observable               se houver probe
Result
Guidance
Evidence
Evidence reference
Domain                   padrão: Uncategorized
```

Use cues separadas por vírgula. O fluxo interativo é propositalmente mínimo;
depois, use `revise` para detalhes avançados e `facet set` para views.

### 7.2 Entrada estruturada por stdin

Esta é a interface recomendada para agentes e integrações:

```bash
python3 book.py --actor agente add-case --stdin <<'JSON'
{
  "title": "Archive de runtime obsoleto pode mascarar o teste atual",
  "cues": ["runtime", "archive", "stale", "native-link"],
  "scope": {
    "components": ["runtime", "test-harness"],
    "conditions": ["link nativo consome archive materializado"]
  },
  "observed_at": "2026-08-25T00:00:00Z",
  "environment": {
    "repository": "exemplo/projeto",
    "backend": "native"
  },
  "problem": "O teste pode consumir um archive anterior à mudança atual.",
  "discriminating_probe": {
    "action": "reconstruir somente o archive e comparar seu hash",
    "observable": "o hash muda ou permanece igual"
  },
  "observed_result": "O rebuild alterou o hash e mudou o resultado do teste.",
  "guidance": "Valide a materialização consumida antes de interpretar a semântica.",
  "contraindications": [
    "Não se aplica quando o archive foi reconstruído depois da mudança."
  ],
  "evidence": [
    {
      "class": "OBSERVED",
      "description": "O hash do artefato mudou após rebuild limitado.",
      "source": "event:E-..."
    }
  ],
  "references": ["event:E-...", "file:tests/runtime-regression.txt"],
  "domain": "Pinker",
  "views": ["Pinker/Runtime", "Pinker/TestHarness"],
  "synthetic": false,
  "author": "agente"
}
JSON
```

Campos semânticos aceitos:

```text
title, cues, scope, observed_at, environment, problem,
discriminating_probe, observed_result, guidance, contraindications,
evidence, references, domain, views, synthetic, author
```

Campos desconhecidos falham fechado. O payload não deve conter `id`,
`schema_version`, `status`, `revision`, timestamps de revisão ou `challenges`.

O Book gera:

```text
B-...                  ID opaco estável
schema_version
status=candidate
revision.number=1
revision.parent_hash=null
revision.hash
revision metadata
timestamps mecânicos
```

Quando `cues` é omitido ou vazio, o Book deriva poucas cues do título. Para boa
recuperação, prefira cues explícitas vindas do sintoma, erro, componente e
ambiente.

`scope` ausente significa `UNKNOWN`, nunca “aplica-se a tudo”. Probe pode ser
`null` quando realmente não existe; não transforme qualquer comando histórico
em probe discriminativo.

### 7.3 Payload inline

Útil para payloads pequenos:

```bash
python3 book.py --actor agente add-case --json \
  '{"title":"...","problem":"...","observed_result":"...","guidance":"...","evidence":[...],"domain":"Bash"}'
```

Para conteúdo grande ou com quoting complexo, prefira `--stdin`.

### 7.4 Epistemologia dos campos

O Book preserva três classes:

- `OBSERVED`: resultado mecanicamente observado;
- `ASSERTED`: interpretação de humano ou agente;
- `VALIDATED`: afirmação ligada a oráculo explícito.

Na autoria semântica, strings em `problem` e `observed_result` são estruturadas
como observações; `guidance` é estruturada como afirmação. Evidências devem
declarar sua classe e uma fonte tipada. Não marque algo `VALIDATED` sem oráculo
relevante.

Uma sequência temporal não prova causalidade:

```text
caso mostrado → teste passou
```

é evidência de acesso seguido de sucesso, não de que o caso causou o sucesso.

### 7.5 Duplicata e near-match

Antes de gravar, `add-case` procura duplicata exata e candidatos lexicalmente
semelhantes.

- `CASE_EXACT_DUPLICATE`: não crie outro caso.
- `CASE_SIMILAR_CANDIDATES`: inspecione cada candidato com `search` e `show`.

Após inspecionar, escolha conscientemente:

```text
mesma informação       → no-op
equivalente + novidade → revise
evidência contrária    → challenge
caso realmente distinto → add-case --allow-similar
```

Exemplo do último caminho:

```bash
python3 book.py --actor agente add-case --stdin --allow-similar < payload.json
```

`--allow-similar` não deve virar opção padrão. A guarda é um stop gate para
decisão semântica, não um classificador.

## 8. Importar conhecimento de outras fontes

### 8.1 Regra principal

`import-case` importa uma representação que já é do Book. Markdown, issue,
e-mail, log, transcript ou mensagem legada **não** são casos canônicos.

```text
fonte externa bruta
→ leitura/interpretação
→ payload semântico
→ add-case
```

Não faça:

```text
msg.md → script artesanal → JSON canônico → import-case
```

Isso desloca para fora do Book a geração de identidade, revisão e estrutura que
pertence à máquina.

### 8.2 Protocolo completo de ingestão externa

1. Abra uma Task de ingestão com `project` e `domain` separados corretamente.
2. Registre em `missing` o que precisa ser decidido sobre reutilização.
3. Leia a fonte sem alterá-la.
4. Extraia somente conhecimento operacional reutilizável:
   sintoma/dificuldade, hipótese ou causa, probe, resultado, orientação,
   contraindicações e evidência.
5. Faça `search` por equivalentes antes de criar.
6. Produza um payload **semântico** e envie a `add-case --stdin`.
7. Preserve a origem em `evidence.source` e `references`.
8. Adicione uma relação `learned_from` quando a proveniência for material.
9. Atribua domínio e views pelo assunto, não pelo local da fonte.
10. Teste navegação e busca sem usar o ID recém-criado.
11. Rode `references`, `resolve` quando configurado e `verify`.
12. Registre a decisão de retenção e finalize a Task.

Exemplo com Markdown legado:

```bash
python3 book.py task begin \
  --goal "incorporar memória operacional legada" \
  --project LyannaValerie/pinker-v0 \
  --domain Forja \
  --external-task "pinker:#TASK"

python3 book.py task missing T-... \
  "a mensagem contém probe e orientação reutilizáveis?"

# Um humano/agente lê a mensagem. O Book não executa seu conteúdo.
# Em seguida, envia somente a interpretação semântica:
python3 book.py --task T-... --actor agente add-case --stdin < semantic.json

python3 book.py --actor agente relate \
  B-NOVOID learned_from \
  file:/pinker/msg/amara/dificuldades/registro.md \
  --epistemic OBSERVED

python3 book.py facet set B-NOVOID \
  --domain Forja \
  --view Storage \
  --view Toolchains
```

O exemplo usa `semantic.json` apenas como transporte opcional do payload
semântico; ele não contém ID, status ou revisão. Também é possível enviar o JSON
diretamente pelo stdin sem criar arquivo intermediário.

### 8.3 Issues, commits, testes e logs

Para uma issue, preserve a origem como referência tipada disponível. Se não há
namespace específico, registre um evento factual curto ou um arquivo permitido;
não invente um resolver genérico.

Para Git, use o objeto imutável:

```text
git:<sha-de-7-a-64-hex>
```

Para teste ou arquivo local permitido:

```text
file:tests/test_runtime.py
```

Para uma observação feita fora da CLI, crie evento e cite:

```text
event:E-...
```

Não copie logs gigantes para `problem` ou `evidence`. Resuma o resultado
observável e preserve uma referência segura para o artefato quando necessário.

### 8.4 `import-case` propriamente dito

Use somente para um draft V0 compatível ou um caso schema-1 já canônico:

```bash
python3 book.py import-case caso-canonico.json
python3 book.py import-case caso-canonico.json --allow-similar
```

O import preserva a identidade existente, valida schema/revision, rejeita ID
duplicado, duplicata semântica e near-match não autorizado.

O modo legado `book add-case arquivo.json` continua aceitando o draft V0 por
compatibilidade. Para novos fluxos, use `add-case` sem arquivo para autoria e
`import-case` para representação externa já canônica.

## 9. Facetas e views

Casos têm identidade opaca; o catálogo mantém projeções navegáveis.

```bash
python3 book.py facet set B-... \
  --domain Forja \
  --view Storage \
  --view Toolchains \
  --view Preflight
```

As views fornecidas são normalizadas sob o domínio:

```text
Forja/Storage
Forja/Toolchains
Forja/Preflight
```

Use `--synthetic` quando o caso for fixture sintética.

Mudar facetas não muda o ID do caso e não exige duplicar conhecimento em vários
diretórios. O catálogo é editorial: ele orienta navegação, mas não transforma o
caso em verdade nem prova aplicabilidade.

Regra de classificação:

- `domain`: sistema/assunto ao qual o conhecimento se aplica;
- `views`: territórios pelos quais alguém tentaria encontrá-lo;
- `project`: local da Task;
- `evidence`/`learned_from`: de onde a informação veio.

## 10. Revisar um caso com controle otimista

Primeiro obtenha o hash corrente:

```bash
python3 book.py show B-...
```

Envie somente campos editáveis e o hash lido:

```bash
python3 book.py --actor amara revise B-... \
  --if-revision HASH_ATUAL \
  --reason "adiciona contraindicação observada" \
  --json '{
    "contraindications": [
      "não se aplica quando o artefato foi reconstruído"
    ]
  }'
```

Também existem:

```bash
python3 book.py revise B-... --if-revision HASH --stdin
python3 book.py revise B-... --if-revision HASH --patch patch.json
```

Campos mecânicos não são editáveis: `id`, `schema_version`, `challenges` e
`revision`. O Book calcula nova revisão, parent hash, timestamp e hash.

Se outro processo revisou o caso depois da leitura, o hash diverge e a operação
falha com conflito de revisão. Recarregue o caso, reconcilie semanticamente e
tente novamente; nunca force a sobrescrita.

Facetas são alteradas por `facet set`, não por `revise`.

## 11. Challenge e validade

Challenge preserva evidência contrária sem apagar o caso histórico:

```bash
python3 book.py --actor amara challenge B-... \
  --if-revision HASH_ATUAL \
  --reason "contraexemplo em ambiente diferente" \
  --json '{
    "statement": "o probe não discriminou a hipótese nesse backend",
    "observed_at": "2026-08-25T00:00:00Z",
    "evidence": [
      {
        "class": "OBSERVED",
        "description": "o oráculo permaneceu igual nos dois ramos",
        "source": "event:E-..."
      }
    ],
    "references": ["event:E-..."]
  }'
```

O Book gera o ID do challenge e atualiza a revisão do caso. Um challenge não
conclui automaticamente que todo o caso é falso. Estados canônicos possíveis:

```text
candidate
verified
challenged
superseded
historical
```

`challenged` exige evidência de challenge. `superseded` e `historical` preservam
história. Mudança de dependência pode tornar uma projeção `SUSPECT`, mas não
prova falsidade nem autoriza marcar `STALE` sem evidência de falha.

## 12. Relações tipadas

Tipos suportados:

```text
references
depends_on
affects
causes
supersedes
validated_by
learned_from
```

Classes epistemológicas:

```text
OBSERVED
ASSERTED
VALIDATED
```

Exemplos:

```bash
python3 book.py --actor amara relate \
  B-ORIGEM learned_from file:registro.md \
  --epistemic OBSERVED

python3 book.py --actor amara relate \
  B-A depends_on book:B-B \
  --epistemic ASSERTED

python3 book.py --actor amara relate \
  B-A validated_by event:E-... \
  --epistemic VALIDATED \
  --oracle event:E-...
```

Uma relação `VALIDATED` exige `--oracle`. Não use `causes` para representar
mera ordem temporal. Ciclos podem existir no grafo; a travessia é limitada.

Consultar arestas de entrada e saída:

```bash
python3 book.py references B-...
python3 book.py references B-... --depth 2
```

Mantenha `--depth` pequeno para recuperação progressiva.

## 13. Referências e resolvers

Namespaces suportados:

```text
book:B-...      caso interno
event:E-...     evento interno
file:caminho    arquivo sob raiz permitida
git:<sha>       objeto Git
trama:<key>     autoridade externa opcional da Pinker
```

Resolver:

```bash
python3 book.py resolve book:B-...
python3 book.py resolve event:E-...
python3 book.py resolve file:docs/user-guide.md
python3 book.py resolve git:348c9cd
python3 book.py resolve trama:semantic.pattern
```

Estados:

- `RESOLVED`: a autoridade respondeu e o alvo existe;
- `UNRESOLVABLE`: sintaxe conhecida, mas alvo ausente/inválido;
- `UNAVAILABLE`: autoridade/adaptador não está configurado ou acessível;
- `UNKNOWN`: namespace desconhecido.

Falha de resolução não prova que a afirmação é falsa.

Configuração opcional em `book.config.json`:

```json
{
  "file_roots": ["/pinker/msg"],
  "git_root": "/caminho/para/repositorio",
  "trama_catalog": "/caminho/para/catalogo-trama.json"
}
```

`file:` resolve sob a raiz do Book e roots explicitamente autorizadas. Path
traversal e symlink que escapam das roots são rejeitados. `git:` aceita somente
hex de 7 a 64 caracteres e consulta metadados com `git cat-file`; não executa
conteúdo do commit. `trama:` é adapter opcional: o Book não cria nem redefine
chaves da Trama.

## 14. Retenção seletiva

Antes de escrever uma descoberta, avalie equivalência usando o mesmo payload
semântico de `add-case`:

```bash
python3 book.py retention assess --stdin < semantic.json
```

Possíveis recomendações:

- `new`: nenhum equivalente lexical relevante;
- `no-op`: duplicata semântica exata;
- `decide-revise-or-challenge`: candidatos exigem inspeção humana/agêntica.

`assess` nunca grava automaticamente.

Depois da decisão e da operação correspondente, registre a retenção:

```bash
python3 book.py retention record \
  --task T-... \
  --decision new \
  --case B-... \
  --summary "probe caro e contraindicação foram preservados"
```

Decisões permitidas:

```text
new
revise
challenge
no-op
```

Retenha seletivamente informação cara de reconstruir: caminho morto, causa não
local, probe discriminativo, ferramenta não óbvia, limitação ambiental,
contraindicação ou workaround. Não crie memória apenas porque uma Task acabou.

## 15. Métricas e utilidade

O Book mede o que observa:

```text
searches
lists
shows
references_followed
content_views
served_bytes
served_chars
estimated_tokens
source_reads_reported
tool_calls_reported
time_to_first_probe
time_to_validation
```

`estimated_tokens` é estimativa, não contagem do contexto interno do modelo.
`source_reads`, `tool_calls`, `wall_time` e afins só são conhecidos quando o
chamador os reporta por evento.

Não use número de views, frequência ou utilidade declarada como autoridade de
correção.

## 16. Percursos derivados

Tasks encerradas produzem traces factuais a partir de eventos como `SEARCH`,
`LIST`, `SHOW`, `REFERENCE_FOLLOW`, `PROBE_RESULT` e `VALIDATION`. O Book agrupa
traces equivalentes em percursos derivados.

```bash
python3 book.py path search "runtime"
python3 book.py path search --success "semantic"
python3 book.py path search "link" --domain Pinker --limit 10
```

O resultado contém assinatura, nodes/refs, usos, usos bem-sucedidos, taxa de
sucesso, custo mediano, fallback de fonte, última validação e status. Ausência de
campo na assinatura significa `UNKNOWN`, não wildcard universal.

Status operacionais:

```text
ACTIVE
SUSPECT
STALE
SUPERSEDED
```

```bash
python3 book.py path mark P-... \
  --status SUSPECT \
  --reason "dependência material mudou"

python3 book.py path mark P-... \
  --status STALE \
  --reason "falhou novamente no mesmo escopo" \
  --evidence event:E-...
```

`STALE` exige razão e evidência tipada. Uma dependência que mudou sugere
`SUSPECT`, não `STALE`.

Comparar exploração P0/P1:

```bash
python3 book.py path explore \
  --primary P-... \
  --candidate P-... \
  --budget 4096
```

O Book pode retornar `ABANDON_P1_RETURN_P0`,
`P1_CANDIDATE_PREFERRED_ROUTE` ou `EXPLORE_P1_WITHIN_BUDGET`. Nada é executado
automaticamente; a saída só prioriza investigação sob evidência histórica.

## 17. ProbeLadders

Uma ladder é procedimento ramificado explicitamente promovido. Ela nunca é
inferida automaticamente de um trace único.

```bash
python3 book.py --actor amara ladder add --stdin <<'JSON'
{
  "title": "Discriminar archive de runtime obsoleto",
  "preconditions": ["falha ocorre em link nativo"],
  "first_probe": {
    "action": "comparar timestamp e hash do archive",
    "observable": "archive anterior ou posterior à mudança"
  },
  "branches": [
    {
      "when": "archive é anterior",
      "next_probe": "rebuild limitado e repetir teste"
    },
    {
      "when": "archive é atual",
      "next_probe": "inspecionar próximo material dependency"
    }
  ],
  "stop_conditions": ["probe não é autorizado", "oráculo é indisponível"],
  "contraindications": ["backend não consome archive estático"],
  "terminal_validation": {
    "oracle": "teste de regressão atual",
    "success": "falha original não reaparece"
  }
}
JSON

python3 book.py ladder list
python3 book.py ladder show L-...
```

A ladder orienta; não executa seus campos `action` ou `next_probe`.

## 18. Manutenção

### 18.1 `verify`

```bash
python3 book.py verify
```

Verifica deterministicamente, entre outros pontos:

- parse e duplicate keys;
- schemas suportados;
- IDs únicos;
- campos e estados válidos;
- hashes e cadeia de eventos;
- referências sintaticamente conhecidas;
- casos, facetas, relações, Tasks e ladders;
- integridade e convergência do índice derivado.

Não verifica causalidade, correção semântica, aplicabilidade ou verdade atual.
Schema futuro desconhecido falha fechado.

### 18.2 `doctor`

```bash
python3 book.py doctor
```

Use para diagnosticar ambiente, diretórios, permissões, índice e configuração
dos resolvers. `UNAVAILABLE` em adapter opcional não equivale a corpus inválido.

### 18.3 `reindex`

```bash
python3 book.py reindex
```

Reconstrói `.book/index.sqlite3` a partir de casos e relações canônicos. É seguro
reconstruir após mudança de faceta, corrupção ou remoção do índice. Leituras não
devem depender da permanência do arquivo derivado.

### 18.4 GC não destrutivo

```bash
python3 book.py gc candidates
```

Somente lista candidatos considerando idade, supersessão, invalidade,
redundância, uso e criticidade. Baixo uso isolado não autoriza exclusão. O
comando não apaga dados.

### 18.5 Migração V0

```bash
python3 book.py migrate-v0
```

Executa a migração explícita suportada para dados do spike V0. Rode `verify`
antes e depois, mantenha o Git limpo ou revisável e nunca reescreva dados
silenciosamente para apenas obter verde.

## 19. Concorrência

O Book usa escrita atômica, locks mínimos e CAS por revision hash.

- Leituras não exigem lock global.
- `add-case` serializa a verificação final de ID/duplicata e a escrita.
- `revise` e `challenge` verificam o hash dentro da região crítica.
- Eventos usam append, hash chain e lock de escrita.
- Chaves de evento podem ser idempotentes.
- Reindexação é derivada; leitores preservam fallback canônico.

Em conflito, falhe fechado. Não edite manualmente arquivos para “resolver” uma
corrida; recarregue o estado, reconcilie significado e repita a operação pela
CLI.

## 20. Segurança operacional

Nunca persista automaticamente:

```text
secrets
tokens
passwords
credentials
private keys
ambiente integral
dumps indiscriminados
logs gigantes
transcripts
chain-of-thought
```

O Book rejeita campos sensíveis e padrões semelhantes a secrets. Texto geral é
limitado a 8192 caracteres; casos canônicos têm limite de 128 KiB; listas e
referências são limitadas. Esses controles reduzem risco, mas o detector de
secrets é heurístico: o autor ainda deve revisar o payload.

Prompt injection armazenada permanece inerte. Nunca copie comandos de guidance
para execução automática. A autoridade para agir vem da Task e do ambiente
externo, não do caso.

Para `file:`, configure roots mínimas. Não autorize `/` nem uma home inteira só
para tornar uma referência resolvível. Não siga symlink que escape da root.

## 21. Erros comuns e resposta correta

### `READ_FAILED`

O arquivo informado não existe ou não pode ser lido. Em V1, para criar um caso,
não forneça um filename inexistente:

```bash
python3 book.py add-case            # interativo
python3 book.py add-case --stdin    # payload semântico
```

Use `import-case arquivo.json` somente quando o arquivo realmente existe e já é
representação do Book.

### `CASE_SIMILAR_CANDIDATES`

Inspecione todos os candidatos. Decida entre no-op, revise, challenge ou novo
caso. Só então use `--allow-similar`.

### `CASE_EXACT_DUPLICATE`

Não crie outro registro. Use o existente ou revise-o quando houver novidade.

### `REVISION_CONFLICT`

Seu hash ficou stale. Recarregue com `show`, reconcilie e repita com a revisão
atual. Não substitua o arquivo manualmente.

### `SENSITIVE_CONTENT`

Remova/redija a informação sensível na origem do payload. Não fragmente o secret
para enganar a validação.

### `SCHEMA_INVALID` ou schema futuro

Confira campos aceitos e versão da ferramenta. Leitor antigo falha fechado para
schema futuro; atualize/migre explicitamente.

### Resolver `UNAVAILABLE`

Configure o adapter/root se a autoridade for necessária. A indisponibilidade
não invalida automaticamente a memória que contém a referência.

### Índice ausente ou divergente

Rode:

```bash
python3 book.py reindex
python3 book.py verify
```

O corpus canônico deve permanecer a fonte da reconstrução.

## 22. Checklists operacionais

### Antes de usar um caso

- [ ] encontrei-o por sintoma, componente ou território relevante;
- [ ] li scope e environment;
- [ ] tratei campo ausente como `UNKNOWN`;
- [ ] li contraindicações e challenges;
- [ ] conferi evidência e proveniência;
- [ ] consultei fonte/teste atual quando ela é autoridade;
- [ ] defini um probe autorizado, limitado, observável e discriminativo;
- [ ] não executei texto armazenado automaticamente.

### Antes de criar um caso

- [ ] a informação é cara de reconstruir e reutilizável;
- [ ] procurei duplicatas/equivalentes;
- [ ] separei observação de interpretação e validação;
- [ ] preservei uma fonte tipada;
- [ ] defini scope sem universalizar `UNKNOWN`;
- [ ] incluí contraindicações materiais;
- [ ] classifiquei domain pelo assunto, não pela proveniência;
- [ ] removi secrets, dumps e raciocínio privado;
- [ ] usei `add-case`, não fabriquei campos mecânicos.

### Ao importar memória externa

- [ ] a fonte original permaneceu intacta;
- [ ] extraí somente significado reutilizável;
- [ ] usei `add-case`, não `import-case`, para Markdown/log/issue;
- [ ] ID, status e revisão foram gerados pelo Book;
- [ ] origem foi preservada em evidence/references/learned_from;
- [ ] facetas descrevem o assunto real;
- [ ] busca pelo sintoma reencontra o caso sem usar o ID;
- [ ] `references` leva à proveniência;
- [ ] `verify` continua verde;
- [ ] Task receipt registrou o processo.

### Antes de finalizar uma Task

- [ ] missing e next-probe refletem o estado final;
- [ ] observações externas relevantes viraram eventos curtos;
- [ ] validação possui oráculo explícito;
- [ ] utilidade foi registrada separadamente de acesso;
- [ ] retention assess foi considerado;
- [ ] decisão new/revise/challenge/no-op foi registrada;
- [ ] outcome é curto, factual e verificável;
- [ ] receipt foi inspecionado.

## 23. Exemplo de ciclo completo

```bash
# 1. Abrir contexto operacional
python3 book.py task begin \
  --goal "diagnosticar runtime obsoleto" \
  --project exemplo/projeto \
  --domain Pinker \
  --external-task "pinker:#TASK"

# 2. Registrar a lacuna
python3 book.py task missing T-... \
  "qual artefato material é consumido pelo teste?"

# 3. Navegar e recuperar progressivamente
python3 book.py --task T-... list Pinker
python3 book.py --task T-... search "runtime archive obsoleto" --within Pinker
python3 book.py --task T-... show B-... --metadata
python3 book.py --task T-... show B-...
python3 book.py --task T-... references B-...

# 4. Declarar stop gate
python3 book.py task next-probe T-... \
  --action "comparar hash antes/depois de rebuild limitado" \
  --observable "hash muda ou não" \
  --oracle "file:build/runtime.a" \
  --authorized --bounded --discriminative --no-high-risk-gap

# 5. Executar fora do Book e registrar o fato
python3 book.py event add \
  --task T-... --kind probe-result \
  --summary "hash mudou e regressão passou" \
  --event-key runtime-probe

# 6. Validar e declarar utilidade sem alegar causalidade
python3 book.py task validate T-... \
  --oracle "file:tests/runtime-regression.txt" \
  --outcome "PASS" --passed
python3 book.py use B-... --as support --task T-...

# 7. Avaliar e registrar retenção
python3 book.py retention assess --stdin < semantic.json
python3 book.py retention record \
  --task T-... --decision no-op \
  --case B-... --summary "caso existente já contém a descoberta"

# 8. Encerrar
python3 book.py task finish T-... --outcome "diagnóstico validado"
python3 book.py task receipt T-...

# 9. Verificar persistência e consultar traces derivados
python3 book.py verify
python3 book.py path search --success "runtime"
```

O ciclo está completo quando uma Task futura consegue reencontrar a experiência
sem herdar a sessão anterior, mas ainda precisa confrontá-la com a realidade
atual antes de agir.
