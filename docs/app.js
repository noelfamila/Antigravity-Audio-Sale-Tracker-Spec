/**
 * Audio Sale Tracker Frontend Logic
 * - APIキーを一切含めず、GitHub Actionsが更新する静的JSON (./data/sales.json) を fetch() で読み込む
 * - カテゴリ絞り込み・価格/割引率ソート・フリーワード検索・NEWバッジ（3日以内）表示
 */

const CATEGORY_LABELS = {
  wired_iem: "有線イヤホン (5万円〜)",
  wired_headphone: "有線ヘッドホン (5万円〜)",
  desktop_dac_amp: "据え置きDAC/AMP (5万円〜)",
  portable_dac_amp: "ポータブルDAC/AMP (5万円〜)",
  earpiece: "イヤーピース (全価格帯)",
};

const state = {
  items: [],
  referenceDateMs: Date.now(),
  selectedCategory: "all",
  searchQuery: "",
  sortBy: "newest",
};

function formatJpy(amount) {
  if (typeof amount !== "number" || Number.isNaN(amount) || amount <= 0) {
    return null;
  }
  return new Intl.NumberFormat("ja-JP", {
    style: "currency",
    currency: "JPY",
    maximumFractionDigits: 0,
  }).format(amount);
}

function formatUpdatedAt(isoString) {
  if (!isoString) return "未取得";
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return isoString;
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(d);
}

/**
 * 割引率テキスト（例: "26%OFF", "10,000円OFF"）や価格差から比較用スコアを算出
 */
function computeDiscountScore(item) {
  const rateText = String(item.discount_rate || "");
  const percentMatch = rateText.match(/(\d+(?:\.\d+)?)\s*%/);
  if (percentMatch) {
    return parseFloat(percentMatch[1]);
  }
  if (
    typeof item.regular_price_jpy === "number" &&
    typeof item.sale_price_jpy === "number" &&
    item.regular_price_jpy > item.sale_price_jpy &&
    item.regular_price_jpy > 0
  ) {
    return ((item.regular_price_jpy - item.sale_price_jpy) / item.regular_price_jpy) * 100;
  }
  const yenMatch = rateText.replace(/,/g, "").match(/(\d+)\s*円/);
  if (yenMatch && typeof item.regular_price_jpy === "number" && item.regular_price_jpy > 0) {
    return (parseFloat(yenMatch[1]) / item.regular_price_jpy) * 100;
  }
  return 0;
}

function getEffectivePrice(item) {
  if (typeof item.sale_price_jpy === "number" && item.sale_price_jpy > 0) {
    return item.sale_price_jpy;
  }
  if (typeof item.regular_price_jpy === "number" && item.regular_price_jpy > 0) {
    return item.regular_price_jpy;
  }
  return 0;
}

/**
 * 直近3日以内に検出されたアイテムかどうかを判定
 */
