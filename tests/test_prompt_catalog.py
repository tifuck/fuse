"""The prompt library catalog (static/chat/composer/prompt-catalog.js) stays well-formed."""
import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "static/chat/composer/prompt-catalog.js"
FIELD = re.compile(r"\{\{([^{}|]+)(?:\|([^{}]*))?\}\}")


def load():
    text = SRC.read_text()
    return json.loads(text[text.index("= ") + 2 : text.rindex(";")])


def prompts(cat):
    return [p for c in cat["categories"] for p in c["prompts"]]


def test_ids_unique_and_modes_defined():
    cat = load()
    ids = [p["id"] for p in prompts(cat)]
    assert len(ids) == len(set(ids))
    for p in prompts(cat):
        assert p["modes"] and all(m in cat["footers"] for m in p["modes"]), p["id"]
        assert p["needs"] in ("project", "any"), p["id"]


def test_every_category_has_an_audit_and_is_not_padding():
    for c in load()["categories"]:
        assert len(c["prompts"]) >= 4, c["id"]
        assert any(p["modes"][0] == "audit" for p in c["prompts"]), f"{c['id']} has no audit"


def test_titles_and_bodies_are_distinct():
    ps = prompts(load())
    assert len({p["title"] for p in ps}) == len(ps)
    firsts = [p["body"].split("\n\n", 1)[-1][:80] for p in ps]
    assert len(set(firsts)) == len(firsts), "two prompts open with the same instruction"


def test_bodies_carry_no_baked_in_rules_or_hard_wraps():
    for p in prompts(load()):
        b = p["body"]
        assert "Do not modify files until" not in b, p["id"]
        assert "Be concise: findings only" not in b, p["id"]
        assert "\n\n\n" not in b and b == b.strip(), p["id"]
        assert not re.search(r"[a-z,]\n[a-z]", b), f"{p['id']} is hard-wrapped"


def test_fields_well_formed():
    for p in prompts(load()):
        assert p["body"].count("{{") == p["body"].count("}}") == len(FIELD.findall(p["body"])), p["id"]
        assert p["needs_input"] == any(m.group(2) is None for m in FIELD.finditer(p["body"])), p["id"]


def test_modifiers_resolve():
    cat = load()
    for mod in cat["modifiers"]:
        assert set(mod["modes"]) <= set(cat["footers"])
