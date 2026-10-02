# Modelo de dados

## OperationalCase

O schema 1 preserva `title`, `cues`, `scope`, `observed_at`, `environment`, `problem`, `discriminating_probe`, `observed_result`, `guidance`, `contraindications`, `evidence`, `references`, `status`, `challenges` e `revision`. IDs `B-...` são opacos. Ausência de scope é `UNKNOWN`, nunca universal.

Claims e evidências distinguem `OBSERVED`, `ASSERTED` e `VALIDATED`. `VALIDATED` exige oráculo explícito. Leitura seguida de sucesso não demonstra causalidade.

### Schema 2: gatilhos de recuperação

Schema 2 é o schema 1 acrescido do campo obrigatório `retrieval`: `null`
(desconhecido) ou objeto com campos opcionais `intents`, `projects`, `actions`,
`phases`, `paths`, `components`, `aliases` e `predicates`. Campo ausente
significa que o caso não declara nada naquela dimensão; listas vazias são
rejeitadas. Gatilhos dizem quando recuperar, não que o caso se aplica.

- `projects`: identificadores `[a-z0-9][a-z0-9._/-]*` (caixa normalizada).
- `actions`, `phases`, `components`: texto normalizado para identificador
  (`Rename column` → `rename-column`); colisões após normalização são rejeitadas.
- `paths`: padrões relativos POSIX com `*`, `?` e `**` de segmento inteiro;
  absoluto, `..`, `\`, `[]` e `{}` são rejeitados.
- `aliases`: `{term, for: "action:<id>"|"component:<id>", lang?}`; o alvo precisa
  estar declarado no próprio caso. Versionados pela revisão do caso.
- `predicates`: `{fact, op, value}` com `op` em `eq ne in not_in lt le gt ge`.

`scope` permanece separado: `scope.components` é texto e `scope.conditions` é
prosa não verificada. Casos sem gatilhos continuam schema 1; `add-case` só
produz schema 2 quando o payload traz `retrieval`. A migração 1 → 2 é explícita
(`migrate-schema`) e não infere gatilhos. Detalhes de consulta, classes e
ordenação: [guia, seção 6.4](user-guide.md#64-consulta-situacional-consult).

Facetas vivem em `catalog/` para permitir múltiplas views e mudanças de território sem alterar identidade nem duplicar casos.

## Relation

Arestas `references`, `depends_on`, `affects`, `causes`, `supersedes`, `validated_by` e `learned_from` contêm classe epistemológica. `causes` não é inferida de ordem temporal. Ciclos são válidos; travessia é limitada.

## Authority references

- `book:` resolve caso interno.
- `file:` resolve somente dentro de raízes autorizadas, sem traversal/symlink escape.
- `git:` consulta objeto no repositório configurado.
- `trama:` consulta adapter opcional; não cria chaves.
- `event:` referencia observação interna append-only.

Estados de resolver: `RESOLVED`, `UNRESOLVABLE`, `UNAVAILABLE`, `UNKNOWN`. Falha de resolução não prova falsidade.

## Task e ContextReceipt

Task armazena somente goal, domínio/projeto, `external_task_ref` opcional,
estados curtos `known/hypotheses/missing`, próximo probe, refs
carregadas/seguidas, validação e outcome. Não armazena transcript ou
chain-of-thought.

`external_task_ref` liga a sessão epistemológica a uma Task soberana de outro
sistema sem compartilhar identidade. Na V1, a forma suportada é
`pinker:<task-id>`. O Book continua gerando seu próprio `T-...`; a referência
não concede autoridade nem permite ao Book governar a Task Pinker. Tasks V1
anteriores sem o campo continuam legíveis como `external_task_ref=null`.

`Ready(probe)` requer flags registradas de autorização, limite, observabilidade, poder discriminativo e ausência de gap de alto risco conhecido. O receipt projeta esses dados e métricas.

## Event

Eventos têm sequência, timestamp, kind, Task opcional, summary limitada, dados allowlisted, previous hash e hash. O log é factual e append-only; não é conhecimento canônico por si só.

## Path

Percurso é `DERIVED` de traces. Guarda assinatura esparsa, nós, usos, sucessos, taxa, mediana de contexto, fallbacks, validação, status e vetor de custo. Campo ausente é `UNKNOWN`. `SUSPECT` sinaliza mudança/risco; `STALE` exige evidência explícita de falha no escopo.

## ProbeLadder

Objeto explícito com preconditions, first probe, branches, stop conditions, contraindications e terminal validation. É diferente de um trace linear e não executa ações.
