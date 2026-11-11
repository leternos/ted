/**
 * Frontend do Buscador Nacional de Jurisprudência Ético-Disciplinar da OAB
 * Escopo Nacional: Conselho Federal da OAB (CFOAB) e 27 Seccionais (TEDs e Câmaras Recursais)
 */

const state = {
  q: "",
  mode: "todos",
  esfera: "TODAS", // "TODAS" | "CFOAB" | "SECCIONAIS"
  seccional: "", // UF (ex: "SP", "SE")
  instancia: "", // "" | "TED" | "RECURSAL"
  apenas_com_acordao: true,
  apenas_inteiro_teor: false,
  apenas_ted: false,
  contexto: "",
  orgao: "",
  dispositivo: "",
  sancao: "",
  resultado: "",
  uf: "", // UF de origem no CFOAB
  relator: "",
  ano_inicio: "",
  ano_fim: "",
  numero_processo: "",
  numero_ementa: "",
  sort: "relevancia",
  page: 1,
  page_size: 20,
};

let currentModalText = "";
let seccionaisData = [];

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sanitizeHighlightedHtml(str) {
  if (!str) return "";
  const placeholderOpen = "___MARK_OPEN___";
  const placeholderClose = "___MARK_CLOSE___";
  const temp = String(str)
    .replace(/<mark>/gi, placeholderOpen)
    .replace(/<\/mark>/gi, placeholderClose);
  return escapeHtml(temp)
    .replaceAll(placeholderOpen, "<mark>")
    .replaceAll(placeholderClose, "</mark>");
}

function formatNumber(n) {
  return Number(n || 0).toLocaleString("pt-BR");
}

function showToast(msg) {
  const toast = document.getElementById("toast-notification");
  if (!toast) return;
  toast.textContent = msg;
  toast.hidden = false;
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 2800);
}

// =========================================================================
// SINCRONIZAÇÃO DE URL (Permite links compartilháveis e navegação fluida)
// =========================================================================

function syncStateToUrl() {
  const url = new URL(window.location.href);
  const p = url.searchParams;

  const setOrDel = (key, val, defaultVal = "") => {
    if (val && val !== defaultVal) p.set(key, val);
    else p.delete(key);
  };

  setOrDel("q", state.q);
  setOrDel("mode", state.mode, "todos");
  setOrDel("esfera", state.esfera, "TODAS");
  setOrDel("seccional", state.seccional);
  setOrDel("instancia", state.instancia);
  setOrDel("acordao", state.apenas_com_acordao ? "" : "0");
  setOrDel("inteiro", state.apenas_inteiro_teor ? "1" : "");
  setOrDel("ted", state.apenas_ted ? "1" : "");
  setOrDel("orgao", state.orgao);
  setOrDel("disp", state.dispositivo);
  setOrDel("sancao", state.sancao);
  setOrDel("resultado", state.resultado);
  setOrDel("uf", state.uf);
  setOrDel("relator", state.relator);
  setOrDel("ano_ini", state.ano_inicio);
  setOrDel("ano_fim", state.ano_fim);
  setOrDel("proc", state.numero_processo || state.numero_ementa);
  setOrDel("sort", state.sort, "relevancia");
  setOrDel("page", state.page > 1 ? String(state.page) : "");

  window.history.replaceState({}, "", url.toString());
}

function restoreStateFromUrl() {
  const p = new URLSearchParams(window.location.search);
  if (p.has("q")) state.q = p.get("q");
  if (p.has("mode")) state.mode = p.get("mode");
  if (p.has("esfera")) state.esfera = p.get("esfera").toUpperCase();
  if (p.has("seccional")) state.seccional = p.get("seccional").toUpperCase();
  if (p.has("instancia")) state.instancia = p.get("instancia").toUpperCase();
  if (p.has("acordao")) state.apenas_com_acordao = p.get("acordao") !== "0";
  if (p.has("inteiro")) state.apenas_inteiro_teor = p.get("inteiro") === "1";
  if (p.has("ted")) state.apenas_ted = p.get("ted") === "1";
  if (p.has("orgao")) state.orgao = p.get("orgao");
  if (p.has("disp")) state.dispositivo = p.get("disp");
  if (p.has("sancao")) state.sancao = p.get("sancao");
  if (p.has("resultado")) state.resultado = p.get("resultado");
  if (p.has("uf")) state.uf = p.get("uf").toUpperCase();
  if (p.has("relator")) state.relator = p.get("relator");
  if (p.has("ano_ini")) state.ano_inicio = p.get("ano_ini");
  if (p.has("ano_fim")) state.ano_fim = p.get("ano_fim");
  if (p.has("proc")) {
    const procVal = p.get("proc");
    if (procVal.includes("/")) state.numero_ementa = procVal;
    else state.numero_processo = procVal;
  }
  if (p.has("sort")) state.sort = p.get("sort");
  if (p.has("page")) state.page = parseInt(p.get("page"), 10) || 1;

  // Atualizar inputs da página com o estado restaurado
  const qInput = document.getElementById("search-query-input");
  if (qInput && state.q) {
    qInput.value = state.q;
    const clearBtn = document.getElementById("clear-query-btn");
    if (clearBtn) clearBtn.hidden = false;
  }
  const chkAcordao = document.getElementById("toggle-apenas-acordao");
  if (chkAcordao) chkAcordao.checked = state.apenas_com_acordao;
  const lblAcordao = document.getElementById("label-toggle-acordao");
  if (lblAcordao) lblAcordao.classList.toggle("checked", state.apenas_com_acordao);

  const chkInteiro = document.getElementById("toggle-apenas-inteiro-teor");
  if (chkInteiro) chkInteiro.checked = state.apenas_inteiro_teor;
  const lblInteiro = document.getElementById("label-toggle-inteiro-teor");
  if (lblInteiro) lblInteiro.classList.toggle("checked", state.apenas_inteiro_teor);

  const chkTed = document.getElementById("toggle-apenas-ted");
  if (chkTed) chkTed.checked = state.apenas_ted;
  const lblTed = document.getElementById("label-toggle-ted");
  if (lblTed) lblTed.classList.toggle("checked", state.apenas_ted);

  const sortSel = document.getElementById("sort-select");
  if (sortSel && state.sort) sortSel.value = state.sort;

  const relInp = document.getElementById("filter-relator");
  if (relInp && state.relator) relInp.value = state.relator;

  const anoIniInp = document.getElementById("filter-ano-inicio");
  if (anoIniInp && state.ano_inicio) anoIniInp.value = state.ano_inicio;

  const anoFimInp = document.getElementById("filter-ano-fim");
  if (anoFimInp && state.ano_fim) anoFimInp.value = state.ano_fim;

  const procInp = document.getElementById("filter-processo");
  if (procInp && (state.numero_processo || state.numero_ementa)) {
    procInp.value = state.numero_processo || state.numero_ementa;
  }
}

