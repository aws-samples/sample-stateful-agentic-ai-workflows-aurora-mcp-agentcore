"""The warm-up can record the conversation id of every chat turn it sends, before sending it."""

import pytest

from scripts import warm_demo



def accept(result):
    """A step is called only when it has a check; this one accepts any reply."""
    return None


CHAT = ("Phase 4 Production", "/api/chat", {"message": "m", "phase": 4}, accept)
READ = ("Catalog", "/api/products?limit=50", None, accept)


def run(monkeypatch, plan, ids_file=None, call=None):
    sent = []

    def fake_call(base, headers, path, body=None):
        sent.append((path, body, ids_file.read_text() if ids_file else None))
        if call:
            return call(path, body)
        return {}

    monkeypatch.setattr(warm_demo, "steps", lambda traveler: plan)
    monkeypatch.setattr(warm_demo, "call", fake_call)
    if ids_file is None:
        result = warm_demo.run_steps("http://testserver", {}, "trv_meridian_demo")
    else:
        result = warm_demo.run_steps("http://testserver", {}, "trv_meridian_demo", ids_file)
    return result, sent


def test_each_chat_turn_gets_its_own_recorded_conversation_id(monkeypatch, tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("", encoding="utf-8")

    _, sent = run(monkeypatch, [READ, CHAT, CHAT], ids_file)

    chat_ids = [body["conversation_id"] for path, body, _ in sent if path == "/api/chat"]
    assert len(chat_ids) == 2 and len(set(chat_ids)) == 2
    assert ids_file.read_text().split() == chat_ids
    assert all(len(conversation_id) <= 50 for conversation_id in chat_ids)
    assert sent[0][1] is None


def test_an_id_is_on_file_before_its_turn_is_sent(monkeypatch, tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("", encoding="utf-8")

    _, sent = run(monkeypatch, [CHAT], ids_file)

    path, body, file_when_sent = sent[0]
    assert body["conversation_id"] in file_when_sent.split()


def test_a_turn_that_raises_still_leaves_its_id_on_file(monkeypatch, tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("", encoding="utf-8")

    def timed_out(path, body):
        raise TimeoutError("read timed out")

    (_, failed), sent = run(monkeypatch, [CHAT], ids_file, call=timed_out)

    assert failed == ["Phase 4 Production"]
    assert ids_file.read_text().split() == [sent[0][1]["conversation_id"]]


def test_without_the_option_the_bodies_are_unchanged(monkeypatch):
    _, sent = run(monkeypatch, [CHAT])

    assert sent[0][1] == {"message": "m", "phase": 4}


def test_the_option_is_parsed_and_the_file_is_started_empty(monkeypatch, tmp_path):
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("stale\n", encoding="utf-8")
    seen = {}

    def fake_run_steps(base, headers, traveler, conversation_ids_file=None):
        seen["file"] = conversation_ids_file
        seen["content"] = conversation_ids_file.read_text()
        return [], []

    monkeypatch.setattr(warm_demo, "load_record", lambda: {})
    monkeypatch.setattr(warm_demo, "release_url", lambda record: "https://site.example")
    monkeypatch.setattr(warm_demo, "require_token_safe_url", lambda base, always=False: None)
    monkeypatch.setattr(warm_demo, "request_headers", lambda hosted, user: {})
    monkeypatch.setattr(warm_demo, "run_steps", fake_run_steps)

    code = warm_demo.main(["--hosted", "--conversation-ids-file", str(ids_file)])

    assert code == 0
    assert seen == {"file": ids_file, "content": ""}


def test_the_option_needs_a_value(capsys):
    with pytest.raises(SystemExit):
        warm_demo.build_parser().parse_args(["--conversation-ids-file"])
