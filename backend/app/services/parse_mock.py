"""Mock parse service — raw DL-01 fields for validation (never confirmed)."""

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import pairwise
from typing import Any

from app.schemas.ledger import MAX_ITEMS
from app.services.non_sale import non_sale_kind
from app.services.payment_phrases import (
    says_clear_partial_payment,
    says_not_paid_now,
    says_partial_payment,
)
from app.services.postprocess import WORD_SUFFIXES, says_credit, says_payment

_CP_PATTERN = re.compile(r"([가-힣A-Za-z0-9]+?)(?:에게|한테|거래처)")
_AMOUNT_PATTERN = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*원")
_UNITS = ("개", "박스", "킬로", "kg", "롤", "접", "단", "포기", "팩", "병", "봉지", "망", "마리", "판", "모")
_QTY_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(" + "|".join(_UNITS) + ")")
# 품목명 후보에서 뺄 말 — 그 말 자체이거나 그 말 + 후처리와 같은 허용 접미어(외상으로·현금이요)일 때만.
# "원두"·"총각김치"·"합판"·"카드지갑"처럼 이 말로 시작하는 품목명은 남긴다. "달아"는 활용형이 많아 시작 일치
_NON_ITEM_WORDS = ("외상", "미수", "현금", "카드", "입금", "원", "총", "합", "총액", "합계", "총합")
_NON_ITEM_FORMS = frozenset(word + suffix for word in _NON_ITEM_WORDS for suffix in ("",) + WORD_SUFFIXES)
_NON_ITEM_PREFIXES = ("달아",)
# 단위는 단어가 단위 자체이거나 "단위+당"(박스당)일 때만 뺀다 — 단무지·접시·개떡처럼 단위로 시작하는 품목명은 남김
_UNIT_WORDS = frozenset(_UNITS) | {unit + "당" for unit in _UNITS}
# 표지(에게·한테·거래처)가 없을 때만 첫 단어 "~에/~로/으로"를 거래처로 (PM #41 리뷰). 시간 표현은 거래처가 아니다
_FIRST_WORD_JOSA = ("으로", "로", "에")
_TIME_WORDS = frozenset(("아침", "점심", "저녁", "오전", "오후", "오늘", "어제", "내일", "아까", "방금", "지난주", "이번주",
                         "다음주", "월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일",
                         "새벽", "밤", "낮", "주말", "저번주", "지난번", "그저께"))
# 시간 낱말만 이어 붙인 말(어제저녁·오늘아침). "새벽상회"처럼 시간 낱말로 시작하는 거래처명은 거래처로 둔다
# 뒤에 말·초·중이 붙어도 시간(오전중·주말초). 이름 전체가 맞을 때만(fullmatch) 쓴다
_TIME_COMPOUND = re.compile("(?:" + "|".join(sorted(_TIME_WORDS, key=len, reverse=True)) + ")+(?:말|초|중)?")
# 낱말 목록 대신 패턴 — 이름 전체가 맞을 때만(fullmatch) 제외 (병철님 #46 리뷰)
_RELATIVE_TIME = re.compile(r"(?:지난|이번|다음|저번|요번|작년|올해|내년)(?:달|주|해|년|월)?(?:말|초|중)?")  # 지난달·작년말
_DELIVERY = re.compile(r"(?:퀵|택배|배달|화물)(?:서비스|기사|차)?")  # 퀵서비스·배달기사 — 접두 일치로 넓히지 않음
# 부사·장소 — 첫 단어 "~로/~에"로 자주 오지만 거래처가 아님 (PM #46 리뷰)
_NOT_COUNTERPARTY = frozenset(("추가", "처음", "마지막", "진짜", "정말", "실제", "반품", "시장", "가게",
                               "서비스", "어젯밤", "한꺼번"))
