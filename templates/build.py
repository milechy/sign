#!/usr/bin/env python3
"""契約書ひな形 → 8つの PDF と、入力欄の座標一覧を作る。
  build.py parts            contracts.md を8つに分けて templates/parts/ に書く
  build.py build [--strict] PDF と *.fields.json を templates/out/ に作る
  build.py check            分割・分類・本文の突き合わせ・設定の反映を、すべて機械で確かめる
元の文面（source/contracts.md）は書き換えない。［　］の置き換え以外で本文を変えない。
"""
import argparse, copy, html, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
import markdown

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "source" / "contracts.md"
PARTS = ROOT / "parts"
OUT = ROOT / "out"
TOKEN = re.compile(r"［[^］]*］")
MM = 72 / 25.4
SIZES = {"S": (28, 7), "M": (60, 7), "L": (120, 7), "XL": (150, 16)}
EXCLUDED = {"この下書きについて", "専門家に確認したい点と無料の窓口"}

# 出力ファイル → (section, テンプレート名)
DOCS = {
    "01_nda": ("nda", "【下書き】秘密保持契約書（NDA）"),
    "02_master": ("master", "【下書き】業務委託基本契約書"),
    "03a_trial": ("indiv", "【下書き】個別契約書（お試し）"),
    "03b_main": ("indiv", "【下書き】個別契約書（本契約）"),
    "03c_security": ("indiv", "【下書き】個別契約書（セキュリティ診断・強化）"),
    "03d_advisor": ("indiv", "【下書き】個別契約書（AI顧問）"),
    "04_permission": ("permit", "【下書き】診断作業の許可書"),
    "05_consent": ("consent", "【下書き】ムーア環境での運用の同意書"),
}
# 個別契約書の「###」節 → どの種類に入るか（共通は abcd）
INDIV_BLOCKS = [
    ("契約の内容", "abcd"), ("お試しの条件", "a"), ("本契約の条件", "b"), ("有料相談の差し引き", "b"),
    ("引き渡し後のサポート", "b"), ("AIセキュリティ診断・強化の条件", "c"), ("AI顧問の条件", "d"),
    ("別紙", "abcd"),
]
SECTION_OF_HEADING = {
    "1. 秘密保持契約書（NDA）": "01_nda",
    "2. 業務委託基本契約書（準委任）": "02_master",
    "3. 個別契約書": "03",
    "4. 診断作業の許可書": "04_permission",
    "5. ムーア環境での運用の同意書": "05_consent",
}


class BuildError(Exception):
    pass


def split_source(text: str) -> dict:
    """contracts.md を8つのマークダウンに分ける。見出しが想定と違えば止める。"""
    lines = text.split("\n")
    h2 = [i for i, l in enumerate(lines) if l.startswith("## ")]
    secs = {}
    for k, i in enumerate(h2):
        end = h2[k + 1] if k + 1 < len(h2) else len(lines)
        secs[lines[i][3:].strip()] = lines[i:end]
    extra = set(secs) - EXCLUDED - set(SECTION_OF_HEADING)
    missing = (set(SECTION_OF_HEADING) | EXCLUDED) - set(secs)
    if extra or missing:
        raise BuildError(f"見出しが想定と違う。多い: {sorted(extra)} / 足りない: {sorted(missing)}")
    out = {}
    for head, key in SECTION_OF_HEADING.items():
        body = secs[head]
        if key != "03":
            out[key] = "\n".join(body).rstrip() + "\n"
            continue
        h3 = [i for i, l in enumerate(body) if l.startswith("### ")]
        pre = body[: h3[0]]
        blocks = []
        for k, i in enumerate(h3):
            end = h3[k + 1] if k + 1 < len(h3) else len(body)
            blocks.append(body[i:end])
        names = [b[0][4:] for b in blocks]
        want = [n for n, _ in INDIV_BLOCKS]
        if [any(n.startswith(w) for w in want) for n in names].count(True) != len(names) or len(names) != len(want):
            raise BuildError(f"個別契約書の節が想定と違う: {names}")
        for letter, key2 in zip("abcd", ["03a_trial", "03b_main", "03c_security", "03d_advisor"]):
            parts = [pre]
            for blk, (w, who) in zip(blocks, INDIV_BLOCKS):
                assert blk[0][4:].startswith(w)
                if letter in who:
                    parts.append(blk)
            out[key2] = "\n".join("\n".join(p).rstrip() + "\n" for p in parts)
    assert set(out) == set(DOCS), set(out) ^ set(DOCS)
    return out


