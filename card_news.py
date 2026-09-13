# -*- coding: utf-8 -*-
"""'소담 AI 랩' 카드뉴스 이미지 생성기.

주 1회 발행되는 'briefing'(AI 브리핑·정책 변화) 글에 붙일 1080x1080 정사각 카드를
만든다. 색상·레이아웃 언어는 소담쌤이 trend-blog-factory/sodam_image.py에 정리해둔
공식 브랜드 가이드(BRAND 팔레트, 상단 포인트 바 + 중앙 제목 + 하단 로고 구조)를 그대로
따른다.

다만 폰트는 다르다: sodam_image.py는 로컬 PC(윈도우 맑은 고딕)에서 도는 걸 전제로
시스템 폰트를 찾아 쓰지만, 이 파일은 GitHub Actions(Linux) 러너에서 실행되어 맑은
고딕이 없다. 그래서 저장소에 함께 담아둔 Noto Sans KR(가변 폰트, 무료 라이선스)을
직접 로드해 같은 두께감(Bold/Regular)을 낸다.
"""
import os

from PIL import Image, ImageDraw, ImageFont

CANVAS_SIZE = 1080
MARGIN_X = 110
TITLE_AREA_TOP = 320
TITLE_AREA_BOTTOM = 820

FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "NotoSansKR-Variable.ttf")

# 소담 공식 브랜드 팔레트 (sodam_image.py의 BRAND 딕셔너리와 동일)
BRAND = {
    "green": (37, 94, 50),        # #255E32 메인 그린
    "green_sub": (91, 154, 66),   # #5B9A42 서브 그린
    "gold": (212, 178, 111),      # #D4B26F 포인트 골드
    "ivory": (248, 249, 246),     # #F8F9F6 배경 아이보리
    "ink": (23, 40, 30),          # #17281E 본문 먹색
    "gray": (92, 107, 97),        # #5C6B61 보조 회색
}


def _font(size, weight=b"Bold"):
    """가변 폰트에서 지정한 굵기(named instance)를 골라 로드한다."""
    try:
        f = ImageFont.truetype(FONT_PATH, size)
        f.set_variation_by_name(weight)
        return f
    except Exception:
        return ImageFont.load_default()


def _text_width(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap_title(draw, text, max_width, font_size):
    """실제 폰트 폭을 재서 어절 단위로 줄바꿈한다 (글자 수 어림짐작 대신 정확히 측정)."""
    font = _font(font_size, b"Bold")
    words = text.split()
    lines, cur = [], ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if _text_width(draw, candidate, font) <= max_width:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return font, lines


def _fit_title(draw, text, max_width, max_height):
    """제목 영역에 들어갈 때까지 폰트 크기를 단계적으로 줄인다."""
    for size in (76, 68, 60, 52, 46):
        font, lines = _wrap_title(draw, text, max_width, size)
        line_height = int(size * 1.5)
        if len(lines) <= 4 and line_height * len(lines) <= max_height:
            return font, lines, line_height
    return font, lines, line_height


def generate_card(headline, eyebrow, out_path):
    """headline(카드 제목)과 eyebrow(상단 라벨, 예: 'AI 정책 브리핑')로 카드뉴스 PNG를 만든다.

    sodam_image.py의 make_thumbnail 구조(상단 포인트 바 → 중앙 제목 → 하단 로고)를
    라이트 톤(아이보리 배경)으로 재현한다. 반환값은 out_path 그대로.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    img = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), BRAND["ivory"])
    draw = ImageDraw.Draw(img)
    center_x = CANVAS_SIZE // 2

    # 상단 포인트 바 (sodam_image.py와 동일한 브랜드 시그니처)
    draw.rectangle([0, 0, CANVAS_SIZE, 14], fill=BRAND["green"])

    # 제목 (자동 줄바꿈 + 자동 크기)
    max_width = CANVAS_SIZE - MARGIN_X * 2
    max_height = TITLE_AREA_BOTTOM - TITLE_AREA_TOP
    font, lines, line_height = _fit_title(draw, headline, max_width, max_height)
    block_height = line_height * len(lines)
    start_y = TITLE_AREA_TOP + max(0, (max_height - block_height) // 2)
    for i, line in enumerate(lines):
        y = start_y + i * line_height
        w = _text_width(draw, line, font)
        draw.text((center_x - w / 2, y), line, font=font, fill=BRAND["ink"])

    # 상단 라벨 (코너 이름) — 제목 위, 골드 포인트
    eyebrow_font = _font(34, b"Bold")
    ew = _text_width(draw, eyebrow, eyebrow_font)
    draw.text((center_x - ew / 2, TITLE_AREA_TOP - 90), eyebrow, font=eyebrow_font, fill=BRAND["gold"])

    # 하단 브랜드 표기
    logo_font = _font(30, b"Bold")
    logo = "소담 AI 랩"
    lw = _text_width(draw, logo, logo_font)
    draw.text((center_x - lw / 2, CANVAS_SIZE - 110), logo, font=logo_font, fill=BRAND["green_sub"])

    img.save(out_path, "PNG", optimize=True)
    return out_path
