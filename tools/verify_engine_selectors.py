"""Dùng thẳng các đoạn JS chạy thật của engine.py để đánh vào trang thật, kiểm tra / chỉnh lại selector.

Cách dùng:
  python3 tools/verify_engine_selectors.py
Sẽ:
  1) Kết nối tới trang muse.ai đang chạy thật
  2) Chạy engine._ATT_JS -> in ra các file đính kèm lấy được
  3) Chạy logic inject + chờ xác nhận của engine, kiểm tra nút "Remove attachment" có xuất hiện không
  4) In ra tất cả button[aria-label] hiện có trong composer
"""
from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace

HERE = __file__.rsplit("/", 2)[0]
sys.path.insert(0, HERE)

from cdp import CDP, http_json  # noqa: E402
import engine as E  # noqa: E402

CDP_PORT = 19210
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
           "IQAAAABJRU5ErkJggg==")


def find_page_ws():
    pages = http_json(f"http://127.0.0.1:{CDP_PORT}/json")
    for p in pages:
        if p.get("type") == "page" and "muse.ai" in (p.get("url") or ""):
            return p.get("webSocketDebuggerUrl"), p.get("url")
    raise SystemExit("Không tìm thấy trang muse.ai")


class FakeEngine:
    """Lớp vỏ nhỏ chỉ mang theo page, tái sử dụng JS / logic của engine."""
    def __init__(self, page):
        self.page = page
        self.cfg = SimpleNamespace(media_dir="/tmp", profile_dir="/tmp")

    # Mượn các phương thức thật
    attachments = E.MuseEngine.attachments
    _clear_attachments = E.MuseEngine._clear_attachments


def main():
    ws, url = find_page_ws()
    print(f"[verify] page = {url}\n")
    c = CDP(ws)
    e = FakeEngine(c)
    try:
        # 0) tất cả aria-label của composer
        labels = c.js("""JSON.stringify(Array.from(document.querySelectorAll('button[aria-label]'))
            .map(function(b){return b.getAttribute('aria-label');}))""")
        print("=== button[aria-label] trên trang ===")
        print(labels)

        # 1) engine._ATT_JS
        atts = e.attachments()
        print(f"\n=== engine.attachments() -> {len(atts)} ===")
        for a in atts[:8]:
            print("  ", json.dumps({k: (str(v)[:70] if k.endswith('Src') or k == 'src' else v)
                                    for k, v in a.items()}, ensure_ascii=False))

        # 2) inject + hai tiêu chí xác nhận
        print("\n=== Inject 1x1 PNG ===")
        res = c.js(E.MuseEngine._attach_image.__doc__ and
                   "0" or "0")  # noop placeholder
        # Bê nguyên đoạn JS inject của engine
        inject = """(function(b64, mime){
            var bin=atob(b64); var arr=new Uint8Array(bin.length);
            for(var i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
            var blob=new Blob([arr],{type:mime});
            var file=new File([blob],'reference_image.png',{type:mime});
            var input=document.querySelectorAll('input[type="file"]')[0];
            if(!input) return JSON.stringify({ok:false,err:'no-file-input'});
            input.setAttribute('accept','image/*');
            var dt=new DataTransfer(); dt.items.add(file);
            input.files=dt.files;
            input.dispatchEvent(new Event('change',{bubbles:true}));
            input.dispatchEvent(new Event('input',{bubbles:true}));
            return JSON.stringify({ok:true});
        })(%s, %s)""" % (json.dumps(PNG_B64), json.dumps("image/png"))
        print("inject:", c.js(inject))

        for i in range(12):
            time.sleep(0.5)
            old = c.js("""(function(){return Boolean(document.querySelector('button[aria-label*="Remove attachment" i]'));})()""")
            zh = c.js("""(function(){return Boolean(document.querySelector('button[aria-label*="移除附件"]'));})()""")
            img = c.js("""(function(){var ov=document.querySelector('[data-testid="hatch-composer-placeholder-overlay"]');
                var comp=ov?ov.closest('form')||ov.parentElement.parentElement.parentElement:null;
                return comp?comp.querySelectorAll('img').length:-1;})()""")
            if old or zh or img:
                print(f"  [t={i*0.5+0.5}s] OLD(Remove attachment EN)={old}  NEW(移除附件 ZH)={zh}  composerImgs={img}")
                if old or zh:
                    break
        # Dọn dẹp
        print("clear:", e._clear_attachments())
    finally:
        c.close()


if __name__ == "__main__":
    main()
