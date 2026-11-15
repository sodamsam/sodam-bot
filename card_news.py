# -*- coding: utf-8 -*-
"""'소담 AI 랩' 카드 이미지 생성기 (v3 — 한눈에 띄는 두 가지 얼굴).

글만 넣으면 카드가 만들어진다. 사진·얼굴·직접 올리는 이미지가 필요 없다.

  · 뉴스 카드(한 장)        : 검정 바탕 + 흰 굵은 글씨 + 주황·보라 포인트  → generate_news_card()
  · 복붙 프롬프트(4장 넘김) : 크림색 종이 + 남색 둥근 글씨 + 주황 강조 + 노란 형광펜 → generate_prompt_slides()

두 종류 모두 캔버스는 1080x1350(4:5)이고, 모든 장 아래에 @sodam_ai_lab이 들어간다.
글자 가장자리가 매끄럽도록 2배 크기로 그린 뒤 줄여서 저장한다.
폰트는 저장소 fonts/ 폴더의 Noto Sans KR(본문)과 Jua(크림 카드 제목, OFL 무료 글꼴)를 쓴다.
함수 이름과 카드 데이터 형식은 v2와 같아서 auto_post.py·corners.py는 고칠 필요가 없다.

뉴스 카드 데이터 형식:
  {
    "category": "신규 출시",            # 뱃지 문구 (8자 이내 권장)
    "date": "10월 6일",                 # 기준일 (기사 발행일, 코드에서 계산)
    "headline": "구글 문서 안에서 Claude를 쓸 수 있어요",
    "points": [{"title": "...", "desc": "..."}, ...],   # 1~3개
    "action_label": "바로 해보기",      # 또는 "신청 방법", "확인할 곳"
    "action": "...",
    "source": "9to5Google (10/6)",
  }

복붙 프롬프트 카드 데이터 형식:
  {"series": "복붙 프롬프트 #12", "headline": "...", "situation": "...",
   "prompt": "...", "example": "...", "tip": "..."}
"""
import os
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1350
PAD = 76
S = 2   # 2배로 그린 뒤 1080x1350으로 줄인다
HANDLE = "@sodam_ai_lab"

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_PATH = os.path.join(_FONT_DIR, "NotoSansKR-Variable.ttf")
TITLE_FONT_PATH = os.path.join(_FONT_DIR, "Jua-Regular.ttf")

# 검정 카드(뉴스)
DARK = {
    "bg": (10, 10, 14), "ink": (255, 255, 255), "sub": (178, 180, 198),
    "accent": (255, 122, 48),      # 주황
    "accent2": (150, 110, 255),    # 보라
    "panel": (24, 24, 33), "line": (58, 58, 78),
}
# 크림 카드(복붙 프롬프트)
CREAM = {
    "bg": (251, 243, 226), "ink": (31, 42, 68), "sub": (98, 104, 124),
    "accent": (232, 98, 42),       # 주황
    "marker": (255, 224, 120),     # 노란 형광펜
    "panel": (255, 253, 247), "line": (226, 212, 186),
    "tip": (255, 236, 170),
}
# 예전 코드·문서가 참조하던 이름 (호환용)
PALETTE = {"white": DARK["ink"], "lavender": DARK["sub"], "lilac": DARK["accent2"]}

_fonts = {}


def _font(size, weight=b"Bold"):
    """본문 글꼴(Noto Sans KR). weight=b"Jua"면 제목용 둥근 글꼴."""
    key = (size, weight)
    if key in _fonts:
        return _fonts[key]
    try:
        if weight == b"Jua":
            f = ImageFont.truetype(TITLE_FONT_PATH, size * S)
        else:
            f = ImageFont.truetype(FONT_PATH, size * S)
            f.set_variation_by_name(weight)
    except Exception:
        try:
            f = ImageFont.truetype(FONT_PATH, size * S)
        except Exception:
            f = ImageFont.load_default()
    _fonts[key] = f
    return f


def _tw(draw, text, font):
    """글자 폭(1080 기준 좌표)."""
    return draw.textlength(text, font=font) / S


