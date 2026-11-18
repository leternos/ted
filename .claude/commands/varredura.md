---
description: Orquestra a varredura das Seccionais da OAB no DEOAB e nos sites de cada Seccional (perfil, coleta, curadoria, auditoria, integração)
argument-hint: "[status | onda N | UF,UF,...]"
---

Você é o maestro da varredura. Argumento recebido: `$ARGUMENTS`.
Leia `docs/VARREDURA.md` antes de agir. O estado está no banco, não na conversa: comece SEMPRE por `ted-cli cobertura` e `ted-cli fontes`.

## O que fazer
- `status` (ou sem argumento): mostre a tabela de cobertura e diga qual é a próxima onda. Não execute nada.
- `onda N`: processe as Seccionais da onda N listadas em `docs/VARREDURA.md` que ainda não estão FECHADAS.
- `UF,UF`: processe só essas.

## Linha de montagem por Seccional
1. **batedor-seccional**: perfil validado. Pode rodar até 3 em paralelo (são poucas requisições).
2. **coletor-seccional**: coleta e carregamento. UMA de cada vez, em fila, da menor para a maior.
3. **curador-orgaos**: classificação. Pode rodar em paralelo com a coleta da Seccional seguinte.
4. **auditor-seccional**: veredito. Sempre um agente novo, sem o contexto de quem coletou.
5. Se o veredito for REABRIR, devolva a Seccional à etapa indicada. No máximo duas voltas; na terceira, pare e relate.

## Trilha dos sites (em paralelo à do DEOAB)
Cada Seccional pode ter bases próprias no site dela (`ted-cli fontes`). Para cada UF pedida:
A. **cartografo-fontes**: verifica as bases da UF e atualiza o registro. Até 3 em paralelo (cada um fala com um site diferente).
B. **construtor-coletor-site**: um por base `verificada`, começando pelas marcadas como exclusivas. Bases de sites diferentes podem
   rodar em paralelo; duas bases do mesmo site, não.
C. **auditor-seccional** confere também as bases de site: livro `COMPLETO` e 10 itens sorteados abertos no site e comparados com o banco.

Uma Seccional só está concluída quando o DEOAB está FECHADO e toda base registrada está `coletada`, `descartada` ou `sem_base`.

Quando todas as Seccionais pedidas estiverem APROVADAS, chame o **integrador-banco** uma vez.

## Regras do maestro
- Você não coleta nem edita: delega e confere os portões.
- Portão entre etapas: perfil `validado: true` -> anos COMPLETO -> testes verdes -> auditoria `aprovada: true`.
- Uma coleta por vez contra o DEOAB. Paralelismo só em perfil, curadoria, auditoria e na trilha dos sites, com no máximo 4 agentes ao todo.
- Só um agente por vez edita `src/` (curador ou construtor): os outros esperam.
- Ao final, relate por UF: decisões antes e depois, situação, e o que ficou em aberto. Não faça merge nem deploy.