# 다중 거래처: 붙어 있는 두 낱말 "A(이)랑 B한테/에게/네/에" (S-11, PM 10/8)
# "하고"는 동사 연결("계산하고 솔바람한테")과 겹쳐 제외(PM 확인). 와/과(○○청과)·그리고는 계속 제외
_LINK_WORD = re.compile(r"([가-힣A-Za-z0-9]+?)(이랑|랑)")
_LINKED_CP_WORD = re.compile(r"([가-힣A-Za-z0-9]+?)(에게|한테|에)|([가-힣A-Za-z0-9]+네)")
# "콩나물에 3천원"·"콩나물에 삼천원"처럼 "에" 바로 뒤가 숫자·금액이면 가격 표현 — 거래처로 세지 않음
_PRICE_START = re.compile(r"\d|[일이삼사오육칠팔구십백천만억]+원")
# 2차 규칙 전용: 띄어 쓴 금액("오천 원")도 가격. #77 "A랑 B에"는 main 그대로 — "A랑 B에 오천 원씩" 두 곳을 놓치지 않게
_SPACED_PRICE_START = re.compile(r"\d|[일이삼사오육칠팔구십백천만억]+\s?원")
# 다중 거래처 2차 (PM 10/8): "X에 … Y에 …"·"A, B한테"·"A랑 B 두 군데". 품목 수량 = 낱말 처음의
# 숫자 + 세는 말(시각·순서·금액 제외) 또는 수사 + 아는 단위("세 박스"·"삼킬로"). "오이"·"사과"는 수량이 아님
_NUMERAL_WORD = r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|[일이삼사오육칠팔구십백천]+)"
_COUNTERS = tuple(sorted(set(_UNITS) | {"권", "근", "묶음", "통", "톳", "손", "다스", "켤레", "자루", "포대", "상자",
                                        "장", "줄", "송이", "다발", "캔", "컵", "잔", "그릇", "인분", "세트", "봉"},
                         key=len, reverse=True))
_QTY_START = re.compile(r"\d+(?:\.\d+)?\s?(?![시분초일월년호번층차주등위원만천억])[가-힣]|"
                        + _NUMERAL_WORD + r"\s?(?:" + "|".join(_COUNTERS) + ")")
# 이름 "전체"가 수량·금액인 말 ("삼킬로"·"세박스"·"만원"·"삼천원"). 한줄김밥·두손푸드·사장님처럼 수사로 시작하는 이름은 남김
_QTY_OR_AMOUNT_WORD = re.compile(_NUMERAL_WORD + r"(?:" + "|".join(_COUNTERS) + r")|[\d,일이삼사오육칠팔구십백천만억]+원")
# 쉼표 앞 부름말("여보, 솔바람네") — 거래처 이름이 아님. "~님"·"~요"로 끝나는 부름도 같이 (김사장님, 저기요)
_CALL_WORDS = frozenset(("여보", "자기", "엄마", "아빠", "어머니", "아버지", "아저씨", "아줌마", "이모", "삼촌", "고모",
                         "언니", "오빠", "누나", "형", "손님", "사장", "총각", "아가씨"))
# "X에" 두 번·"A랑 B 두 군데"는 적어도 한 곳이 가게 이름(이나 한테·에게)일 때만 — 상자에·트럭에·"대파랑 쪽파 양쪽" 오탐 방지 (PM 10/8)
# 두 글자 이상 끝말만 — 한 글자 집·점·네는 우리집·편의점·언니네 오탐 때문에 뺌 ("○○네·○○집" 두 곳은 main처럼 놓침)
_SHOP_SUFFIXES = ("상회", "마트", "청과", "식당", "상사", "식품", "푸드", "수산", "정육", "반찬", "분식",
                  "유통", "농산", "상점", "슈퍼", "마켓", "축산", "식자재", "건어물", "문구", "제과", "농원", "상가",
                  "카페", "꽃집")
