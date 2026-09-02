"""번역 — 믿을 수 있는 번역기부터.

여기서 지켜야 할 것.
  · 좋은 번역기를 먼저 쓰고, 안 되면 조용히 다음으로 넘어간다
  · 어느 번역기가 옮겼는지 항상 알 수 있어야 한다
  · 실패해도 예외를 올리지 않는다 (원문은 화면에 그대로 남는다)
  · 열쇠는 환경변수로도 넣을 수 있어야 한다
"""

import json

import pytest

from stock_analysis.translate import Translator, apply_glossary, parse_free


def free_payload(korean: str) -> str:
    return json.dumps([[[korean, "original", None, None, 10]], None, "en"])


class FakeHttp:
    """번역기별로 어떤 응답을 줄지 정해두는 가짜 통신."""

    def __init__(self, deepl=None, azure=None, papago=None, google=None, free=None,
                 broken=()):
        self.answers = {"deepl": deepl, "azure": azure, "papago": papago,
                        "google": google, "free": free}
        self.broken = set(broken)
        self.calls: list[str] = []
        self.headers: list[dict] = []

    def _which(self, url: str) -> str:
        if "deepl.com" in url:
            return "deepl"
        if "cognitive.microsofttranslator" in url:
            return "azure"
        if "naveropenapi" in url:
            return "papago"
        if "translation.googleapis.com/language" in url:
            return "google"
        return "free"

    def _answer(self, url, headers=None):
        which = self._which(url)
        self.calls.append(which)
        self.headers.append(headers or {})
        if which in self.broken:
            raise RuntimeError("막힘")
        value = self.answers.get(which)
        if value is None:
            raise RuntimeError("설정되지 않은 번역기")
        return which, value

    def post_form(self, url, form, headers=None, timeout=None):
        which, value = self._answer(url, headers)
        if which == "deepl":
            return json.dumps({"translations": [{"text": value}]})
        return json.dumps({"message": {"result": {"translatedText": value}}})

    def post_json(self, url, body, headers=None, timeout=None):
        which, value = self._answer(url, headers)
        if which == "azure":
            return json.dumps([{"translations": [{"text": value}]}])
        return json.dumps({"data": {"translations": [{"translatedText": value}]}})

    def get_text(self, url, **kwargs):
        _, value = self._answer(url)
        return free_payload(value)


SENTENCE = "The board appointed a new advisory committee."


# --- 무료 경로 응답 해석 ----------------------------------------------------
def test_free_response_is_parsed():
    assert parse_free(free_payload("이사회가 위원회를 만들었습니다.")) == "이사회가 위원회를 만들었습니다."


def test_broken_response_returns_nothing():
    assert parse_free("not json") == ""
    assert parse_free("[]") == ""


# --- 번역기 줄 세우기 -------------------------------------------------------
def test_deepl_is_used_first_when_a_key_exists(tmp_path):
    http = FakeHttp(deepl="딥엘 번역", free="무료 번역")
    translator = Translator(http, tmp_path, settings={"deepl_key": "abc:fx"})

    result = translator.translate(SENTENCE)
    assert result.text == "딥엘 번역"
    assert result.provider == "deepl" and result.label == "DeepL"
    assert http.calls == ["deepl"]          # 무료 경로는 건드리지도 않았다


def test_free_path_is_used_when_no_key_is_set(tmp_path):
    http = FakeHttp(free="무료 번역")
    result = Translator(http, tmp_path).translate(SENTENCE)
    assert result.text == "무료 번역"
    assert result.label == "무료 번역"


def test_a_dead_provider_falls_through_to_the_next(tmp_path):
    """DeepL 한도가 끝나도 화면이 비면 안 된다."""
    http = FakeHttp(azure="애저 번역", free="무료 번역", broken=["deepl"])
    translator = Translator(http, tmp_path,
                            settings={"deepl_key": "x", "azure_key": "y"})

    result = translator.translate(SENTENCE)
    assert result.text == "애저 번역"
    assert result.provider == "azure"


