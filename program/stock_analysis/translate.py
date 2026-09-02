"""번역 — 믿을 수 있는 것부터 순서대로.

번역을 '믿을 수 있다' 는 말에는 두 가지가 들어 있다.
  1) 번역 품질이 좋을 것
  2) 어느 날 갑자기 끊기지 않을 것

그래서 번역기를 하나로 정하지 않고 **줄을 세운다.** 열쇠(API 키)를 넣어둔
정식 번역기를 먼저 쓰고, 없으면 열쇠가 필요 없는 무료 경로로 내려간다.
어느 번역기가 옮긴 문장인지 화면에 항상 표시한다 — 그래야 어디까지 믿을지
읽는 사람이 정한다.

  DeepL   금융·법률 문장에 특히 강하다. 무료 열쇠로 월 50만 자.
  Azure   마이크로소프트. 무료로 월 200만 자. 잘 끊기지 않는다.
  Papago  네이버. 한국어 표현이 가장 자연스럽다. 네이버 클라우드 열쇠 필요.
  Google  구글 클라우드 정식 API. 열쇠 필요.
  무료    열쇠 없이 쓰는 공개 경로. 언제든 막힐 수 있어 맨 뒤에 둔다.

번역문은 원문을 대체하지 않는다. 화면에는 늘 원문이 함께 남는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)

MAX_CHUNK = 1200        # 한 번에 보낼 글자 수
MAX_TEXT = 4000         # 이보다 긴 문단은 앞부분만 옮긴다

# --------------------------------------------------------------------------
# 금융 용어 굳히기
#   기계 번역은 금융 용어를 일상어로 풀어버리는 일이 잦다.
#   ("guidance" → 지침 / "backlog" → 밀린 일). 그런 것만 골라 되돌린다.
#   문맥을 바꾸지 않도록 **번역 결과에서만**, 확실한 것만 손본다.
# --------------------------------------------------------------------------
GLOSSARY_WORDS: list[tuple[str, str]] = [
    # revenue 를 '수익' 으로 옮기는 일이 잦다. 한국어에서 수익은 이익 쪽에 가깝다.
    # 수익률·수익성·순수익·영업 수익처럼 뜻이 다른 자리는 건드리지 않는다.
    (r"(?<![순총이고])(?<!영업 )(?<!이자 )(?<!기타 )(?<!투자 )수익(?![률성자원])", "매출"),
    (r"지침", "가이던스"),
    (r"중대한 약점", "중대한 결함"),
    (r"독립적인 등록 공공회계법인|독립 등록 공인회계법인|독립적인 공인회계법인", "외부감사인"),
    (r"운영 결과", "영업 실적"),
    (r"실질적이고 부정적인 영향|중대하고 부정적인 영향", "중대한 악영향"),
    (r"수주 잔고", "수주잔고"),
    (r"밀린 일감|밀린 주문|백로그", "수주잔고"),
    (r"계속 기업|고잉 컨선", "계속기업"),
    (r"희석 효과|묽어짐", "희석"),
    (r"감가상각 전 영업이익", "EBITDA"),
    (r"장부가치 하락|감액 손실", "손상차손"),
    (r"약정 사항 위반|계약 조항 위반", "재무약정 위반"),
    (r"총 마진|매출 총 이익률|총이익 마진", "매출총이익률"),
    (r"영업 현금 흐름", "영업현금흐름"),
    (r"주당 순이익", "주당순이익"),
    (r"자사주 매입 프로그램", "자사주 매입"),
    (r"증권 거래 위원회", "SEC"),
]

# 말을 바꾸면 뒤에 붙은 조사가 틀어진다. "지침을" → "가이던스을" 이 되는 식.
# 받침 유무를 보고 조사를 다시 고른다.
_PARTICLE_PAIRS = [("을", "를"), ("이", "가"), ("은", "는"), ("과", "와"), ("으로", "로")]
_PARTICLE_OF = {}
for _with_final, _without_final in _PARTICLE_PAIRS:
    _PARTICLE_OF[_with_final] = (_with_final, _without_final)
    _PARTICLE_OF[_without_final] = (_with_final, _without_final)

_PARTICLE_RE = "(으로|을|를|이|가|은|는|과|와|로)?"
_GLOSSARY = [
    (re.compile(f"(?:{pattern}){_PARTICLE_RE}"), word) for pattern, word in GLOSSARY_WORDS
]


def has_final(char: str) -> bool:
    """한글 글자에 받침이 있는지. 조사를 고르는 기준."""
    if not char:
        return False
    code = ord(char) - 0xAC00
    if 0 <= code <= 11171:
        return code % 28 != 0
    return False        # 한글이 아니면(SEC·EBITDA 등) 받침 없는 쪽으로 읽는다


def fix_particle(word: str, particle: str) -> str:
    """바뀐 말에 맞는 조사를 돌려준다."""
    pair = _PARTICLE_OF.get(particle)
    if not pair:
        return particle
    return pair[0] if has_final(word[-1:]) else pair[1]


def apply_glossary(text: str) -> str:
    """번역 결과의 금융 용어를 우리가 쓰는 말로 맞춘다.

    번역기는 'guidance' 를 '지침', 'backlog' 를 '밀린 주문' 처럼 일상어로
    풀어버린다. 뜻은 맞지만 주식 화면에서는 읽기 나쁘다. 그런 것만 되돌린다.
    """
    for pattern, word in _GLOSSARY:
        text = pattern.sub(lambda m, w=word: w + fix_particle(w, m.group(1) or ""), text)
    return text


# --------------------------------------------------------------------------
# 번역기 하나하나
# --------------------------------------------------------------------------
@dataclass
class Provider:
    key: str                # 내부 이름
    label: str              # 화면에 보일 이름
    needs_key: bool
    note: str               # 어디서 열쇠를 받는지


PROVIDERS = [
    Provider("deepl", "DeepL", True, "https://www.deepl.com/pro-api (무료 가입, 월 50만 자)"),
    Provider("azure", "Azure 번역기", True, "https://portal.azure.com Translator 만들기 (월 200만 자 무료)"),
    Provider("papago", "파파고", True, "https://www.ncloud.com Papago Translation"),
    Provider("google_cloud", "Google 번역 API", True, "https://console.cloud.google.com"),
    Provider("free", "무료 번역", False, "열쇠가 필요 없지만 언제든 막힐 수 있습니다"),
]
PROVIDER_BY_KEY = {p.key: p for p in PROVIDERS}
ORDER = [p.key for p in PROVIDERS]


class Result:
    """번역 결과와 '누가 옮겼는지'."""

    __slots__ = ("text", "provider")

    def __init__(self, text: str = "", provider: str = "") -> None:
        self.text = text
        self.provider = provider

    @property
    def label(self) -> str:
        entry = PROVIDER_BY_KEY.get(self.provider)
        return entry.label if entry else self.provider

    def __bool__(self) -> bool:
        return bool(self.text)


def _deepl(http, text: str, key: str, target: str) -> str:
    # 무료 열쇠는 ':fx' 로 끝난다. 주소가 달라서 갈라 써야 한다.
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    payload = http.post_form(
        f"https://{host}/v2/translate",
        {"text": text, "target_lang": target.upper(), "source_lang": "EN"},
        headers={"Authorization": f"DeepL-Auth-Key {key}"},
    )
    data = json.loads(payload)
    return "".join(item.get("text", "") for item in data.get("translations", []))


def _azure(http, text: str, key: str, target: str, region: str = "") -> str:
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json"}
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region
    payload = http.post_json(
        f"https://api.cognitive.microsofttranslator.com/translate?api-version=3.0&from=en&to={target}",
        [{"Text": text}],
        headers=headers,
    )
    data = json.loads(payload)
    if not data or not isinstance(data, list):
        return ""
    return "".join(t.get("text", "") for t in (data[0].get("translations") or []))


def _papago(http, text: str, client_id: str, secret: str, target: str) -> str:
    payload = http.post_form(
        "https://naveropenapi.apigw.ntruss.com/nmt/v1/translation",
        {"source": "en", "target": target, "text": text},
        headers={"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": secret},
    )
    data = json.loads(payload)
    return ((data.get("message") or {}).get("result") or {}).get("translatedText", "")


def _google_cloud(http, text: str, key: str, target: str) -> str:
    payload = http.post_json(
        f"https://translation.googleapis.com/language/translate/v2?key={quote(key)}",
        {"q": text, "source": "en", "target": target, "format": "text"},
    )
    data = json.loads(payload)
    items = ((data.get("data") or {}).get("translations")) or []
    return items[0].get("translatedText", "") if items else ""


FREE_URL = (
    "https://translate.googleapis.com/translate_a/single"
    "?client=gtx&sl=en&tl={target}&dt=t&q={text}"
)


def _free(http, text: str, target: str) -> str:
    return parse_free(http.get_text(FREE_URL.format(target=target, text=quote(text)), timeout=20, retries=1))


def parse_free(payload: str) -> str:
    """무료 경로는 중첩 배열로 온다. 번역된 조각만 이어 붙인다."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return ""
    pieces = []
    for row in data[0]:
        if isinstance(row, list) and row and isinstance(row[0], str):
            pieces.append(row[0])
    return "".join(pieces).strip()


