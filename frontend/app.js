/**
 * app.js — Finance-AI Frontend Application Controller
 * ─────────────────────────────────────────────────────────────
 * Vanilla JS SPA controller. No build step required.
 * Single source of truth: App.state object.
 */

const App = (() => {

  // ── State ────────────────────────────────────────────────────
  const state = {
    user: null,
    isGuest: false,
    accounts: [],
    currentPage: "overview",
    currentPeriod: "monthly",
    charts: {},
    txPage: 1,
  };

  // ── Init ─────────────────────────────────────────────────────
  function init() {
    _bindAuthEvents();
    _bindNavEvents();
    _bindUploadEvents();

    const token = api.getToken();
    if (token) {
      api.getMe()
        .then(user => { state.user = user; showDashboard(); })
        .catch(() => { api.setToken(null); showAuth(); });
    } else {
      showAuth();
    }
  }

  // ── Auth ─────────────────────────────────────────────────────
  function _bindAuthEvents() {
    // Tab switching
    document.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const tab = btn.dataset.tab;
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
        btn.classList.add("active");
        _el(`tab-${tab}`).classList.add("active");
      });
    });

    // Login
    _el("btn-login").addEventListener("click", async () => {
      const phone = _el("login-phone").value.trim();
      const pin   = _el("login-pin").value.trim();
      _clearError("login-error");
      try {
        const resp = await api.login(phone, pin);
        api.setToken(resp.access_token);
        state.isGuest = false;
        state.user = await api.getMe();
        showDashboard();
        toast("Welcome back!", "success");
      } catch (e) { _showError("login-error", e.message); }
    });

    // Register
    _el("btn-register").addEventListener("click", async () => {
      const phone = _el("reg-phone").value.trim();
      const name  = _el("reg-name").value.trim();
      const pin   = _el("reg-pin").value.trim();
      const conf  = _el("reg-pin-confirm").value.trim();
      _clearError("reg-error");
      if (pin !== conf) { _showError("reg-error", "PINs do not match."); return; }
      try {
        const resp = await api.register(phone, pin, name || null);
        api.setToken(resp.access_token);
        state.isGuest = false;
        state.user = await api.getMe();
        showDashboard();
        toast("Account created!", "success");
      } catch (e) { _showError("reg-error", e.message); }
    });

    // Guest
    _el("btn-guest").addEventListener("click", async () => {
      try {
        const resp = await api.guestSession();
        api.setToken(resp.access_token);
        state.isGuest = true;
        state.user = { display_name: "Guest", id: null };
        showDashboard();
        toast("Guest session started. Data wipes on logout.", "info");
      } catch (e) { toast("Could not start guest session.", "error"); }
    });

    // Logout
    _el("btn-logout").addEventListener("click", async () => {
      await api.logout().catch(() => {});
      state.user = null;
      state.isGuest = false;
      state.accounts = [];
      Object.values(state.charts).forEach(c => c?.destroy());
      state.charts = {};
      showAuth();
      toast("Logged out.", "info");
    });
  }

  // ── Navigation ─────────────────────────────────────────────
  function _bindNavEvents() {
    document.querySelectorAll(".nav-link").forEach(link => {
      link.addEventListener("click", e => {
        e.preventDefault();
        navigateTo(link.dataset.page);
      });
    });

    _el("period-selector").addEventListener("change", e => {
      state.currentPeriod = e.target.value;
      if (state.currentPage === "overview") _loadOverview();
    });

    _el("btn-upload-quick").addEventListener("click", () => navigateTo("upload"));
  }

  function navigateTo(page) {
    state.currentPage = page;

    document.querySelectorAll(".nav-link").forEach(l => l.classList.remove("active"));
    document.querySelectorAll(`[data-page="${page}"]`).forEach(l => l.classList.add("active"));
    document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
    _el(`page-${page}`)?.classList.add("active");

    const titles = {
      overview: "Overview", transactions: "Transactions",
      accounts: "Accounts", upload: "Upload", insights: "Insights"
    };
    _el("page-title").textContent = titles[page] || page;
    _el("breadcrumb").textContent = `Dashboard / ${titles[page] || page}`;

    const loaders = {
      overview: _loadOverview,
      transactions: _loadTransactions,
      accounts: _loadAccounts,
      upload: _loadUploadHistory,
      insights: _loadInsights,
    };
    loaders[page]?.();
  }

  // ── Screen Switching ──────────────────────────────────────
  function showAuth() {
    _el("auth-screen").classList.add("active");
    _el("dashboard-screen").classList.remove("active");
  }

  function showDashboard() {
    _el("auth-screen").classList.remove("active");
    _el("dashboard-screen").classList.add("active");
    _el("user-name").textContent = state.user?.display_name || "User";
    _el("user-mode").textContent = state.isGuest ? "Guest Session" : "Registered";
    _loadAccounts().then(() => navigateTo("overview"));

    // Show onboarding guide for new users (unless dismissed)
    const dismissed = localStorage.getItem("finance_onboarding_dismissed");
    if (!dismissed) {
      _showOnboarding();
    }
  }

  // ── Overview ─────────────────────────────────────────────
  async function _loadOverview() {
    try {
      const data = await api.getDashboardSummary(state.currentPeriod);
      _renderKPIs(data.summary);
      _renderAccountBalances(data.account_balances);
      _renderCategoryChart(data.top_categories);
    } catch (e) {
      toast("Failed to load dashboard: " + e.message, "error");
    }
    try {
      const trend = await api.getSpendingTrend("monthly", 6);
      _renderTrendChart(trend);
    } catch (e) { /* silent */ }
  }

  function _renderKPIs(s) {
    const fmt = n => "₹" + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
    _el("kpi-income").textContent   = fmt(s.total_income);
    _el("kpi-expenses").textContent = fmt(s.total_expenses);
    _el("kpi-savings").textContent  = fmt(s.net_savings);
    _el("kpi-savings-rate").textContent = `${s.savings_rate}% savings rate`;
    _el("kpi-health").textContent   = s.financial_health_score;
    const score = s.financial_health_score;
    _el("kpi-health").style.color = score >= 70
      ? "var(--success)" : score >= 40 ? "var(--warning)" : "var(--danger)";
  }

  function _renderAccountBalances(balances) {
    const c = _el("account-balances-list");
    if (!balances?.length) {
      c.innerHTML = `<div class="empty-state">No accounts yet.</div>`; return;
    }
    c.innerHTML = balances.map(a => `
      <div class="account-item">
        <div>
          <div class="account-name">${_esc(a.nickname)}</div>
          <div class="account-type">${_esc(a.account_type.replace("_", " "))}</div>
        </div>
        <div class="account-balance">
          ${a.currency} ${Number(a.current_balance).toLocaleString("en-IN")}
        </div>
      </div>
    `).join("");
  }

  function _renderCategoryChart(categories) {
    const ctx = document.getElementById("chart-category")?.getContext("2d");
    if (!ctx || !categories?.length) return;
    state.charts.category?.destroy();
    const COLORS = ["#00e676","#40c4ff","#ffab40","#ff5252","#e040fb","#80cbc4","#ffcc02","#69f0ae"];
    state.charts.category = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: categories.map(c => c.category),
        datasets: [{
          data: categories.map(c => c.total_amount),
          backgroundColor: COLORS.slice(0, categories.length),
          borderWidth: 0,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          legend: { position: "right", labels: { color: "#80cbc4", font: { size: 11 }, padding: 8 } },
        },
        cutout: "65%",
      }
    });
  }

  function _renderTrendChart(trend) {
    const ctx = document.getElementById("chart-trend")?.getContext("2d");
    if (!ctx || !trend?.length) return;
    state.charts.trend?.destroy();
    state.charts.trend = new Chart(ctx, {
      type: "bar",
      data: {
        labels: trend.map(t => t.period_label),
        datasets: [
          {
            label: "Income",
            data: trend.map(t => t.income),
            backgroundColor: "rgba(0,230,118,0.6)",
            borderColor: "#00e676", borderWidth: 1, borderRadius: 2,
          },
          {
            label: "Expenses",
            data: trend.map(t => t.expenses),
            backgroundColor: "rgba(255,82,82,0.5)",
            borderColor: "#ff5252", borderWidth: 1, borderRadius: 2,
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { grid: { color: "rgba(0,230,118,0.05)" }, ticks: { color: "#4db6ac", font: { size: 10 } } },
          y: { grid: { color: "rgba(0,230,118,0.05)" }, ticks: { color: "#4db6ac", font: { size: 10 },
            callback: v => "₹" + (v / 1000).toFixed(0) + "k" } }
        },
        plugins: { legend: { labels: { color: "#80cbc4", font: { size: 11 } } } },
      }
    });
  }

  // ── Accounts ─────────────────────────────────────────────
  async function _loadAccounts() {
    try {
      state.accounts = (await api.listAccounts()) || [];
      _renderAccountsGrid();
      _populateAccountSelects();
    } catch (e) { /* not logged in yet */ }
  }

  function _renderAccountsGrid() {
    const grid = _el("accounts-grid");
    if (!grid) return;
    if (!state.accounts.length) {
      grid.innerHTML = `<div class="empty-state">No accounts yet.<br>
        <button class="btn btn-primary btn-sm" style="margin-top:12px"
          onclick="App.showAddAccountModal()">+ Add Account</button></div>`;
      return;
    }
    const icons = { savings:"◆", credit_card:"◈", wallet:"◇", upi:"⬡", cash:"◉" };
    grid.innerHTML = state.accounts.map(a => `
      <div class="account-item" style="padding:16px">
        <div>
          <div class="account-name">${icons[a.account_type] || "◇"} ${_esc(a.nickname)}</div>
          <div class="account-type">${_esc(a.bank_name || "")} · ${a.account_type.replace("_"," ")}</div>
          ${a.last_four_digits ? `<div class="account-type">**** ${a.last_four_digits}</div>` : ""}
        </div>
        <div style="text-align:right">
          <div class="account-balance">${a.currency} ${Number(a.current_balance).toLocaleString("en-IN")}</div>
          ${a.credit_utilization !== null && a.credit_utilization !== undefined
            ? `<div class="account-type">Utilization: ${a.credit_utilization}%</div>` : ""}
        </div>
      </div>
    `).join("");
  }

  function _populateAccountSelects() {
    const sel = _el("upload-account-select");
    if (!sel) return;
    sel.innerHTML = `<option value="">— Choose account —</option>` +
      state.accounts.map(a =>
        `<option value="${a.id}">${_esc(a.nickname)}</option>`
      ).join("");
  }

  // ── Transactions ─────────────────────────────────────────
  async function _loadTransactions() {
    try {
      const data = await api.listTransactions({ page: state.txPage, page_size: 50 });
      _renderTransactionsTable(data);
    } catch (e) { toast("Failed to load transactions.", "error"); }
  }

  function _renderTransactionsTable(data) {
    const tbody = _el("transactions-tbody");
    if (!tbody) return;
    if (!data?.items?.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-state">
        No transactions. Upload a bank statement to get started.</td></tr>`;
      return;
    }
    tbody.innerHTML = data.items.map(t => `
      <tr>
        <td>${t.date}</td>
        <td title="${_esc(t.description || "")}">${_esc((t.description || "").slice(0, 40))}</td>
        <td><span class="badge" style="background:rgba(0,230,118,0.08);
          color:var(--text-muted);border:1px solid rgba(0,230,118,0.15)">
          ${_esc(t.category || "Other")}</span></td>
        <td>${_esc(_accountName(t.account_id))}</td>
        <td class="text-right">
          <span class="amount-${t.type}">₹${Number(t.amount).toLocaleString("en-IN")}</span>
        </td>
        <td><span class="badge badge-${t.type}">${t.type}</span></td>
      </tr>
    `).join("");

    const pag = _el("tx-pagination");
    if (pag && data.total_pages > 1) {
      pag.innerHTML = `
        <span style="color:var(--text-dim);font-size:12px">
          Page ${data.page} of ${data.total_pages} · ${data.total} records
        </span>
        <button class="btn btn-ghost btn-sm" ${data.page <= 1 ? "disabled" : ""}
          onclick="App.txPrev()">← Prev</button>
        <button class="btn btn-ghost btn-sm" ${data.page >= data.total_pages ? "disabled" : ""}
          onclick="App.txNext(${data.total_pages})">Next →</button>
      `;
    }
  }

  function _accountName(id) {
    return state.accounts.find(a => a.id === id)?.nickname || `#${id}`;
  }

  // ── Upload ────────────────────────────────────────────────
  function _bindUploadEvents() {
    const fileInput = _el("file-input");
    fileInput?.addEventListener("change", e => {
      if (e.target.files[0]) _handleFile(e.target.files[0]);
    });
    const dz = _el("drop-zone");
    dz?.addEventListener("dragenter", () => dz.classList.add("drag-over"));
    dz?.addEventListener("dragleave", () => dz.classList.remove("drag-over"));
  }

  function handleDrop(e) {
    e.preventDefault();
    _el("drop-zone").classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file) _handleFile(file);
  }

  async function _handleFile(file) {
    const accountId = _el("upload-account-select")?.value;
    if (!accountId) { toast("Please select an account first.", "error"); return; }

    const progress = _el("upload-progress");
    const result   = _el("upload-result");
    progress.classList.remove("hidden");
    result.classList.add("hidden");
    result.className = "hidden";

    _el("progress-fill").style.width = "30%";
    _el("progress-label").textContent = `Uploading ${file.name}...`;

    try {
      _el("progress-fill").style.width = "60%";
      const data = await api.uploadFile(file, parseInt(accountId));
      _el("progress-fill").style.width = "100%";
      _el("progress-label").textContent = "Complete!";
      setTimeout(() => progress.classList.add("hidden"), 1000);

      result.className = "upload-result-success";
      result.innerHTML = `
        <strong>✓ Upload complete</strong><br>
        Parsed: ${data.records_parsed} ·
        Inserted: ${data.records_inserted} ·
        Duplicates: ${data.records_duplicate} ·
        Failed: ${data.records_failed}<br>
        <span style="font-size:11px;color:var(--text-dim)">
          Processed in ${data.processing_time_ms}ms
        </span>
      `;
      toast(`Inserted ${data.records_inserted} transactions.`, "success");
      _loadUploadHistory();
      _loadOverview();
    } catch (e) {
      progress.classList.add("hidden");
      result.className = "upload-result-error";
      result.innerHTML = `<strong>✗ Upload failed</strong><br>${_esc(e.message)}`;
      toast("Upload failed: " + e.message, "error");
    }
  }

  async function _loadUploadHistory() {
    try {
      const logs = await api.getUploadLogs();
      const c = _el("upload-history");
      if (!logs?.length) { c.innerHTML = `<div class="empty-state">No uploads yet.</div>`; return; }
      c.innerHTML = logs.slice(0, 10).map(l => `
        <div class="account-item" style="padding:10px 16px">
          <div>
            <div class="account-name">${_esc(l.file_name)}</div>
            <div class="account-type">
              ${new Date(l.upload_date).toLocaleDateString("en-IN")} · ${l.file_type.toUpperCase()}
            </div>
          </div>
          <div style="text-align:right">
            <span class="badge ${l.status === "completed" ? "badge-credit" : "badge-debit"}">
              ${l.status}
            </span>
            <div class="account-type">
              ${l.records_inserted} inserted · ${l.records_duplicate} dupes
            </div>
          </div>
        </div>
      `).join("");
    } catch (e) { /* silent */ }
  }

  // ── Insights ─────────────────────────────────────────────
  async function _loadInsights() {
    try {
      const items = await api.listInsights();
      const list = _el("insights-list");
      if (!items?.length) {
        list.innerHTML = `<div class="empty-state">
          No insights yet. Upload transactions to generate insights.</div>`;
        return;
      }
      list.innerHTML = items.map(i => `
        <div class="insight-card ${i.severity}">
          <div class="insight-title">${_esc(i.title)}</div>
          <div class="insight-body">${_esc(i.body)}</div>
          <div class="insight-meta">
            ${i.insight_type} · ${new Date(i.generated_at).toLocaleDateString("en-IN")}
          </div>
        </div>
      `).join("");
    } catch (e) { /* silent */ }
  }

  // ── Add Account Modal ─────────────────────────────────────
  function showAddAccountModal() {
    _openModal("Add Account", `
      <div class="form-group">
        <label>Nickname</label>
        <input type="text" id="new-acc-name" placeholder="e.g. HDFC Savings" />
      </div>
      <div class="form-group">
        <label>Account Type</label>
        <select id="new-acc-type">
          <option value="savings">Savings Account</option>
          <option value="credit_card">Credit Card</option>
          <option value="wallet">Wallet</option>
          <option value="upi">UPI</option>
          <option value="cash">Cash</option>
        </select>
      </div>
      <div class="form-group">
        <label>Bank Name <span class="optional">(optional)</span></label>
        <input type="text" id="new-acc-bank" placeholder="e.g. HDFC Bank" />
      </div>
      <div class="form-group">
        <label>Last 4 Digits <span class="optional">(optional)</span></label>
        <input type="text" id="new-acc-digits" placeholder="1234" maxlength="4" />
      </div>
      <div class="form-group">
        <label>Opening Balance (₹)</label>
        <input type="number" id="new-acc-balance" value="0" />
      </div>
      <button class="btn btn-primary btn-full" onclick="App.submitNewAccount()">
        Create Account
      </button>
      <div id="new-acc-error" class="error-msg hidden"></div>
    `);
  }

  async function submitNewAccount() {
    const name    = _el("new-acc-name")?.value.trim();
    const type    = _el("new-acc-type")?.value;
    const bank    = _el("new-acc-bank")?.value.trim();
    const digits  = _el("new-acc-digits")?.value.trim();
    const balance = parseFloat(_el("new-acc-balance")?.value) || 0;

    if (!name) { _showError("new-acc-error", "Nickname is required."); return; }
    try {
      await api.createAccount({
        nickname: name,
        account_type: type,
        bank_name: bank || null,
        last_four_digits: digits || null,
        current_balance: balance,
      });
      _closeModal();
      toast("Account created!", "success");
      await _loadAccounts();
      navigateTo("accounts");
    } catch (e) { _showError("new-acc-error", e.message); }
  }

  // ── Modal ─────────────────────────────────────────────────
  function _openModal(title, bodyHtml) {
    _el("modal-title").textContent = title;
    _el("modal-body").innerHTML = bodyHtml;
    _el("modal-overlay").classList.remove("hidden");
  }

  function _closeModal() {
    _el("modal-overlay").classList.add("hidden");
  }

  _el("modal-close")?.addEventListener("click", _closeModal);
  _el("modal-overlay")?.addEventListener("click", e => {
    if (e.target === _el("modal-overlay")) _closeModal();
  });

  // ── Onboarding Guide ──────────────────────────────────────
  function _showOnboarding() {
    const overlay = _el("onboarding-overlay");
    const guestNote = _el("guest-note");
    if (!overlay) return;

    // Show/hide guest-specific note
    if (state.isGuest && guestNote) {
      guestNote.classList.remove("hidden");
    } else if (guestNote) {
      guestNote.classList.add("hidden");
    }

    // Reset checkbox
    const checkbox = _el("dont-show-again");
    if (checkbox) checkbox.checked = false;

    overlay.classList.remove("hidden");
  }

  function _hideOnboarding() {
    const overlay = _el("onboarding-overlay");
    const checkbox = _el("dont-show-again");
    if (!overlay) return;

    if (checkbox && checkbox.checked) {
      localStorage.setItem("finance_onboarding_dismissed", "true");
    }

    overlay.classList.add("hidden");
  }

  // Bind onboarding close button
  _el("btn-onboarding-close")?.addEventListener("click", _hideOnboarding);

  // Bind Help sidebar link
  _el("nav-help")?.addEventListener("click", (e) => {
    e.preventDefault();
    _showOnboarding();
  });

  // ── Toast ─────────────────────────────────────────────────
  function toast(msg, type = "info") {
    const c = document.getElementById("toast-container");
    const t = document.createElement("div");
    t.className = `toast ${type}`;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => t.remove(), 4000);
  }

  // ── Utilities ─────────────────────────────────────────────
  function _el(id) { return document.getElementById(id); }

  function _esc(s) {
    const d = document.createElement("div");
    d.textContent = String(s || "");
    return d.innerHTML;
  }

  function _showError(id, msg) {
    const e = _el(id);
    if (e) { e.textContent = msg; e.classList.remove("hidden"); }
  }

  function _clearError(id) {
    const e = _el(id);
    if (e) { e.textContent = ""; e.classList.add("hidden"); }
  }

  function txPrev() {
    if (state.txPage > 1) { state.txPage--; _loadTransactions(); }
  }

  function txNext(max) {
    if (state.txPage < max) { state.txPage++; _loadTransactions(); }
  }

  // ── Start ─────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", init);

  // ── Public API ────────────────────────────────────────────
  return {
    navigateTo,
    showAddAccountModal,
    submitNewAccount,
    handleDrop,
    txPrev,
    txNext,
    toast,
    showOnboarding: _showOnboarding,
  };

})();