# 표지 없는 이름(PM 10/16)엔 좁은 꼬리말만 — 식품·푸드·반찬·제과 등은 금액 뒤 브랜드·품목("육천 원 바다푸드 어묵",
# "육천 원 밑반찬 두 팩")과 겹쳐 거래처 한 곳 문장을 막는다. 표지 있는 이름(#77·#83)은 위 목록 그대로
# 카페·꽃집 (PM 10/26 새 문장 "소나무카페 빨대 이만 원 별빛꽃집 장미 삼만 원 외상") — 품목 브랜드로는 드문 꼬리말
_BARE_SHOP_SUFFIXES = ("상회", "식당", "마트", "분식", "청과", "상사", "슈퍼", "마켓", "상점", "카페", "꽃집")
# 원문 품목 수 (#120): 숫자 + 세는 말(포 = 포대) 덩어리. LLM이 품목을 줄여 내도 품목 초과를 잡으려고 센다
_SPOKEN_COUNTERS = "|".join(sorted(set(_COUNTERS) | {"포"}, key=len, reverse=True))
_SPOKEN_QTY = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s?(" + _SPOKEN_COUNTERS + ")")
# 표지 없는 가게 이름 ("가나상회 배추 … 외상 다라식당 대파 …", PM 10/16). 조사 에·엔·에는·한테·에게·은·는은 떼고 본다
_BARE_SHOP_WORD = re.compile(r"([가-힣A-Za-z0-9]+?)(?:에게|한테|에는|엔|에|은|는)?[,.]?")
# 수량은 정규화된 숫자 + 세는 말만 ("2박스") — 수사 판정은 "사장님"을 사(4)+장으로 읽는다
_DIGIT_QTY_START = re.compile(r"\d+(?:\.\d+)?\s?(?:" + _SPOKEN_COUNTERS + ")")
# 거래 경계 = 앞 말이 금액("10000원")·쉼표·마침표로 끝나거나 이어 주는 말 — 결제어·시간어는 아래 함수에서
_AFTER_AMOUNT = re.compile(r".*\d원[가-힣]*[,.]?|.*[,.]")
_JOIN_WORDS = frozenset(("그리고", "또"))
_CORRECTION = re.compile(r"아니|말고|정정|잘못")  # "2박스 아니 3박스" — 뒤 덩어리가 앞 덩어리를 대체
# 단가 = 같은 품목의 수량 덩어리가 바로 뒤에 올 때만 ("2개에 3000원씩 6개"). "사과 2박스에 4만원 배 …"는
# 뒤에 수량이 없어 품목 1개로 센다 — 적게 세면 LLM이 줄인 품목이 부분 저장된다
_NEXT_QTY = r"\s?(?=\d+(?:\.\d+)?\s?(?:" + _SPOKEN_COUNTERS + "))"
_UNIT_PRICE_AFTER = re.compile(r"(?:에|당)\s?\d[\d,]*\s?원\s?(?:씩|짜리)?" + _NEXT_QTY)  # 수량과 상관없이
_ONE_PRICE_EACH_AFTER = re.compile(r"\s?\d[\d,]*\s?원\s?씩" + _NEXT_QTY)  # "1개 2000원씩 5개" (수량 1일 때만)
_SPEC_GAP = "짜리"  # "1kg짜리 4봉지" = 규격 + 수량, 한 품목
_PACK_AFTER = re.compile(r"입|들이")  # "24개입" = 입수
# 몰아서 말한 품목 (PM 10/26): "배추 무 대파 쪽파 1박스씩" — 수량 하나를 이름 여럿이 나눠 씀. 이름 사이 쉼표·랑·하고는 뗀다
_EACH_AFTER = re.compile(r"\s?씩")
_LIST_JOINER = re.compile(r"(?:이랑|랑|하고)?[,.]?$")
# 품목 이름 앞에 붙는 꾸밈말 — 이름 하나로 센다 ("국산 배추 무 1박스씩" = 2품목)
_ITEM_MODIFIERS = frozenset(("국산", "중국산", "수입", "수입산", "햇", "큰", "작은", "좋은", "상품", "특상", "특", "왕"))
# 거래처마다 금액만 말한 경우 (PM 10/26): "가나상회 배추 30000원 다라식당 무 20000원 외상" — 수량 대신 금액도 그 거래의 값
_DIGIT_AMOUNT = re.compile(r"\d[\d,]*\s?원")
# "~에"로 자주 오지만 이름이 아닌 말 — 때·순서·대명사·장부·말잇기 ("한번에"·"거기에"·"장부에"·"그래서,")
_NOT_NAME_WORDS = frozenset(("한번", "나중", "다음", "그다음", "중간", "동시", "대신", "때문", "덕분", "거기", "여기",
                             "저기", "이거", "그거", "저거", "이것", "그것", "장부", "그리고", "그래서", "그러면",
                             "그럼", "근데", "그런데", "그러니까", "아니", "일단", "우선", "그냥", "다시"))
