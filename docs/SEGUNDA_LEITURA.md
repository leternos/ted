# Segunda leitura independente — plano e andamento

A primeira auditoria de cada Seccional devolveu "REABRIR" e as correções foram aplicadas no código.
O `sec-auditar` passa em todas, mas ele só mede cobertura com a mesma regra da coleta: não enxerga erro de
classificação. Uma Seccional só fecha depois de uma segunda leitura feita por um agente que não viu a primeira.

## O que a segunda leitura confere (igual para todas)

1. **Correções da primeira auditoria**: cada id citado no relatório anterior está como deveria (dentro ou fora, marca 0 ou 1).
2. **Rejeitados**: amostra aleatória por motivo (`sem_formula_de_julgamento`, `tipo_amplo_sem_decisao`,
   `despacho_transcrito`, `republicacao_identica`, `errata_sem_decisao`); mínimo 60 por motivo ou todos, o que for menor.
3. **Banco**: 40 registros com marca 1 e 40 com marca 0, com semente nova; depois varredura por regex de cada defeito achado.
4. **Fora da classificação**: publicações do setor do TED e de tipos incomuns que não viraram decisão, cruzadas por palavras de julgamento.
5. **Veredito**: APROVADA só com zero decisão colegiada perdida na amostra e erro de marca abaixo de 2%.

## Ordem e pontos de atenção de cada uma

| Ordem | UF | Por que nesta ordem | Ponto de atenção |
|---|---|---|---|
| 1 | CE | mudou mais: de 1.513 para 373 | 2020–2024 passaram a ter 85 registros via tipo "Edital"; conferir se são decisões com desfecho ou só intimações |
| 2 | BA | nunca lida; 5.659 rejeitados | 2019–2022 têm só 23 julgados: modo de publicar ou regra? |
| 3 | ES | nunca lida; 23.738 rejeitados | tipo amplo "Notificação" (republicações de acórdão da Segunda Câmara) |
| 4 | GO | nunca lida; 3.742 rejeitados | "Notificação/Intimação de Acórdão"; 1.105 com marca 0 |
| 5 | MA | 1.468 rejeitados | editais de sanção e notificações de decisão da Câmara: regra hoje inconsistente (207895 entra, os demais não) |
| 6 | PE | nunca lida | intimações "tomar ciência do acórdão" sem o teor (ex.: registros sem a palavra EMENTA: 418) |
| 7 | PA | regra do tipo amplo mudou duas vezes | 357 sem nº de processo: publicações que agrupam vários acórdãos |
| 8 | MT | 53 decisões recuperadas | editais com dezenas de ementas num só registro |
| 9 | PB | correções pontuais | decisões da Primeira Câmara por tipo "Despacho"/"Voto" |
| 10 | SE, RN | nunca lidas, perfil simples | SE: 130 `sem_formula_de_julgamento`; RN: decisões das Turmas do TED |
| 11 | PI | pequena | atas do Conselho Pleno com desfecho disciplinar (decisão de política pendente) |
| 12 | AC | refeita em modo censo | perfil ainda não validado por leitura |
| 13 | AL, TO, AP, RR | já aprovadas; só reconferir após a recarga geral | TO perdeu 6 registros por republicação idêntica |

SP fica fora desta rodada: foi auditada e a regra dela é outra (`deoab_sp`).

## Como rodar

Um agente `auditor-seccional` por Seccional, no máximo três ao mesmo tempo (eles leem o banco; nenhuma carga pode
rodar durante a leitura). Depois de cada relatório: aplicar as correções, recarregar só aquela Seccional,
`cobertura --sync UF`. Ao fim da rodada: `cobertura --fts`, testes, reempacotar e enviar.

## Decisões de política que a leitura não resolve sozinha

- Atas de Conselho Pleno que registram desfecho disciplinar (PI): entram como decisão?
- Editais que só comunicam a sanção aplicada, sem o texto do julgado (MA, CE, PB): entram?
- Consultas respondidas pelo TED (CE, PI): contam como matéria disciplinar na busca?

## Andamento (20/11/2026)

Leituras feitas e correções aplicadas (todas devolveram REABRIR; a leitura de confirmação ainda falta em cada uma):

