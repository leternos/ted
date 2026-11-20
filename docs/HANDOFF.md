# Handoff — repo `leternos/ted` (20/11/2026, 20h15 UTC)

Documento para continuar o trabalho numa sessão nova, na nuvem. Leia inteiro antes de mexer em qualquer coisa.

## 1. Onde estamos

- **No ar:** https://ted-oab.web.app (atrás da senha de acesso), com **222.670 julgados**, dos quais 198.255 são disciplinares e 2.473 têm o selo "inteiro teor". O que está no ar é o `main` em `9dec8cd` (PR #9).
- **Fontes:**
  - Diário (DEOAB) das 27 Seccionais.
  - Bases próprias dos sites de SP, GO, PR, DF, SE, BA, SC, RJ, MA, MT (1996–2005), PB, RO e RN.
- **Branch de trabalho:** `docs/leituras-confirmacao`. Tem as regras novas de classificação ainda **não verificadas** (seção 3) e este arquivo.

## 2. Regras do dono do produto (inegociáveis)

O cliente é **Antonio Carlos Monteiro da Silva Filho, relator do TED**.

1. **Nunca mascarar, abreviar, anonimizar, cortar ou redigir texto por conta própria.** Texto e nomes entram no banco exatamente como a fonte publica. Repita essa regra explicitamente a qualquer subagente.
2. **Ficam dentro, marcados como disciplinares:**
   - reabilitação;
   - consultas respondidas pelo TED;
   - suspensão preventiva;
   - exclusão;
   - publicações de PE do tipo Penalidade ou Suspensão Preventiva que tragam voto e resultado;
   - extratos do RS "dá-se ciência… por unanimidade, decidiu" da 2ª Câmara Recursal.
3. **Ficam fora:**
   - atas do Conselho Pleno;
   - avisos só de sanção;
   - listas e pautas de processos;
   - adiamentos;
   - despachos e decisões monocráticas;
   - extratos do DF (Pleno/Prerrogativas), a menos que o Guh diga "extratos do DF dentro".
4. **Julgado sem número de processo legível fica.** Não gastar mais esforço com números de processo.
5. **Selo "Inteiro teor":** só para documento que realmente é inteiro teor. A caixa de citação mostra o texto exatamente como foi publicado.
6. **Coleta de sites:** nunca contornar login, captcha, WAF/Cloudflare, bloqueio geográfico ou `robots.txt`. Estão bloqueados e assim ficam:
   - portal do RS (reCAPTCHA);
   - MS, CE, ES e MG (Cloudflare/WAF);
   - AC (bloqueio geográfico; pode ser tentado da máquina do Guh);
   - TO (o robots bloqueia bots de IA);
   - diário próprio de AL (o robots proíbe ClaudeBot).
7. **Autorização de merge e deploy:** o Guh deu sinal verde para mergear PRs quando os testes passam e o banco confere, e para fazer o deploy pelo workflow do GitHub.
8. **Segurança:** a chave da conta de serviço do GCP fica só no secret `GCP_SA_KEY` do GitHub; nunca pedir nem colar no chat. Não entrar no site passando pela senha para conferir o banco ao vivo.

## 3. Trabalho interrompido: correções das leituras de confirmação

As leituras de confirmação (só leitura, por agentes `auditor-seccional`) de **PR, BA, ES e MS** deram todas **REABRIR**. São erros mecânicos:

| UF | Erro principal |
|---|---|
| PR | Cerca de 17 com marca 0 que deviam ser 1 (abrem com "Representante/Representado", art. 34, suspensão preventiva, reexame de exclusão, consulta). Cerca de 40 com marca 1 que deviam ser 0 ("Averiguação de idoneidade moral" para inscrição, anistia de débitos, prestação de contas da CAA). Entre 59 e 166 republicações duplicadas. |
| BA | Cerca de 29 listas "ficam NOTIFICADOS… declaradas prescritas", convocações de turma (384313, 384315) e cerca de 11 despachos monocráticos (1065254, 959340, 668048…) que deviam estar fora. Acórdãos que abrem com "… TURMA TRIBUNAL DE ÉTICA E DISCIPLINA" marcados 0 (1071156, 1073464, 1093103, 1138257). Reabilitação marcada 0 (593651, 782507). Câmara de Prerrogativas "/CPRR" marcada 1 (747819, 881345, 895910, 977024, 1140532, 1142154). 17 grupos duplicados. |
| ES | Orçamento da CAAES, isenção, inscrição, edição de súmula e "SÚMULA Nº …/TED" marcados 1. Erratas "onde se lê" e aviso "notifica… do resultado do julgamento" que deviam estar fora. Representações marcadas 0 (79140, 479825, 823326, 463229). 13 grupos duplicados. |
| MS | Intimações "acórdão prolatado pelas Turmas do TED" marcadas 0 (501833). Reabilitação e exclusão marcadas 0 (182171, 884789). Carta "Ilmo Sr." (259290) e convocação para sessão (575548) que deviam estar fora. 7 duplicatas. Volume muito baixo em 2019–2021 ainda sem explicação. |