_ADVERBS = frozenset(("각각", "각", "다", "모두", "전부", "똑같이", "따로", "같이"))
# 쉼표 "A, B한테·에게·네·에"는 품목 나열("양파, 솔바람한테 3망"·"대파, 쪽파에")과 겹쳐서 나누는 말이 있을 때만 두 곳
_SPLIT_WORDS = re.compile(r"씩|각각|따로|양쪽|(?:두|세|네|[234])\s?(?:군데|곳|집)")
# "A랑 B 두 군데"처럼 이름 바로 뒤의 곳 수. "두 박스"·"둘 다"는 품목에도 써서 넣지 않음
_PLACE_COUNT = re.compile(r"(?:두|세|네|[234])\s?(?:군데|곳|집)|양쪽")


@dataclass
class ParserResult:
    """Parser output before business validation (validation.py decides 200 vs 400)."""

    fields: dict[str, Any]
    utterance_text: str
    counterparty_count: int = 1
    item_count: int | None = None
    # #120: items counted from the utterance (corrections merged) — the LLM path borrows this, not item_count
    spoken_item_count: int = 0
    parser_type: str = "mock"
    is_fallback_used: bool = False
    processing_time_ms: int = 0
    ai_model_used: str | None = None
    # DL-30 (internal, never in the API candidate): "sale" | "purchase" | "return" | "partial_payment" | None.
    # None = not judged / ambiguous → normal candidate + user check. The manual (repair) path stays None.
    transaction_type: str | None = None
    # PM #23: which LLM post-process version ran and which rules fired — stored with the candidate
    # so "규칙이 외상을 비웠는데 사용자가 어떻게 고쳤나" can be traced. None/[] on Rule and manual paths.
    postprocess_version: str | None = None
    postprocess_applied: list[str] = field(default_factory=list)


def _sanitize_utterance(raw: str) -> str:
    """Lightweight strip of markdown/emoji noise before structuring."""
    text = raw.strip()
    # Replace with a space, not "", so "2~3박스" does not become the quantity 23.
    text = re.sub(r"[*_`#>~]", " ", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF]", " ", text)
    return " ".join(text.split())


def _first_word_counterparty(cleaned: str) -> str | None:
    """Counterparty from the first word "○○에/○○로/○○으로" (조사만 뗌, D04-1); None when it is not a name.

    Only the first word is read, so "한 판에" in the middle of a sentence is never a counterparty.
    """
    first = cleaned.split(" ", 1)[0]
    if not re.fullmatch(r"[가-힣A-Za-z0-9]+", first):
        return None
    josa = next((j for j in _FIRST_WORD_JOSA if first.endswith(j)), None)
    if josa is None:
        return None
    name = first[: -len(josa)]
    # 2글자 이하 제외 — 이번·도매·택배 등, 2글자 진짜 거래처는 400 되묻기 (PM #46 2차 리뷰)
    if len(name) < 3 or name[0].isdigit():  # "바로"→"바", "새로"→"새", "3박스에"
        return None
    if says_payment(name) or says_credit(name):  # 현금으로·카드로·외상으로
        return None
    if name in _NON_ITEM_FORMS or name in _UNIT_WORDS or name.startswith(_NON_ITEM_PREFIXES):
        return None
    if _TIME_COMPOUND.fullmatch(name) or re.fullmatch(r"\d+시", name):  # 아침·주말·어제저녁, 3시
        return None
    if _RELATIVE_TIME.fullmatch(name) or _DELIVERY.fullmatch(name):  # 지난달에·작년말에, 퀵서비스로·화물로
        return None
    if name in _NOT_COUNTERPARTY:  # 추가로·반품으로·시장에·한꺼번에
        return None
    return name


def _could_be_name(name: str) -> bool:
    """False for words that are never a counterparty name (1 letter, number, unit, payment, time, adverb)."""
    if len(name) < 2 or name[0].isdigit():
        return False
    if says_payment(name) or says_credit(name):
        return False
    if name in _NON_ITEM_FORMS or name in _UNIT_WORDS or name.startswith(_NON_ITEM_PREFIXES):
        return False
    if _TIME_COMPOUND.fullmatch(name) or _RELATIVE_TIME.fullmatch(name) or _DELIVERY.fullmatch(name):
        return False
    return name not in _NOT_COUNTERPARTY