# --------------------------------------------------------------------------
def _chunks(text: str) -> list[str]:
    """문장 경계에서 끊는다. 문장 중간에서 자르면 번역이 망가진다."""
    if len(text) <= MAX_CHUNK:
        return [text]
    out, buffer = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(buffer) + len(sentence) + 1 > MAX_CHUNK and buffer:
            out.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}".strip()
    if buffer:
        out.append(buffer)
    return out


def _key_of(text: str, target: str, provider: str) -> str:
    return hashlib.sha1(f"{provider}:{target}:{text}".encode("utf-8")).hexdigest()[:20]


class Translator:
    """영어 → 한국어. 믿을 수 있는 번역기부터 차례로 시도한다."""

    def __init__(self, http, cache_dir: str | Path = ".cache", enabled: bool = True,
                 target: str = "ko", settings: dict | None = None) -> None:
        self.http = http
        self.enabled = enabled
        self.target = target
        self.settings = settings or {}
        self.cache_dir = Path(cache_dir) / "translations"
        self._lock = threading.Lock()
        self._memory: dict[str, str] = {}
        self._dead: set[str] = set()      # 이번 실행에서 실패한 번역기
        self._chosen: str = ""            # 실제로 쓰인 번역기

    # --- 열쇠 ------------------------------------------------------------
    def secret(self, name: str) -> str:
        """환경변수를 먼저 본다. 열쇠를 config 파일에 적지 않아도 되게."""
        env = {
            "deepl": "DEEPL_API_KEY",
            "azure": "AZURE_TRANSLATOR_KEY",
            "azure_region": "AZURE_TRANSLATOR_REGION",
            "papago_id": "PAPAGO_CLIENT_ID",
            "papago_secret": "PAPAGO_CLIENT_SECRET",
            "google_cloud": "GOOGLE_TRANSLATE_API_KEY",
        }.get(name, "")
        value = os.environ.get(env, "") if env else ""
        return str(value or self.settings.get(f"{name}_key", "") or self.settings.get(name, "")).strip()

    def available(self) -> list[str]:
        """지금 쓸 수 있는 번역기를, 믿을 수 있는 순서대로."""
        wanted = str(self.settings.get("provider", "auto")).lower()
        order = ORDER if wanted in ("auto", "", "none") else [wanted]

        out = []
        for key in order:
            if key in self._dead:
                continue
            if key == "deepl" and self.secret("deepl"):
                out.append(key)
            elif key == "azure" and self.secret("azure"):
                out.append(key)
            elif key == "papago" and self.secret("papago_id") and self.secret("papago_secret"):
                out.append(key)
            elif key == "google_cloud" and self.secret("google_cloud"):
                out.append(key)
            elif key == "free" and self.settings.get("allow_free", True):
                out.append(key)
        return out

    # --- 저장해둔 것 ------------------------------------------------------
    def _cached(self, key: str) -> str | None:
        if key in self._memory:
            return self._memory[key]
        path = self.cache_dir / f"{key}.txt"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                self._memory[key] = text
                return text
            except OSError:
                return None
        return None

    def _store(self, key: str, text: str) -> None:
        self._memory[key] = text
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.txt").write_text(text, encoding="utf-8")
        except OSError as exc:
            log.debug("번역 캐시 저장 실패: %s", exc)

    # --- 번역 -------------------------------------------------------------
    def translate(self, text: str) -> Result:
        """번역 결과와 어느 번역기를 썼는지. 절대 예외를 올리지 않는다."""
        text = (text or "").strip()
        if not text or not self.enabled:
            return Result()
        if not re.search(r"[A-Za-z]{3}", text):
            return Result()                 # 이미 한글이거나 숫자뿐
        text = text[:MAX_TEXT]

        for provider in self.available():
            key = _key_of(text, self.target, provider)
            cached = self._cached(key)
            if cached:
                self._chosen = provider
                return Result(cached, provider)

            translated = self._run(provider, text)
            if translated:
                translated = apply_glossary(translated)
                self._store(key, translated)
                self._chosen = provider
                return Result(translated, provider)
        return Result()

    def _run(self, provider: str, text: str) -> str:
        """한 번역기로 끝까지 옮긴다. 일부만 되면 아무것도 내보내지 않는다."""
        pieces = []
        for chunk in _chunks(text):
            try:
                piece = self._call(provider, chunk)
            except Exception as exc:
                log.info("%s 번역 실패(다음 번역기로 넘어갑니다): %s",
                         PROVIDER_BY_KEY[provider].label, exc)
                self._dead.add(provider)
                return ""
            if not piece:
                self._dead.add(provider)
                return ""
            pieces.append(piece)
        return " ".join(pieces).strip()

    def _call(self, provider: str, chunk: str) -> str:
        if provider == "deepl":
            return _deepl(self.http, chunk, self.secret("deepl"), self.target)
        if provider == "azure":
            return _azure(self.http, chunk, self.secret("azure"), self.target,
                          self.secret("azure_region"))
        if provider == "papago":
            return _papago(self.http, chunk, self.secret("papago_id"),
                           self.secret("papago_secret"), self.target)
        if provider == "google_cloud":
            return _google_cloud(self.http, chunk, self.secret("google_cloud"), self.target)
        return _free(self.http, chunk, self.target)

    def translate_many(self, texts: list[str], limit: int = 12) -> dict[str, Result]:
        """여러 문장을 한 번에. 이미 받아둔 것은 네트워크를 쓰지 않는다."""
        out: dict[str, Result] = {}
        if not self.enabled:
            return out
        with self._lock:
            for text in texts[:limit]:
                result = self.translate(text)
                if result:
                    out[text] = result
        return out