def load_cfg() -> dict:
    """config.json に、コミットしない config.local.json（住所など）を重ねる。"""
    cfg = json.load(open(ROOT / "config.json", encoding="utf-8"))
    local = ROOT / "config.local.json"
    if local.exists():
        over = json.load(open(local, encoding="utf-8")).get("values", {})
        unknown = set(over) - set(cfg["values"])
        if unknown:
            raise BuildError(f"config.local.json に、config.json にないキー: {sorted(unknown)}")
        cfg["values"].update(over)
        print(f"config.local.json を重ねた: {sorted(over)}", file=sys.stderr)
    return cfg


def load_rules():
    return json.load(open(ROOT / "fields.json", encoding="utf-8"))["rules"]


def process(md: str, section: str, cfg: dict, rules: list, used: set):
    """［…］ を処理する。A は設定値に、B は空欄に置き換える。
    返り値: (html 用 md, 突き合わせ用の本文 md, 入力欄の一覧, 未設定の設定キー)"""
    fields, unset = [], []
    html_lines, plain_lines = [], []
    in_sign = False
    seq = {"F": 0, "S": 0}
    for line in md.split("\n"):
        if re.match(r"^(締結日|許可日|同意日)：", line):
            in_sign = True
            html_lines += ['<div class="signblock" markdown="1">', ""]
        counts = {}
        h_out, p_out, pos = [], [], 0
        for m in TOKEN.finditer(line):
            tok = m.group(0)
            n = counts.get(tok, 0)
            counts[tok] = n + 1
            hit = None
            for ri, r in enumerate(rules):
                if r[0] != section or not tok.startswith(r[1]):
                    continue
                if r[2] and not re.search(r[2], line):
                    continue
                if len(r) > 8 and r[8] != "" and r[8] != n:
                    continue
                hit = (ri, r)
                break
            if not hit:
                raise BuildError(f"分類されていない［…］: section={section} {tok[:40]!r} 行: {line[:50]!r}")
            used.add(hit[0])
            r = hit[1]
            h_out.append(line[pos:m.start()]); p_out.append(line[pos:m.start()])
            if r[3] == "A":
                v = cfg["values"][r[4]]
                if v.startswith("［"):
                    unset.append(r[4])
                h_out.append(v); p_out.append(v)
            else:
                seq["F"] += 1
                fid = f"F{seq['F']:03d}"
                w, h = SIZES[r[7]]
                fields.append({"id": fid, "name": r[4], "who": r[5], "type": r[6].upper(), "token": tok[:30], "w_mm": w, "h_mm": h})
                cell = bool(re.match(r"^\|", line)) and bool(re.search(r"(^|\|)\s*" + re.escape(tok) + r"\s*(\||$)", line))
                cls = "blank cell" if cell else "blank"
                h_out.append(f'<span class="{cls}" style="width:{w}mm;height:{h}mm"><span class="mk">§{fid}§</span><span class="mk r">§{fid}e§</span></span>')
            pos = m.end()
        h_out.append(line[pos:]); p_out.append(line[pos:])
        html_lines.append("".join(h_out)); plain_lines.append("".join(p_out))
        if in_sign and re.match(r"^(甲|乙)", line):
            who = "customer" if line.startswith("甲") else "moore"
            seq["S"] += 1
            s = f"S{seq['S']:03d}"
            fields.append({"id": s + "s", "name": "署名", "who": who, "type": "SIGNATURE", "token": "", "w_mm": 60, "h_mm": 16})
            fields.append({"id": s + "d", "name": "署名日", "who": who, "type": "DATE", "token": "", "w_mm": 35, "h_mm": 8})
            html_lines.append("")
            html_lines.append(f'<div class="sigrow"><span class="blank sig"><span class="mk">§{s}s§</span><span class="mk r">§{s}se§</span></span>'
                              f'<span class="blank date"><span class="mk">§{s}d§</span><span class="mk r">§{s}de§</span></span></div>')
    if in_sign:
        html_lines += ["", "</div>"]
    return "\n".join(html_lines), "\n".join(plain_lines), fields, sorted(set(unset))


