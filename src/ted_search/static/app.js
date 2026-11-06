/**
 * Frontend do Buscador de Jurisprudências do CFOAB / TED
 */

const state = {
  q: "",
  mode: "todos",
  apenas_cfoab: true,
  apenas_ted: false,
  apenas_inteiro_teor: false,
  contexto: "",
  orgao: "",
  dispositivo: "",
  sancao: "",
  resultado: "",
  uf: "",
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
  // Preserva apenas <mark> e </mark> gerados pelo SQLite FTS5
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
  toast.textContent = msg;
  toast.hidden = false;
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => {
    toast.hidden = true;
  }, 2800);
}

async function loadHeaderStats() {
  try {
    const resp = await fetch("/api/stats");
    const data = await resp.json();
    document.getElementById("stat-total-cfoab").textContent = formatNumber(data.total_cfoab);
    document.getElementById("stat-total-ted").textContent = formatNumber(data.total_ted_disciplinar);
    document.getElementById("stat-total-pdfs").textContent = formatNumber(data.total_com_pdf_url);
    document.getElementById("stat-total-orgaos").textContent = formatNumber(data.total_orgao_especifico_identificado);
  } catch (err) {
    console.error("Erro ao carregar estatísticas:", err);
  }
}

function buildSearchParams() {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  params.set("mode", state.mode);
  params.set("apenas_cfoab", String(state.apenas_cfoab));
  params.set("apenas_ted", String(state.apenas_ted));
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
    apenas_cfoab: String(state.apenas_cfoab),
    apenas_ted: String(state.apenas_ted),
    apenas_inteiro_teor: String(state.apenas_inteiro_teor),
  });
  try {
    const resp = await fetch(`/api/facets?${params.toString()}`);
    const facets = await resp.json();
    renderFacetList("facet-orgaos-list", facets.orgaos, "orgao");
    renderFacetList("facet-dispositivos-list", facets.dispositivos, "dispositivo");
    renderFacetList("facet-sancoes-list", facets.sancoes, "sancao");
    renderFacetList("facet-resultados-list", facets.resultados, "resultado");
    populateUfSelect(facets.ufs);
  } catch (err) {
    console.error("Erro ao carregar facetas:", err);
  }
}

function renderFacetList(containerId, items, stateKey) {
  const ul = document.getElementById(containerId);
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
      executeSearch();
    });
  });
}

function populateUfSelect(ufs) {
  const sel = document.getElementById("filter-uf");
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
  const tags = [];
  const labels = {
    contexto: "Contexto",
    orgao: "Órgão",
    dispositivo: "Dispositivo",
    sancao: "Sanção",
    resultado: "Resultado",
    uf: "UF",
    relator: "Relator",
    ano_inicio: "Desde",
    ano_fim: "Até",
    numero_processo: "Processo",
    numero_ementa: "Ementa",
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
      executeSearch();
      fetchFacets();
    });
  });
}

function buildCitationText(item) {
  const parts = [];
  parts.push(`CFOAB - ${item.orgao_julgador_norm || "Conselho Federal"}`);
  if (item.numero_ementa) parts.push(`Ementa n. ${item.numero_ementa}`);
  if (item.numero_processo) parts.push(`Proc. n. ${item.numero_processo}`);
  if (item.relator_principal) parts.push(`Rel. ${item.relator_principal}`);
  if (item.data_decisao) parts.push(`j. ${item.data_decisao}`);
  if (item.publicacao_str) parts.push(`pub. ${item.publicacao_str}`);
  return `${parts.join(", ")}.\n\n"${item.descricao_ementa || ""}"${item.descricao_acordao ? "\n\nACÓRDÃO: " + item.descricao_acordao : ""}`;
}

