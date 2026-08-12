"""기계 번역.

번역은 얹는 것이지 대체하는 게 아니다. 실패해도 화면은 그대로 돌아가야 한다.
"""

import json

from stockbot.translate import Translator, parse_google


def google_payload(korean: str) -> str:
    """구글이 돌려주는 중첩 배열 모양."""
    return json.dumps([[[korean, "original", None, None, 10]], None, "en"])


class FakeHttp:
    def __init__(self, mapping=None, fail=False):
        self.mapping = mapping or {}
        self.fail = fail
        self.calls = []

    def get_text(self, url, **kwargs):
        self.calls.append(url)
        if self.fail:
            raise RuntimeError("막힘")
        for key, value in self.mapping.items():
            if key in url:
                return google_payload(value)
        return google_payload("번역됨")


# --- 응답 해석 --------------------------------------------------------------
def test_google_response_is_parsed():
    assert parse_google(google_payload("매출이 늘었습니다.")) == "매출이 늘었습니다."


def test_multi_piece_response_is_joined():
    payload = json.dumps([[["앞부분 ", "a", None], ["뒷부분", "b", None]], None, "en"])
    assert parse_google(payload) == "앞부분 뒷부분"


def test_broken_response_returns_nothing():
    assert parse_google("not json") == ""
    assert parse_google("[]") == ""
    assert parse_google(json.dumps([None])) == ""


# --- 번역 -------------------------------------------------------------------
def test_text_is_translated(tmp_path):
    translator = Translator(FakeHttp({"q=": "이사회가 새 위원회를 만들었습니다."}), tmp_path)
    assert translator.translate("The board formed a new committee.") == \
        "이사회가 새 위원회를 만들었습니다."


def test_a_failure_returns_empty_and_never_raises(tmp_path):
    translator = Translator(FakeHttp(fail=True), tmp_path)
    assert translator.translate("The board formed a new committee.") == ""


def test_one_failure_stops_further_attempts(tmp_path):
    """막힌 걸 알면서 문장마다 계속 두드리면 화면이 느려진다."""
    http = FakeHttp(fail=True)
    translator = Translator(http, tmp_path)
    for _ in range(5):
        translator.translate("Some english sentence here for testing.")
    assert len(http.calls) == 1


def test_disabled_translator_does_nothing(tmp_path):
    http = FakeHttp()
    translator = Translator(http, tmp_path, enabled=False)
    assert translator.translate("The board formed a new committee.") == ""
    assert http.calls == []


def test_korean_text_is_not_sent(tmp_path):
    http = FakeHttp()
    translator = Translator(http, tmp_path)
    assert translator.translate("이미 한글입니다") == ""
    assert http.calls == []


def test_result_is_cached_on_disk(tmp_path):
    http = FakeHttp()
    Translator(http, tmp_path).translate("A sentence to remember for later.")
    first = len(http.calls)

    # 새 인스턴스여도 디스크에 저장된 걸 읽어 쓴다
    fresh = Translator(http, tmp_path)
    assert fresh.translate("A sentence to remember for later.") == "번역됨"
    assert len(http.calls) == first


def test_long_text_is_split_at_sentence_ends(tmp_path):
    http = FakeHttp()
    long_text = " ".join(["This is a fairly long sentence about the business."] * 40)
    Translator(http, tmp_path).translate(long_text)
    assert len(http.calls) > 1          # 나눠 보냈다


def test_partial_failure_does_not_produce_half_translated_text(tmp_path):
    """일부만 번역된 글을 내보내면 읽는 사람이 오해한다."""

    class Flaky(FakeHttp):
        def get_text(self, url, **kwargs):
            self.calls.append(url)
            if len(self.calls) == 1:
                return google_payload("첫 조각")
            raise RuntimeError("두 번째부터 막힘")

    http = Flaky()
    long_text = " ".join(["This is a fairly long sentence about the business."] * 40)
    assert Translator(http, tmp_path).translate(long_text) == ""


def test_translate_many_only_returns_what_worked(tmp_path):
    http = FakeHttp({"first": "첫 문장"})
    translator = Translator(http, tmp_path)
    result = translator.translate_many(["The first sentence.", "이미 한글"])
    assert list(result) == ["The first sentence."]