**O que já foi escrito** (commitado como WIP nesta branch, **sem conferência antes/depois**):

- `src/ted_search/collectors/deoab_nacional.py`, função `eh_materia_disciplinar`: bloco "leituras de confirmação de PR, BA, ES e MS". Ele cobre Câmara de Prerrogativas e /CPRR = 0, matéria administrativa no cabeçalho = 0, averiguação de idoneidade para inscrição = 0, acórdão que abre com turma do TED = 1, Representante + Representado no início = 1, reexame de exclusão = 1, intimação de acórdão das Turmas (MS) = 1 e pedido de reabilitação = 1. Também foi ampliado o padrão `ANISTIA D[EOA]S …`.
- `src/ted_search/varredura.py`, função `motivo_fora_de_escopo`: bloco "leituras de confirmação (20/11/2026)". Ele tira a lista de prescritas, "CONVOCA OS SEUS MEMBROS", "NOTIFICA AS PARTES… PARA COMPARECEREM À SESSÃO", aviso de resultado sem ementa, "Ilmo Sr." e a errata "onde se lê" sem ementa.
- Os testes passam (110), mas as regras **não foram comparadas antes/depois**. Pode haver regra pegando julgado verdadeiro.

**Ainda falta, vindo das leituras:**

- dedupe de republicações do PR, com a chave (órgão, Número Acórdão, processo);
- despachos monocráticos da BA ("DECISÃO: … Trata-se de", "Vistos, etc." sem ementa);
- registrar os veredictos em `docs/SEGUNDA_LEITURA.md`.

## 4. ATENÇÃO: o cache bruto não está no repositório

- `data/raw/` tem 2,1 GB e está no `.gitignore`. É dele que `normalizar_decisoes` lê, e portanto também os scripts de conferência e `ted-cli sec-carregar`.
- O banco vem no repositório empacotado: `data/ted_jurisprudencia.db.xz.parte-00/01` mais o manifesto. Para abrir: `python -m ted_search.banco desempacotar`.
- Numa máquina nova, **sem o cache bruto não dá para conferir nem recarregar as regras**. Opções:
  1. **Refazer o cache pelo Diário:** `ted-cli sec-coletar --uf UF` para cada UF que for recarregar. Basta PR, BA, ES e MS para esta correção, mas a conferência antes/depois deve rodar em todas as 26. A coleta é retomável e demora horas no total.
  2. O Guh pode copiar `data/raw/` para um bucket do GCP e a sessão nova baixa de lá. Precisa de credencial na sessão; não fazer sem ele pedir.
- Os caches dos sites (`data/raw/site_*`) também só existem aqui. Recoletar é lento: SC levou cerca de 35 minutos e RJ tem pausa de 10 s por requisição. Só é preciso se for mexer nesses coletores.

## 5. Protocolo de correção de regras (siga à risca)

Os scripts ficam em `scripts/curadoria/`. Copie-os para `/tmp` ou ajuste os caminhos; eles esperam rodar da raiz do repositório e gravam em `/tmp`.

1. **Linha de base, com o código ANTIGO:**
   - `git stash` (ou `git checkout main -- src/`);
   - `python scripts/curadoria/snap.py /tmp/snap_antes.json CE BA ES GO PE SE RN MT PA PB MA PI AC AL TO AP RR RJ MG RO MS AM SC PR RS DF` (cerca de 20 minutos; rode em background com timeout longo);
   - `git stash pop`.
2. `pytest -q`.
3. Rode o mesmo comando com saída em `/tmp/snap_depois.json`.
4. `rm -f /tmp/diff.out` **antes** de comparar. Depois rode `python scripts/curadoria/diff.py`, que imprime por UF quantos passaram de 0 para 1, de 1 para 0, saíram e entraram, e grava `/tmp/diff_UF.json`.
5. Para **cada** UF que mudou, rode `python scripts/curadoria/ver.py UF up|dn|saiu N` e leia os textos. Estreite qualquer regra que pegue julgado verdadeiro.
   - Este passo já pegou regras ruins antes: 32 julgados da BA removidos e 2.364 avisos de RO admitidos.
   - `scripts/curadoria/olha.py UF N id…` mostra o início de registros específicos do banco.
