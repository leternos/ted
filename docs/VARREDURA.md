# Varredura das 27 Seccionais: DEOAB e bases próprias

Objetivo: ter no banco **todas** as decisões ético-disciplinares de cada Seccional, vindas de duas trilhas:

1. **DEOAB**: o que cada Seccional publicou no Diário Eletrônico da OAB desde 2019.
2. **Sites das Seccionais**: ementários, buscas de jurisprudência e PDFs que cada OAB mantém no próprio site.
   É onde estão as consultas das turmas deontológicas e quase tudo o que é anterior a 2019.

Nas duas, com prova de completude registrada no livro-razão.

`ted-cli` abaixo é `.venv/bin/ted-cli`. O comando `/varredura` (Claude Code) conduz tudo; os mesmos passos
podem ser seguidos à mão ou por outro agente lendo este arquivo.

## Por que não basta "buscar por ACORDAO"

Medido em nov/2026, com censo de todas as publicações de dias sorteados:

| Seccional | Achado |
|---|---|
| RJ | o termo `ACORDAO` encontra 5% dos acórdãos; `EMENTA` encontra 98%; só `EMENTA` + `DECISAO` chega a 100% |
| SP | `EMENTA` perde 6% dos acórdãos; `ACORDAO` encontra todos |
| AC, RO | existem tipos próprios ("Ementa e Acordão", "Retificação Acórdão") além de "Acórdão" e "Ementa" |

Cada Seccional publica com vocabulário e tipos próprios. Por isso a varredura começa por um **perfil** medido,
e termina com uma **auditoria** que não usa termo de busca nenhum.

## As cinco etapas

| # | Etapa | Agente | Comando | Portão de saída |
|---|---|---|---|---|
| 1 | Perfil | `batedor-seccional` | `ted-cli sec-perfil --uf UF` | `data/perfis/UF.json` com `validado: true` e recall de 100% no censo |
| 2 | Coleta e carga | `coletor-seccional` | `ted-cli sec-coletar --uf UF` e `sec-carregar --uf UF` | todos os anos `COMPLETO` no livro-razão |
| 3 | Curadoria | `curador-orgaos` | edita regras em `src/` + testes | `pytest` verde; sigla genérica explicada |
| 4 | Auditoria | `auditor-seccional` | `ted-cli sec-auditar --uf UF` | duas auditorias com `aprovada: true` |
| 5 | Integração | `integrador-banco` | `ted-cli cobertura --fts` | PR da onda aberto |

O maestro (a sessão que roda `/varredura`) só delega e confere portões.

## O livro-razão (`cobertura_coleta`)

Uma linha por Seccional e ano. Um ano só é `COMPLETO` quando as duas contas fecham:

1. **Índice:** ids indexados = `quantidade` informada pela API, em todas as janelas e para cada termo;
   todas as publicações candidatas foram inspecionadas; nenhuma falha de download.
2. **Banco:** decisões = registros no banco + fundidos com outra fonte + rejeitados pelo normalizador
   (os rejeitados ficam listados em `data/raw/deoab_uf/rejeitados.jsonl`).

`ted-cli cobertura` mostra a situação de cada Seccional:
`NÃO INICIADA -> EM ANDAMENTO -> AGUARDA AUDITORIA -> AGUARDA PERFIL -> FECHADA`.

Como o estado mora no banco e em `data/raw/`, qualquer sessão retoma de onde a anterior parou.

## Regras de convivência com o DEOAB

- O DEOAB é um servidor só. **Uma coleta por vez**, com `--concurrency 10`.
- Consultas de período longo são lentas (20s por página sob carga). O coletor indexa por mês e cai para dia a dia
  quando o mês passa de 300 resultados.
- Perfil, curadoria e auditoria fazem poucas requisições e podem rodar em paralelo (até 3 agentes).

## Ondas

A ordem vai da menor para a maior: os erros de processo aparecem onde refazer é barato.
"Universo estimado" é o maior entre os totais de `ACORDAO` e `EMENTA` de 2020 a 2026 no
`diagnostic_27_seccionais.json`. É só ordem de grandeza: o caso do RJ mostra que pode estar subestimado.

| Onda | Seccionais | Universo estimado | Observação |
|---|---|---|---|
| 0 (piloto) | AC, SP | 259 e 41 mil | coleta fechada e uma auditoria aprovada nas duas; faltam a revisão do perfil pelo batedor e a segunda auditoria |
| 1 | RR, AP, AL, TO, PI, MA, CE, PA, MT, PB | de 29 a 939 cada | pequenas; TO e AP quase vazias no banco |
| 2 | RN, SE, PE, ES, BA, GO | de 1,2 a 2,5 mil cada | |
| 3 | RO, MS, AM, SC, DF, MG, RJ | de 2,9 a 7,4 mil cada | RJ e RO: `EMENTA` muito acima de `ACORDAO`; DF: o inverso |
| 4 | RS, PR | 14 mil e 11 mil | as maiores depois de SP |
| 5 | pendências do `OAB_JURIS` | | datas inválidas (1900 e futuras) |

## Trilha dos sites

O registro `data/fontes/<UF>.json` lista as bases conhecidas (`ted-cli fontes`). Em nov/2026 eram 34 bases em 22 Seccionais;
AL, AP, MG, PE e RR não mostraram base própria. Só as de SP, RJ, SC, GO e uma da BA foram abertas na pesquisa: as demais
precisam ser confirmadas pelo `cartografo-fontes`.

| Etapa | Agente | Comando | Portão de saída |
|---|---|---|---|
| A. Mapa | `cartografo-fontes` | edita `data/fontes/<UF>.json` | base `verificada`, com endpoint, volume medido e se é exclusiva |
| B. Coletor | `construtor-coletor-site` | `ted-cli site-coletar` e `site-carregar --uf UF --fonte slug` | linha `COMPLETO`: listados = total do site; banco + rejeitados = baixados |
| C. Conferência | `auditor-seccional` | abre itens sorteados no site | texto, data e órgão iguais aos do banco |

Cada base tem um adaptador em `src/ted_search/collectors/sites/<uf>_<slug>.py` (contrato em `src/ted_search/fontes.py`) e entra
no banco com `fonte = SITE_<UF>_<SLUG>`.

Ordem sugerida, pelo que só existe ali:

| Prioridade | Bases | Por quê |
|---|---|---|
| 1 | SP (ementário novo e as três páginas legadas), GO | consultas da Turma de Ética Profissional; coletânea 1995-2018; GO com cerca de 9 mil ementas desde 1998 |
| 2 | RS (PDFs 2013-2021), MT, MS, ES, CE, AM, DF, SE, TO, BA | PDFs e páginas anteriores a 2019, e consultas |
| 3 | PR, RJ, SC, portal do RS | grandes e estruturadas, mas sobrepostas ao DEOAB: servem para enriquecer e para o acervo antigo do PR |
| 4 | AC, MA, PA, PI, PB, RN, RO | bases não confirmadas ou residuais |

Bases que se sobrepõem ao DEOAB entram com fonte própria; a fusão por número de processo é feita na integração, não na coleta.

## Depois de cada onda

- O banco cresce. Ele vai no repositório como `.xz` em partes de 90 MB com manifesto (`python -m ted_search.banco empacotar`); cada versão publicada pode ser arquivada num bucket com `scripts/banco_bucket.sh publicar`.
  ou 3 o banco deixa de caber no repositório e precisa ir para um bucket (o `integrador-banco` para e avisa).
- Deploy continua manual.