def _could_be_new_name(name: str) -> bool:
    """`_could_be_name` plus words that are never a name in the 2차 rules (한번·거기·장부·그래서, 삼킬로·만원).

    Kept apart so #77's "A랑 B" judgement stays exactly as on main (세모·한판 stay names there).
    """
    return _could_be_name(name) and name not in _NOT_NAME_WORDS and not _QTY_OR_AMOUNT_WORD.fullmatch(name)


def _is_shop_name(name: str, suffixes: tuple[str, ...] = _SHOP_SUFFIXES) -> bool:
    """True for a shop name such as 새벽상회·솔밭식품 — longer than the ending, so "마트"·"반찬" alone are not."""
    return any(name.endswith(suffix) and len(name) > len(suffix) for suffix in suffixes)


def _starts_with_price(words: list[str], start: int) -> bool:
    """True when words[start:] begins with a number or amount ("3천원", "오만 원")."""
    return bool(_SPACED_PRICE_START.match(" ".join(words[start:start + 2])))


def _starts_with_quantity(words: list[str], start: int) -> bool:
    """True when words[start:] begins with an item quantity ("3박스", "세 박스", "삼킬로") — not a time or price."""
    return bool(_QTY_START.match(" ".join(words[start:start + 2])))


def _linked_counterparties(cleaned: str) -> set[str]:
    """Names joined as "A(이)랑 B한테/에게/네/에" — counted for MULTIPLE only.

    Only two adjacent words are read, so item lists ("사과 3킬로랑 배 2킬로") are not counted.
    "B에" is skipped when the next word is a number or amount ("두부랑 콩나물에 3천원").
    """
    words = cleaned.split(" ")
    names: set[str] = set()
    for i in range(len(words) - 1):
        link = _LINK_WORD.fullmatch(words[i])
        target = _LINKED_CP_WORD.fullmatch(words[i + 1].rstrip(",."))
        if not link or not target:
            continue
        second, marker = (target.group(1), target.group(2)) if target.group(1) else (target.group(3), "네")
        following = words[i + 2] if i + 2 < len(words) else ""
        if marker == "에" and _PRICE_START.match(following):
            continue
        first = link.group(1)
        if first != second and _could_be_name(first) and _could_be_name(second):
            names |= {first, second}
    return names


def _counterparties_each_with_items(cleaned: str) -> set[str]:
    """Names in "X에 [품목 수량] Y에 [품목 수량]" — each "X에/한테/에게" followed by its own item quantity
    (or amount, "가나상회에 배추 30000원, 다라식당에 무 20000원", PM 10/26).

    Returned only when two or more such names are found and at least one is a shop name (새벽상회·한빛마트)
    or marked 한테·에게, so "솔바람에 … 상자에 무 2개" is not counted. "X에" right before a quantity or
    price is an item or price ("큰거에 3개").
    """
    words = cleaned.split(" ")
    anchors: list[tuple[int, str, str]] = []
    for i, word in enumerate(words):
        match = re.fullmatch(r"([가-힣A-Za-z0-9]+?)(에게|한테|에)[,.]?", word)
        if not match or not _could_be_new_name(match.group(1)):  # "삼킬로에"·"만원에"·"한번에"
            continue
        if i + 1 >= len(words) or _starts_with_quantity(words, i + 1) or _starts_with_price(words, i + 1):
            continue
        anchors.append((i, match.group(1), match.group(2)))
    names: set[str] = set()
    sure = False  # 가게 이름이거나 한테·에게로 부른 곳이 하나라도 있나
    for k, (i, name, marker) in enumerate(anchors):
        end = anchors[k + 1][0] if k + 1 < len(anchors) else len(words)
        if any(_starts_with_quantity(words, j) or _DIGIT_AMOUNT.match(" ".join(words[j:j + 2]))
               for j in range(i + 1, end)):
            names.add(name)
            sure = sure or marker != "에" or _is_shop_name(name)
    return names if len(names) >= 2 and sure else set()


