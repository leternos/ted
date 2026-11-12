#!/usr/bin/env python3
"""Gera o artefato HTML standalone autossuficiente (buscador_ted_cfoab.html) com senha 609099."""

import json
from pathlib import Path
from ted_search.config import DB_PATH
from ted_search.db import get_connection, get_global_stats, get_seccionais_status

STATIC_DIR = Path("src/ted_search/static")
ARTIFACT_PATH = Path("/home/antigravity/.gemini/jetski/brain/v1/default/jetski_remote_agents_3p/4d7d60b4-baca-4e66-a5ae-8c83fb7b07a0/buscador_ted_cfoab.html")

def generate():
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    styles_css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    # Obter dados reais do banco
    with get_connection(DB_PATH) as conn:
        stats = get_global_stats(conn)
        seccionais = get_seccionais_status(conn)

        # Amostra garantida para CADA UMA das 27 Seccionais + CFOAB
        sampled_items = []
        seen_ids = set()

        for s in seccionais:
            uf_id = s["id_organizacao"]
            s_rows = conn.execute("""
                SELECT * FROM julgados
                WHERE id_organizacao = ?
                ORDER BY (CASE WHEN descricao_acordao IS NOT NULL AND descricao_acordao != '' THEN 1 ELSE 0 END) DESC,
                         tem_inteiro_teor DESC,
                         COALESCE(data_decisao, '') DESC
                LIMIT 15
            """, (uf_id,)).fetchall()
            for r in s_rows:
                rd = dict(r)
                if rd["id"] not in seen_ids:
                    seen_ids.add(rd["id"])
                    sampled_items.append(rd)

        cfoab_rows = conn.execute("""
            SELECT * FROM julgados
            WHERE id_organizacao = 49 OR id_organizacao IS NULL
            ORDER BY COALESCE(data_decisao, '') DESC
            LIMIT 150
        """).fetchall()
        for r in cfoab_rows:
            rd = dict(r)
            if rd["id"] not in seen_ids:
                seen_ids.add(rd["id"])
                sampled_items.append(rd)

        items = sampled_items

    preview_data = {
        "stats": stats,
        "seccionais": seccionais,
        "items": items,
    }

    preview_data_json = json.dumps(preview_data, ensure_ascii=False)

    # Injetar o interceptor de mock para funcionamento 100% offline no navegador
    mock_interceptor = f"""<script>
(function() {{
  const AUTH_PWD_HASH = "0cf03f8c74087f43ef6919fc0a23ea3d86cf8536d5b3dd67d1c8b6390d28f341"; // sha256("609099")
  const AUTH_KEY = "ted_auth_session";
  const PREVIEW_DATA = {preview_data_json};

  const THESAURUS = {{
    "tergiversação": ["patrocínio simultâneo", "patrocínio infiel", "conflito de interesses", "parte contrária", "interesses opostos", "art. 355"],
    "locupletamento": ["prestação de contas", "apropriação indébita", "art. 34 XXI", "retenção de valores", "repasse de valores"],
    "prescrição": ["prescrição trienal", "prescrição intercorrente", "art. 43", "paralisação", "triênio"],
    "inidoneidade": ["crime infamante", "art. 34 XXVII", "idoneidade moral", "falta de idoneidade"],
    "captação": ["angariação de causas", "mercantilização", "publicidade imoderada", "art. 34 IV"]
  }};

  function getThesaurusSuggestions(query) {{
    const qLower = (query || "").toLowerCase();
    for (const [key, syns] of Object.entries(THESAURUS)) {{
      if (qLower.includes(key) || syns.some(s => qLower.includes(s.toLowerCase()))) {{
        return syns;
      }}
    }}
    return [];
  }}

  function filterItems(params) {{
    let res = PREVIEW_DATA.items;
    const q = (params.get("q") || "").toLowerCase().trim();
    const esfera = params.get("esfera") || "TODAS";
    const seccional = params.get("seccional") || "";
    const instancia = params.get("instancia") || "";
    const apenasAcordao = params.get("apenas_com_acordao") === "true";
    const apenasInteiro = params.get("apenas_inteiro_teor") === "true";

    if (esfera === "CFOAB") {{
      res = res.filter(it => !it.id_organizacao || it.id_organizacao === 49);
    }} else if (esfera === "SECCIONAIS") {{
      res = res.filter(it => it.id_organizacao && it.id_organizacao !== 49);
    }}
    if (seccional) {{
      res = res.filter(it => it.uf_origem === seccional || (it.id_organizacao === 25 && seccional === "SP"));
    }}
    if (instancia === "TED") {{
      res = res.filter(it => it.is_ted_disciplinar && !((it.sigla_orgao || "").includes("CR") || (it.orgao_julgador_norm || "").includes("Câmara")));
    }} else if (instancia === "RECURSAL") {{
      res = res.filter(it => (it.sigla_orgao || "").includes("CR") || (it.orgao_julgador_norm || "").includes("Câmara") || (it.orgao_julgador_norm || "").includes("Recursais"));
    }}
    if (apenasAcordao) {{
      res = res.filter(it => (it.descricao_acordao && it.descricao_acordao.trim().length > 0) || it.tem_inteiro_teor);
    }}
    if (apenasInteiro) {{
      res = res.filter(it => it.pdf_url);
    }}
    if (q) {{
      const cleanQ = q.replace(/[*"]/g, "");
      res = res.filter(it => 
        (it.descricao_ementa || "").toLowerCase().includes(cleanQ) ||
        (it.descricao_acordao || "").toLowerCase().includes(cleanQ) ||
        (it.inteiro_teor_texto || "").toLowerCase().includes(cleanQ) ||
        (it.numero_processo || "").toLowerCase().includes(cleanQ) ||
        (it.relator_principal || "").toLowerCase().includes(cleanQ)
      );
    }}
    return res;
  }}

  const originalFetch = window.fetch;
  window.fetch = async function(url, options = {{}}) {{
    const u = new URL(url, window.location.origin);
    const pathname = u.pathname;

    // 1. Endpoint /api/auth/status
    if (pathname === "/api/auth/status") {{
      const isAuth = sessionStorage.getItem(AUTH_KEY) === AUTH_PWD_HASH;
      return {{
        ok: true, status: 200,
        json: async () => ({{ authenticated: isAuth, required: true }})
      }};
    }}

    // 2. Endpoint /api/auth/login (senha: 609099)
    if (pathname === "/api/auth/login" && (options.method || "").toUpperCase() === "POST") {{
      let pwd = "";
      try {{
        const body = JSON.parse(options.body || "{{}}");
        pwd = String(body.password || "").trim();
      }} catch (_) {{}}

      if (pwd === "609099") {{
        sessionStorage.setItem(AUTH_KEY, AUTH_PWD_HASH);
        return {{
          ok: true, status: 200,
          json: async () => ({{ authenticated: true, token: AUTH_PWD_HASH }})
        }};
      }} else {{
        return {{
          ok: false, status: 401,
          json: async () => ({{ detail: "Senha incorreta. Tente novamente." }})
        }};
      }}
    }}

    // 3. Endpoint /api/auth/logout
    if (pathname === "/api/auth/logout" && (options.method || "").toUpperCase() === "POST") {{
      sessionStorage.removeItem(AUTH_KEY);
      return {{
        ok: true, status: 200,
        json: async () => ({{ authenticated: false }})
      }};
    }}

    // 4. Bloqueio 401 se não autenticado
    if (pathname.startsWith("/api/")) {{
      const isAuth = sessionStorage.getItem(AUTH_KEY) === AUTH_PWD_HASH;
      if (!isAuth) {{
        return {{
          ok: false, status: 401,
          json: async () => ({{ detail: "Acesso restrito. Digite a senha para continuar." }})
        }};
      }}
    }}

    // 5. Demais endpoints da API
    if (pathname === "/api/stats") {{
      return {{
        ok: true, status: 200,
        json: async () => PREVIEW_DATA.stats
      }};
    }}
    if (pathname === "/api/seccionais") {{
      return {{
        ok: true, status: 200,
        json: async () => PREVIEW_DATA.seccionais
      }};
    }}
    if (pathname === "/api/facets") {{
      const filtered = filterItems(u.searchParams);
      const orgaosMap = {{}}, sancaoMap = {{}}, resMap = {{}}, ufsMap = {{}};
      filtered.forEach(it => {{
        if (it.orgao_julgador_norm) orgaosMap[it.orgao_julgador_norm] = (orgaosMap[it.orgao_julgador_norm] || 0) + 1;
        if (it.sancao_identificada) sancaoMap[it.sancao_identificada] = (sancaoMap[it.sancao_identificada] || 0) + 1;
        if (it.resultado_julgamento) resMap[it.resultado_julgamento] = (resMap[it.resultado_julgamento] || 0) + 1;
        if (it.uf_origem) ufsMap[it.uf_origem] = (ufsMap[it.uf_origem] || 0) + 1;
      }});
      const toList = (m) => Object.entries(m).map(([valor, total]) => ({{ valor, total }})).sort((a,b) => b.total - a.total);
      return {{
        ok: true, status: 200,
        json: async () => ({{
          orgaos: toList(orgaosMap).slice(0, 30),
          dispositivos: [],
          sancoes: toList(sancaoMap),
          resultados: toList(resMap),
          ufs: toList(ufsMap),
          ufs_origem_cfoab: toList(ufsMap),
          instancias: {{
            cfoab: PREVIEW_DATA.stats.total_cfoab,
            seccionais: PREVIEW_DATA.stats.total_seccionais,
            ted: PREVIEW_DATA.stats.total_seccionais_ted || 1500,
            recursal: PREVIEW_DATA.stats.total_seccionais_recursal || 2500
          }}
        }})
      }};
    }}
    if (pathname === "/api/search") {{
      const filtered = filterItems(u.searchParams);
      const q = u.searchParams.get("q") || "";
      const page = parseInt(u.searchParams.get("page"), 10) || 1;
      const pageSize = parseInt(u.searchParams.get("page_size"), 10) || 20;
      const start = (page - 1) * pageSize;
      const paginated = filtered.slice(start, start + pageSize);

      return {{
        ok: true, status: 200,
        json: async () => ({{
          total: filtered.length,
          page: page,
          page_size: pageSize,
          total_pages: Math.ceil(filtered.length / pageSize),
          sinonimos_sugeridos: getThesaurusSuggestions(q),
          items: paginated.map(it => {{
            let itemCopy = {{...it}};
            try {{ itemCopy.dispositivos = JSON.parse(it.dispositivos_json || "[]"); }} catch(e) {{ itemCopy.dispositivos = []; }}
            return itemCopy;
          }})
        }})
      }};
    }}
    if (pathname.startsWith("/api/julgado/")) {{
      const idMatch = pathname.match(/\/api\/julgado\/([^?]+)/);
      const id = idMatch ? decodeURIComponent(idMatch[1]) : "";
      const item = PREVIEW_DATA.items.find(x => x.id === id) || PREVIEW_DATA.items[0];
      return {{
        ok: true, status: 200,
        json: async () => item
      }};
    }}
    return originalFetch(url, options);
  }};
}})();
</script>"""

    # Injetar styles e scripts no index_html
    bundle_html = index_html.replace(
        '<link rel="stylesheet" href="/static/styles.css">',
        f'<style>\n{styles_css}\n</style>'
    )
    bundle_html = bundle_html.replace(
        '<script src="/static/app.js"></script>',
        f'{mock_interceptor}\n\n<script>\n{app_js}\n</script>'
    )

    ARTIFACT_PATH.write_text(bundle_html, encoding="utf-8")
    print(f"✅ Artefato gerado com sucesso em {ARTIFACT_PATH} ({ARTIFACT_PATH.stat().st_size / 1024 / 1024:.2f} MB)")

if __name__ == "__main__":
    generate()