def test_a_provider_that_failed_is_not_tried_again(tmp_path):
    http = FakeHttp(free="무료 번역", broken=["deepl"])
    translator = Translator(http, tmp_path, settings={"deepl_key": "x"})

    translator.translate(SENTENCE)
    translator.translate("Another different sentence entirely here.")
    assert http.calls.count("deepl") == 1


def test_everything_failing_returns_empty_and_never_raises(tmp_path):
    http = FakeHttp(broken=["deepl", "azure", "papago", "google", "free"])
    translator = Translator(http, tmp_path, settings={"deepl_key": "x"})
    assert translator.translate(SENTENCE).text == ""


def test_a_specific_provider_can_be_forced(tmp_path):
    http = FakeHttp(deepl="딥엘", papago="파파고 번역")
    translator = Translator(http, tmp_path, settings={
        "provider": "papago", "deepl_key": "x",
        "papago_id_key": "id", "papago_secret_key": "secret",
    })
    result = translator.translate(SENTENCE)
    assert result.provider == "papago"
    assert http.calls == ["papago"]


def test_the_free_path_can_be_switched_off(tmp_path):
    """열쇠 없는 경로를 못 믿겠으면 아예 끌 수 있어야 한다."""
    http = FakeHttp(free="무료 번역")
    translator = Translator(http, tmp_path, settings={"allow_free": False})
    assert translator.available() == []
    assert translator.translate(SENTENCE).text == ""
    assert http.calls == []


# --- 열쇠 ------------------------------------------------------------------
def test_keys_can_come_from_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPL_API_KEY", "from-env")
    http = FakeHttp(deepl="딥엘 번역")
    translator = Translator(http, tmp_path)

    assert translator.secret("deepl") == "from-env"
    assert translator.translate(SENTENCE).provider == "deepl"


def test_the_key_is_sent_in_the_header_not_the_url(tmp_path):
    http = FakeHttp(deepl="딥엘 번역")
    Translator(http, tmp_path, settings={"deepl_key": "secret-key"}).translate(SENTENCE)
    assert "DeepL-Auth-Key secret-key" in http.headers[0]["Authorization"]


def test_azure_region_is_passed_when_given(tmp_path):
    http = FakeHttp(azure="애저 번역")
    Translator(http, tmp_path,
               settings={"azure_key": "k", "azure_region_key": "koreacentral"}).translate(SENTENCE)
    assert http.headers[0]["Ocp-Apim-Subscription-Region"] == "koreacentral"


# --- 금융 용어 굳히기 -------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("회사는 지침을 상향했습니다.", "회사는 가이던스를 상향했습니다."),
        ("밀린 주문 규모가 늘었습니다.", "수주잔고 규모가 늘었습니다."),
        ("총 마진이 개선되었습니다.", "매출총이익률이 개선되었습니다."),
        ("주당 순이익이 늘었습니다.", "주당순이익이 늘었습니다."),
        ("증권 거래 위원회에 제출했습니다.", "SEC에 제출했습니다."),
    ],
)
def test_finance_words_are_pinned_down(raw, expected):
    """기계 번역은 금융 용어를 일상어로 풀어버린다. 그것만 되돌린다."""
    assert apply_glossary(raw) == expected


def test_glossary_is_applied_to_translations(tmp_path):
    http = FakeHttp(free="회사는 지침을 상향했습니다.")
    assert Translator(http, tmp_path).translate(SENTENCE).text == \
        "회사는 가이던스를 상향했습니다."


# --- 그 밖의 안전장치 -------------------------------------------------------
def test_korean_text_is_not_sent(tmp_path):
    http = FakeHttp(free="x")
    assert Translator(http, tmp_path).translate("이미 한글입니다").text == ""
    assert http.calls == []


def test_disabled_translator_does_nothing(tmp_path):
    http = FakeHttp(free="x")
    assert Translator(http, tmp_path, enabled=False).translate(SENTENCE).text == ""
    assert http.calls == []


