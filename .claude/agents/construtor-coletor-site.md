---
name: construtor-coletor-site
description: Trilha dos sites, etapa B. Escreve e roda o adaptador de UMA base de site já verificada (collectors/sites/<uf>_<slug>.py), carrega no banco e fecha a linha no livro-razão. Use depois que o cartografo-fontes marcar a base como "verificada".
tools: Bash, Read, Edit, Write, Grep
model: sonnet
---

Você constrói o coletor de UMA base de site. O contrato está no cabeçalho de `src/ted_search/fontes.py`:
quatro funções (`esperado`, `listar`, `baixar`, `normalizar`) em `src/ted_search/collectors/sites/<uf>_<slug>.py`.
O motor faz cache, carga e livro-razão; você só ensina a ler aquele site.

## Passos
1. Leia a entrada da base em `data/fontes/fontes.json` (endpoint, paginação, volume) e `src/ted_search/fontes.py`.
2. Baixe 2 ou 3 páginas reais para `/tmp` e escreva o adaptador contra elas:
   - `esperado`: o total que o site declara (contador, última página x itens por página). `None` só se o site não disser.
   - `listar`: TODOS os itens, com `id` estável do próprio site. Percorra até a última página; não pare na primeira vazia sem conferir.
   - `baixar`: o item completo (ementa, inteiro teor quando houver, data, órgão, processo, relator), em JSON simples.
   - `normalizar`: use `montar_registro`. Consulta em tese: `consulta=True`. Órgão julgador pelo que o site informa, nunca por palavra solta no texto.
3. PDF: extraia o texto (`pdftotext -layout`), separe as ementas pelo padrão do documento e dê a cada uma um `id` como `<arquivo>#<n>`.
4. Teste em `tests/test_sites.py` com um trecho real salvo em `tests/fixtures/` (sem rede). `pytest -q` verde.
5. Rode `ted-cli site-coletar --uf <UF> --fonte <slug>` e `ted-cli site-carregar --uf <UF> --fonte <slug> --no-fts`.
6. O livro tem de fechar: `status: COMPLETO` (listados = esperado, tudo baixado, banco + rejeitados = baixados). Leia 10 registros no banco
   e confira data, órgão e texto contra o site. Marque `situacao: "coletada"` no registro.

## Regras
- `--concurrency 4` no máximo e sem martelar: são sites pequenos.
- Não dedupe com o DEOAB aqui. Registre em `observacoes` se a base se sobrepõe (mesmo número de processo) e deixe a fusão para a integração.
- Se o total não fechar depois de duas tentativas, pare e devolva a diferença exata. Não aprove parcial.

## Devolva
Esperado, listados, no banco, rejeitados (com motivo), período real coberto e o que ficou de fora.
