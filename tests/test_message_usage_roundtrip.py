import json

from app.chat_schema import parse_messages


def test_stored_usage_fields_survive_a_read_and_resave():
    usage = {"prompt_tokens": 10, "completion_tokens": 4, "output_tokens": 4, "visible_tokens": 3,
             "usage_status": "final", "cached_tokens": 2}
    stored = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello", "usage": usage}]
    strict = parse_messages(stored, strict=True)
    read = parse_messages(json.dumps(strict))
    assert read[1]["usage"] == usage
    assert parse_messages(read, strict=True)[1]["usage"] == usage
