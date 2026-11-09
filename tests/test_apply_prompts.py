from hh_applicant_tool.operations import apply_vacancies as av
from hh_applicant_tool.utils.string import render_template

FULL = {
    "id": "1",
    "description": "<p>Node.js и <b>NestJS</b></p>",
    "key_skills": [{"name": "NestJS"}, {"name": "PostgreSQL"}],
    "experience": {"name": "От 3 до 6 лет"},
}

RESUME = {
    k: "x"
    for k in (
        "first_name resume_title resume_total_experience resume_skills "
        "resume_about resume_experience resume_salary resume_work_format"
    ).split()
}


class Api:
    def __init__(self):
        self.calls = 0

    def get(self, path):
        self.calls += 1
        return FULL


def make_op():
    op = object.__new__(av.Operation)
    op.tool = type("Tool", (), {"api_client": Api()})()
    return op


def test_details_fetched_once_for_search_item():
    op, ph = make_op(), {}
    op._add_vacancy_details({"id": "1"}, ph)
    op._add_vacancy_details({"id": "1"}, ph)
    assert op.api_client.calls == 1
    assert ph["vacancy_description"] == "Node.js и NestJS"
    assert ph["vacancy_skills"] == "NestJS, PostgreSQL"
    assert ph["vacancy_experience"] == "От 3 до 6 лет"


def test_full_vacancy_not_refetched():
    op, ph = make_op(), {}
    op._add_vacancy_details(FULL, ph)
    assert op.api_client.calls == 0


def test_default_prompts_render():
    op, ph = make_op(), {
        **RESUME,
        "vacancy_name": "v",
        "employer_name": "e",
    }
    op._add_vacancy_details({"id": "1"}, ph)
    for tpl in (av.DEFAULT_COVER_LETTER_PROMPT, av.DEFAULT_AI_FILTER_PROMPT):
        assert "NestJS" in render_template(tpl, ph)
