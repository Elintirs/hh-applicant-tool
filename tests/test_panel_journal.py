from hh_applicant_tool.panel.server import _journal_light


def test_journal_light_strips_prompts_keeps_answers():
    rec = {
        "vacancy": "Node.js",
        "ai_filter": {"model": "m", "sec": 1, "system": "S" * 5000, "prompt": "P" * 5000, "answer": "да", "suitable": True},
        "letter": "Здравствуйте!",
        "test": [{"q": "Город?", "answer": "Калининград", "system": "S", "prompt": "P"}, {"q": "Без AI", "answer": "Да"}],
    }
    light = _journal_light(rec, 7)
    assert light["_line"] == 7
    assert "system" not in light["ai_filter"] and "prompt" not in light["ai_filter"]
    assert light["ai_filter"]["answer"] == "да" and light["ai_filter"]["has_prompt"] is True
    assert light["test"][0] == {"q": "Город?", "answer": "Калининград", "has_prompt": True}
    assert light["test"][1] == {"q": "Без AI", "answer": "Да"}  # без промпта не трогаем
    assert rec["ai_filter"]["system"]  # исходная запись не изменилась