// =========================================================================
// CARREGAMENTO DE ESTATÍSTICAS E SECCIONAIS
// =========================================================================

async function loadHeaderStats() {
  try {
    const resp = await fetch("/api/stats");
    const data = await resp.json();

    const elGeral = document.getElementById("stat-total-geral");
    if (elGeral) elGeral.textContent = formatNumber(data.total_geral);

    const elCfoab = document.getElementById("stat-total-cfoab");
    if (elCfoab) elCfoab.textContent = formatNumber(data.total_cfoab);

    const elSecc = document.getElementById("stat-total-seccionais");
    if (elSecc) elSecc.textContent = formatNumber(data.total_seccionais);

    const elLblSecc = document.getElementById("stat-seccionais-label");
    if (elLblSecc) {
      elLblSecc.textContent = `Seccionais · ${data.seccionais_ativas || 2}/${data.seccionais_total || 27} ativas`;
    }

    const elPdfs = document.getElementById("stat-total-pdfs");
    if (elPdfs) elPdfs.textContent = formatNumber(data.total_com_pdf_url);

    // Contadores da barra de navegação
    const navTodas = document.getElementById("nav-count-todas");
    if (navTodas) navTodas.textContent = `(${formatNumber(data.total_geral)})`;

    const navCfoab = document.getElementById("nav-count-cfoab");
    if (navCfoab) navCfoab.textContent = `(${formatNumber(data.total_cfoab)})`;

    const navSecc = document.getElementById("nav-count-seccionais");
    if (navSecc) navSecc.textContent = `(${formatNumber(data.total_seccionais)})`;
  } catch (err) {
    console.error("Erro ao carregar estatísticas:", err);
  }
}

async function loadSeccionais() {
  try {
    const resp = await fetch("/api/seccionais");
    seccionaisData = await resp.json();
    buildSeccionaisMegaMenu(seccionaisData);
    populateSeccionalFilterSelect(seccionaisData);
  } catch (err) {
    console.error("Erro ao carregar seccionais:", err);
  }
}

function buildSeccionaisMegaMenu(items) {
  const grid = document.getElementById("mega-menu-grid");
  if (!grid) return;

  const coverageEl = document.getElementById("mega-menu-coverage");
  if (coverageEl) {
    const ativas = items.filter((s) => s.status === "ativa").length;
    coverageEl.textContent = `${ativas} de ${items.length} Seccionais integradas`;
  }

  // Agrupar por região (ordem padrão brasileira)
  const regiaoOrdem = ["Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"];
  const porRegiao = {};
  regiaoOrdem.forEach((r) => (porRegiao[r] = []));
  items.forEach((s) => {
    const reg = s.regiao || "Outros";
    if (!porRegiao[reg]) porRegiao[reg] = [];
    porRegiao[reg].push(s);
  });

  let html = "";
  regiaoOrdem.forEach((reg) => {
    const lista = porRegiao[reg] || [];
    if (lista.length === 0) return;
    html += `
      <div class="mega-region">
        <div class="mega-region-title">${escapeHtml(reg)}</div>
        <div class="mega-region-list">
          ${lista
            .map((s) => {
              const isAtiva = s.status === "ativa";
              const isSelected = state.esfera === "SECCIONAIS" && state.seccional === s.uf;
              return `
                <button
                  type="button"
                  class="mega-uf-btn ${isSelected ? "selected" : ""} ${isAtiva ? "" : "is-disabled"}"
                  data-uf="${escapeHtml(s.uf)}"
                  data-nome="${escapeHtml(s.nome)}"
                  ${isAtiva ? "" : 'title="Base em fase de coleta e integração"'}
                >
                  <span class="mega-uf-code">OAB/${escapeHtml(s.uf)}</span>
                  <span class="mega-uf-name">${escapeHtml(s.nome)}</span>
                  <span class="mega-uf-meta">
                    ${isAtiva ? `${formatNumber(s.total)} julgados` : "Em breve"}
                  </span>
                </button>
              `;
            })
            .join("")}
        </div>
      </div>
    `;
  });

  grid.innerHTML = html;

  grid.querySelectorAll(".mega-uf-btn:not(.is-disabled)").forEach((btn) => {
    btn.addEventListener("click", () => {
      const uf = btn.getAttribute("data-uf");
      setScope("SECCIONAIS", uf, "");
      closeMegaMenu();
    });
  });
}

function populateSeccionalFilterSelect(items) {
  const sel = document.getElementById("filter-seccional");
  if (!sel) return;

  const current = state.seccional;
  const regiaoOrdem = ["Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"];
  const porRegiao = {};
  regiaoOrdem.forEach((r) => (porRegiao[r] = []));
  items.forEach((s) => {
    const reg = s.regiao || "Outros";
    if (!porRegiao[reg]) porRegiao[reg] = [];
    porRegiao[reg].push(s);
  });

  let html = `<option value="">Todas as Seccionais</option>`;
  regiaoOrdem.forEach((reg) => {
    const lista = porRegiao[reg] || [];
    if (lista.length === 0) return;
    html += `<optgroup label="${escapeHtml(reg)}">`;
    lista.forEach((s) => {
      const isAtiva = s.status === "ativa";
      const selected = s.uf === current ? "selected" : "";
      const extra = isAtiva ? `(${formatNumber(s.total)})` : "(em breve)";
      html += `<option value="${escapeHtml(s.uf)}" ${selected} ${isAtiva ? "" : "disabled"}>OAB/${escapeHtml(s.uf)} — ${escapeHtml(s.nome)} ${extra}</option>`;
    });
    html += `</optgroup>`;
  });

  sel.innerHTML = html;
}