function isNewItem(detectedDateStr, referenceMs) {
  if (!detectedDateStr) return false;
  const detectedMs = Date.parse(`${detectedDateStr}T00:00:00+09:00`);
  if (Number.isNaN(detectedMs)) return false;
  const diffDays = (referenceMs - detectedMs) / (1000 * 60 * 60 * 24);
  return diffDays >= -1 && diffDays <= 3.5;
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeUrl(url) {
  try {
    const parsed = new URL(String(url), window.location.href);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
  } catch (_) {
    // ignore invalid URL
  }
  return "#";
}

function updateCategoryCounts() {
  const counts = {
    all: state.items.length,
    wired_iem: 0,
    wired_headphone: 0,
    desktop_dac_amp: 0,
    portable_dac_amp: 0,
    earpiece: 0,
  };

  for (const item of state.items) {
    if (counts[item.category] !== undefined) {
      counts[item.category] += 1;
    }
  }

  document.querySelectorAll("[data-count-for]").forEach((el) => {
    const key = el.getAttribute("data-count-for");
    if (key && counts[key] !== undefined) {
      el.textContent = String(counts[key]);
    }
  });
}

function getFilteredAndSortedItems() {
  const q = state.searchQuery.trim().toLowerCase();

  const filtered = state.items.filter((item) => {
    if (state.selectedCategory !== "all" && item.category !== state.selectedCategory) {
      return false;
    }
    if (!q) return true;
    const haystack = [
      item.brand,
      item.product_name,
      item.specs_summary,
      item.sale_name,
      item.discount_rate,
      CATEGORY_LABELS[item.category] || "",
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(q);
  });

  filtered.sort((a, b) => {
    if (state.sortBy === "discount_desc") {
      return computeDiscountScore(b) - computeDiscountScore(a);
    }
    if (state.sortBy === "price_desc") {
      return getEffectivePrice(b) - getEffectivePrice(a);
    }
    if (state.sortBy === "price_asc") {
      return getEffectivePrice(a) - getEffectivePrice(b);
    }
    // default: newest
    const dateCmp = String(b.detected_date || "").localeCompare(String(a.detected_date || ""));
    if (dateCmp !== 0) return dateCmp;
    return computeDiscountScore(b) - computeDiscountScore(a);
  });

  return filtered;
}

function renderCard(item) {
  const categoryLabel = CATEGORY_LABELS[item.category] || item.category;
  const regularFormatted = formatJpy(item.regular_price_jpy);
  const saleFormatted = formatJpy(item.sale_price_jpy);
  const showNewBadge = isNewItem(item.detected_date, state.referenceDateMs);

  let priceHtml = "";
  if (saleFormatted && regularFormatted && item.regular_price_jpy > item.sale_price_jpy) {
    priceHtml = `
      <span class="regular-price">参考: ${escapeHtml(regularFormatted)}</span>
      <span class="sale-price">${escapeHtml(saleFormatted)}</span>
    `;
  } else if (saleFormatted) {
    priceHtml = `<span class="sale-price">${escapeHtml(saleFormatted)}</span>`;
  } else if (regularFormatted) {
    priceHtml = `<span class="sale-price">通常: ${escapeHtml(regularFormatted)}</span>`;
  } else {
    priceHtml = `<span class="sale-price">価格詳細はリンク先参照</span>`;
  }

  return `
    <article class="product-card" data-id="${escapeHtml(item.id)}">
      <div class="card-top">
        <div class="badges-row">
          <div class="badges-left">
            ${showNewBadge ? '<span class="new-badge">NEW</span>' : ""}
            <span class="category-badge category-${escapeHtml(item.category)}">
              ${escapeHtml(categoryLabel)}
            </span>
          </div>
          <time class="detected-date" datetime="${escapeHtml(item.detected_date)}">
            検出日: ${escapeHtml(item.detected_date)}
          </time>
        </div>

        <h2 class="product-heading">
          <span class="product-brand">${escapeHtml(item.brand)}</span>
          <span class="product-name">${escapeHtml(item.product_name)}</span>
        </h2>

        <p class="specs-summary">${escapeHtml(item.specs_summary)}</p>
      </div>

      <div class="card-bottom">
        <div class="sale-name-tag">${escapeHtml(item.sale_name || "Amazon セール情報")}</div>

        <div class="price-block">
          <div class="prices-left">
            ${priceHtml}
          </div>
          <span class="discount-badge">${escapeHtml(item.discount_rate || "セール特価")}</span>
        </div>

        <div class="card-actions">
          <a
            class="action-btn btn-primary"
            href="${safeUrl(item.product_url)}"
            target="_blank"
            rel="noopener noreferrer"
          >
            製品ページ / Amazonを見る ↗
          </a>
          <a
            class="action-btn btn-secondary"
            href="${safeUrl(item.source_url)}"
            target="_blank"
            rel="noopener noreferrer"
          >
            情報元記事を見る ↗
          </a>
        </div>
      </div>
    </article>
  `;
}

function render() {
  const gridEl = document.getElementById("cards-grid");
  const emptyEl = document.getElementById("empty-state");
  const summaryEl = document.getElementById("result-summary");

  const items = getFilteredAndSortedItems();
  const categoryName =
    state.selectedCategory === "all"
      ? "全カテゴリ"
      : CATEGORY_LABELS[state.selectedCategory] || state.selectedCategory;

  summaryEl.textContent = `${categoryName}：全 ${state.items.length} 件中 ${items.length} 件を表示中`;

  if (items.length === 0) {
    gridEl.innerHTML = "";
    emptyEl.classList.remove("hidden");
    return;
  }

  emptyEl.classList.add("hidden");
  gridEl.innerHTML = items.map(renderCard).join("");
}

function bindEvents() {
  const tabsContainer = document.getElementById("category-tabs");
  const searchInput = document.getElementById("search-input");
  const sortSelect = document.getElementById("sort-select");
  const resetBtn = document.getElementById("reset-filters-btn");

  tabsContainer.addEventListener("click", (event) => {
    const btn = event.target.closest(".category-tab");
    if (!btn) return;
    const category = btn.getAttribute("data-category") || "all";
    state.selectedCategory = category;

    tabsContainer.querySelectorAll(".category-tab").forEach((tab) => {
      const isSelected = tab === btn;
      tab.classList.toggle("active", isSelected);
      tab.setAttribute("aria-selected", isSelected ? "true" : "false");
    });

    render();
  });

  searchInput.addEventListener("input", (event) => {
    state.searchQuery = event.target.value || "";
    render();
  });

  sortSelect.addEventListener("change", (event) => {
    state.sortBy = event.target.value || "newest";
    render();
  });

  resetBtn.addEventListener("click", () => {
    state.selectedCategory = "all";
    state.searchQuery = "";
    state.sortBy = "newest";
    searchInput.value = "";
    sortSelect.value = "newest";
    tabsContainer.querySelectorAll(".category-tab").forEach((tab) => {
      const isAll = tab.getAttribute("data-category") === "all";
      tab.classList.toggle("active", isAll);
      tab.setAttribute("aria-selected", isAll ? "true" : "false");
    });
    render();
  });
}

async function init() {
  bindEvents();

  const updatedAtEl = document.getElementById("updated-at");
  const bannerEl = document.getElementById("sale-banner");
  const bannerTextEl = document.getElementById("sale-banner-text");
  const bannerLinkEl = document.getElementById("sale-banner-link");
  const summaryEl = document.getElementById("result-summary");

  try {
    const response = await fetch("./data/sales.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();

    updatedAtEl.textContent = formatUpdatedAt(data.updated_at);
    if (data.updated_at) {
      const parsedRef = Date.parse(data.updated_at);
      if (!Number.isNaN(parsedRef)) {
        state.referenceDateMs = Math.max(Date.now(), parsedRef);
      }
    }

    if (data.upcoming_sale_notice) {
      bannerTextEl.textContent = data.upcoming_sale_notice;
      if (data.upcoming_sale_url) {
        bannerLinkEl.href = safeUrl(data.upcoming_sale_url);
        bannerLinkEl.classList.remove("hidden");
      } else {
        bannerLinkEl.classList.add("hidden");
      }
      bannerEl.classList.remove("hidden");
    } else {
      bannerEl.classList.add("hidden");
    }

    state.items = Array.isArray(data.items) ? data.items : [];
    updateCategoryCounts();
    render();
  } catch (err) {
    console.error("Failed to load ./data/sales.json:", err);
    updatedAtEl.textContent = "読み込みエラー";
    summaryEl.textContent =
      "データの読み込みに失敗しました。ローカルで確認する場合は python -m http.server 等で配信してください。";
  }
}

document.addEventListener("DOMContentLoaded", init);