6. Recarregue cada UF que mudou: `ted-cli sec-carregar --uf UF --no-fts`. Depois rode `ted-cli cobertura --sync A,B,C`, com as UFs separadas por vírgula.
7. Faça o commit.

## 6. Procedimento de publicação

1. `ted-cli cobertura --fts` (cerca de 1 minuto).
2. `pytest -q`.
3. Confira as contagens: `select count(*), sum(is_ted_disciplinar), sum(tem_inteiro_teor) from julgados`.
4. `python -m ted_search.banco empacotar` e **depois** `python -m ted_search.banco conferir`. Rode em sequência, nunca em paralelo com o FTS. Leva cerca de 12 minutos e gera partes de 90 MB.
5. Faça commit e push.
6. Abra o PR pela API, porque o GraphQL não está disponível e `gh pr create` falha:

   ```
   gh api repos/leternos/ted/pulls -f title=... -f head=<branch> -f base=main -f body=...
   ```

   O corpo termina com "🤖 Generated with [Claude Code](https://claude.com/claude-code)" e a URL da sessão.
7. Merge: `gh api -X PUT repos/leternos/ted/pulls/N/merge -f merge_method=merge`.
8. Deploy: `gh api -X POST repos/leternos/ted/actions/workflows/416308544/dispatches -f ref=main -f 'inputs[project_id]=dicionario-503613' -f 'inputs[region]=us-central1'`.
9. Acompanhe com `gh api repos/leternos/ted/actions/runs/<id>`. Depois confira que https://ted-oab.web.app responde 200 e que `/health` responde `{"status":"ok"}`.

**Sobre o serviço:** ele roda no Cloud Run (projeto `dicionario-503613`, `us-central1`) com `TED_DB_READONLY=1`. O banco abre em `mode=ro&immutable=1`. Sem isso, o container de 2 GiB não sobe.

## 7. Ferramentas do repositório

- **`ted-cli`:**
  - `sec-perfil`, `sec-coletar`, `sec-carregar --uf UF --no-fts`, `cobertura [--sync A,B] [--fts]`;
  - sites: `site-coletar`, `site-carregar --uf UF --fonte <slug> --no-fts`, `fontes`.
- **Perfis do Diário:** `data/perfis/*.json` (27). **Levantamento dos sites:** `data/fontes/<UF>.json`. **Coletores de site:** `src/ted_search/collectors/sites/`.
- **Agentes do projeto** (em `.claude/agents`):
  - `batedor-seccional`, `coletor-seccional`, `curador-orgaos`;
  - `auditor-seccional`: só leitura; dê a cada um o seu próprio `/tmp/aud_<uf>`, porque já houve sobrescrita;
  - `integrador-banco`, `cartografo-fontes`, `construtor-coletor-site`.
- **Skill:** `varredura`.
- **Diário de leituras:** `docs/SEGUNDA_LEITURA.md`.

## 8. Próximos passos, em ordem

1. Refazer o cache bruto (seção 4) e terminar a correção da seção 3 com o protocolo da seção 5.
2. Publicar (seção 6).
3. Fazer as próximas leituras de confirmação, 3 agentes por vez: CE, MA, PA, MT, PB, SE, RN, PI, AC, RO, DF e AL/TO/AP/RR.
4. Resíduos já conhecidos (lista em `docs/SEGUNDA_LEITURA.md`):
   - decisões monocráticas de presidente em PI 246897, AC 546575 e RO 601935;
   - republicações corrigidas do RN;
   - cerca de 13 registros de inscrição do MT;
   - 60 julgados do RS sob o tipo "Despacho" e 43 grupos duplicados do RS;
   - 25 duplicatas e 4 erratas longas do PR.

## 9. Perguntas esperando o Guh (não agir sem resposta; não insistir)

1. **17 consultas da BA com o CPF do consulente na capa do PDF:** estão fora da carga. Liberar por `data/raw/site_ba_consultivo/liberados.txt` + `site-carregar`.
2. **Livro de ementas de 2018 do MT ("proibida a reprodução sem autorização"):** não foi coletado.
3. **Repositório público:** o banco empacotado pode ser baixado por qualquer pessoa, e a senha padrão `609099` está no código.
4. **8 denúncias de PE por exercício ilegal da profissão** (feitas contra não advogados) estão marcadas como disciplinares.
5. **Extratos do DF:** cerca de 1.440, hoje fora.
6. **Diário próprio de AL (2015–2021):** o robots bloqueia; o Guh pode pedir à OAB/AL ou baixar ele mesmo.