// =========================================================================
// CONTROLE DE ESCOPO E NAVEGAÇÃO
// =========================================================================

function setScope(esfera, seccional = "", instancia = "") {
  state.esfera = esfera;
  state.seccional = seccional;
  state.instancia = instancia;
  state.orgao = "";
  state.page = 1;

  updateScopeUI();
  syncStateToUrl();
  executeSearch();
  fetchFacets();
}

function updateScopeUI() {
  // 1. Atualizar abas principais
  document.querySelectorAll(".main-nav .nav-item").forEach((btn) => {
    const esf = btn.getAttribute("data-esfera");
    if (esf === state.esfera) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });

  // 2. Visibilidade e controles da instância
  const instControl = document.getElementById("instancia-control");
  const isSeccionalScope = state.esfera === "SECCIONAIS" || Boolean(state.seccional);
  if (instControl) {
    instControl.hidden = !isSeccionalScope;
    instControl.querySelectorAll(".seg-btn").forEach((btn) => {
      const inst = btn.getAttribute("data-instancia");
      btn.classList.toggle("active", inst === state.instancia);
    });
  }

  // 3. Exibição de filtros no sidebar
  const secSec = document.getElementById("filter-section-seccional");
  if (secSec) {
    secSec.hidden = state.esfera === "CFOAB";
    const secSelect = document.getElementById("filter-seccional");
    if (secSelect) secSelect.value = state.seccional || "";
  }

  const ufOrigSec = document.getElementById("filter-section-uf-origem");
  if (ufOrigSec) {
    ufOrigSec.hidden = state.esfera === "SECCIONAIS";
  }

  // 4. Breadcrumb e dica de escopo
  renderScopeBreadcrumb();
  updateNavScopeHint();

  // 5. Destacar no mega-menu se aberto
  const megaGrid = document.getElementById("mega-menu-grid");
  if (megaGrid) {
    megaGrid.querySelectorAll(".mega-uf-btn").forEach((btn) => {
      const uf = btn.getAttribute("data-uf");
      btn.classList.toggle("selected", state.esfera === "SECCIONAIS" && state.seccional === uf);
    });
  }
}

function renderScopeBreadcrumb() {
  const bc = document.getElementById("scope-breadcrumb");
  if (!bc) return;

  const parts = [];
  parts.push(`<span class="scope-label">Escopo:</span>`);

  if (state.esfera === "TODAS") {
    parts.push(`<strong>Todas as bases</strong> <span style="color:var(--text-muted);font-weight:normal;">(Conselho Federal + 27 Seccionais)</span>`);
  } else if (state.esfera === "CFOAB") {
    parts.push(`
      <span class="scope-chip">
        <span>Conselho Federal (CFOAB)</span>
        <button type="button" class="scope-chip-clear" id="scope-clear-cfoab" title="Voltar para todas as bases">&times;</button>
      </span>
    `);
  } else if (state.esfera === "SECCIONAIS") {
    parts.push(`<span>Seccionais</span>`);
    if (state.seccional) {
      parts.push(`<span class="scope-sep">&rsaquo;</span>`);
      parts.push(`
        <span class="scope-chip">
          <span>OAB/${escapeHtml(state.seccional)}</span>
          <button type="button" class="scope-chip-clear" id="scope-clear-seccional" title="Ver todas as Seccionais">&times;</button>
        </span>
      `);
    } else {
      parts.push(`<span class="scope-sep">&rsaquo;</span>`);
      parts.push(`<span>Todas as Seccionais</span>`);
    }
  }

  if (state.instancia) {
    parts.push(`<span class="scope-sep">&rsaquo;</span>`);
    const labelInst = state.instancia === "TED" ? "1ª Instância (TED)" : "2ª Instância (Recursais)";
    parts.push(`
      <span class="scope-chip">
        <span>${labelInst}</span>
        <button type="button" class="scope-chip-clear" id="scope-clear-instancia" title="Todas as instâncias">&times;</button>
      </span>
    `);
  }

  bc.innerHTML = parts.join(" ");

  const btnClrCfoab = document.getElementById("scope-clear-cfoab");
  if (btnClrCfoab) {
    btnClrCfoab.addEventListener("click", () => setScope("TODAS", "", ""));
  }
  const btnClrSecc = document.getElementById("scope-clear-seccional");
  if (btnClrSecc) {
    btnClrSecc.addEventListener("click", () => setScope("SECCIONAIS", "", state.instancia));
  }
  const btnClrInst = document.getElementById("scope-clear-instancia");
  if (btnClrInst) {
    btnClrInst.addEventListener("click", () => setScope(state.esfera, state.seccional, ""));
  }
}

function updateNavScopeHint() {
  const hintEl = document.getElementById("nav-scope-hint");
  if (!hintEl) return;

  if (state.esfera === "TODAS") {
    hintEl.textContent = "Pesquisando em todo o acervo nacional da OAB";
  } else if (state.esfera === "CFOAB") {
    hintEl.textContent = "Pesquisando nas Câmaras e Órgão Especial do Conselho Federal";
  } else if (state.esfera === "SECCIONAIS") {
    if (state.seccional) {
      hintEl.textContent = `Pesquisando na Seccional OAB/${state.seccional}${
        state.instancia ? (state.instancia === "TED" ? " (1ª Instância · TED)" : " (2ª Instância · Câmaras Recursais)") : ""
      }`;
    } else {
      hintEl.textContent = "Pesquisando nos Tribunais de Ética e Câmaras Recursais das Seccionais";
    }
  }
}

function toggleMegaMenu() {
  const menu = document.getElementById("seccionais-mega-menu");
  const btn = document.getElementById("nav-seccionais");
  if (!menu || !btn) return;

  const isHidden = menu.hidden;
  menu.hidden = !isHidden;
  btn.setAttribute("aria-expanded", String(isHidden));
}

function closeMegaMenu() {
  const menu = document.getElementById("seccionais-mega-menu");
  const btn = document.getElementById("nav-seccionais");
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute("aria-expanded", "false");
}

