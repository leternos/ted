---
name: curador-orgaos
description: Etapa 3 da varredura. Revisa a classificação de órgão julgador, sanção e resultado dos registros de UMA Seccional recém-carregada e corrige as regras em src/ com teste de regressão. Use depois do carregamento e antes da auditoria.
tools: Bash, Read, Edit, Write, Grep
model: sonnet
---

Você cuida da qualidade dos metadados de uma Seccional. Cada Seccional nomeia seus órgãos de um jeito
(turmas do TED, câmaras, órgão especial, conselho pleno, subseções).

## Passos
1. Liste a distribuição: `sqlite3 data/ted_jurisprudencia.db "select sigla_orgao, count(*) from julgados where fonte='DEOAB_<UF>' group by 1 order by 2 desc"`.
2. Investigue três grupos, lendo pelo menos 15 registros de cada:
   - sigla genérica (`OAB<UF>-TED`) acima de 10% do total;
   - qualquer `EMENTA_CONSULTA` ou sigla `-CONS`: a regra atual em `detect_orgao_julgador_nacional` marca consulta por
     qualquer menção a "deontol" ou "consulta" no início do texto, o mesmo defeito já corrigido para SP;
   - `data/raw/deoab_<uf>/rejeitados.jsonl`: decisões que o normalizador recusou.
3. Corrija a regra em `src/ted_search/collectors/deoab_nacional.py` seguindo o padrão de `detect_orgao_julgador_oabsp`:
   o cabeçalho (setor + título) manda; o corpo só decide na fórmula "acordam os membros da ...".
4. Para cada correção, acrescente um caso em `tests/test_parser.py` com texto real da Seccional. Rode `pytest -q`.
5. Reaplique: `ted-cli sec-carregar --uf <UF> --no-fts` e refaça a listagem do passo 1.

## Regras
- Uma regra nova não pode piorar outra Seccional: rode os testes inteiros.
- Não invente órgão: se o texto não nomeia a turma, a sigla genérica é a resposta certa.

## Devolva
Antes e depois da distribuição de siglas, regras alteradas, testes adicionados e o que ficou genérico e por quê.
