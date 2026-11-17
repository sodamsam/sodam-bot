# -*- coding: utf-8 -*-
"""AI 티 지우기 — 소담쌤의 'AI 티 지우기' 스킬(10가지 규칙)을 자동 글쓰기에 적용한다.

세 가지를 한다.
  1) rules_prompt() : 글을 쓰기 전에 AI에게 주는 규칙(스킬의 10가지 규칙 그대로)
  2) clean(text)    : 뜻을 바꾸지 않는 기계적인 손질(긴 대시, 굵게 표시, 챗봇식 인사, 상투적인 문장 첫머리)
  3) find_tells(text): 남아 있는 AI 티를 찾아 이름 목록으로 돌려준다(많으면 다시 쓰게 하는 데 쓴다)

의미·사실·숫자·경험담은 건드리지 않는다. AI 탐지기를 피한다고 약속하는 도구가 아니다(스킬의 '주의').
규칙을 바꾸고 싶으면 이 파일 옆 style/ai_tell_rules.md 를 만들어 고치면 된다(있으면 그 내용을 쓴다).
소담봇·쿠팡100·더하루가 같은 파일을 복사해 쓴다 — 고칠 때는 소담봇의 이 파일을 고치고 나머지에 복사한다.
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
RULES_FILE = os.path.join(_HERE, "style", "ai_tell_rules.md")

DEFAULT_RULES = """[AI 티 지우기 — 반드시 지킬 것]
1. 긴 대시(—)를 쓰지 않는다. 쉼표, 마침표, 줄바꿈, 괄호로 바꾼다.
2. "~가 아니라 ~입니다"처럼 아무도 하지 않은 말을 부정하고 뒤집는 문장을 쓰지 않는다. 하고 싶은 말을 바로 쓴다.
3. 형용사·장점·예시를 세 개씩 묶지 않는다. 두 개로 줄이고 빈자리에는 숫자나 실제 장면을 넣는다.
4. "좋은 질문이에요", "안녕하세요 여러분" 같은 챗봇식 인사와 맞장구를 넣지 않는다.
5. 글 끝에서 앞 내용을 다시 요약하지 않는다. 읽는 사람이 바로 할 행동 하나로 끝낸다.
6. 굵은 글씨(**)는 쓰지 않는다.
7. 소제목·항목마다 이모지를 붙이지 않는다. 쓰더라도 글 전체에서 한두 개만.
8. "혁신적인", "획기적인", "완벽한" 같은 광고성 단어 대신 숫자나 사실을 쓴다.
9. 근거 없이 의미를 키우는 꼬리 문장("~를 보여줍니다", "~의 중요성을 알 수 있습니다")을 쓰지 말고 실제 장면 하나를 쓴다.
10. "전문가들은", "많은 사람들이" 같은 모호한 출처를 쓰지 않는다. 누가, 언제, 몇 개인지 알 때만 쓰고, 모르면 그 문장을 빼라(숫자를 지어내지 마라).
덧붙여: 같은 어미로 네 문장 이상 잇지 말고 "~요", "~죠"를 섞는다."""

# 문장 첫머리에서 지워도 뜻이 달라지지 않는 상투어
_OPENERS = ("또한", "뿐만 아니라", "더 나아가", "결론적으로", "요약하자면", "정리하자면", "이처럼", "아울러")
_OPENER_RE = re.compile(r"(^|(?<=[.!?…\n])[ \t]*)(?:" + "|".join(map(re.escape, _OPENERS)) + r")[ \t]*,?[ \t]+", re.M)
# 챗봇식 인사·맞장구(문장 통째로 지운다) — 규칙 4
_GREETING_RE = re.compile(
    r"(?:^|(?<=[.!?\n]))[ \t]*(?:좋은 질문이에요|좋은 질문입니다|안녕하세요[,\s]*여러분|여러분[,\s]*안녕하세요)[^.!?\n]*[.!?]?[ \t]*",
    re.M)

_ENDING_RE = re.compile(r"(습니다|입니다|됩니다|합니다|있어요|해요|세요|이에요|예요|죠|거든요|니다)[.!?]?$")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

# (이름, 정규식, 이 횟수 이상이면 AI 티로 본다) — 번호는 스킬의 규칙 번호
_TELLS = [
    ("1 긴 대시", re.compile(r"[—–]"), 1),
    ("2 부정 후 뒤집기", re.compile(r"(?:가|이)\s?아니라[^.!?\n]{0,30}(?:입니다|이에요|예요|거든요|죠)"), 1),
    ("4 챗봇식 인사", re.compile(r"좋은 질문|안녕하세요[,\s]*여러분"), 1),
    ("5 끝에서 요약", re.compile(r"(?:정리하면|요약하면|요약하자면|정리하자면|결론적으로|오늘 알아본)"), 1),
    ("6 굵은 글씨", re.compile(r"\*\*"), 2),
    ("8 광고성 단어", re.compile(r"혁신적|획기적|완벽한|게임\s?체인저|최고의"), 1),
    ("9 의미 키우는 꼬리 문장", re.compile(r"보여줍니다|중요성을\s?(?:알|깨달)|시사하는\s?바|의미가\s?큽니다|것이\s?중요합니다"), 1),
    ("10 모호한 출처", re.compile(r"전문가들은|많은\s?사람들이|많은\s?분들이|연구에\s?따르면"), 1),
    ("상투적인 문장 첫머리", re.compile(r"(?:^|[.!?\n]\s*)(?:" + "|".join(map(re.escape, _OPENERS)) + r")[\s,]", re.M), 1),
    ("'할 수 있습니다' 반복", re.compile(r"[할될쓸볼]\s?수\s?있습니다"), 3),
]


def rules_prompt():
    """글쓰기 프롬프트 끝에 붙일 규칙. style/ai_tell_rules.md 가 있으면 그 내용을 쓴다."""
    try:
        with open(RULES_FILE, encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return text
    except OSError:
        pass
    return DEFAULT_RULES


def clean(text):
    """뜻을 바꾸지 않는 선에서 AI 티를 기계적으로 지운다."""
    if not text:
        return text
    t = str(text)
    t = re.sub(r"[​‌‍⁠﻿]", "", t)          # 보이지 않는 글자
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)                           # 규칙 6: **굵게**
    t = re.sub(r"(?<=\d)[ \t]*[—–][ \t]*(?=\d)", "~", t)             # 규칙 1: 숫자 범위 10—20 → 10~20
    t = re.sub(r"[ \t]*[—–][ \t]*", ", ", t)                         # 규칙 1: 긴 대시 → 쉼표
    t = _GREETING_RE.sub("", t)                                      # 규칙 4: 챗봇식 인사
    t = _OPENER_RE.sub(lambda m: m.group(1), t)                      # 상투적인 문장 첫머리
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r"(^|\n),\s*", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def _same_ending_run(text):
    """같은 어미로 끝나는 문장이 연달아 몇 번 나오는지(가장 긴 것)."""
    best = run = 0
    last = None
    for s in re.split(r"(?<=[.!?])\s+|\n+", text):
        m = _ENDING_RE.search(s.strip())
        end = m.group(1) if m else None
        run = run + 1 if end and end == last else (1 if end else 0)
        last = end
        best = max(best, run)
    return best


def find_tells(text):
    """남아 있는 AI 티의 이름 목록. 비어 있으면 깨끗한 글."""
    found = [name for name, pattern, limit in _TELLS if len(pattern.findall(text)) >= limit]
    if len(_EMOJI_RE.findall(text)) >= 3:
        found.append("7 이모지 많음")
    if _same_ending_run(text) >= 4:
        found.append("같은 어미가 네 문장 넘게 이어짐")
    return found