def _comma_or_place_count_counterparties(cleaned: str) -> set[str]:
    """Names joined as "A, B한테/에게/네" (with 씩·각각), "A, B에" (also an item after), or "A랑/A, B 두 군데·양쪽".

    A comma also lists items ("양파, 솔바람한테 3망"), so a comma pair counts only with a splitting word.

    "두 군데·양쪽" is counted only when A or B is a shop name (새벽상회), so "대파랑 쪽파 양쪽" stays one.
    """
    words = cleaned.split(" ")
    names: set[str] = set()
    for i in range(len(words) - 1):
        comma = re.fullmatch(r"([가-힣A-Za-z0-9]+),", words[i])
        link = comma or _LINK_WORD.fullmatch(words[i])
        if not link:
            continue
        first, following = link.group(1), words[i + 1].rstrip(",.")
        if comma and _is_call_word(first):  # "여보, 솔바람네"·"사장님, 솔바람한테"
            continue
        rest = " ".join(words[i + 2:])
        second = None
        target = _LINKED_CP_WORD.fullmatch(following) if comma else None
        if target:
            second, marker = (target.group(1), target.group(2)) if target.group(1) else (target.group(3), "네")
            if not _SPLIT_WORDS.search(rest) or (marker == "에" and not _item_comes_next(words, i + 2)):
                second = None
        if (second is None and re.fullmatch(r"[가-힣A-Za-z0-9]+", following) and _PLACE_COUNT.match(rest)
                and (_is_shop_name(first) or _is_shop_name(following))):
            second = following
        if second and first != second and _could_be_new_name(first) and _could_be_new_name(second):
            names |= {first, second}
    return names


def _starts_transaction(words: list[str], i: int) -> bool:
    """True when words[i] opens a new transaction: sentence start, or right after an amount, a comma, a payment
    word (외상·현금), 그리고·또 or a time word. "다라식품 두부"·"3모 바다푸드" (brands inside an item) are not."""
    if i == 0:
        return True
    prev = words[i - 1]
    bare = prev.rstrip(",.")
    return (bool(_AFTER_AMOUNT.fullmatch(prev)) or says_credit(prev) or says_payment(prev)
            or bare in _JOIN_WORDS or bare in _TIME_WORDS)


def _unmarked_shop_counterparties(cleaned: str) -> set[str]:
    """Shop names without a marker, each opening its own transaction with its own quantity (PM 10/16):
    "가나상회 배추 2박스 10000원 외상 다라식당 대파 1단 3000원 외상".

    Returned only for two or more different names. A shop name counts only at a transaction boundary
    (`_starts_transaction`) and only when a digit quantity ("2박스") or amount ("30000원", PM 10/26) comes before
    the next counted name, so
    "가나상회 아니 다라식당에 …"·"다라식당 거랑 같은"·"다라식당 사장님이" and brand names stay one place.
    Only `_BARE_SHOP_SUFFIXES` count here, so a brand after an amount ("육천 원 바다푸드 어묵") stays an item.
    Known gaps: "가나상회 배추 2박스 다라식당 대파 1단 합쳐서 13000원" (a shop right after a quantity) — counting
    that place would also block brands ("두부 3모 바다푸드 어묵"); two brand-like shops ("다라식품 … 바다푸드 …").
    """
    words = cleaned.split(" ")
    anchors: list[tuple[int, str]] = []
    for i, word in enumerate(words):
        match = _BARE_SHOP_WORD.fullmatch(word)
        if match and _is_shop_name(match.group(1), _BARE_SHOP_SUFFIXES) and _starts_transaction(words, i):
            anchors.append((i, match.group(1)))
    names: set[str] = set()
    for k, (i, name) in enumerate(anchors):
        end = anchors[k + 1][0] if k + 1 < len(anchors) else len(words)
        if any(_DIGIT_QTY_START.match(" ".join(words[j:j + 2])) or _DIGIT_AMOUNT.match(" ".join(words[j:j + 2]))
               for j in range(i + 1, end)):
            names.add(name)
    return names if len(names) >= 2 else set()


def _is_call_word(word: str) -> bool:
    """True for a word used to call someone before a comma (여보, 어머님, 사장님, 저기요) — never a counterparty."""
    return word in _CALL_WORDS or word.endswith(("님", "요"))


