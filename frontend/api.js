/**
 * api.js — Finance-AI Frontend API Client
 * ─────────────────────────────────────────────────────────────
 * Thin fetch() wrapper that:
 *   - Injects Authorization Bearer token
 *   - Handles JSON parsing and error normalization
 *   - Provides typed methods for every backend endpoint
 */

const API_BASE = "/api/v1";

class ApiClient {
  constructor() {
    this._token = localStorage.getItem("finance_token") || null;
  }

  setToken(token) {
    this._token = token;
    if (token) localStorage.setItem("finance_token", token);
    else localStorage.removeItem("finance_token");
  }

  getToken() { return this._token; }

  async _request(method, path, body = null, isFormData = false) {
    const headers = {};
    if (this._token) headers["Authorization"] = `Bearer ${this._token}`;
    if (!isFormData) headers["Content-Type"] = "application/json";

    const opts = { method, headers };
    if (body) opts.body = isFormData ? body : JSON.stringify(body);

    const resp = await fetch(`${API_BASE}${path}`, opts);
    if (resp.status === 204) return null;

    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data?.detail || data?.error || `HTTP ${resp.status}`);
    }
    return data;
  }

  get(path)        { return this._request("GET", path); }
  post(path, body) { return this._request("POST", path, body); }
  put(path, body)  { return this._request("PUT", path, body); }
  del(path)        { return this._request("DELETE", path); }

  // ── Auth ───────────────────────────────────────────────────
  async register(phone, pin, displayName) {
    return this.post("/auth/register", {
      phone_number: phone, pin, display_name: displayName
    });
  }
  async login(phone, pin) {
    return this.post("/auth/login", { phone_number: phone, pin });
  }
  async guestSession() { return this.post("/auth/guest", {}); }
  async logout() {
    await this.post("/auth/logout", {});
    this.setToken(null);
  }
  async getMe() { return this.get("/auth/me"); }

  // ── Accounts ───────────────────────────────────────────────
  async listAccounts()          { return this.get("/accounts/"); }
  async createAccount(data)     { return this.post("/accounts/", data); }
  async getAccountSummary(id)   { return this.get(`/accounts/${id}/summary`); }
  async updateAccount(id, data) { return this.put(`/accounts/${id}`, data); }
  async deleteAccount(id)       { return this.del(`/accounts/${id}`); }

  // ── Transactions ───────────────────────────────────────────
  async listTransactions(params = {}) {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params).filter(
          ([, v]) => v !== null && v !== undefined && v !== ""
        )
      )
    ).toString();
    return this.get(`/transactions/${qs ? "?" + qs : ""}`);
  }
  async searchTransactions(q, limit = 50) {
    return this.get(
      `/transactions/search?q=${encodeURIComponent(q)}&limit=${limit}`
    );
  }
  async updateTransaction(id, data) { return this.put(`/transactions/${id}`, data); }
  async deleteTransaction(id)       { return this.del(`/transactions/${id}`); }

  // ── Upload ─────────────────────────────────────────────────
  async uploadFile(file, accountId) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("account_id", accountId);
    return this._request("POST", "/upload/file", fd, true);
  }
  async getUploadLogs() { return this.get("/upload/logs"); }

  // ── Dashboard ──────────────────────────────────────────────
  async getDashboardSummary(period = "monthly") {
    return this.get(`/dashboard/summary?period=${period}`);
  }
  async getSpendingTrend(granularity = "monthly", months = 6) {
    return this.get(
      `/dashboard/trend?granularity=${granularity}&months=${months}`
    );
  }
  async getHeatmap(days = 90) {
    return this.get(`/dashboard/heatmap?days=${days}`);
  }

  // ── Insights ───────────────────────────────────────────────
  async listInsights()       { return this.get("/insights/"); }
  async markInsightRead(id)  { return this.put(`/insights/${id}/read`, {}); }
}

window.api = new ApiClient();