def test_result_is_cached_per_provider(tmp_path):
    http = FakeHttp(free="무료 번역")
    Translator(http, tmp_path).translate(SENTENCE)
    before = len(http.calls)

    fresh = Translator(http, tmp_path)          # 새 인스턴스도 디스크 캐시를 읽는다
    assert fresh.translate(SENTENCE).text == "무료 번역"
    assert len(http.calls) == before


def test_long_text_is_split_at_sentence_ends(tmp_path):
    http = FakeHttp(free="조각")
    long_text = " ".join(["This is a fairly long sentence about the business."] * 40)
    Translator(http, tmp_path).translate(long_text)
    assert len(http.calls) > 1


def test_partial_failure_does_not_produce_half_translated_text(tmp_path):
    class Flaky(FakeHttp):
        def get_text(self, url, **kwargs):
            self.calls.append("free")
            if len(self.calls) == 1:
                return free_payload("첫 조각")
            raise RuntimeError("두 번째부터 막힘")

    http = Flaky(free="x")
    long_text = " ".join(["This is a fairly long sentence about the business."] * 40)
    assert Translator(http, tmp_path).translate(long_text).text == ""


def test_translate_many_reports_the_engine(tmp_path):
    http = FakeHttp(deepl="딥엘 번역")
    translator = Translator(http, tmp_path, settings={"deepl_key": "x"})
    results = translator.translate_many([SENTENCE, "이미 한글"])
    assert list(results) == [SENTENCE]
    assert results[SENTENCE].label == "DeepL"


# --- 조사 고르기 ------------------------------------------------------------
@pytest.mark.parametrize(
    "word,given,expected",
    [
        ("가이던스", "을", "를"),      # 받침 없음 → 를
        ("수주잔고", "을", "를"),
        ("매출총이익률", "를", "을"),  # 받침 있음 → 을
        ("주당순이익", "가", "이"),
        ("가이던스", "은", "는"),
        ("손상차손", "는", "은"),
        ("SEC", "을", "를"),           # 한글이 아니면 받침 없는 쪽
        ("가이던스", "에", "에"),      # 변하지 않는 조사는 그대로
    ],
)
def test_particles_follow_the_new_word(word, given, expected):
    from stock_analysis.translate import fix_particle

    assert fix_particle(word, given) == expected


def test_words_without_a_particle_are_left_alone():
    assert apply_glossary("지침 상향") == "가이던스 상향"
    assert apply_glossary("아무 상관 없는 문장입니다.") == "아무 상관 없는 문장입니다."


# --- '수익' 은 조심해서 다룬다 ------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        # revenue 를 옮긴 '수익' 은 '매출' 로 (한국어에서 수익은 이익 쪽에 가깝다)
        ("당사 수익의 상당 부분은 소수 고객에서 나옵니다.", "당사 매출의 상당 부분은 소수 고객에서 나옵니다."),
        ("연간 수익 가이던스를 올렸습니다.", "연간 매출 가이던스를 올렸습니다."),
    ],
)
def test_revenue_word_is_corrected(raw, expected):
    assert apply_glossary(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "수익률이 개선되었습니다.",      # 이자·투자 수익률
        "수익성이 좋아졌습니다.",        # 수익성
        "순수익이 늘었습니다.",          # 순이익
        "영업 수익이 증가했습니다.",     # operating income
        "이자 수익이 늘었습니다.",
        "고수익 채권에 투자했습니다.",
    ],
)
def test_other_meanings_of_the_word_are_left_alone(text):
    """뜻이 다른 자리까지 바꾸면 번역이 거짓말이 된다."""
    assert apply_glossary(text) == text


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("중대한 약점을 확인했습니다.", "중대한 결함을 확인했습니다."),
        ("독립적인 등록 공공회계법인은", "외부감사인은"),
        ("운영 결과에 영향을 줍니다.", "영업 실적에 영향을 줍니다."),
        ("실질적이고 부정적인 영향을 미칩니다.", "중대한 악영향을 미칩니다."),
    ],
)
def test_accounting_terms_use_the_standard_wording(raw, expected):
    assert apply_glossary(raw) == expected