// =========================================================================
// PARÂMETROS E BUSCA
// =========================================================================

function buildSearchParams() {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  params.set("mode", state.mode);
  params.set("esfera", state.esfera);
  if (state.seccional) params.set("seccional", state.seccional);
  if (state.instancia) params.set("instancia", state.instancia);
  params.set("apenas_ted", String(state.apenas_ted));
  params.set("apenas_com_acordao", String(state.apenas_com_acordao));
  params.set("apenas_inteiro_teor", String(state.apenas_inteiro_teor));
  if (state.contexto) params.set("contexto", state.contexto);
  if (state.orgao) params.set("orgao", state.orgao);
  if (state.dispositivo) params.set("dispositivo", state.dispositivo);
  if (state.sancao) params.set("sancao", state.sancao);
  if (state.resultado) params.set("resultado", state.resultado);
  if (state.uf) params.set("uf", state.uf);
  if (state.relator) params.set("relator", state.relator);
  if (state.ano_inicio) params.set("ano_inicio", state.ano_inicio);
  if (state.ano_fim) params.set("ano_fim", state.ano_fim);
  if (state.numero_processo) params.set("numero_processo", state.numero_processo);
  if (state.numero_ementa) params.set("numero_ementa", state.numero_ementa);
  params.set("sort", state.sort);
  params.set("page", String(state.page));
  params.set("page_size", String(state.page_size));
  return params;
}

async function fetchFacets() {
  const params = new URLSearchParams({
    q: state.q,
    mode: state.mode,
    esfera: state.esfera,
    seccional: state.seccional,
    instancia: state.instancia,
    apenas_ted: String(state.apenas_ted),
    apenas_com_acordao: String(state.apenas_com_acordao),
    apenas_inteiro_teor: String(state.apenas_inteiro_teor),
  });

  try {
    const resp = await fetch(`/api/facets?${params.toString()}`);
    const facets = await resp.json();

    renderFacetList("facet-orgaos-list", facets.orgaos, "orgao");
    renderFacetList("facet-dispositivos-list", facets.dispositivos, "dispositivo");
    renderFacetList("facet-sancoes-list", facets.sancoes, "sancao");
    renderFacetList("facet-resultados-list", facets.resultados, "resultado");

    // Preencher select de UF de origem do recurso (CFOAB)
    const ufsList = facets.ufs_origem_cfoab || facets.ufs || [];
    populateUfSelect(ufsList);

    // Atualizar contadores das instâncias
    if (facets.instancias) {
      const instAll = document.getElementById("inst-count-all");
      const instTed = document.getElementById("inst-count-ted");
      const instRec = document.getElementById("inst-count-recursal");
      const totalInst = (facets.instancias.ted || 0) + (facets.instancias.recursal || 0);

      if (instAll) instAll.textContent = `(${formatNumber(state.esfera === "SECCIONAIS" && !state.seccional ? facets.instancias.seccionais : totalInst)})`;
      if (instTed) instTed.textContent = `(${formatNumber(facets.instancias.ted)})`;
      if (instRec) instRec.textContent = `(${formatNumber(facets.instancias.recursal)})`;
    }
  } catch (err) {
    console.error("Erro ao carregar facetas:", err);
  }
}

function renderFacetList(containerId, items, stateKey) {
  const ul = document.getElementById(containerId);
  if (!ul) return;
  if (!items || items.length === 0) {
    ul.innerHTML = `<li style="font-size:0.78rem;color:var(--text-muted);padding:0.25rem 0.4rem;">Nenhuma opção no filtro atual</li>`;
    return;
  }
  ul.innerHTML = items
    .map((item) => {
      const isSelected = state[stateKey] === item.valor;
      return `
        <li class="facet-item ${isSelected ? "selected" : ""}" data-key="${stateKey}" data-val="${escapeHtml(item.valor)}">
          <span class="facet-name" title="${escapeHtml(item.valor)}">${escapeHtml(item.valor)}</span>
          <span class="facet-count">${formatNumber(item.total)}</span>
        </li>
      `;
    })
    .join("");

  ul.querySelectorAll(".facet-item").forEach((li) => {
    li.addEventListener("click", () => {
      const key = li.getAttribute("data-key");
      const val = li.getAttribute("data-val");
      state[key] = state[key] === val ? "" : val;
      state.page = 1;
      syncStateToUrl();
      executeSearch();
    });
  });
}

function populateUfSelect(ufs) {
  const sel = document.getElementById("filter-uf");
  if (!sel) return;
  const current = state.uf;
  const opts = [`<option value="">Todas as UFs</option>`];
  (ufs || []).forEach((u) => {
    const selected = u.valor === current ? "selected" : "";
    opts.push(`<option value="${escapeHtml(u.valor)}" ${selected}>OAB/${escapeHtml(u.valor)} (${formatNumber(u.total)})</option>`);
  });
  sel.innerHTML = opts.join("");
}

function renderActiveFilterTags() {
  const bar = document.getElementById("active-filters-bar");
  if (!bar) return;
  const tags = [];
  const labels = {
    contexto: "Contexto",
    orgao: "Órgão",
    dispositivo: "Dispositivo",
    sancao: "Sanção",
    resultado: "Resultado",
    uf: "UF Origem",
    relator: "Relator",
    ano_inicio: "Desde",
    ano_fim: "Até",
    numero_processo: "Processo",
    numero_ementa: "Ementa/Acórdão",
  };

  Object.entries(labels).forEach(([key, label]) => {
    if (state[key]) {
      tags.push(`
        <span class="active-filter-tag">
          <span>${label}: ${escapeHtml(state[key])}</span>
          <button type="button" data-clear-key="${key}" title="Remover filtro">&times;</button>
        </span>
      `);
    }
  });

  if (tags.length === 0) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }
  bar.hidden = false;
  bar.innerHTML = tags.join("");
  bar.querySelectorAll("button[data-clear-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const k = btn.getAttribute("data-clear-key");
      state[k] = "";
      if (k === "contexto") document.getElementById("filter-contexto").value = "";
      if (k === "uf") document.getElementById("filter-uf").value = "";
      if (k === "relator") document.getElementById("filter-relator").value = "";
      if (k === "ano_inicio") document.getElementById("filter-ano-inicio").value = "";
      if (k === "ano_fim") document.getElementById("filter-ano-fim").value = "";
      if (k === "numero_processo" || k === "numero_ementa") document.getElementById("filter-processo").value = "";
      state.page = 1;
      syncStateToUrl();
      executeSearch();
      fetchFacets();
    });
  });
}

