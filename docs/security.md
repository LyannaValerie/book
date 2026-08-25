# Segurança

## Modelo de ameaça

Todo conteúdo persistido e recuperado é `UNTRUSTED_DATA`, inclusive texto como “ignore política” ou “execute rm”. O Book serializa e mostra esse texto; nunca o interpreta como autoridade nem o executa.

## Controles

- validação estrutural estrita e schema futuro fail-closed;
- rejeição heurística de secrets em autoria, relações, Tasks e eventos;
- redação de summary de evento quando conteúdo secret-like é detectado;
- limites de tamanho/lista e saída compacta;
- eventos com allowlist, sem env, logs brutos, transcript ou chain-of-thought;
- escrita atômica, locks e CAS;
- `file:` confinado a raízes autorizadas após resolução real, rejeitando traversal e symlink escape;
- adapters sem shell arbitrário;
- índice derivado, nunca autoridade;
- nenhum comando armazenado é executado;
- nenhum caso, path ou ladder autoriza mutação/publicação.

Detectores de secrets são defesa em profundidade, não prova de ausência. Autores devem fornecer evidência mínima e higienizada, nunca dumps indiscriminados.
