// ==UserScript==
// @name         Trợ thủ nhập Cookie Muse2API
// @namespace    https://github.com/czg86389-hub/muse2api
// @version      1.1.0
// @description  Xuất và đẩy Cookie cốt lõi từ trang muse.ai vào pool tài khoản muse2api chỉ với một lệnh, hỗ trợ bảng nổi hiện đại, chỉ báo trạng thái và sao lưu clipboard
// @author       MUSE2API Contributors
// @match        https://muse.ai/*
// @match        https://*.muse.ai/*
// @grant        GM_cookie
// @grant        GM_setClipboard
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      *
// @run-at       document-end
// ==/UserScript==

(function () {
  'use strict';

  const STORE_KEY = '***';
  const ESSENTIAL = ['hatch_sess', 'hatch_gw', 'hatch_vml'];

  function loadCfg() {
    const fallback = { base: '', key: '', label: '' };
    if (typeof GM_getValue === 'function') {
      return GM_getValue(STORE_KEY, fallback);
    }
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY)) || fallback;
    } catch (_) {
      return fallback;
    }
  }

  function saveCfg(cfg) {
    if (typeof GM_setValue === 'function') {
      GM_setValue(STORE_KEY, cfg);
    } else {
      localStorage.setItem(STORE_KEY, JSON.stringify(cfg));
    }
  }

  function normBase(v) {
    let s = (v || '').trim();
    if (!s) return '';
    if (!/^https?:\/\//i.test(s)) {
      s = (/^(127\.|100\.|10\.|192\.168\.|localhost)/i.test(s) ? 'http://' : 'https://') + s;
    }
    s = s.replace(/\/+$/, '').replace(/\/v1$/i, '');
    return s;
  }

  function showToast(msg, type = 'info') {
    const el = document.createElement('div');
    el.textContent = msg;
    const bg = type === 'ok' ? '#059669' : type === 'err' ? '#dc2626' : '#2563eb';
    el.style.cssText = `position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:9999999;padding:10px 18px;background:${bg};color:#fff;border-radius:10px;font-size:13px;font-weight:500;font-family:-apple-system,BlinkMacSystemFont,sans-serif;box-shadow:0 12px 30px rgba(0,0,0,0.5);transition:opacity 0.3s;max-width:420px;text-align:center;line-height:1.5;pointer-events:none;`;
    document.body.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 300);
    }, 4000);
  }

  async function getCookies() {
    const out = {}, exp = {};
    if (typeof GM_cookie !== 'undefined' && typeof GM_cookie.list === 'function') {
      try {
        const queries = [
          { url: 'https://muse.ai/' },
          { domain: '.muse.ai' },
          { domain: 'muse.ai' },
        ];
        const all = [];
        for (const q of queries) {
          try {
            const list = await new Promise((resolve) => {
              GM_cookie.list(q, (cookies, error) => {
                resolve(error ? [] : (cookies || []));
              });
            });
            if (Array.isArray(list)) all.push(...list);
          } catch (_) {}
        }

        for (const c of all) {
          if (!c || !c.name) continue;
          const dom = (c.domain || '').replace(/^\./, '');
          if (!dom.endsWith('muse.ai')) continue;
          out[c.name] = c.value;
          if (c.expirationDate) exp[c.name] = Math.floor(c.expirationDate);
        }
      } catch (_) {}
    }

    try {
      const parts = document.cookie.split(';');
      for (const p of parts) {
        const idx = p.indexOf('=');
        if (idx > 0) {
          const k = p.slice(0, idx).trim();
          const v = p.slice(idx + 1).trim();
          if (k && !(k in out)) out[k] = v;
        }
      }
    } catch (_) {}

    return { cookies: out, expires: exp };
  }

  async function checkSessionStatus() {
    const { cookies } = await getCookies();
    const missing = ESSENTIAL.filter(n => !(n in cookies));
    const hasCore = missing.length === 0;
    return { hasCore, count: Object.keys(cookies).length, missing, cookies };
  }

  async function executePush(btnEl = null) {
    const cfg = loadCfg();
    const base = normBase(cfg.base);
    const key = (cfg.key || '').trim();
    const label = (cfg.label || '').trim();

    if (btnEl) {
      btnEl.disabled = true;
      btnEl.innerHTML = '<span style="opacity:0.8">⏳ Đang trích xuất…</span>';
    }

    try {
      const { cookies, expires } = await getCookies();
      const names = Object.keys(cookies);

      if (!names.length) {
        showToast('Không phát hiện Cookie muse.ai nào, hãy chắc chắn bạn đã đăng nhập tài khoản!', 'err');
        return;
      }

      const missing = ESSENTIAL.filter(n => !(n in cookies));
      if (missing.length) {
        showToast(`Đọc được ${names.length} Cookie, nhưng thiếu mục cốt lõi: ${missing.join(', ')}. Hãy tải lại trang rồi thử lại.`, 'err');
        return;
      }

      const cookieStr = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; ');
      if (typeof GM_setClipboard === 'function') {
        GM_setClipboard(cookieStr);
      }

      if (!base) {
        showToast('✓ Đã copy Cookie đầy đủ vào clipboard! Hãy điền địa chỉ dịch vụ ở phần cấu hình phía trên để bật nhập một lệnh.', 'ok');
        return;
      }

      if (btnEl) btnEl.innerHTML = '<span style="opacity:0.8">🚀 Đang đẩy lên dịch vụ…</span>';

      const headers = { 'Content-Type': 'application/json' };
      if (key) {
        headers['Authorization'] = 'Bearer ' + key;
      }

      const payload = JSON.stringify({ label, cookies, expires });

      const doFetch = () => {
        if (typeof GM_xmlhttpRequest === 'function') {
          return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
              method: 'POST',
              url: base + '/admin/accounts',
              headers: headers,
              data: payload,
              onload: (res) => resolve({ ok: res.status >= 200 && res.status < 300, status: res.status, text: () => Promise.resolve(res.responseText) }),
              onerror: reject,
              ontimeout: reject,
            });
          });
        }
        return fetch(base + '/admin/accounts', { method: 'POST', headers, body: payload });
      };

      const r = await doFetch();
      const txt = await r.text();
      let data = {};
      try { data = JSON.parse(txt); } catch (_) {}

      if (r.status === 401) {
        showToast('Đẩy thất bại: API Key sai (401)', 'err');
        return;
      }
      if (!r.ok) {
        showToast(`Đẩy thất bại (HTTP ${r.status}): ${txt.slice(0, 80)}. Đã copy Cookie vào clipboard!`, 'err');
        return;
      }

      const a = (data.added && data.added[0]) || {};
      const expDate = a.expires_at ? new Date(a.expires_at * 1000).toLocaleDateString() : 'còn hiệu lực';
      showToast(`✓ Nhập tài khoản vào muse2api thành công!\nID: ${a.id || '?'}\nHết hạn: ${expDate}`, 'ok');
      updateStatusBadge();
    } catch (e) {
      showToast(`Yêu cầu gặp lỗi: ${e.message || e}. Đã tự động copy Cookie vào clipboard!`, 'err');
    } finally {
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.innerHTML = '⚡ Nhập vào pool tài khoản một lệnh';
      }
    }
  }

  async function updateStatusBadge() {
    const badge = document.getElementById('muse-status-tag');
    if (!badge) return;
    const st = await checkSessionStatus();
    if (st.hasCore) {
      badge.style.color = '#22c55e';
      badge.textContent = `✓ Phiên đăng nhập đầy đủ (${st.count} mục)`;
    } else {
      badge.style.color = '#f59e0b';
      badge.textContent = `⚠ Phiên đăng nhập chưa sẵn sàng`;
    }
  }

  function togglePanel() {
    const existing = document.getElementById('muse2api-token-panel');
    if (existing) {
      existing.remove();
      return;
    }
    showPanel();
  }

  function showPanel() {
    if (document.getElementById('muse2api-token-panel')) {
      document.getElementById('muse2api-token-panel').remove();
    }

    const cfg = loadCfg();
    const panel = document.createElement('div');
    panel.id = 'muse2api-token-panel';
    panel.innerHTML = `
      <div style="position:fixed; top:20px; right:20px; z-index:999999;
                  background:linear-gradient(180deg, rgba(15, 23, 42, 0.95) 0%, rgba(10, 15, 30, 0.96) 100%);
                  color:#f8fafc; padding:20px 22px; width:360px; max-width:calc(100vw - 40px);
                  border-radius:16px; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                  box-shadow:0 20px 50px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(96, 165, 250, 0.25);
                  backdrop-filter:blur(16px); font-size:13px; line-height:1.4;">

        <!-- Phần đầu -->
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; padding-bottom:10px; border-bottom:1px solid #1e293b;">
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#38bdf8; box-shadow:0 0 10px #38bdf8;"></span>
            <b style="font-size:15px; color:#f1f5f9; letter-spacing:0.3px;">Bảng nhập muse2api</b>
            <span style="font-size:11px; background:#1e293b; color:#94a3b8; padding:1px 6px; border-radius:6px;">v1.1</span>
          </div>
          <button id="muse-close-btn" style="background:transparent; border:none; color:#64748b; font-size:18px; cursor:pointer; padding:0 4px; line-height:1;">✕</button>
        </div>

        <!-- Thanh chỉ báo trạng thái -->
        <div style="background:#0f172a; border:1px solid #1e293b; border-radius:10px; padding:10px 12px; margin-bottom:14px; display:flex; justify-content:space-between; align-items:center;">
          <span style="font-size:12px; color:#94a3b8;">Phiên trang web hiện tại</span>
          <span id="muse-status-tag" style="font-size:12px; font-weight:600; color:#94a3b8;">Đang kiểm tra…</span>
        </div>

        <!-- Mục cấu hình form (nhúng thẳng ô nhập, tự động ghi nhớ) -->
        <div style="margin-bottom:12px;">
          <div style="font-size:11px; color:#94a3b8; margin-bottom:5px; font-weight:500;">Địa chỉ dịch vụ (Base URL)</div>
          <input id="muse-cfg-base" type="text" placeholder="ví dụ https://muse.yourdomain.com hoặc http://localhost:18610"
                 value="${cfg.base || ''}"
                 style="width:100%; box-sizing:border-box; padding:8px 11px; background:#0b1120; border:1px solid #334155;
                        border-radius:8px; color:#f1f5f9; font-size:12px; font-family:monospace; outline:none; transition:border-color 0.2s;">
        </div>

        <div style="margin-bottom:12px;">
          <div style="font-size:11px; color:#94a3b8; margin-bottom:5px; font-weight:500;">API Key (có thể để trống nếu dịch vụ không bật xác thực)</div>
          <input id="muse-cfg-key" type="password" placeholder="m2a_..."
                 value="${cfg.key || ''}"
                 style="width:100%; box-sizing:border-box; padding:8px 11px; background:#0b1120; border:1px solid #334155;
                        border-radius:8px; color:#f1f5f9; font-size:12px; font-family:monospace; outline:none;">
        </div>

        <div style="margin-bottom:16px;">
          <div style="font-size:11px; color:#94a3b8; margin-bottom:5px; font-weight:500;">Nhãn tài khoản (tên hiển thị tùy chọn)</div>
          <input id="muse-cfg-label" type="text" placeholder="ví dụ acc-01 (để trống sẽ tự tạo)"
                 value="${cfg.label || ''}"
                 style="width:100%; box-sizing:border-box; padding:8px 11px; background:#0b1120; border:1px solid #334155;
                        border-radius:8px; color:#f1f5f9; font-size:12px; outline:none;">
        </div>

        <!-- Khu vực thao tác -->
        <div style="display:flex; flex-direction:column; gap:8px;">
          <button id="muse-action-push" style="width:100%; padding:10px 0; border:none; border-radius:10px;
                  background:linear-gradient(135deg, #0284c7, #2563eb); color:#fff;
                  font-weight:600; font-size:13px; cursor:pointer; box-shadow:0 4px 14px rgba(37, 99, 235, 0.4);
                  transition:all 0.2s;">
            ⚡ Nhập vào pool tài khoản một lệnh
          </button>

          <div style="display:flex; gap:8px;">
            <button id="muse-action-copy" style="flex:1; padding:7px 0; border:1px solid #334155; border-radius:8px;
                    background:#0f172a; color:#cbd5e1; font-size:12px; cursor:pointer; transition:all 0.2s;">
              📋 Chỉ copy Cookie
            </button>
            <button id="muse-action-save" style="flex:1; padding:7px 0; border:1px solid #334155; border-radius:8px;
                    background:#0f172a; color:#cbd5e1; font-size:12px; cursor:pointer; transition:all 0.2s;">
              💾 Lưu cấu hình
            </button>
          </div>
        </div>

        <!-- Gợi ý cuối -->
        <div style="margin-top:14px; padding-top:10px; border-top:1px solid #1e293b; display:flex; justify-content:space-between; align-items:center; font-size:11px; color:#64748b;">
          <span>Cấu hình sẽ tự động lưu trong trình duyệt cục bộ</span>
          <span>Nhấn Esc hoặc nút đóng góc phải trên</span>
        </div>
      </div>
    `;

    document.body.appendChild(panel);
    updateStatusBadge();

    // Gắn sự kiện và tự động lưu
    const baseIn = document.getElementById('muse-cfg-base');
    const keyIn = document.getElementById('muse-cfg-key');
    const labelIn = document.getElementById('muse-cfg-label');

    function saveInputs() {
      const newCfg = {
        base: normBase(baseIn.value),
        key: keyIn.value.trim(),
        label: labelIn.value.trim(),
      };
      saveCfg(newCfg);
      return newCfg;
    }

    baseIn.addEventListener('change', saveInputs);
    keyIn.addEventListener('change', saveInputs);
    labelIn.addEventListener('change', saveInputs);

    document.getElementById('muse-close-btn').onclick = () => panel.remove();
    document.getElementById('muse-action-save').onclick = () => {
      saveInputs();
      showToast('✓ Đã lưu cấu hình!', 'ok');
    };

    document.getElementById('muse-action-copy').onclick = async () => {
      const { cookies } = await getCookies();
      const str = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; ');
      if (str) {
        if (typeof GM_setClipboard === 'function') GM_setClipboard(str);
        showToast('✓ Đã copy Cookie đầy đủ vào clipboard!', 'ok');
      } else {
        showToast('Không phát hiện Cookie nào!', 'err');
      }
    };

    document.getElementById('muse-action-push').onclick = function () {
      saveInputs();
      executePush(this);
    };
  }

  function mountFloatingTrigger() {
    if (document.getElementById('muse2api-floating-ball')) return;
    const ball = document.createElement('div');
    ball.id = 'muse2api-floating-ball';
    ball.innerHTML = `
      <div style="position:fixed; bottom:24px; right:24px; z-index:999990;
                  width:42px; height:42px; border-radius:50%; background:linear-gradient(135deg, #0284c7, #2563eb);
                  color:#fff; display:flex; align-items:center; justify-content:center;
                  cursor:pointer; box-shadow:0 8px 24px rgba(37, 99, 235, 0.45); font-size:18px;
                  user-select:none; transition:transform 0.2s, box-shadow 0.2s;"
           title="Nhấn để mở bảng nhập muse2api"
           onmouseover="this.style.transform='scale(1.08)'"
           onmouseout="this.style.transform='scale(1)'">
        ⚡
      </div>
    `;
    ball.addEventListener('click', togglePanel);
    document.body.appendChild(ball);
  }

  if (typeof GM_registerMenuCommand === 'function') {
    GM_registerMenuCommand('🚀 Mở bảng nhập muse2api', togglePanel);
    GM_registerMenuCommand('⚡ Đẩy thẳng tài khoản hiện tại một lệnh', () => executePush());
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountFloatingTrigger);
  } else {
    mountFloatingTrigger();
  }
})();