function classifyInstancia(item) {
  const sigla = (item.sigla_orgao || "").toUpperCase();
  const orgao = (item.orgao_julgador_norm || "").toUpperCase();

  if (sigla.endsWith("TD") || sigla.endsWith("-TED") || sigla.endsWith("-TDEONT") || orgao.includes("TURMA") || orgao.includes("DEONTOLÓGICA") || orgao.includes("DEONTOLOGICA")) {
    return { tipo: "TED", label: "1ª Instância · TED" };
  }
  if (sigla.endsWith("CR") || orgao.includes("CÂMARA RECURSAL") || orgao.includes("CAMARA RECURSAL")) {
    return { tipo: "RECURSAL", label: "2ª Instância · Recursal" };
  }
  return null;
}

function buildCitationText(item) {
  const parts = [];
  const isCfoab = !item.id_organizacao || item.id_organizacao === 49;
  const seccionalUf = item.uf_origem || (item.id_organizacao === 25 ? "SP" : "Seccional");

  if (isCfoab) {
    parts.push(`CFOAB - ${item.orgao_julgador_norm || "Conselho Federal"}`);
  } else {
    parts.push(`OAB/${seccionalUf} - ${item.orgao_julgador_norm || "Tribunal de Ética e Disciplina"}`);
  }

  if (item.numero_ementa) parts.push(`Ementa/Acórdão n. ${item.numero_ementa}`);
  if (item.numero_processo) parts.push(`Proc. n. ${item.numero_processo}`);
  if (item.relator_principal) parts.push(`Rel. ${item.relator_principal}`);
  if (item.data_decisao) parts.push(`j. ${item.data_decisao}`);
  if (item.publicacao_str) parts.push(`pub. ${item.publicacao_str}`);

  return `${parts.join(", ")}.\n\n"${item.descricao_ementa || ""}"${
    item.descricao_acordao ? "\n\nACÓRDÃO: " + item.descricao_acordao : ""
  }`;
}

async function executeSearch() {
  const listEl = document.getElementById("results-list-container");
  const summaryEl = document.getElementById("results-summary-text");
  renderActiveFilterTags();

  summaryEl.innerHTML = `Buscando jurisprudências…`;
  const params = buildSearchParams();

  try {
    const resp = await fetch(`/api/search?${params.toString()}`);
    const data = await resp.json();

    // Contexto de escopo no resumo
    let escopoDesc = "em todo o acervo";
    if (state.esfera === "CFOAB") escopoDesc = "no Conselho Federal";
    else if (state.esfera === "SECCIONAIS") {
      escopoDesc = state.seccional ? `na Seccional OAB/${state.seccional}` : "nas Seccionais";
    }

    summaryEl.innerHTML = `Encontrados <strong>${formatNumber(data.total)}</strong> julgados ${escopoDesc} ${
      state.q ? `para <em>"${escapeHtml(state.q)}"</em>` : ""
    } (Página ${data.page} de ${Math.max(1, data.total_pages)})`;

    if (!data.items || data.items.length === 0) {
      listEl.innerHTML = `
        <div class="julgado-card" style="text-align:center;padding:2.5rem;">
          <h3 style="margin-top:0;">Nenhum julgado encontrado com estes critérios</h3>
          <p style="color:var(--text-secondary);">Experimente alternar o modo para "Qualquer termo", trocar de Seccional ou remover alguns filtros laterais.</p>
        </div>
      `;
      document.getElementById("pagination-bar").innerHTML = "";
      return;
    }

    listEl.innerHTML = data.items
      .map((item) => {
        const isCfoab = !item.id_organizacao || item.id_organizacao === 49;
        const seccionalUf = item.uf_origem || (item.id_organizacao === 25 ? "SP" : "");
        const instClass = classifyInstancia(item);

        const dispositivosHtml = (item.dispositivos || [])
          .map((d) => `<span class="disp-tag" data-disp="${escapeHtml(d)}">${escapeHtml(d)}</span>`)
          .join("");

        const acordaoHtml = item.descricao_acordao
          ? `<div class="acordao-box"><strong>Acórdão:</strong> ${escapeHtml(item.descricao_acordao)}</div>`
          : "";

        const snippetInteiroTeor =
          item.tem_inteiro_teor && item.inteiro_teor_snippet
            ? `<div class="inteiro-teor-preview">
                 <span class="inteiro-teor-preview-label">Trecho do Inteiro Teor (Relatório, Voto e Acórdão):</span>
                 ${sanitizeHighlightedHtml(item.inteiro_teor_snippet)}
               </div>`
            : "";

        const hasFullTextOrPdf = item.tem_inteiro_teor || item.pdf_url;

        // Badges do card
        const scopeBadgeHtml = isCfoab
          ? `<span class="badge badge-scope-cfoab">CFOAB</span>`
          : `<span class="badge badge-scope-seccional">OAB/${escapeHtml(seccionalUf || "Seccional")}</span>`;

        const instBadgeHtml = instClass
          ? `<span class="badge badge-instancia">${escapeHtml(instClass.label)}</span>`
          : "";

        return `
          <article class="julgado-card" data-id="${escapeHtml(item.id)}">
            <div class="card-top-bar">
              <div class="badges-row">
                ${scopeBadgeHtml}
                ${instBadgeHtml}
                <span class="badge badge-orgao-soft">${escapeHtml(item.orgao_julgador_norm || (isCfoab ? "Conselho Federal" : "Tribunal de Ética"))}</span>
                ${item.numero_ementa ? `<span class="badge badge-ementa-num">Acórdão/Ementa ${escapeHtml(item.numero_ementa)}</span>` : ""}
                ${isCfoab && item.uf_origem ? `<span class="badge badge-uf" title="Origem do recurso">Recurso OAB/${escapeHtml(item.uf_origem)}</span>` : ""}
                ${item.sancao_identificada ? `<span class="badge badge-sancao">${escapeHtml(item.sancao_identificada)}</span>` : ""}
                ${item.resultado_julgamento ? `<span class="badge badge-resultado">${escapeHtml(item.resultado_julgamento)}</span>` : ""}
                ${hasFullTextOrPdf ? `<span class="badge badge-inteiro-teor">PDF Inteiro Teor</span>` : ""}
              </div>
              <div class="card-date-proc">
                ${item.numero_processo ? `<span>Proc. ${escapeHtml(item.numero_processo)}</span>` : ""}
                ${item.data_decisao ? `<span>${escapeHtml(item.data_decisao)}</span>` : item.ano_ementa ? `<span>Ano ${item.ano_ementa}</span>` : ""}
              </div>
            </div>

            <div class="card-meta-grid">
              ${item.relator_principal ? `<span class="card-meta-item"><strong>Relator(a):</strong> ${escapeHtml(item.relator_principal)}</span>` : ""}
              ${item.publicacao_str ? `<span class="card-meta-item"><strong>Publicação:</strong> ${escapeHtml(item.publicacao_str)}</span>` : ""}
            </div>

            <div class="ementa-body">${sanitizeHighlightedHtml(item.ementa_highlighted || item.descricao_ementa)}</div>
            ${acordaoHtml}
            ${snippetInteiroTeor}
            ${dispositivosHtml ? `<div class="dispositivos-row">${dispositivosHtml}</div>` : ""}

            <div class="card-actions">
              <div class="action-buttons-left">
                <button type="button" class="btn-outline btn-open-detail ${hasFullTextOrPdf ? "btn-accent-green" : ""}" data-id="${escapeHtml(item.id)}">
                  ${hasFullTextOrPdf ? "Ler Inteiro Teor & Visualizar PDF" : "Ver Ficha Completa"}
                </button>
                ${
                  item.pdf_url
                    ? `<a href="${escapeHtml(item.pdf_url)}" target="_blank" rel="noopener" class="btn-outline">Abrir PDF Oficial</a>`
                    : ""
                }
                ${
                  item.diario_url
                    ? `<a href="${escapeHtml(item.diario_url)}" target="_blank" rel="noopener" class="btn-outline">Matéria no DEOAB</a>`
                    : ""
                }
              </div>
              <div class="action-buttons-right">
                <button type="button" class="btn-outline btn-copy-citation" data-id="${escapeHtml(item.id)}">
                  Copiar Citação Formatada
                </button>
              </div>
            </div>
          </article>
        `;
      })
      .join("");

    // Eventos nos elementos da listagem
    listEl.querySelectorAll(".disp-tag").forEach((tag) => {
      tag.addEventListener("click", () => {
        state.dispositivo = tag.getAttribute("data-disp");
        state.page = 1;
        syncStateToUrl();
        executeSearch();
        fetchFacets();
      });
    });

    listEl.querySelectorAll(".btn-copy-citation").forEach((btn, idx) => {
      btn.addEventListener("click", () => {
        const item = data.items[idx];
        navigator.clipboard.writeText(buildCitationText(item));
        showToast("Citação copiada para a área de transferência!");
      });
    });

    listEl.querySelectorAll(".btn-open-detail").forEach((btn) => {
      btn.addEventListener("click", () => openJulgadoModal(btn.getAttribute("data-id")));
    });

    renderPagination(data.page, data.total_pages);
  } catch (err) {
    console.error("Erro na pesquisa:", err);
    summaryEl.textContent = "Erro ao consultar a base de jurisprudência.";
  }
}

