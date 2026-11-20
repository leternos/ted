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
