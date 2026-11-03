const state = { page: 1, totalPages: 1 };

const el = Object.fromEntries([
  "counts", "filter", "search", "pageSize", "summary", "gallery",
  "emptyState", "previous", "next", "pageNumbers", "message"
].map(id => [id, document.getElementById(id)]));

async function api(url) {
  const response = await fetch(url);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function resultValue(value, fallback = "—") {
  return value ? escapeHtml(value) : `<span class="card-empty">${fallback}</span>`;
}

function equalResult(left, right) {
  return String(left || "").trim().toUpperCase() === String(right || "").trim().toUpperCase();
}

function browseQuery(page = state.page) {
  return new URLSearchParams({
    status: el.filter.value,
    search: el.search.value,
    page: String(page),
    page_size: el.pageSize.value
  });
}

function annotationUrl(imageId) {
  const returnPath = `/browse?${browseQuery()}`;
  return `/?image=${imageId}&return=${encodeURIComponent(returnPath)}`;
}

function notify(text) {
  el.message.textContent = text;
  el.message.className = "message visible error";
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => { el.message.className = "message"; }, 4000);
}

function updateCounts(counts) {
  el.counts.textContent = `${counts.confirmed} confirmed · ${counts.llm_labeled} LLM labeled · ${counts.total} total`;
}

function card(item) {
  const confirmed = item.human_status === "confirmed";
  const hasLlm = Boolean(item.llm_state && item.llm_plate);
  const compared = confirmed && hasLlm;
  const stateMatches = compared && equalResult(item.llm_state, item.human_state);
  const plateMatches = compared && equalResult(item.llm_plate, item.human_plate);
  const correct = stateMatches && plateMatches;
  let comparisonClass = "pending";
  let comparisonText = "Not reviewed";
  if (!hasLlm) comparisonText = "No LLM";
  else if (compared && correct) {
    comparisonClass = "correct";
    comparisonText = "LLM correct";
  } else if (compared) {
    comparisonClass = "incorrect";
    comparisonText = "LLM incorrect";
  }
  const openUrl = annotationUrl(item.id);
  return `
    <article class="plate-card">
      <a href="${openUrl}" title="Open in annotation view">
        <img src="${item.image_url}?v=${encodeURIComponent(item.sha256 || "")}" loading="lazy" alt="License plate ${escapeHtml(item.filename)}">
      </a>
      <div class="card-body">
        <div class="card-file" title="${escapeHtml(item.relative_path)}">${escapeHtml(item.relative_path)}</div>
        <div class="card-results">
          <div class="card-result">
            <div class="card-label">LLM</div>
            <div class="card-state${compared && !stateMatches ? " field-different" : ""}">${resultValue(item.llm_state, "Not labeled")}</div>
            <div class="card-plate${compared && !plateMatches ? " field-different" : ""}">${resultValue(item.llm_plate)}</div>
          </div>
          <div class="card-result">
            <div class="card-label">Human</div>
            <div class="card-state">${resultValue(item.human_state, "Not reviewed")}</div>
            <div class="card-plate">${resultValue(item.human_plate)}</div>
          </div>
        </div>
        <div class="card-footer">
          <span class="comparison-badge ${comparisonClass}">${comparisonText}</span>
          <a class="muted" href="${openUrl}">Open</a>
        </div>
      </div>
    </article>`;
}

function renderPages() {
  const start = Math.max(1, state.page - 2);
  const end = Math.min(state.totalPages, start + 4);
  const adjustedStart = Math.max(1, end - 4);
  el.pageNumbers.innerHTML = "";
  for (let page = adjustedStart; page <= end; page += 1) {
    const button = document.createElement("button");
    button.textContent = page;
    button.className = `page-button${page === state.page ? " current" : ""}`;
    button.addEventListener("click", () => load(page));
    el.pageNumbers.appendChild(button);
  }
  el.previous.disabled = state.page <= 1;
  el.next.disabled = state.page >= state.totalPages;
}

async function load(page = 1) {
  const query = browseQuery(page);
  try {
    const data = await api(`/api/browse?${query}`);
    state.page = data.page;
    state.totalPages = data.total_pages;
    window.history.replaceState(null, "", `/browse?${browseQuery(data.page)}`);
    updateCounts(data.counts);
    el.gallery.innerHTML = data.items.map(card).join("");
    el.gallery.hidden = data.items.length === 0;
    el.emptyState.hidden = data.items.length !== 0;
    const first = data.total ? (data.page - 1) * data.page_size + 1 : 0;
    const last = Math.min(data.page * data.page_size, data.total);
    el.summary.textContent = `${first}–${last} of ${data.total}`;
    renderPages();
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    notify(error.message);
  }
}

el.previous.addEventListener("click", () => { if (state.page > 1) load(state.page - 1); });
el.next.addEventListener("click", () => { if (state.page < state.totalPages) load(state.page + 1); });
el.filter.addEventListener("change", () => load(1));
el.pageSize.addEventListener("change", () => load(1));
let searchTimer;
el.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => load(1), 250);
});

const initialParams = new URLSearchParams(window.location.search);
if (["all", "unlabeled", "unreviewed", "confirmed", "match", "disagreement", "unknown"].includes(initialParams.get("status"))) {
  el.filter.value = initialParams.get("status");
}
if (["24", "48", "96"].includes(initialParams.get("page_size"))) {
  el.pageSize.value = initialParams.get("page_size");
}
el.search.value = initialParams.get("search") || "";
const initialPage = Math.max(1, Number.parseInt(initialParams.get("page") || "1", 10) || 1);
load(initialPage);