async function executeSearch() {
  const listEl = document.getElementById("results-list-container");
  const summaryEl = document.getElementById("results-summary-text");
  renderActiveFilterTags();

  summaryEl.innerHTML = `Buscando jurisprudências...`;
  const params = buildSearchParams();

  try {
    const resp = await fetch(`/api/search?${params.toString()}`);
    const data = await resp.json();

    summaryEl.innerHTML = `Encontrados <strong>${formatNumber(data.total)}</strong> julgados ${
      state.q ? `para <em>"${escapeHtml(state.q)}"</em>` : "no acervo"
    } (Página ${data.page} de ${Math.max(1, data.total_pages)})`;

    if (!data.items || data.items.length === 0) {
      listEl.innerHTML = `
        <div class="julgado-card" style="text-align:center;padding:2.5rem;">
          <h3 style="margin-top:0;">Nenhum julgado encontrado com estes critérios</h3>
          <p style="color:var(--text-secondary);">Experimente alternar o modo para "Qualquer termo" ou remover alguns filtros laterais.</p>
        </div>
      `;
      document.getElementById("pagination-bar").innerHTML = "";
      return;
    }

    listEl.innerHTML = data.items
      .map((item) => {
        const dispositivosHtml = (item.dispositivos || [])
          .map((d) => `<span class="disp-tag" data-disp="${escapeHtml(d)}">${escapeHtml(d)}</span>`)
          .join("");

        const acordaoHtml = item.descricao_acordao
          ? `<div class="acordao-box"><strong>Acórdão:</strong> ${escapeHtml(item.descricao_acordao)}</div>`
          : "";

        const snippetInteiroTeor =
          item.tem_inteiro_teor && item.inteiro_teor_snippet
            ? `<div class="inteiro-teor-preview">
                 <span class="inteiro-teor-preview-label">Trecho do Inteiro Teor (Relatório e Voto):</span>
                 ${sanitizeHighlightedHtml(item.inteiro_teor_snippet)}
               </div>`
            : "";

        const hasFullTextOrPdf = item.tem_inteiro_teor || item.pdf_url;

        return `
          <article class="julgado-card" data-id="${escapeHtml(item.id)}">
            <div class="card-top-bar">
              <div class="badges-row">
                <span class="badge badge-orgao">${escapeHtml(item.orgao_julgador_norm || "Conselho Federal")}</span>
                ${item.numero_ementa ? `<span class="badge badge-ementa-num">Ementa ${escapeHtml(item.numero_ementa)}</span>` : ""}
                ${item.uf_origem ? `<span class="badge badge-uf">OAB/${escapeHtml(item.uf_origem)}</span>` : ""}
                ${item.sancao_identificada ? `<span class="badge badge-sancao">${escapeHtml(item.sancao_identificada)}</span>` : ""}
                ${item.resultado_julgamento ? `<span class="badge badge-resultado">${escapeHtml(item.resultado_julgamento)}</span>` : ""}
                ${hasFullTextOrPdf ? `<span class="badge badge-inteiro-teor">Inteiro Teor / PDF</span>` : ""}
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
                  ${hasFullTextOrPdf ? "Ler Inteiro Teor (Relatório e Voto)" : "Ver Ficha Completa"}
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

    // Eventos dos cards
    listEl.querySelectorAll(".disp-tag").forEach((tag) => {
      tag.addEventListener("click", () => {
        state.dispositivo = tag.getAttribute("data-disp");
        state.page = 1;
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
    summaryEl.textContent = "Erro ao consultar a API de busca.";
  }
}

function renderPagination(page, totalPages) {
  const nav = document.getElementById("pagination-bar");
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
      executeSearch();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
  if (next && page < totalPages) {
    next.addEventListener("click", () => {
      state.page += 1;
      executeSearch();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
}

async function openJulgadoModal(julgadoId) {
  const modal = document.getElementById("inteiro-teor-modal");
  const titleEl = document.getElementById("modal-title-text");
  const bodyEl = document.getElementById("modal-body-content");
  const linksEl = document.getElementById("modal-footer-links");

  modal.hidden = false;
  titleEl.textContent = "Carregando julgado e extraindo inteiro teor...";
  bodyEl.innerHTML = `<p>Consultando base e processando documento PDF oficial...</p>`;
  linksEl.innerHTML = "";

  try {
    const resp = await fetch(`/api/julgado/${encodeURIComponent(julgadoId)}?auto_extract_pdf=true`);
    const item = await resp.json();

    titleEl.textContent = `${item.orgao_julgador_norm || "CFOAB"} — ${item.numero_ementa ? "Ementa " + item.numero_ementa : item.numero_processo || item.id}`;

    const fullText = item.inteiro_teor_texto || "";
    currentModalText = fullText || buildCitationText(item);

    bodyEl.innerHTML = `
      <div style="margin-bottom:1.25rem;font-family:var(--font-sans);font-size:0.88rem;color:var(--text-secondary);">
        ${item.numero_processo ? `<div><strong>Processo:</strong> ${escapeHtml(item.numero_processo)}</div>` : ""}
        ${item.relator_principal ? `<div><strong>Relator(a):</strong> ${escapeHtml(item.relator_principal)}</div>` : ""}
        ${item.uf_origem ? `<div><strong>Seccional de Origem:</strong> OAB/${escapeHtml(item.uf_origem)}</div>` : ""}
        ${item.publicacao_str ? `<div><strong>Publicação:</strong> ${escapeHtml(item.publicacao_str)}</div>` : ""}
      </div>
      <h4 style="font-family:var(--font-sans);text-transform:uppercase;font-size:0.8rem;color:var(--text-muted);margin-bottom:0.4rem;">Ementa</h4>
      <div class="ementa-body" style="margin-bottom:1.25rem;">${escapeHtml(item.descricao_ementa)}</div>
      ${
        item.descricao_acordao
          ? `<div class="acordao-box" style="margin-bottom:1.25rem;"><strong>Acórdão:</strong> ${escapeHtml(item.descricao_acordao)}</div>`
          : ""
      }
      ${
        fullText
          ? `<h4 style="font-family:var(--font-sans);text-transform:uppercase;font-size:0.8rem;color:var(--accent-green);margin-bottom:0.4rem;">Inteiro Teor Extraído do PDF (Relatório, Voto e Acórdão)</h4>
             <div class="inteiro-teor-fulltext">${escapeHtml(fullText)}</div>`
          : `<p style="font-family:var(--font-sans);font-size:0.86rem;color:var(--text-muted);">Este registro histórico possui apenas a Ementa e Acórdão publicados no Ementário Oficial.</p>`
      }
    `;

    const footerBtns = [];
    if (item.pdf_url) {
      footerBtns.push(`<a href="${escapeHtml(item.pdf_url)}" target="_blank" rel="noopener" class="btn-outline">Baixar PDF Original</a>`);
    }
    if (item.diario_url) {
      footerBtns.push(`<a href="${escapeHtml(item.diario_url)}" target="_blank" rel="noopener" class="btn-outline">Abrir no Diário Eletrônico (DEOAB)</a>`);
    }
    linksEl.innerHTML = footerBtns.join("");
  } catch (err) {
    bodyEl.innerHTML = `<p>Erro ao carregar detalhes do julgado.</p>`;
  }
}

function setupEventListeners() {
  const form = document.getElementById("search-form");
  const qInput = document.getElementById("search-query-input");
  const clearBtn = document.getElementById("clear-query-btn");

  qInput.addEventListener("input", () => {
    clearBtn.hidden = !qInput.value;
  });

  clearBtn.addEventListener("click", () => {
    qInput.value = "";
    clearBtn.hidden = true;
    state.q = "";
    state.page = 1;
    executeSearch();
    fetchFacets();
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    state.q = qInput.value.trim();
    state.page = 1;
    executeSearch();
    fetchFacets();
  });

  // Seletor de modo (todos / frase / qualquer)
  document.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.mode = btn.getAttribute("data-mode");
      state.page = 1;
      executeSearch();
      fetchFacets();
    });
  });

  // Checkboxes pill
  const bindToggle = (inputId, labelId, stateKey) => {
    const inp = document.getElementById(inputId);
    const lbl = document.getElementById(labelId);
    inp.addEventListener("change", () => {
      state[stateKey] = inp.checked;
      lbl.classList.toggle("checked", inp.checked);
      state.page = 1;
      executeSearch();
      fetchFacets();
    });
  };
  bindToggle("toggle-apenas-ted", "label-toggle-ted", "apenas_ted");
  bindToggle("toggle-apenas-inteiro-teor", "label-toggle-inteiro-teor", "apenas_inteiro_teor");
  bindToggle("toggle-apenas-cfoab", "label-toggle-cfoab", "apenas_cfoab");

  // Atalhos de teses
  document.querySelectorAll(".thesis-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const q = chip.getAttribute("data-query");
      qInput.value = q;
      clearBtn.hidden = false;
      state.q = q;
      state.page = 1;
      executeSearch();
      fetchFacets();
    });
  });

  // Filtros laterais de formulário
  document.getElementById("filter-contexto").addEventListener("change", (e) => {
    state.contexto = e.target.value;
    state.page = 1;
    executeSearch();
  });

  document.getElementById("filter-uf").addEventListener("change", (e) => {
    state.uf = e.target.value;
    state.page = 1;
    executeSearch();
  });

  document.getElementById("sort-select").addEventListener("change", (e) => {
    state.sort = e.target.value;
    state.page = 1;
    executeSearch();
  });

  let filterTimer = null;
  const bindDebouncedInput = (id, callback) => {
    document.getElementById(id).addEventListener("input", (e) => {
      clearTimeout(filterTimer);
      filterTimer = setTimeout(() => {
        callback(e.target.value.trim());
        state.page = 1;
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

  // Resetar todos os filtros
  document.getElementById("reset-all-filters-btn").addEventListener("click", () => {
    state.q = "";
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
    state.apenas_inteiro_teor = false;
    state.page = 1;

    qInput.value = "";
    clearBtn.hidden = true;
    document.getElementById("filter-contexto").value = "";
    document.getElementById("filter-uf").value = "";
    document.getElementById("filter-relator").value = "";
    document.getElementById("filter-ano-inicio").value = "";
    document.getElementById("filter-ano-fim").value = "";
    document.getElementById("filter-processo").value = "";
    document.getElementById("toggle-apenas-ted").checked = false;
    document.getElementById("label-toggle-ted").classList.remove("checked");
    document.getElementById("toggle-apenas-inteiro-teor").checked = false;
    document.getElementById("label-toggle-inteiro-teor").classList.remove("checked");

    executeSearch();
    fetchFacets();
  });

  // Modal close
  const modal = document.getElementById("inteiro-teor-modal");
  document.getElementById("close-modal-btn").addEventListener("click", () => {
    modal.hidden = true;
  });
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.hidden = true;
  });
  document.getElementById("modal-copy-btn").addEventListener("click", () => {
    if (currentModalText) {
      navigator.clipboard.writeText(currentModalText);
      showToast("Texto copiado para a área de transferência!");
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupEventListeners();
  loadHeaderStats();
  fetchFacets();
  executeSearch();
});