def prep_lists(md: str) -> str:
    """番号つきの行を、番号ごと本文として残す（リスト化しない）。"""
    out = []
    for l in md.split("\n"):
        m = re.match(r"^(\d+)\. (.*)$", l)
        if m:
            out += [f"{m.group(1)}\\. {m.group(2)}", ""]
        else:
            out.append(l)
    return "\n".join(out)


def css(cfg: dict) -> str:
    mark = ""
    if cfg.get("draft_mark"):
        mark = f'@top-right {{ content: "{cfg.get("draft_mark_text", "下書き")}"; font-size: 8pt; color: #666; }}'
    return f"""
@page {{ size: A4; margin: 20mm; {mark} @bottom-center {{ content: counter(page) " / " counter(pages); font-size: 9pt; }} }}
html {{ font-family: "Noto Serif CJK JP","Noto Serif JP","Noto Sans CJK JP","Noto Sans JP",sans-serif; font-size: 10.5pt; line-height: 1.75; }}
body {{ margin: 0; }}
h2 {{ font-size: 15pt; font-weight: 700; margin: 0 0 8mm; }}
h3 {{ font-size: 12pt; font-weight: 700; margin: 7mm 0 3mm; }}
p {{ margin: 0 0 3mm; }}
table {{ border-collapse: collapse; width: 100%; margin: 2mm 0 4mm; }}
th, td {{ border: 0.3mm solid #333; padding: 1.5mm 2mm; vertical-align: top; text-align: left; font-weight: normal; }}
th {{ font-weight: 700; }}
tr {{ break-inside: avoid; }}
.blank {{ display: inline-block; position: relative; vertical-align: bottom; border-bottom: 0.3mm solid #444; }}
.blank.sig, .blank.date {{ border: 0.3mm solid #999; }}
.blank.sig {{ width: 60mm; height: 16mm; }}
.blank.date {{ width: 35mm; height: 8mm; margin-left: 6mm; }}
.blank.cell {{ display: block; width: auto !important; }}
.mk {{ position: absolute; left: 0; top: 0; font-size: 2pt; color: transparent; }}
.mk.r {{ left: auto; right: 0; }}
.sigrow {{ margin: 1mm 0 6mm; }}
.signblock {{ break-inside: avoid; }}
.blank:not(.cell):not(.sig):not(.date) {{ margin-right: 3mm; }}
.kv th:first-child, .kv td:first-child {{ width: 40mm; }}
"""