def _wrap(draw, text, font, max_width):
    """어절 단위로 줄바꿈하되, 한 어절이 한 줄보다 길면 글자 단위로 쪼갠다."""
    lines, cur = [], ""
    for word in text.split():
        cand = (cur + " " + word).strip()
        if _tw(draw, cand, font) <= max_width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
            cur = ""
        if _tw(draw, word, font) <= max_width:
            cur = word
            continue
        piece = ""
        for ch in word:
            if _tw(draw, piece + ch, font) <= max_width:
                piece += ch
            else:
                lines.append(piece)
                piece = ch
        cur = piece
    if cur:
        lines.append(cur)
    return lines


def _ellipsize(draw, text, font, max_width):
    """한 줄에 안 들어가면 끝을 …로 줄인다."""
    if _tw(draw, text, font) <= max_width:
        return text
    while text and _tw(draw, text + "…", font) > max_width:
        text = text[:-1]
    return text + "…"


def _fit(draw, text, weight, max_width, max_lines, sizes):
    """max_lines 안에 들어가는 가장 큰 글자 크기를 고른다."""
    for size in sizes:
        font = _font(size, weight)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines, size
    font = _font(sizes[-1], weight)
    lines = _wrap(draw, text, font, max_width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ellipsize(draw, lines[-1] + "…", font, max_width)
        if weight == b"Jua":   # Jua 글꼴에는 말줄임표(…) 글자가 없어 마침표 세 개로 바꾼다
            lines[-1] = lines[-1].rstrip("…") + "..."
    return font, lines, sizes[-1]


def _fit_block(draw, text, weight, max_width, max_height, sizes, spacing=1.5):
    """여러 줄 글(줄바꿈 포함)이 max_height 안에 들어가는 가장 큰 글자 크기를 고른다."""
    def lay(size):
        font = _font(size, weight)
        lines = []
        for para in (text or "").split("\n"):
            lines += _wrap(draw, para, font, max_width) if para.strip() else [""]
        return font, lines, int(size * spacing)

    for size in sizes:
        font, lines, line_h = lay(size)
        if len(lines) * line_h <= max_height:
            return font, lines, line_h
    font, lines, line_h = lay(sizes[-1])
    keep = max(1, int(max_height // line_h))
    if len(lines) > keep:
        lines = lines[:keep]
        lines[-1] = _ellipsize(draw, lines[-1] + "…", font, max_width)
    return font, lines, line_h


# ── 그리기 도구 (좌표는 모두 1080x1350 기준) ─────────────────────────

def _rr(draw, box, radius, fill=None, outline=None, width=0):
    draw.rounded_rectangle([v * S for v in box], radius=radius * S, fill=fill, outline=outline, width=width * S)


def _text(draw, xy, text, font, fill, anchor="la"):
    draw.text((xy[0] * S, xy[1] * S), text, font=font, fill=fill, anchor=anchor)


def _sparkle(draw, cx, cy, r, fill):
    """네 갈래 별 장식."""
    cx, cy, r = cx * S, cy * S, r * S
    k = r * 0.28
    draw.polygon([(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k),
                  (cx, cy + r), (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)], fill=fill)


_NUM = re.compile(r"\d")


def _headline(draw, lines, font, size, x, y, t, line_gap=1.26, center_w=None, marker_line=None):
    """제목을 그린다. 숫자가 든 어절(3가지, 10분 등)은 강조색으로, marker_line 줄에는 형광펜을 깐다."""
    box = font.getbbox("가")
    g_top, g_bot = box[1] / S, box[3] / S
    for i, line in enumerate(lines):
        lw = _tw(draw, line, font)
        lx = x + (center_w - lw) / 2 if center_w else x
        ly = y + i * size * line_gap
        if marker_line == i and t.get("marker"):
            _rr(draw, [lx - 8, ly + g_top + (g_bot - g_top) * 0.5, lx + lw + 8, ly + g_bot + size * 0.07], 8, fill=t["marker"])
        px = lx
        words = line.split(" ")
        for j, word in enumerate(words):
            piece = word + (" " if j < len(words) - 1 else "")
            _text(draw, (px, ly), piece, font, t["accent"] if _NUM.search(word) else t["ink"])
            px += _tw(draw, piece, font)
    return y + len(lines) * size * line_gap


def _mascot(draw, cx, cy, r, t):
    """소담 마스코트: 반짝이는 김이 오르는 웃는 그릇 (도형으로 직접 그린다)."""
    body = t["accent"]
    face = t["bg"] if t is DARK else (255, 255, 255)
    X, Y, R = cx * S, cy * S, r * S
    draw.pieslice([X - R, Y - R, X + R, Y + R], 0, 180, fill=body)
    draw.rounded_rectangle([X - R * 1.08, Y - R * 0.12, X + R * 1.08, Y + R * 0.1], radius=R * 0.1, fill=body)
    draw.rounded_rectangle([X - R * 0.45, Y + R * 0.9, X + R * 0.45, Y + R * 1.08], radius=R * 0.08, fill=body)
    e = R * 0.09
    for ex in (-0.34, 0.34):
        draw.ellipse([X + R * ex - e, Y + R * 0.3 - e, X + R * ex + e, Y + R * 0.3 + e], fill=face)
    draw.arc([X - R * 0.2, Y + R * 0.3, X + R * 0.2, Y + R * 0.62], 20, 160, fill=face, width=max(2, int(R * 0.06)))
    _sparkle(draw, cx, cy - r * 0.75, r * 0.34, t.get("accent2", t["ink"]))
    _sparkle(draw, cx - r * 0.62, cy - r * 0.42, r * 0.18, body)
    _sparkle(draw, cx + r * 0.6, cy - r * 0.5, r * 0.22, body)


def _pill(draw, x, y, text, font, size, fg, bg=None, outline=None, padx=26, pady=11, center_w=None):
    """알약 모양 라벨. center_w를 주면 그 폭 안에서 가운데 정렬한다. (폭, 높이)를 돌려준다."""
    w = _tw(draw, text, font) + padx * 2
    h = size + pady * 2 + 8
    if center_w:
        x = x + (center_w - w) / 2
    _rr(draw, [x, y, x + w, y + h], h / 2, fill=bg, outline=outline, width=3 if outline else 0)
    _text(draw, (x + padx, y + h / 2), text, font, fg, anchor="lm")
    return w, h


def _save(img, out_path):
    img = img.resize((W, H), Image.LANCZOS)
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


# ── 뉴스 카드 (검정) ────────────────────────────────────────────────

def _dark_background():
    """검정 바탕에 보라(오른쪽 위)·주황(왼쪽 아래) 빛을 은은하게 깐다."""
    base = Image.new("RGB", (W // 4, H // 4), DARK["bg"])
    glow = Image.new("RGB", (W // 4, H // 4), DARK["bg"])
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W // 4 * 0.45, -H // 4 * 0.25, W // 4 * 1.25, H // 4 * 0.3], fill=(52, 30, 112))
    gd.ellipse([-W // 4 * 0.3, H // 4 * 0.8, W // 4 * 0.4, H // 4 * 1.2], fill=(78, 34, 14))
    glow = glow.filter(ImageFilter.GaussianBlur(46))
    return Image.blend(base, glow, 0.9).resize((W * S, H * S), Image.BICUBIC)


def generate_news_card(data, out_path):
    """카드 데이터(dict)로 검정 뉴스 카드 PNG를 만들어 out_path에 저장한다."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    t = DARK
    img = _dark_background()
    draw = ImageDraw.Draw(img)
    inner_w = W - PAD * 2

    # ① 상단: 분류 뱃지 + 기준일
    y = 78
    _pill(draw, PAD, y, data.get("category", "AI 뉴스"), _font(34, b"ExtraBold"), 34, (255, 255, 255), bg=t["accent"])
    date_text = data.get("date", "")
    if date_text:
        _text(draw, (W - PAD, y + 32), f"{date_text} 기준", _font(30, b"Medium"), t["sub"], anchor="rm")

    # ② 헤드라인
    y = 196
    head_font, head_lines, head_size = _fit(draw, data["headline"], b"Black", inner_w, 3, (82, 74, 66, 58))
    y = _headline(draw, head_lines, head_font, head_size, PAD, y, t, 1.27) + 34

    # ③ 핵심 포인트 (최대 3개) — 행동 박스 높이를 먼저 계산해 남는 공간을 나눠 준다
    points = (data.get("points") or [])[:3]
    action = (data.get("action") or "").strip()
    action_font = _font(38, b"Bold")
    action_lines = _wrap(draw, action, action_font, inner_w - 72)[:3] if action else []
    action_h = (40 + 44 + len(action_lines) * 54 + 34) if action_lines else 0
    footer_h, gap = 120, 22
    avail = H - y - footer_h - (action_h + gap if action_h else 0) - 20

    n = max(1, len(points))
    row_h = min(220, (avail - gap * (n - 1)) // n)
    tight = row_h < 176   # 헤드라인이 길어 칸이 낮아지면 글자를 줄이고 한 줄씩만 쓴다
    title_font = _font(38 if tight else 42, b"ExtraBold")
    desc_font = _font(30 if tight else 33, b"Medium")
    num_font = _font(46, b"Black")
    t_gap, d_gap = (46, 38) if tight else (54, 44)
    for i, p in enumerate(points):
        top = y + i * (row_h + gap)
        _rr(draw, [PAD, top, W - PAD, top + row_h], 30, fill=t["panel"], outline=t["line"], width=2)
        _text(draw, (PAD + 64, top + row_h / 2), f"{i + 1:02d}", num_font, t["accent2"], anchor="mm")
        tx = PAD + 132
        tmax = W - PAD - 32 - tx
        if tight:   # 잘린 줄은 …로 표시
            t_lines = [_ellipsize(draw, p.get("title", ""), title_font, tmax)]
            d_lines = [_ellipsize(draw, p.get("desc", ""), desc_font, tmax)]
        else:
            t_lines = _wrap(draw, p.get("title", ""), title_font, tmax)[:2]
            d_lines = _wrap(draw, p.get("desc", ""), desc_font, tmax)[:2]
        block = len(t_lines) * t_gap + len(d_lines) * d_gap + 6
        ty = top + (row_h - block) / 2 - 4
        for ln in t_lines:
            _text(draw, (tx, ty), ln, title_font, t["ink"])
            ty += t_gap
        ty += 6
        for ln in d_lines:
            _text(draw, (tx, ty), ln, desc_font, t["sub"])
            ty += d_gap
    y += n * row_h + (n - 1) * gap + gap

    # ④ 행동 박스 (주황 테두리)
    if action_lines:
        _rr(draw, [PAD, y, W - PAD, y + action_h], 32, fill=(38, 22, 14), outline=t["accent"], width=3)
        _pill(draw, PAD + 36, y + 28, data.get("action_label", "바로 해보기"), _font(28, b"ExtraBold"), 28,
              (255, 255, 255), bg=t["accent"], padx=18, pady=6)
        ay = y + 30 + 48 + 14
        for ln in action_lines:
            _text(draw, (PAD + 36, ay), ln, action_font, t["ink"])
            ay += 54

    # ⑤ 하단: 출처 + 계정
    foot_y = H - 92
    draw.line([PAD * S, (foot_y - 26) * S, (W - PAD) * S, (foot_y - 26) * S], fill=t["line"], width=2 * S)
    brand_font = _font(32, b"ExtraBold")
    bw = _tw(draw, HANDLE, brand_font)
    source = data.get("source", "")
    if source:
        src_font = _font(28, b"Medium")
        _text(draw, (PAD, foot_y), _ellipsize(draw, f"출처 {source}", src_font, inner_w - bw - 40), src_font, t["sub"])
    _text(draw, (W - PAD - bw, foot_y - 2), HANDLE, brand_font, t["ink"])
    return _save(img, out_path)


def save_jpeg_copy(png_path, quality=93):
    """PNG 카드의 JPEG 사본을 같은 폴더에 만들고 경로를 돌려준다 (인스타는 JPEG만 받는다)."""
    jpg_path = os.path.splitext(png_path)[0] + ".jpg"
    Image.open(png_path).convert("RGB").save(jpg_path, "JPEG", quality=quality, optimize=True)
    return jpg_path


def generate_card(headline, eyebrow, out_path):
    """예전 호출 방식(제목+라벨) 호환용 — 포인트 없이 헤드라인만 있는 카드를 만든다."""
    return generate_news_card({"category": eyebrow, "headline": headline}, out_path)


# ── 여러 장 넘겨보는 '복붙 프롬프트' 카드 (크림) ─────────────────────
#
# 저장해두고 꺼내 쓰는 용도라, 한 장에 다 우겨넣지 않고 4장으로 나눈다.
#   1) 표지     : 시리즈 번호 + 무엇을 해결하는지
#   2) 프롬프트 : 그대로 복사해 쓸 문장 (본문 글에도 같은 문장이 들어간다)
#   3) 결과 예시 : "이렇게 나와요"
#   4) 팁·팔로우 : 한 가지 더 + 팔로우 안내

SLIDE_TOTAL = 4


def _slide_start(cover=False):
    t = CREAM
    img = Image.new("RGB", (W * S, H * S), t["bg"])
    draw = ImageDraw.Draw(img)
    stars = [(84, 1212, 12), (1000, 1198, 16)]
    if cover:
        stars += [(92, 176, 16), (990, 262, 12)]
    for x, y, r in stars:
        _sparkle(draw, x, y, r, t["accent"])
    _text(draw, (W / 2, 92), "소담쌤의 AI 쉬운가이드", _font(28, b"Medium"), t["sub"], anchor="mm")
    return img, draw, t


def _slide_finish(img, draw, t, page, out_path):
    _text(draw, (W / 2, H - 62), f"—  {HANDLE}  —", _font(30, b"Bold"), t["sub"], anchor="mm")
    _text(draw, (W - PAD, H - 62), f"{page} / {SLIDE_TOTAL}", _font(28, b"Medium"), t["sub"], anchor="rm")
    return _save(img, out_path)


def _slide_panel_text(data_text, badge, title, out_path, page, note="", strong=True):
    """'제목 + 큰 패널 안 본문' 형태의 슬라이드(프롬프트·결과 예시 공용)."""
    img, draw, t = _slide_start()
    inner_w = W - PAD * 2
    y = 156
    _pill(draw, PAD, y, badge, _font(30, b"ExtraBold"), 30, (255, 255, 255), bg=t["accent"], padx=22, pady=8)
    y += 82
    title_font, title_lines, title_size = _fit(draw, title, b"Jua", inner_w, 2, (64, 58, 52))
    y = _headline(draw, title_lines, title_font, title_size, PAD, y, t, 1.26) + 30

    max_bottom = H - 150 - (56 if note else 0)
    pad = 44
    font, lines, line_h = _fit_block(draw, data_text, b"Bold", inner_w - pad * 2,
                                     max_bottom - y - pad * 2, (46, 42, 38, 34, 30, 27))
    panel_bottom = min(max_bottom, y + pad * 2 + len(lines) * line_h + 8)
    _rr(draw, [PAD, y, W - PAD, panel_bottom], 34, fill=t["panel"],
        outline=t["accent"] if strong else t["line"], width=4 if strong else 3)
    ty = y + pad
    for ln in lines:
        _text(draw, (PAD + pad, ty), ln, font, t["ink"])
        ty += line_h
    if note:
        note_font = _font(28, b"Medium")
        _text(draw, (PAD, panel_bottom + 20), _ellipsize(draw, note, note_font, inner_w), note_font, t["sub"])
    return _slide_finish(img, draw, t, page, out_path)


def generate_prompt_slides(data, out_dir, stem="slide"):
    """복붙 프롬프트 카드 4장을 out_dir에 만들고 파일 경로 목록을 돌려준다."""
    os.makedirs(out_dir, exist_ok=True)
    paths = [os.path.join(out_dir, f"{stem}_{i}.png") for i in range(1, SLIDE_TOTAL + 1)]
    inner_w = W - PAD * 2

    # 1) 표지 — 가운데 정렬, 첫 줄에 형광펜
    img, draw, t = _slide_start(cover=True)
    _pill(draw, 0, 176, data.get("series", "복붙 프롬프트"), _font(36, b"ExtraBold"), 36, t["bg"], bg=t["ink"], center_w=W)
    head_font, head_lines, head_size = _fit(draw, data["headline"], b"Jua", inner_w - 40, 4, (112, 100, 90, 80, 70, 62))
    block = len(head_lines) * head_size * 1.24
    y = 300 + max(0, (440 - block) / 2)
    y = _headline(draw, head_lines, head_font, head_size, PAD + 20, y, t, 1.24, center_w=inner_w - 40, marker_line=0) + 22
    sit = (data.get("situation") or "").strip()
    if sit:
        sit_font, sit_lines, sit_h = _fit_block(draw, sit, b"Medium", inner_w - 60, 150, (40, 36, 32), spacing=1.45)
        for ln in sit_lines:
            _text(draw, (W / 2, y + sit_h / 2), ln, sit_font, t["sub"], anchor="mm")
            y += sit_h
    _mascot(draw, W / 2, 1040, 92, t)
    _pill(draw, 0, 1170, "옆으로 넘겨서 확인하세요  →", _font(30, b"ExtraBold"), 30, t["ink"], outline=t["ink"], center_w=W)
    _slide_finish(img, draw, t, 1, paths[0])

    # 2) 프롬프트
    _slide_panel_text(data["prompt"], "복사해서 쓰세요", "이 문장을 그대로 붙여넣으세요", paths[1], 2,
                      note="글 본문에도 같은 문장이 있어요. 거기서 복사하셔도 돼요")
    # 3) 결과 예시
    _slide_panel_text(data["example"], "이렇게 나와요", "이런 결과가 나와요", paths[2], 3,
                      note="※ 예시예요. 넣는 내용에 따라 결과는 달라져요", strong=False)

    # 4) 팁 + 팔로우
    img, draw, t = _slide_start()
    y = 170
    tip = (data.get("tip") or "").strip()
    tip_font, tip_lines, tip_h = _fit_block(draw, tip, b"Bold", inner_w - 72, 380, (46, 42, 38, 34), spacing=1.5)
    box_h = 72 + len(tip_lines) * tip_h + 40
    _rr(draw, [PAD, y, W - PAD, y + box_h], 34, fill=t["tip"])
    _pill(draw, PAD + 36, y + 28, "더 잘 쓰는 팁", _font(28, b"ExtraBold"), 28, (255, 255, 255), bg=t["ink"], padx=18, pady=6)
    ty = y + 28 + 48 + 20
    for ln in tip_lines:
        _text(draw, (PAD + 36, ty), ln, tip_font, t["ink"])
        ty += tip_h
    y += box_h

    room = (H - 150) - y          # 팁 상자 아래 남는 높이 안에 마스코트·문구·버튼을 고르게 놓는다
    mid = y + room / 2
    r = max(46, min(88, room * 0.13))
    _mascot(draw, W / 2, mid - room * 0.27, r, t)
    f1 = _font(60 if room > 520 else 50, b"Jua")
    line1 = "저장해두고 필요할 때 꺼내 쓰세요"
    if _tw(draw, line1, f1) > inner_w - 20:
        line1 = "저장해두고 꺼내 쓰세요"
    _text(draw, (W / 2, mid + room * 0.03), line1, f1, t["ink"], anchor="mm")
    _text(draw, (W / 2, mid + room * 0.03 + 70), "매일 아침·저녁, 쉬운 AI 활용법을 올려요", _font(32, b"Medium"), t["sub"], anchor="mm")
    _pill(draw, 0, min(H - 150 - 96, mid + room * 0.27), f"{HANDLE} 팔로우", _font(40, b"ExtraBold"), 40,
          (255, 255, 255), bg=t["accent"], padx=40, pady=14, center_w=W)
    _slide_finish(img, draw, t, 4, paths[3])
    return paths