| Rodada | Seccionais | O que a leitura achou |
|---|---|---|
| segunda leitura | CE, BA, ES | fórmulas fracas admitiam intimações e comunicados de sanção |
| segunda leitura | GO, MA, PE | nenhum julgado perdido; erro na marca disciplinar (2–5%); PE passou a aceitar penalidade e suspensão preventiva com voto e resultado |
| segunda leitura | PA, MT, PB | 10 julgados recuperados (acórdão numerado, decisão de Câmara); marca de prerrogativas e de inscrição |
| segunda leitura | SE, RN, PI, AC | consultas do TED de SE recuperadas; 15 certidões do Pleno do PI retiradas |
| primeira leitura | RJ, MG, RO | marca: ~440 de MG (Órgão Especial) e ~220 do RJ (Turmas do TED) estavam como administrativos; 3 tipos faltavam no perfil do RJ |
| primeira leitura | MS, AM, SC, DF | AM: 95 editais só com o resultado retirados; SC: 50 avisos de adiamento retirados; DF: o TED não publica o teor no Diário |

Pendências conhecidas:

- DF: ~1.440 notificações do Conselho Pleno e do Tribunal de Defesa das Prerrogativas com extrato do julgamento (só o resultado), e 13 decisões do
  Tribunal de Prerrogativas que saíram na última recarga por não trazerem "ACORDAM". Fora, aguardando decisão.
- Decisões monocráticas de presidente ainda no banco: PI 246897, AC 546575, RO 601935.
- RN: republicações corrigidas (fica a primeira; deveria ficar a última). AM: 7 pares de duplicata com nº de processo curto.
- MT: ~13 pedidos de inscrição com marca 1 ("Representado:" no cabeçalho).
- Atas da Comissão Eleitoral com julgamento (RN 844146, RO 961380, MS 6 registros): fora.
- Reconferir AL, TO, AP e RR depois das mudanças de regra.

### Leituras de confirmação (20/11/2026, tarde)

| UF | Veredito | Erro de marca medido | Julgados perdidos | O que ficou |
|---|---|---|---|---|
| AM | **APROVADA** | ≈0,1% | 0 | 3 registros isolados (322778, 857349, 125735) |
| PE | perto do limite | 0,6% | 2 (certidão eleitoral 801505; embargos do Pleno 1111246) | denúncias de exercício ilegal por não advogado (CEFAPA) com marca 1: 8 registros, política a decidir |
| SC | perto do limite | 1,0–1,7% | 8 → 3 depois da correção | ~11 notificações de despacho no banco; ~21 anotações/inscrições com marca 1 (assunto depois do cabeçalho) |
| RJ | reaberta | ≈1% no total; ~3% dentro da marca 0 | 1 | corrigido em seguida; falta nova medida |
| MG | reaberta | ~1% marca 1; ~3% dentro da marca 0 | 7 (3 do Pleno, 4 eleitorais) | corrigido em seguida; ~339 recursos sem ementa não dá para provar |
| GO | perto do limite | ≥1,2% em cada sentido | 0 | ~13 assuntos administrativos sem marcador; embargos sem processo-pai |

### Onda 4 — RS e PR (20/11/2026)

| UF | No banco | O que a primeira leitura achou | Estado |
|---|---|---|---|
| RS | 18.500 | ~15 mil com ementa e acórdão do TED; ~3.900 extratos de julgamento da 2ª Câmara Recursal, do Órgão Especial e do Pleno (resultado colegiado, sem ementa) — **ficam, com marca disciplinar, por decisão do Guh**; 894 republicações da mesma ementa fundidas; 71 avisos retirados; marca 0 errava 11% (Turma Julgadora, 2ª Câmara) | corrigido; falta confirmação |
| PR | 12.142 | quase tudo julgado real (tipo Acórdão); 21 do formato novo "Número Acórdão / Caso em exame / Dispositivo e tese" recuperados; marca: ~51 administrativos com marca 1 e ~4% dos marca 0 indevidos | corrigido em parte; falta confirmação |

Regra nova que vale para todas as Seccionais: mesma ementa do mesmo processo, republicada em outra publicação com cabeçalho ou data
diferentes, é republicação (fica a primeira). Retirou de 3 a 40 registros por Seccional.

Pendências da onda 4: RS — 60 acórdãos publicados sob o tipo "Despacho" e 2 rejeitados como ato monocrático (240891, 1006716);
PR — 25 duplicatas com número curto, 4 erratas longas aceitas.

Fonte fora do Diário (PR): API pública do buscador de jurisprudência (`celulaadv.oabpr.org.br/api-jurisprudencia`), 54.135 acórdãos,
~38 mil exclusivos (2003–2018), só ementa e dispositivo, sem voto nem PDF. Traz o nome completo do advogado: na coleta, guardar só as iniciais.
