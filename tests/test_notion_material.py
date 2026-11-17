# -*- coding: utf-8 -*-
"""노션 자료 자동 등록 — 마크다운→노션 블록 변환, 원고 검사, 주제 선정."""
import os

import notion_api
import notion_material

HERE = os.path.dirname(os.path.abspath(__file__))


def sample():
    with open(os.path.join(HERE, "..", "notion_drafts", "01_회의록_정리.md"), encoding="utf-8") as f:
        return f.read()


def test_existing_draft_passes_check_and_converts():
    md = sample()
    assert notion_material.check_material(md) == []
    blocks = notion_api.markdown_to_blocks(md)
    kinds = [b["type"] for b in blocks]
    assert kinds.count("code") == 5
    assert kinds.count("numbered_list_item") >= 3
    assert "heading_1" not in kinds and kinds.count("heading_2") >= 6
    bold = [b for b in blocks if b["type"] == "paragraph"
            and any(t.get("annotations", {}).get("bold") for t in b["paragraph"]["rich_text"])]
    assert bold, "굵은 글씨(이럴 때 씁니다)가 살아 있어야 한다"
    code = next(b for b in blocks if b["type"] == "code")
    assert "[여기에" in code["code"]["rich_text"][0]["text"]["content"]


def test_long_text_is_split_under_notion_limit():
    rich = notion_api._rich("가" * 4500)
    assert len(rich) == 3 and all(len(r["text"]["content"]) <= 2000 for r in rich)


def test_check_catches_broken_material():
    assert notion_material.check_material("# 제목\n본문") != []
    assert "가격·비율 표현이 들어 있음" in notion_material.check_material(sample() + "\n월 29,000원")


def test_write_material_strips_wrapper_and_fails_closed():
    wrapped = "```markdown\n" + sample() + "\n```"
    assert notion_material.write_material("회의록 정리하기", llm=lambda p: wrapped).startswith("# ")
    assert notion_material.write_material("회의록 정리하기", llm=lambda p: "# 제목\n짧음") is None


def test_pick_topic_skips_registered_keywords():
    import auto_post
    first_topic, first_kw = next(iter(auto_post.PROMPT_BANK.values()))[0]
    assert notion_material.pick_topic([]) == (first_topic, first_kw)
    assert notion_material.pick_topic([first_kw]) != (first_topic, first_kw)