function renderPagination(page, totalPages) {
  const nav = document.getElementById("pagination-bar");
  if (!nav) return;
  if (totalPages <= 1) {
    nav.innerHTML = "";
    return;
  }
  nav.innerHTML = `
    <button type="button" class="btn-outline" id="page-prev-btn" ${page <= 1 ? "disabled" : ""}>&larr; Anterior</button>
    <span class="page-info">Página <strong>${page}</strong> de <strong>${formatNumber(totalPages)}</strong></span>
    <button type="button" class="btn-outline" id="page-next-btn" ${page >= totalPages ? "disabled" : ""}>Próxima &rarr;</button>
  `;
  const prev = document.getElementById("page-prev-btn");
  const next = document.getElementById("page-next-btn");
  if (prev && page > 1) {
    prev.addEventListener("click", () => {
      state.page -= 1;
      syncStateToUrl();
      executeSearch();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
  if (next && page < totalPages) {
    next.addEventListener("click", () => {
      state.page += 1;
      syncStateToUrl();
      executeSearch();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
}

// =========================================================================
// MODAL DE INTEIRO TEOR E PDF
// =========================================================================

async function openJulgadoModal(julgadoId) {
  const modal = document.getElementById("inteiro-teor-modal");
  const titleEl = document.getElementById("modal-title-text");
  const bodyEl = document.getElementById("modal-body-content");
  const linksEl = document.getElementById("modal-footer-links");

  modal.hidden = false;
  titleEl.textContent = "Carregando julgado e preparando PDF do Inteiro Teor...";
  bodyEl.innerHTML = `<p style="padding:1rem;color:var(--text-secondary);">Consultando base e localizando documento PDF oficial...</p>`;
  linksEl.innerHTML = "";

  try {
    const resp = await fetch(`/api/julgado/${encodeURIComponent(julgadoId)}?auto_extract_pdf=true`);
    const item = await resp.json();

    const isCfoab = !item.id_organizacao || item.id_organizacao === 49;
    const seccionalUf = item.uf_origem || (item.id_organizacao === 25 ? "SP" : "");
    const prefixo = isCfoab ? "CFOAB" : `OAB/${seccionalUf || "Seccional"}`;

    titleEl.textContent = `${prefixo} — ${item.orgao_julgador_norm || "Julgamento"} ${
      item.numero_ementa ? "(Acórdão/Ementa " + item.numero_ementa + ")" : ""
    }`;

    const fullText = item.inteiro_teor_texto || "";
    currentModalText = fullText || buildCitationText(item);

    const pdfEmbedHtml = item.pdf_url
      ? `
        <div style="margin-bottom:1.25rem;">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.45rem;">
            <h4 style="font-family:var(--font-sans);text-transform:uppercase;font-size:0.8rem;color:var(--accent-primary);margin:0;">
              Documento PDF Oficial do Inteiro Teor
            </h4>
            <a href="${escapeHtml(item.pdf_url)}" target="_blank" rel="noopener" class="btn-outline" style="padding:0.25rem 0.65rem;font-size:0.76rem;">
              Abrir PDF em Nova Guia &nearr;
            </a>
          </div>
          <iframe
            src="${escapeHtml(item.pdf_url)}"
            title="PDF Oficial do Inteiro Teor"
            class="pdf-frame"
          ></iframe>
        </div>
      `
      : "";

    bodyEl.innerHTML = `
      <div style="margin-bottom:1.25rem;font-family:var(--font-sans);font-size:0.88rem;color:var(--text-secondary);">
        ${item.numero_processo ? `<div><strong>Processo:</strong> ${escapeHtml(item.numero_processo)}</div>` : ""}
        ${item.relator_principal ? `<div><strong>Relator(a):</strong> ${escapeHtml(item.relator_principal)}</div>` : ""}
        ${item.uf_origem ? `<div><strong>${isCfoab ? "Seccional de Origem" : "Seccional"}:</strong> OAB/${escapeHtml(item.uf_origem)}</div>` : ""}
        ${item.publicacao_str ? `<div><strong>Publicação:</strong> ${escapeHtml(item.publicacao_str)}</div>` : ""}
      </div>
      ${pdfEmbedHtml}
      <h4 style="font-family:var(--font-sans);text-transform:uppercase;font-size:0.8rem;color:var(--text-muted);margin-bottom:0.4rem;">Ementa</h4>
      <div class="ementa-body" style="margin-bottom:1.25rem;">${escapeHtml(item.descricao_ementa)}</div>
      ${
        item.descricao_acordao
          ? `<div class="acordao-box" style="margin-bottom:1.25rem;"><strong>Acórdão:</strong> ${escapeHtml(item.descricao_acordao)}</div>`
          : ""
      }
      ${
        fullText
          ? `<h4 style="font-family:var(--font-sans);text-transform:uppercase;font-size:0.8rem;color:var(--accent-green);margin-bottom:0.4rem;">Texto Integral Extraído (Ementa, Relatório, Voto e Acórdão)</h4>
             <div class="inteiro-teor-fulltext">${escapeHtml(fullText)}</div>`
          : ""
      }
    `;

    const footerBtns = [];
    if (item.pdf_url) {
      footerBtns.push(`<a href="${escapeHtml(item.pdf_url)}" target="_blank" rel="noopener" class="btn-outline btn-accent-green">Baixar / Abrir PDF Oficial</a>`);
    }
    if (item.diario_url) {
      footerBtns.push(`<a href="${escapeHtml(item.diario_url)}" target="_blank" rel="noopener" class="btn-outline">Abrir no Diário Eletrônico (DEOAB)</a>`);
    }
    linksEl.innerHTML = footerBtns.join("");
  } catch (err) {
    bodyEl.innerHTML = `<p style="padding:1rem;color:var(--accent-danger);">Erro ao carregar detalhes do julgado.</p>`;
  }
}

// =========================================================================
// EVENT LISTENERS E INICIALIZAÇÃO
// =========================================================================

function setupEventListeners() {
  const form = document.getElementById("search-form");
  const qInput = document.getElementById("search-query-input");
  const clearBtn = document.getElementById("clear-query-btn");

  if (qInput) {
    qInput.addEventListener("input", () => {
      if (clearBtn) clearBtn.hidden = !qInput.value;
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      qInput.value = "";
      clearBtn.hidden = true;
      state.q = "";
      state.page = 1;
      syncStateToUrl();
      executeSearch();
      fetchFacets();
    });
  }

  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      state.q = qInput.value.trim();
      state.page = 1;
      syncStateToUrl();
      executeSearch();
      fetchFacets();
    });
  }

  // 1. Menu superior: botões de esfera
  const navTodas = document.getElementById("nav-todas");
  if (navTodas) {
    navTodas.addEventListener("click", () => {
      closeMegaMenu();
      setScope("TODAS", "", "");
    });
  }

  const navCfoab = document.getElementById("nav-cfoab");
  if (navCfoab) {
    navCfoab.addEventListener("click", () => {
      closeMegaMenu();
      setScope("CFOAB", "", "");
    });
  }

  const navSecc = document.getElementById("nav-seccionais");
  if (navSecc) {
    navSecc.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleMegaMenu();
    });
  }

  const megaAll = document.getElementById("mega-all-seccionais");
  if (megaAll) {
    megaAll.addEventListener("click", () => {
      closeMegaMenu();
      setScope("SECCIONAIS", "", "");
    });
  }

  // Fechar mega menu ao clicar fora ou pressionar Esc
  document.addEventListener("click", (e) => {
    const dropdown = document.querySelector(".nav-dropdown");
    if (dropdown && !dropdown.contains(e.target)) {
      closeMegaMenu();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeMegaMenu();
      const modal = document.getElementById("inteiro-teor-modal");
      if (modal && !modal.hidden) modal.hidden = true;
    }
  });

  // 2. Instância control (TED vs Recursal)
  const instControl = document.getElementById("instancia-control");
  if (instControl) {
    instControl.querySelectorAll(".seg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const inst = btn.getAttribute("data-instancia") || "";
        state.instancia = inst;
        state.page = 1;
        updateScopeUI();
        syncStateToUrl();
        executeSearch();
        fetchFacets();
      });
    });
  }

  // 3. Modos de busca (todos / frase / qualquer)
  document.querySelectorAll(".mode-group .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mode-group .seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.mode = btn.getAttribute("data-mode");
      state.page = 1;
      syncStateToUrl();
      executeSearch();
      fetchFacets();
    });
  });

  // 4. Checkboxes pill
  const bindToggle = (inputId, labelId, stateKey) => {
    const inp = document.getElementById(inputId);
    const lbl = document.getElementById(labelId);
    if (!inp || !lbl) return;
    inp.addEventListener("change", () => {
      state[stateKey] = inp.checked;
      lbl.classList.toggle("checked", inp.checked);
      state.page = 1;
      syncStateToUrl();
      executeSearch();
      fetchFacets();
    });
  };
  bindToggle("toggle-apenas-acordao", "label-toggle-acordao", "apenas_com_acordao");
  bindToggle("toggle-apenas-ted", "label-toggle-ted", "apenas_ted");
  bindToggle("toggle-apenas-inteiro-teor", "label-toggle-inteiro-teor", "apenas_inteiro_teor");

  // 5. Atalhos de teses
  document.querySelectorAll(".thesis-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const q = chip.getAttribute("data-query");
      if (qInput) qInput.value = q;
      if (clearBtn) clearBtn.hidden = false;
      state.q = q;
      state.page = 1;
      syncStateToUrl();
      executeSearch();
      fetchFacets();
    });
  });

  // 6. Filtros laterais
  const fSeccional = document.getElementById("filter-seccional");
  if (fSeccional) {
    fSeccional.addEventListener("change", (e) => {
      const uf = e.target.value;
      if (uf) {
        state.esfera = "SECCIONAIS";
        state.seccional = uf;
      } else {
        state.seccional = "";
      }
      state.page = 1;
      updateScopeUI();
      syncStateToUrl();
      executeSearch();
      fetchFacets();
    });
  }

  const fContexto = document.getElementById("filter-contexto");
  if (fContexto) {
    fContexto.addEventListener("change", (e) => {
      state.contexto = e.target.value;
      state.page = 1;
      syncStateToUrl();
      executeSearch();
    });
  }

  const fUf = document.getElementById("filter-uf");
  if (fUf) {
    fUf.addEventListener("change", (e) => {
      state.uf = e.target.value;
      state.page = 1;
      syncStateToUrl();
      executeSearch();
    });
  }

  const fSort = document.getElementById("sort-select");
  if (fSort) {
    fSort.addEventListener("change", (e) => {
      state.sort = e.target.value;
      state.page = 1;
      syncStateToUrl();
      executeSearch();
    });
  }

  let filterTimer = null;
  const bindDebouncedInput = (id, callback) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", (e) => {
      clearTimeout(filterTimer);
      filterTimer = setTimeout(() => {
        callback(e.target.value.trim());
        state.page = 1;
        syncStateToUrl();
        executeSearch();
      }, 380);
    });
  };

  bindDebouncedInput("filter-relator", (val) => {
    state.relator = val;
  });
  bindDebouncedInput("filter-ano-inicio", (val) => {
    state.ano_inicio = val;
  });
  bindDebouncedInput("filter-ano-fim", (val) => {
    state.ano_fim = val;
  });
  bindDebouncedInput("filter-processo", (val) => {
    if (val.includes("/")) {
      state.numero_ementa = val;
      state.numero_processo = "";
    } else {
      state.numero_processo = val;
      state.numero_ementa = "";
    }
  });

  // 7. Resetar todos os filtros
  const resetBtn = document.getElementById("reset-all-filters-btn");
  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      state.q = "";
      state.esfera = "TODAS";
      state.seccional = "";
      state.instancia = "";
      state.contexto = "";
      state.orgao = "";
      state.dispositivo = "";
      state.sancao = "";
      state.resultado = "";
      state.uf = "";
      state.relator = "";
      state.ano_inicio = "";
      state.ano_fim = "";
      state.numero_processo = "";
      state.numero_ementa = "";
      state.apenas_ted = false;
      state.apenas_com_acordao = true;
      state.apenas_inteiro_teor = false;
      state.page = 1;

      if (qInput) qInput.value = "";
      if (clearBtn) clearBtn.hidden = true;
      if (fContexto) fContexto.value = "";
      if (fUf) fUf.value = "";
      if (fSeccional) fSeccional.value = "";
      const fRel = document.getElementById("filter-relator");
      if (fRel) fRel.value = "";
      const fAnoIni = document.getElementById("filter-ano-inicio");
      if (fAnoIni) fAnoIni.value = "";
      const fAnoFim = document.getElementById("filter-ano-fim");
      if (fAnoFim) fAnoFim.value = "";
      const fProc = document.getElementById("filter-processo");
      if (fProc) fProc.value = "";

      const chkAc = document.getElementById("toggle-apenas-acordao");
      if (chkAc) chkAc.checked = true;
      const lblAc = document.getElementById("label-toggle-acordao");
      if (lblAc) lblAc.classList.add("checked");

      const chkTed = document.getElementById("toggle-apenas-ted");
      if (chkTed) chkTed.checked = false;
      const lblTed = document.getElementById("label-toggle-ted");
      if (lblTed) lblTed.classList.remove("checked");

      const chkInt = document.getElementById("toggle-apenas-inteiro-teor");
      if (chkInt) chkInt.checked = false;
      const lblInt = document.getElementById("label-toggle-inteiro-teor");
      if (lblInt) lblInt.classList.remove("checked");

      updateScopeUI();
      syncStateToUrl();
      executeSearch();
      fetchFacets();
    });
  }

  // 8. Modal close & copy
  const modal = document.getElementById("inteiro-teor-modal");
  const modalCloseBtn = document.getElementById("close-modal-btn");
  if (modalCloseBtn) {
    modalCloseBtn.addEventListener("click", () => {
      if (modal) modal.hidden = true;
    });
  }
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.hidden = true;
    });
  }
  const modalCopyBtn = document.getElementById("modal-copy-btn");
  if (modalCopyBtn) {
    modalCopyBtn.addEventListener("click", () => {
      if (currentModalText) {
        navigator.clipboard.writeText(currentModalText);
        showToast("Texto copiado para a área de transferência!");
      }
    });
  }
}

// =========================================================================
// BOOTSTRAP
// =========================================================================

document.addEventListener("DOMContentLoaded", async () => {
  restoreStateFromUrl();
  setupEventListeners();
  updateScopeUI();

  await Promise.all([
    loadHeaderStats(),
    loadSeccionais(),
  ]);

  fetchFacets();
  executeSearch();
});
