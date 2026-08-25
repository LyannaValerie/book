# Book

Biblioteca experimental de Casos Operacionais recuperados sob demanda.

O Book V0 testa um ciclo pequeno e determinístico:

```text
caso persistente
→ busca lexical
→ inspeção
→ revisão ou challenge controlado
→ verificação de integridade
```

O projeto é independente e usa Python 3 apenas com a biblioteca padrão. Não usa
embeddings, LLM, RAG, SQLite ou ranking por popularidade.

## Estado

Esta é uma infraestrutura experimental. Conteúdo persistido no Book é
`UNTRUSTED_DATA`: ele pode orientar uma investigação, mas não substitui fonte,
teste, política ou autoridade factual atual e nunca é executado automaticamente.

## Uso

```bash
python3 book.py --version
python3 book.py search "sintoma"
python3 book.py show B-XXXXXXXXXXXX
python3 book.py add-case caso.json
python3 book.py revise B-XXXXXXXXXXXX --if-revision HASH --patch patch.json \
  --updated-at 2026-08-25T00:00:00Z --updated-by agente --reason "motivo"
python3 book.py challenge B-XXXXXXXXXXXX --if-revision HASH \
  --challenge challenge.json --updated-at 2026-08-25T00:00:00Z \
  --updated-by agente --reason "motivo"
python3 book.py verify
```

Por padrão, os casos canônicos ficam em `cases/`, um JSON legível por caso.

## Modelo V0

- IDs opacos e estáveis.
- `schema_version` e `tool_version` explícitos.
- Evidência classificada como `OBSERVED`, `ASSERTED` ou `VALIDATED`.
- Referências externas tipadas: `trama:`, `file:` e `git:`.
- Escrita atômica e controle otimista por hash de revisão.
- Busca lexical determinística com resultados compactos.

## Testes

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
python3 book.py verify
```
