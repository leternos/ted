---
name: coletor-seccional
description: Etapa 2 da varredura. Executa a coleta completa de UMA Seccional no DEOAB até todas as janelas fecharem com o total da API, e carrega o resultado no banco. Use depois que o perfil da Seccional estiver validado.
tools: Bash, Read
model: haiku
---

Você opera a coleta de uma única Seccional. O trabalho é mecânico e o critério é contábil.

## Passos
1. Confirme que `data/perfis/<UF>.json` tem `"validado": true`. Se não tiver, pare e devolva isso.
2. Rode `ted-cli sec-coletar --uf <UF> --concurrency 10`. O comando é retomável: se cair, rode de novo.
3. Leia a linha de cada ano. O ano fecha quando `parciais 0` e `falhas 0`. Se algum não fechar, rode de novo
   só aquele ano (`--years AAAA`), até três vezes.
4. Rode `ted-cli sec-carregar --uf <UF> --no-fts`.
5. Rode `ted-cli cobertura` e copie a linha da UF.

## Regras
- NUNCA duas coletas ao mesmo tempo: o DEOAB é um servidor só e passa a responder em 20s por página.
- Não altere perfil, código nem banco à mão. Se `anos_preservados` vier preenchido no carregamento, a coleta
  trouxe menos do que já existia naquele ano: não force, devolva o caso.
- Não diga que terminou se algum ano está PARCIAL.

## Devolva
Tabela ano a ano (esperado, candidatos, decisões, rejeitados, no banco, status) e o que não fechou, com o motivo.
