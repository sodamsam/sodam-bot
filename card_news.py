# -*- coding: utf-8 -*-
"""'소담 AI 랩' 뉴스 카드 이미지 생성기 (v2 — 보랏빛 한눈에 보기 카드).

주 1회 발행되는 'briefing' 글에 붙는 카드다. 이전 버전은 제목 한 줄뿐이라 정보가
빈약했다. 이제 한 장 안에서 아래 순서로 "한눈에" 읽히게 만든다.

  ① 상단 뱃지 : 분류(신규 출시 / 신청 가능 / 정책 변경) + 기준일
  ② 헤드라인  : 가장 큰 글씨, 최대 3줄
  ③ 핵심 3가지: 번호 + 짧은 제목 + 한 줄 설명
  ④ 행동 박스 : "바로 해보기" 또는 "신청 방법" (라벤더 솔리드 박스)
  ⑤ 하단      : 출처 / @sodam_ai_lab

캔버스는 1080x1350(4:5). 폰트는 저장소에 담긴 Noto Sans KR 가변 폰트를 쓴다.
카드에 들어갈 데이터(dict)는 auto_post.py의 브리핑 초안 생성 단계가 만든다.

카드 데이터 형식:
  {
    "category": "신규 출시",            # 뱃지 문구 (8자 이내 권장)
    "date": "10월 6일",                 # 기준일 (기사 발행일, 코드에서 계산)
    "headline": "구글 문서 안에서 Claude를 쓸 수 있어요",
    "points": [{"title": "...", "desc": "..."}, ...],   # 1~3개
    "action_label": "바로 해보기",      # 또는 "신청 방법", "확인할 곳"
    "action": "...",
    "source": "9to5Google (10/6)",
  }
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1350
PAD = 76

FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "NotoSansKR-Variable.ttf")

# 보랏빛 트렌디 팔레트
PALETTE = {
    "bg_top": (22, 8, 56),         # #160838 깊은 보라
    "bg_mid": (59, 20, 120),       # #3B1478
    "bg_bottom": (96, 38, 190),    # #6026BE
    "glow": (167, 91, 255),        # 네온 글로우
    "white": (255, 255, 255),
    "lavender": (221, 205, 255),   # #DDCDFF 보조 텍스트
    "lilac": (196, 168, 255),      # #C4A8FF 포인트
    "panel": (255, 255, 255, 30),  # 반투명 패널
    "panel_line": (255, 255, 255, 60),
    "action_bg": (233, 220, 255),  # #E9DCFF 행동 박스
    "action_ink": (38, 14, 84),    # #260E54 행동 박스 글씨
    "badge_bg": (255, 255, 255),
    "badge_ink": (84, 30, 170),
}


def _font(size, weight=b"Bold"):
    try:
        f = ImageFont.truetype(FONT_PATH, size)
        f.set_variation_by_name(weight)
        return f
    except Exception:
        return ImageFont.load_default()


def _tw(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


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
    lines = _wrap(draw, text, font, max_width)[:max_lines]
    return font, lines, sizes[-1]


def _background():
    """세로 보라 그라데이션 + 네온 글로우 두 개."""
    grad = Image.new("RGB", (1, H))
    px = grad.load()
    top, mid, bot = PALETTE["bg_top"], PALETTE["bg_mid"], PALETTE["bg_bottom"]
    for y in range(H):
        t = y / (H - 1)
        if t < 0.55:
            k = t / 0.55
            a, b = top, mid
        else:
            k = (t - 0.55) / 0.45
            a, b = mid, bot
        px[0, y] = tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))
    img = grad.resize((W, H)).convert("RGBA")

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 520, -260, W + 220, 480], fill=PALETTE["glow"] + (150,))
    gd.ellipse([-320, H - 520, 380, H + 160], fill=(120, 70, 255, 120))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    return Image.alpha_composite(img, glow)


def _sparkle(draw, cx, cy, r, fill):
    """네 갈래 별 장식."""
    draw.polygon(
        [(cx, cy - r), (cx + r * 0.28, cy - r * 0.28), (cx + r, cy),
         (cx + r * 0.28, cy + r * 0.28), (cx, cy + r), (cx - r * 0.28, cy + r * 0.28),
         (cx - r, cy), (cx - r * 0.28, cy - r * 0.28)],
        fill=fill,
    )


def generate_news_card(data, out_path):
    """카드 데이터(dict)로 보라색 뉴스 카드 PNG를 만들어 out_path에 저장한다."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    img = _background()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    draw = ImageDraw.Draw(img)   # 글자 크기 측정 전용 (실제 글자는 패널을 합성한 뒤 그린다)
    texts = []                   # (xy, 문구, 폰트, 색) — 패널 위에 나중에 그린다
    inner_w = W - PAD * 2

    # ── ① 상단 뱃지 ─────────────────────────────────────────
    y = 84
    badge_font = _font(34, b"ExtraBold")
    badge_text = data.get("category", "AI 뉴스")
    bw = _tw(draw, badge_text, badge_font) + 56
    od.rounded_rectangle([PAD, y, PAD + bw, y + 64], radius=32, fill=PALETTE["badge_bg"] + (255,))
    texts.append(((PAD + 28, y + 9), badge_text, badge_font, PALETTE["badge_ink"]))

    date_font = _font(32, b"Medium")
    date_text = data.get("date", "")
    if date_text:
        label = f"{date_text} 기준"
        dw = _tw(draw, label, date_font)
        texts.append(((W - PAD - dw, y + 12), label, date_font, PALETTE["lavender"]))

    # ── ② 헤드라인 ─────────────────────────────────────────
    y = 200
    head_font, head_lines, head_size = _fit(
        draw, data["headline"], b"Black", inner_w, 3, (78, 70, 62, 56)
    )
    line_h = int(head_size * 1.28)
    for i, line in enumerate(head_lines):
        texts.append(((PAD, y + i * line_h), line, head_font, PALETTE["white"]))
    y += line_h * len(head_lines) + 40
    _sparkle(od, W // 2 + 40, 118, 22, PALETTE["lilac"] + (255,))
    _sparkle(od, W // 2 + 92, 96, 10, PALETTE["lavender"] + (255,))

    # ── ③ 핵심 포인트 (최대 3개) ───────────────────────────
    points = (data.get("points") or [])[:3]
    action = (data.get("action") or "").strip()

    # 행동 박스 높이를 먼저 계산해 남는 공간을 포인트에 나눠 준다
    action_font = _font(38, b"Bold")
    action_label_font = _font(30, b"ExtraBold")
    action_lines = _wrap(draw, action, action_font, inner_w - 72) if action else []
    action_lines = action_lines[:3]
    action_h = 0
    if action_lines:
        action_h = 40 + 44 + len(action_lines) * 54 + 34
    footer_h = 120
    gap = 26
    avail = H - y - footer_h - (action_h + gap if action_h else 0) - 20

    n = max(1, len(points))
    row_h = min(230, (avail - gap * (n - 1)) // n)

    tight = row_h < 176   # 헤드라인이 길어 칸이 낮아지면 글자를 줄이고 한 줄씩만 쓴다
    title_font = _font(38 if tight else 42, b"ExtraBold")
    desc_font = _font(30 if tight else 33, b"Medium")
    num_font = _font(40, b"Black")
    t_gap, d_gap = (46, 38) if tight else (54, 44)
    max_t_lines, max_d_lines = (1, 1) if tight else (2, 2)
    for i, p in enumerate(points):
        top = y + i * (row_h + gap)
        od.rounded_rectangle([PAD, top, W - PAD, top + row_h], radius=34,
                             fill=PALETTE["panel"], outline=PALETTE["panel_line"], width=2)
        cx, cy = PAD + 62, top + row_h // 2
        od.ellipse([cx - 34, cy - 34, cx + 34, cy + 34], fill=PALETTE["lilac"] + (255,))
        nw = _tw(draw, str(i + 1), num_font)
        texts.append(((cx - nw / 2, cy - 30), str(i + 1), num_font, PALETTE["action_ink"]))

        tx = PAD + 124
        tmax = W - PAD - 32 - tx
        t_lines = _wrap(draw, p.get("title", ""), title_font, tmax)[:max_t_lines]
        d_lines = _wrap(draw, p.get("desc", ""), desc_font, tmax)[:max_d_lines]
        if tight:   # 잘린 줄은 …로 표시
            t_lines = [_ellipsize(draw, p.get("title", ""), title_font, tmax)]
            d_lines = [_ellipsize(draw, p.get("desc", ""), desc_font, tmax)]
        block = len(t_lines) * t_gap + len(d_lines) * d_gap + 6
        ty = top + (row_h - block) // 2
        for ln in t_lines:
            texts.append(((tx, ty), ln, title_font, PALETTE["white"]))
            ty += t_gap
        ty += 6
        for ln in d_lines:
            texts.append(((tx, ty), ln, desc_font, PALETTE["lavender"]))
            ty += d_gap
    y += n * row_h + (n - 1) * gap + gap

    # ── ④ 행동 박스 ────────────────────────────────────────
    if action_lines:
        od.rounded_rectangle([PAD, y, W - PAD, y + action_h], radius=36,
                             fill=PALETTE["action_bg"] + (255,))
        label = data.get("action_label", "바로 해보기")
        lw = _tw(draw, label, action_label_font) + 36
        od.rounded_rectangle([PAD + 36, y + 30, PAD + 36 + lw, y + 30 + 48], radius=24,
                             fill=PALETTE["badge_ink"] + (255,))
        texts.append(((PAD + 36 + 18, y + 35), label, action_label_font, PALETTE["white"]))
        ay = y + 30 + 48 + 18
        for ln in action_lines:
            texts.append(((PAD + 36, ay), ln, action_font, PALETTE["action_ink"]))
            ay += 54

    # 패널·도형을 먼저 합성하고, 그 위에 글자를 올린다
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    for xy, text, font, fill in texts:
        draw.text(xy, text, font=font, fill=fill)

    # ── ⑤ 하단 ─────────────────────────────────────────────
    foot_y = H - 92
    draw.line([PAD, foot_y - 26, W - PAD, foot_y - 26], fill=(255, 255, 255, 70), width=2)
    src_font = _font(28, b"Medium")
    brand_font = _font(32, b"ExtraBold")
    source = data.get("source", "")
    brand = "@sodam_ai_lab"
    bw2 = _tw(draw, brand, brand_font)
    if source:
        src_text = _ellipsize(draw, f"출처 {source}", src_font, W - PAD * 2 - bw2 - 40)
        draw.text((PAD, foot_y), src_text, font=src_font, fill=PALETTE["lavender"])
    draw.text((W - PAD - bw2, foot_y - 2), brand, font=brand_font, fill=PALETTE["white"])

    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def generate_card(headline, eyebrow, out_path):
    """예전 호출 방식(제목+라벨) 호환용 — 포인트 없이 헤드라인만 있는 카드를 만든다."""
    return generate_news_card({"category": eyebrow, "headline": headline}, out_path)


# ── 여러 장 넘겨보는 '복붙 프롬프트' 카드 ───────────────────────────
#
# 저장해두고 꺼내 쓰는 용도라, 한 장에 다 우겨넣지 않고 4장으로 나눈다.
#   1) 표지     : 시리즈 번호 + 무엇을 해결하는지
#   2) 프롬프트 : 그대로 복사해 쓸 문장 (본문 글에도 같은 문장이 들어간다)
#   3) 결과 예시 : "이렇게 나와요"
#   4) 팁·팔로우 : 한 가지 더 + 팔로우 안내
#
# 카드 데이터 형식:
#   {"series": "복붙 프롬프트 #12", "headline": "...", "situation": "...",
#    "prompt": "...", "example": "...", "tip": "..."}

SLIDE_TOTAL = 4


def _slide_start(badge_text):
    img = _background()
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    draw = ImageDraw.Draw(img)
    texts = []
    badge_font = _font(34, b"ExtraBold")
    bw = _tw(draw, badge_text, badge_font) + 56
    od.rounded_rectangle([PAD, 84, PAD + bw, 148], radius=32, fill=PALETTE["badge_bg"] + (255,))
    texts.append(((PAD + 28, 93), badge_text, badge_font, PALETTE["badge_ink"]))
    _sparkle(od, W - PAD - 40, 116, 22, PALETTE["lilac"] + (255,))
    _sparkle(od, W - PAD - 92, 94, 10, PALETTE["lavender"] + (255,))
    return img, overlay, od, draw, texts


def _slide_finish(img, overlay, texts, page, out_path):
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)
    for xy, text, font, fill in texts:
        draw.text(xy, text, font=font, fill=fill)
    foot_y = H - 92
    draw.line([PAD, foot_y - 26, W - PAD, foot_y - 26], fill=(255, 255, 255, 70), width=2)
    page_font = _font(30, b"Medium")
    brand_font = _font(32, b"ExtraBold")
    draw.text((PAD, foot_y), f"{page} / {SLIDE_TOTAL}", font=page_font, fill=PALETTE["lavender"])
    brand = "@sodam_ai_lab"
    draw.text((W - PAD - _tw(draw, brand, brand_font), foot_y - 2), brand, font=brand_font, fill=PALETTE["white"])
    img.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def _fit_block(draw, text, weight, max_width, max_height, sizes, spacing=1.5):
    """여러 줄 글(줄바꿈 포함)이 max_height 안에 들어가는 가장 큰 글자 크기를 고른다."""
    for size in sizes:
        font = _font(size, weight)
        lines = []
        for para in (text or "").split("\n"):
            lines += _wrap(draw, para, font, max_width) if para.strip() else [""]
        line_h = int(size * spacing)
        if len(lines) * line_h <= max_height:
            return font, lines, line_h
    font = _font(sizes[-1], weight)
    line_h = int(sizes[-1] * spacing)
    lines = []
    for para in (text or "").split("\n"):
        lines += _wrap(draw, para, font, max_width) if para.strip() else [""]
    keep = max(1, max_height // line_h)
    if len(lines) > keep:
        lines = lines[:keep]
        lines[-1] = _ellipsize(draw, lines[-1] + "…", font, max_width)
    return font, lines, line_h


def _slide_panel_text(data_text, badge, title, out_path, page, note=""):
    """'제목 + 큰 패널 안 본문' 형태의 슬라이드(프롬프트·결과 예시 공용)."""
    img, overlay, od, draw, texts = _slide_start(badge)
    inner_w = W - PAD * 2
    title_font, title_lines, title_size = _fit(draw, title, b"Black", inner_w, 2, (60, 54, 48))
    y = 196
    for i, ln in enumerate(title_lines):
        texts.append(((PAD, y + i * int(title_size * 1.28)), ln, title_font, PALETTE["white"]))
    y += int(title_size * 1.28) * len(title_lines) + 28

    max_bottom = H - 190 - (60 if note else 0)
    pad = 44
    font, lines, line_h = _fit_block(draw, data_text, b"Bold", inner_w - pad * 2,
                                     max_bottom - y - pad * 2, (46, 42, 38, 34, 30, 27))
    panel_bottom = min(max_bottom, y + pad * 2 + len(lines) * line_h + 8)
    od.rounded_rectangle([PAD, y, W - PAD, panel_bottom], radius=36,
                         fill=PALETTE["panel"], outline=PALETTE["panel_line"], width=2)
    ty = y + pad
    for ln in lines:
        texts.append(((PAD + pad, ty), ln, font, PALETTE["white"]))
        ty += line_h
    if note:
        note_font = _font(28, b"Medium")
        texts.append(((PAD, panel_bottom + 22), _ellipsize(draw, note, note_font, inner_w), note_font, PALETTE["lavender"]))
    return _slide_finish(img, overlay, texts, page, out_path)


def generate_prompt_slides(data, out_dir, stem="slide"):
    """복붙 프롬프트 카드 4장을 out_dir에 만들고 파일 경로 목록을 돌려준다."""
    os.makedirs(out_dir, exist_ok=True)
    paths = [os.path.join(out_dir, f"{stem}_{i}.png") for i in range(1, SLIDE_TOTAL + 1)]
    inner_w = W - PAD * 2

    # 1) 표지
    img, overlay, od, draw, texts = _slide_start(data.get("series", "복붙 프롬프트"))
    head_font, head_lines, head_size = _fit(draw, data["headline"], b"Black", inner_w, 4, (92, 84, 76, 68, 60))
    line_h = int(head_size * 1.28)
    y = 330
    for i, ln in enumerate(head_lines):
        texts.append(((PAD, y + i * line_h), ln, head_font, PALETTE["white"]))
    y += line_h * len(head_lines) + 40
    sit = (data.get("situation") or "").strip()
    if sit:
        sit_font, sit_lines, sit_h = _fit_block(draw, sit, b"Medium", inner_w, 190, (42, 38, 34), spacing=1.45)
        for ln in sit_lines:
            texts.append(((PAD, y), ln, sit_font, PALETTE["lavender"]))
            y += sit_h
    hint = "옆으로 넘겨서 확인하세요  →"
    hint_font = _font(36, b"ExtraBold")
    hw = _tw(draw, hint, hint_font) + 64
    od.rounded_rectangle([PAD, H - 290, PAD + hw, H - 290 + 84], radius=42, fill=PALETTE["action_bg"] + (255,))
    texts.append(((PAD + 32, H - 290 + 17), hint, hint_font, PALETTE["action_ink"]))
    _slide_finish(img, overlay, texts, 1, paths[0])

    # 2) 프롬프트
    _slide_panel_text(data["prompt"], "복사해서 쓰세요", "이 문장을 그대로 붙여넣으세요", paths[1], 2,
                      note="글 본문에도 같은 문장이 있어요. 거기서 복사하셔도 돼요")
    # 3) 결과 예시
    _slide_panel_text(data["example"], "이렇게 나와요", "이런 결과가 나와요", paths[2], 3,
                      note="※ 예시예요. 넣는 내용에 따라 결과는 달라져요")

    # 4) 팁 + 팔로우
    img, overlay, od, draw, texts = _slide_start("한 가지 더")
    y = 210
    tip = (data.get("tip") or "").strip()
    tip_font, tip_lines, tip_h = _fit_block(draw, tip, b"Bold", inner_w - 72, 420, (46, 42, 38, 34), spacing=1.5)
    box_h = 72 + len(tip_lines) * tip_h + 40
    od.rounded_rectangle([PAD, y, W - PAD, y + box_h], radius=36, fill=PALETTE["action_bg"] + (255,))
    label_font = _font(30, b"ExtraBold")
    label = "더 잘 쓰는 팁"
    lw = _tw(draw, label, label_font) + 36
    od.rounded_rectangle([PAD + 36, y + 28, PAD + 36 + lw, y + 28 + 48], radius=24, fill=PALETTE["badge_ink"] + (255,))
    texts.append(((PAD + 54, y + 33), label, label_font, PALETTE["white"]))
    ty = y + 28 + 48 + 22
    for ln in tip_lines:
        texts.append(((PAD + 36, ty), ln, tip_font, PALETTE["action_ink"]))
        ty += tip_h
    y += box_h + 44

    fh = min(H - 190 - y, 400)
    od.rounded_rectangle([PAD, y, W - PAD, y + fh], radius=36, fill=PALETTE["panel"], outline=PALETTE["panel_line"], width=2)
    f1 = _font(46, b"Black")
    f2 = _font(34, b"Medium")
    f3 = _font(44, b"ExtraBold")
    cx = W // 2
    line1 = "저장해두고 필요할 때 꺼내 쓰세요"
    if _tw(draw, line1, f1) > inner_w - 40:
        line1 = "저장해두고 꺼내 쓰세요"
    texts.append(((cx - _tw(draw, line1, f1) / 2, y + 62), line1, f1, PALETTE["white"]))
    sub = "매일 아침·저녁, 쉬운 AI 활용법을 올려요"
    texts.append(((cx - _tw(draw, sub, f2) / 2, y + 144), sub, f2, PALETTE["lavender"]))
    handle = "@sodam_ai_lab 팔로우"
    texts.append(((cx - _tw(draw, handle, f3) / 2, y + fh - 118), handle, f3, PALETTE["lilac"]))
    _slide_finish(img, overlay, texts, 4, paths[3])
    return paths