def _item_comes_next(words: list[str], start: int) -> bool:
    """True when the next word after adverbs (각각·모두) is an item name, not a quantity or price."""
    while start < len(words) and words[start] in _ADVERBS:
        start += 1
    return start < len(words) and not _starts_with_quantity(words, start) and not _starts_with_price(words, start)


def _is_listed_item_word(word: str) -> bool:
    """True for a bare item name in a run before a shared quantity ("배추", "대파랑") — not a counterparty, time,
    payment word, number or unit. A shop name or a word marked 에·한테·에게·네 ends the run (it is the counterparty)."""
    if not re.fullmatch(r"[가-힣A-Za-z]+", word):
        return False
    if re.search(r"(?:에게|한테|에|네)$", word) or _is_shop_name(word):
        return False
    if word in _NON_ITEM_FORMS or word in _UNIT_WORDS or word.startswith(_NON_ITEM_PREFIXES):
        return False
    if says_payment(word) or says_credit(word) or word in _JOIN_WORDS:
        return False
    return not (word in _TIME_WORDS or _TIME_COMPOUND.fullmatch(word) or _RELATIVE_TIME.fullmatch(word))


def _listed_items_before(text: str, start: int) -> int:
    """Item names listed right before a shared quantity ("가나상회 배추 무 대파 쪽파 1박스씩" → 4), adverbs (각각·다)
    skipped; a modifier (국산·큰) belongs to the next name. 0 or 1 when it is not a list."""
    words = text[:start].split()
    while words and words[-1] in _ADVERBS:
        words.pop()
    names = 0
    for word in reversed(words):
        bare = _LIST_JOINER.sub("", word)
        if not _is_listed_item_word(bare):
            break
        names += bare not in _ITEM_MODIFIERS
    return names


def _count_spoken_items(text: str) -> int:
    """Number of items spoken in `text` (#120): quantity + counter chunks (one per listed name before "N단위씩"),
    minus unit prices followed by the item's quantity ("2개에 3000원씩 6개", "1개 2000원씩 5개"), pack sizes ("24개입"), a spec right before a quantity ("1kg 4봉지"·"1kg짜리 4봉지") and a
    chunk replaced by a correction."""
    chunks = [m for m in _SPOKEN_QTY.finditer(text)
              if not _PACK_AFTER.match(text, m.end()) and not _UNIT_PRICE_AFTER.match(text, m.end())
              and not (m.group(1) == "1" and _ONE_PRICE_EACH_AFTER.match(text, m.end()))]
    gaps = [text[a.end():b.start()].strip() for a, b in pairwise(chunks)]
    # "배추 무 대파 쪽파 1박스씩" — one quantity shared by every listed name (PM 10/26)
    shared = sum(max(_listed_items_before(text, m.start()) - 1, 0) for m in chunks if _EACH_AFTER.match(text, m.end()))
    return len(chunks) + shared - sum(1 for gap in gaps if gap in ("", _SPEC_GAP) or _CORRECTION.search(gap))


