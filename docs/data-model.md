# Modelo de dados

## OperationalCase

O schema 1 preserva `title`, `cues`, `scope`, `observed_at`, `environment`, `problem`, `discriminating_probe`, `observed_result`, `guidance`, `contraindications`, `evidence`, `references`, `status`, `challenges` e `revision`. IDs `B-...` são opacos. Ausência de scope é `UNKNOWN`, nunca universal.

Claims e evidências distinguem `OBSERVED`, `ASSERTED` e `VALIDATED`. `VALIDATED` exige oráculo explícito. Leitura seguida de sucesso não demonstra causalidade.

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

Task armazena somente goal, domínio/projeto, estados curtos `known/hypotheses/missing`, próximo probe, refs carregadas/seguidas, validação e outcome. Não armazena transcript ou chain-of-thought.

`Ready(probe)` requer flags registradas de autorização, limite, observabilidade, poder discriminativo e ausência de gap de alto risco conhecido. O receipt projeta esses dados e métricas.

## Event

Eventos têm sequência, timestamp, kind, Task opcional, summary limitada, dados allowlisted, previous hash e hash. O log é factual e append-only; não é conhecimento canônico por si só.

## Path

Percurso é `DERIVED` de traces. Guarda assinatura esparsa, nós, usos, sucessos, taxa, mediana de contexto, fallbacks, validação, status e vetor de custo. Campo ausente é `UNKNOWN`. `SUSPECT` sinaliza mudança/risco; `STALE` exige evidência explícita de falha no escopo.

## ProbeLadder

Objeto explícito com preconditions, first probe, branches, stop conditions, contraindications e terminal validation. É diferente de um trace linear e não executa ações.