def chrome() -> str:
    for c in (os.environ.get("CHROME_BIN"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              shutil.which("google-chrome"), shutil.which("chromium")):
        if c and Path(c).exists():
            return c
    raise BuildError("Chrome が見つからない（環境変数 CHROME_BIN で指定できる）")


def render_pdf(html_text: str, pdf: Path):
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "doc.html"
        h.write_text(html_text, encoding="utf-8")
        r = subprocess.run([chrome(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--user-data-dir={td}/profile", f"--print-to-pdf={pdf}", h.as_uri()],
                           capture_output=True, text=True, timeout=180)
        if not pdf.exists() or pdf.stat().st_size < 1000:
            raise BuildError(f"PDF が作れない: {r.stderr[-300:]}")


def locate(pdf: Path, fields: list) -> dict:
    doc = fitz.open(pdf)
    placed = []
    for f in fields:
        mk = f"§{f['id']}§"
        for pno in range(len(doc)):
            rects = doc[pno].search_for(mk)
            ends = doc[pno].search_for(f"§{f['id']}e§")
            if rects and ends:
                pw, ph = doc[pno].rect.width, doc[pno].rect.height
                r, e = rects[0], ends[0]
                width = e.x1 - r.x0  # 実際の幅（セルいっぱいの欄は、ここで決まる）
                placed.append({**f, "page": pno + 1, "w_mm": round(width / MM, 1),
                               "x_pct": round(r.x0 / pw * 100, 3), "y_pct": round(r.y0 / ph * 100, 3),
                               "w_pct": round(width / pw * 100, 3), "h_pct": round(f["h_mm"] * MM / ph * 100, 3)})
                break
        else:
            raise BuildError(f"入力欄の位置が PDF から取れない: {f['id']} {f['name']}")
    return {"pages": len(doc), "fields": placed}


def pdf_body_text(pdf: Path) -> str:
    doc = fitz.open(pdf)
    txt = []
    for p in doc:
        clip = fitz.Rect(40, 40, p.rect.width - 40, p.rect.height - 50)  # 余白の上下（ヘッダー・ページ番号）を除く
        txt.append(p.get_text("text", clip=clip))
    t = "".join(txt)
    t = re.sub(r"§[^§\s]+§", "", t)
    return re.sub(r"\s+", "", t)


def plain_expected(plain_md: str) -> str:
    out = []
    for l in plain_md.split("\n"):
        if re.match(r"^\|\s*-{3}", l):
            continue
        l = re.sub(r"^#+\s*", "", l).replace("**", "").replace("\\.", ".").replace("|", "")
        out.append(l)
    return re.sub(r"\s+", "", "".join(out))


def build_all(cfg: dict, out: Path, only=None, strict=False) -> dict:
    rules = load_rules()
    used = set()
    docs = split_source(SRC.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, (section, title) in DOCS.items():
        if only and name not in only:
            continue
        html_md, plain_md, fields, unset = process(docs[name], section, cfg, rules, used)
        if strict and unset:
            raise BuildError(f"{name}: 設定が未記入: {unset}")
        body = markdown.markdown(prep_lists(html_md), extensions=["tables", "md_in_html"])
        body = re.sub(r"<table>(\s*<thead>\s*<tr>\s*<th>項目</th>)", r'<table class="kv">\1', body)
        page = f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{css(cfg)}</style></head><body>{body}</body></html>'
        pdf = out / f"{name}.pdf"
        render_pdf(page, pdf)
        placed = locate(pdf, fields)
        meta = {"name": name, "title": title, "section": section, "unset_config": unset, **placed}
        (out / f"{name}.fields.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        result[name] = {"pdf": pdf, "plain": plain_md, "meta": meta}
    if not only:
        unused = [rules[i][:2] for i in range(len(rules)) if i not in used]
        if unused:
            raise BuildError(f"どの書面でも使われていない分類規則（原文が変わった可能性）: {unused}")
    return result


def cmd_parts(_):
    PARTS.mkdir(exist_ok=True)
    docs = split_source(SRC.read_text(encoding="utf-8"))
    for k, v in docs.items():
        (PARTS / f"{k}.md").write_text(v, encoding="utf-8")
    print(f"{len(docs)} 個を templates/parts/ に書いた")


def cmd_build(a):
    cfg = load_cfg()
    res = build_all(cfg, OUT, strict=a.strict)
    for k, v in res.items():
        m = v["meta"]
        print(f"{k}: {m['pages']}ページ, 入力欄 {len(m['fields'])}（署名等を含む）" + (f" 未設定: {m['unset_config']}" if m["unset_config"] else ""))


def cmd_check(a):
    errs = []
    cfg = load_cfg()
    docs = split_source(SRC.read_text(encoding="utf-8"))
    for k, v in docs.items():
        p = PARTS / f"{k}.md"
        if not p.exists() or p.read_text(encoding="utf-8") != v:
            errs.append(f"parts/{k}.md が原文の分割と違う（`build.py parts` で作り直す）")
    # 原文との対応: 各 parts の行は、すべて原文に存在する
    src_lines = set(SRC.read_text(encoding="utf-8").split("\n"))
    for k, v in docs.items():
        for l in v.split("\n"):
            if l and l not in src_lines:
                errs.append(f"{k}: 原文にない行がある: {l[:40]!r}")
    with tempfile.TemporaryDirectory() as td:
        res = build_all(cfg, Path(td), strict=a.strict)
        total_tokens = 0
        for k, v in res.items():
            pdf_text = pdf_body_text(v["pdf"])
            exp = plain_expected(v["plain"])
            if pdf_text != exp:
                i = next((i for i, (x, y) in enumerate(zip(pdf_text, exp)) if x != y), min(len(pdf_text), len(exp)))
                errs.append(f"{k}: PDF の本文と原文が違う（{i}文字目）\n  PDF: …{pdf_text[max(0,i-20):i+30]}\n  原文: …{exp[max(0,i-20):i+30]}")
            # ［ が残っていないこと（未設定の設定値を除く）
            unset_vals = [cfg['values'][u] for u in v['meta']['unset_config']]
            left = [t for t in TOKEN.findall(exp) if not any(t in uv for uv in unset_vals)]
            if left:
                errs.append(f"{k}: ［…］ が残っている: {left[:3]}")
            total_tokens += sum(1 for f in v["meta"]["fields"] if f["token"])
            fs = v["meta"]["fields"]
            for f in fs:
                if f["x_pct"] < 0 or f["y_pct"] < 0 or f["x_pct"] + f["w_pct"] > 100.01 or f["y_pct"] + f["h_pct"] > 100.01:
                    errs.append(f"{k}: 入力欄がページからはみ出す: {f['id']} {f['name']}")
            for i, f in enumerate(fs):
                for g in fs[i + 1:]:
                    if f["page"] == g["page"] and f["x_pct"] < g["x_pct"] + g["w_pct"] - 0.2 and g["x_pct"] < f["x_pct"] + f["w_pct"] - 0.2 \
                            and f["y_pct"] < g["y_pct"] + g["h_pct"] - 0.2 and g["y_pct"] < f["y_pct"] + f["h_pct"] - 0.2:
                        errs.append(f"{k}: 入力欄が重なる: {f['name']}({f['id']}) と {g['name']}({g['id']})")
        unset = sorted({u for v in res.values() for u in v["meta"]["unset_config"]})
        # 設定の変更が PDF に反映される
        cfg2 = copy.deepcopy(cfg)
        cfg2["values"]["court"] = "大阪地方裁判所"
        r2 = build_all(cfg2, Path(td) / "m", only={"01_nda"})["01_nda"]
        t2 = pdf_body_text(r2["pdf"])
        if "大阪地方裁判所" not in t2 or "東京地方裁判所" in t2:
            errs.append("config.json の値を変えても PDF に反映されない（court）")
    if unset:
        print(f"注意: 設定が未記入 → {unset}（`--strict` で失敗にする）")
    if errs:
        print("\n".join("NG: " + e for e in errs))
        sys.exit(1)
    print(f"OK: 8書面。入力欄（B）{total_tokens}件、分割・分類・本文の突き合わせ・設定の反映を確認")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("parts")
    for n in ("build", "check"):
        p = sub.add_parser(n)
        p.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    try:
        {"parts": cmd_parts, "build": cmd_build, "check": cmd_check}[a.cmd](a)
    except BuildError as e:
        print(f"NG: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