def mock_parse_utterance(utterance_text: str) -> ParserResult:
    """
    Deterministic scaffold parser for local FE wiring — single item only.

    Real path (later): LLM ≤5s → Rule fallback. Missing values stay null (never invented).
    """
    started = time.perf_counter()
    cleaned = _sanitize_utterance(utterance_text)

    cp_matches = _CP_PATTERN.findall(cleaned)
    counterparty = cp_matches[0] if cp_matches else None
    # 대체용: 표지 거래처가 없을 때만. counterparty_count(다중 거래처 판정)는 표지·연결어 이름으로 센다 — LLM 경로도 이 값을 빌려 씀
    first_word_cp = None if cp_matches else _first_word_counterparty(cleaned)
    if first_word_cp:
        counterparty = first_word_cp

    # 금액이 하나면 품목 금액=총액, 여럿이면(단가·총액 등) 품목 금액은 비우고 마지막 값을 총액으로
    amounts = [int(m.replace(",", "")) for m in _AMOUNT_PATTERN.findall(cleaned)]
    total_amount = amounts[-1] if amounts else None
    amount = amounts[0] if len(amounts) == 1 else None

    qty: Decimal | None = None  # float 금지 — 금액 계산 오차 (D04-4)
    unit: str | None = None
    qty_match = _QTY_PATTERN.search(cleaned)
    if qty_match:
        qty = Decimal(qty_match.group(1))
        unit = qty_match.group(2)

    # 후처리와 같은 낱말 경계 판정 ("카드지갑"은 결제어 아님). 외상어·결제어가 둘 다 있으면 null → 사용자 확인
    credit_said, payment_said = says_credit(cleaned), says_payment(cleaned)
    credit: bool | None = credit_said if credit_said != payment_said else None
    if says_partial_payment(cleaned):  # 부분 결제("중에 3000원 받았어", "나머지는 외상") → 확인 (DL-30)
        credit = None
    # "현금 아직 못 받았어"·"현금 나중에"·"카드는 월말에" → 현금 아님, 확인 (PM #82 · 10/12 미래 표지)
    if credit is False and says_not_paid_now(cleaned):
        credit = None

    # 거래처로 쓴 첫 단어("솔바람에")는 품목명 후보에서 뺀다
    rest = _CP_PATTERN.sub(" ", cleaned.partition(" ")[2] if first_word_cp else cleaned)
    words = [w for w in re.findall(r"[가-힣A-Za-z]+", rest) if not w.startswith(_NON_ITEM_PREFIXES) and w not in _NON_ITEM_FORMS and w not in _UNIT_WORDS]
    name = words[0] if words else None

    has_item = name is not None or qty is not None or amount is not None
    items = [{"name": name, "qty": qty, "unit": unit, "unit_price": None, "amount": amount}] if has_item else []

    # 수량 표현 2개 이상 = 여러 품목 → Rule은 1품목으로 확정하지 않음 (PM #46, CLAUDE.md §4-2)
    qty_matches = _QTY_PATTERN.findall(cleaned)
    item_count: int | None = None
    if len(qty_matches) >= 2:
        item_count = len(qty_matches)  # 4개 이상 → EXCEED_MAX_ITEMS
    spoken_item_count = _count_spoken_items(cleaned)
    if spoken_item_count > MAX_ITEMS:  # 근·통·톳처럼 _UNITS에 없는 단위로 말한 4품목 (#120)
        item_count = max(item_count or 0, spoken_item_count)
    if 2 <= len(qty_matches) <= 3:
        # 첫 품목 이름만 읽고 나머지 이름·금액은 지어내지 않음 → MISSING(items[i].name)로 되묻기
        items = [{"name": name if i == 0 else None, "qty": Decimal(q), "unit": u, "unit_price": None,
                  "amount": amount if i == 0 else None} for i, (q, u) in enumerate(qty_matches)]

    counterparty_count = len(set(cp_matches) | _linked_counterparties(cleaned) | _counterparties_each_with_items(cleaned)
                             | _comma_or_place_count_counterparties(cleaned) | _unmarked_shop_counterparties(cleaned)) or 1
    # 확실한 부분 결제("2만원 받고 남은 1만원 외상") → 지원하지 않는 거래, 다시 말하기 (DL-30, PR #128 PM 요청).
    # AI 경로도 이 판정을 쓴다 (parse_pipeline). 거래처가 둘 이상이면 판정하지 않는다 — "솔바람한테 1만원 외상이고
    # 새벽상회한테 9천원 현금"은 거래처마다 다른 결제라 다중 거래처 안내가 맞다 (PM 10/14 후속)
    transaction_type = "partial_payment" if counterparty_count == 1 and says_clear_partial_payment(cleaned) else None
    # 판매가 아닌 말 (PM 10/26) — 입금·고치기·외상 조회·외상 옮기기는 그 화면으로 안내. AI 경로도 이 판정을 쓴다
    transaction_type = transaction_type or non_sale_kind(
        cleaned, has_quantity=_SPOKEN_QTY.search(cleaned) is not None, amounts=len(amounts))

    return ParserResult(
        fields={"counterparty": counterparty, "items": items, "total_amount": total_amount, "credit": credit},
        utterance_text=cleaned,
        counterparty_count=counterparty_count,
        item_count=item_count,
        spoken_item_count=spoken_item_count,
        processing_time_ms=int((time.perf_counter() - started) * 1000),
        transaction_type=transaction_type,
    )
