/* Nhập Cookie Muse2API —— đọc cookie của muse.ai và POST đến dịch vụ muse2api.
 *
 * Điểm mấu chốt: dùng chrome.cookies chứ không phải document.cookie.
 * 4 cookie cốt lõi của muse.ai (hatch_sess / hatch_gw / hatch_vml /
 * hatch_native_auth_device) đều là httpOnly, JS trên trang web không đọc được,
 * chỉ có API cookies của tiện ích trình duyệt mới lấy được.
 */

const $ = (id) => document.getElementById(id);
const STORE = 'muse2api_ext_cfg';

const ESSENTIAL = ['hatch_sess', 'hatch_gw', 'hatch_vml', 'hatch_native_auth_device'];

function log(html, cls) {
  const el = $('log');
  el.className = 'show';
  el.innerHTML = cls ? `<span class="${cls}">${html}</span>` : html;
}

/* Chuẩn hóa địa chỉ dịch vụ: bỏ dấu gạch chéo ở cuối và hậu tố /v1 */
function normBase(v) {
  let s = (v || '').trim();
  if (!s) return '';
  if (!/^https?:\/\//i.test(s)) s = 'https://' + s;
  s = s.replace(/\/+$/, '');
  s = s.replace(/\/v1$/i, '');
  return s;
}

async function loadCfg() {
  const o = await chrome.storage.local.get(STORE);
  const c = o[STORE] || {};
  if (c.base) $('base').value = c.base;
  if (c.key) $('key').value = c.key;
  if (c.label) $('label').value = c.label;
  // Nếu chưa từng cấu hình thì thử đoán từ tab hiện tại (khi người dùng đang ở trang quản trị)
  if (!c.base) {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const u = tab && tab.url ? new URL(tab.url) : null;
      if (u && /\/admin/.test(u.pathname)) {
        $('base').value = u.origin;
        const k = new URLSearchParams(u.search).get('key');
        if (k) $('key').value = k;
      }
    } catch (e) { /* bỏ qua */ }
  }
}

async function saveCfg() {
  await chrome.storage.local.set({
    [STORE]: {
      base: normBase($('base').value),
      key: $('key').value.trim(),
      label: $('label').value.trim(),
    },
  });
}

async function grabCookies() {
  const all = await chrome.cookies.getAll({ domain: 'muse.ai' });
  const out = {}, exp = {};
  for (const c of all) {
    const dom = (c.domain || '').replace(/^\./, '');
    if (!dom.endsWith('muse.ai')) continue;
    out[c.name] = c.value;
    if (c.expirationDate) exp[c.name] = Math.floor(c.expirationDate);
  }
  return { cookies: out, expires: exp };
}

async function run() {
  const base = normBase($('base').value);
  const key = $('key').value.trim();
  const label = $('label').value.trim();

  if (!base) return log('Vui lòng điền địa chỉ dịch vụ trước', 'bad');
  if (!key) return log('Vui lòng điền API Key trước', 'bad');

  $('go').disabled = true;
  log('Đang đọc Cookie của muse.ai…');

  try {
    const { cookies, expires } = await grabCookies();
    const names = Object.keys(cookies);
    if (!names.length) {
      return log('Không đọc được Cookie nào của muse.ai.\n'
                 + 'Hãy mở và đăng nhập https://muse.ai/ trong trình duyệt này, '
                 + 'rồi quay lại bấm thêm một lần nữa.', 'bad');
    }
    const missing = ESSENTIAL.filter((n) => !(n in cookies));
    if (missing.length) {
      log(`Đọc được ${names.length} Cookie, nhưng thiếu mục cốt lõi: ${missing.join(', ')}\n`
          + 'Nghĩa là trình duyệt này vẫn chưa đăng nhập thành công. Hãy đăng nhập đến khi thấy được giao diện trò chuyện rồi thử lại.', 'warn');
      return;
    }

    log(`Đọc được ${names.length} Cookie, đang tải lên ${base} …`);

    const r = await fetch(base + '/admin/accounts', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + key,
      },
      body: JSON.stringify({ label, cookies, expires }),
    });

    const text = await r.text();
    let data;
    try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }

    if (r.status === 401) {
      return log('API Key không đúng (dịch vụ trả về 401).\n'
                 + 'Hãy vào đầu trang「Nhóm tài khoản」của trang quản trị để sao chép lại API Key chính xác.', 'bad');
    }
    if (!r.ok) {
      return log(`Nhập thất bại: HTTP ${r.status}\n${text.slice(0, 300)}`, 'bad');
    }

    const a = (data.added && data.added[0]) || {};
    await saveCfg();
    log(`✓ Nhập thành công\nNhãn tài khoản: ${a.label || label || '(tự động)'}\n`
        + `ID tài khoản: ${a.id || '?'}\nSố lượng Cookie: ${a.cookie_count || names.length}\n`
        + `Có hiệu lực đến: ${a.expires_at ? new Date(a.expires_at * 1000).toLocaleString() : 'không rõ'}\n`
        + (data.warning ? `\nLưu ý: ${data.warning}` : ''), 'ok');
  } catch (e) {
    log('Đã xảy ra lỗi: ' + (e && e.message ? e.message : String(e))
        + '\n\nNguyên nhân thường gặp:\n'
        + '· Điền sai địa chỉ dịch vụ hoặc dịch vụ chưa khởi động\n'
        + '· Địa chỉ này không phải https (hoặc chứng chỉ không được tin cậy)\n'
        + '· Trình duyệt đã chặn yêu cầu cross-origin', 'bad');
  } finally {
    $('go').disabled = false;
  }
}

$('go').addEventListener('click', run);
loadCfg();
