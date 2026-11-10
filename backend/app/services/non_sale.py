"""Sentences that are not a sale (PM 10/26 내부 테스트 ②) — each belongs to another screen, not a new record.

Read with the Rule on the normalized utterance; the LLM path borrows the result like partial payment (DL-30), so the
model's guess (items · amount) never turns them into a sale that a 되묻기 answer then saves. Narrow on purpose: each
kind needs its own words, and apart from 고치기 a spoken item quantity ("3박스") means a sale. Pure function, no
logging (PII).
"""

import re
from typing import Literal

NonSale = Literal["payment", "edit", "inquiry", "moved_credit"]

# ① 입금 — 외상값을 받았다: 빚 낱말 + 받은 낱말 ("외상값 5만 원 입금 들어왔어", "외상 다 갚았어")
_DEBT = re.compile(r"외상\s?(?:값|대금)|외상\s?(?:을|은|를)?\s?(?:다\s?)?갚|밀린\s?(?:돈|외상|값)|미수금")
_RECEIVED = re.compile(r"입금|들어왔|받았|받아\s?(?:냄|냈)|갚았|송금|보내\s?왔|부쳐\s?왔")
# "값" 없이도 외상 + 금액 + 입금/들어왔/받았 순서면 입금 — "외상 오만 원 입금 들어왔어" (PM 내부 테스트 2-17).
# 사이에는 어디로·언제 받았는지만 낄 수 있다 — "김씨네 외상 오만 원 계좌로 들어왔어" (PM 11/2)
# 입금은 끝났다는 말이거나 문장 끝일 때만: "… 외상 입금은 내일", "외상 오만 원 입금 안 들어왔어"는 입금이 아님
_PAID_VIA = r"(?:(?:계좌|통장|현금|현찰|카드|이체|송금)(?:으로|로)?|오늘|방금|아까|어제)"
_CREDIT_AMOUNT_PAID = re.compile(rf"외상\s?(?:이|을|은)?\s?[\d,]+\s?원\s?(?:{_PAID_VIA}\s){{0,2}}"
                                 r"(?:입금(?:\s?(?:들어왔|들어옴|됐|되었|했|받았|받음|왔|완료|함(?=[\s.,!?~]|$))|[\s.,!?~]*$)"
                                 r"|들어(?:왔|옴)|받았|받음|받아\s?(?:냄|냈))")
# ② 고치기 — 이미 적은 장부: "아까 … 3박스 아니고 4박스였어" (지난 기록 + 아니고 … 였어) 또는 고쳐 달라는 말.
# "배추 3박스 아니 4박스 외상"처럼 지금 말하며 바로잡는 건 새 판매 (#120 정정)
# 기록을 가리키는 말은 고치기, 때 말만 있으면 수량 + 금액 하나인 완전한 판매는 판매 — "어제 … 아니고 무 두 박스 이만 원
# 외상이었어" (승현 #235 리뷰)
# "넣은"은 빼둠 — "가나상회에 넣은 배추"는 납품 (승현 #237 리뷰 문장). "아까 넣은 거 …"는 때 말 쪽에서 고치기로 남음
_RECORD_REF = re.compile(r"장부에|적은|기록한|입력한")
_TIME_REF = re.compile(r"아까|방금|좀\s?전|어제|그저께|지난번|저번에")
# 끝말은 "였어"(지난 일)도 "이야"(지금 말투)도 — "아까 적은 명함 다섯 통 아니고 여섯 통이야" (PM 10/26 새 문장)
# 끝말은 낱말 끝에서만 ("야채"의 야 X), 외상·현금에 붙은 지금 말투는 판매의 끝말 — "… 외상이에요" (수경 #228 사후 리뷰)
_WAS_NOT = re.compile(r"(?:아니고|아니라)\s.*?(?:(?:였|이었)(?:어|어요|네|네요|다|음|지|거든|는데)"
                      r"|(?<!외상)(?<!현금)(?<!외상이)(?<!현금이)(?:이야|야|이에요|에요|예요|입니다))(?=[\s.,!?~]|$)")
_MISRECORDED = re.compile(r"잘못\s?(?:적|입력|넣|기록|저장|들어)")
# 고쳐 줘·정정해는 값 없이 말할 때만 — "8만원이라고 했는데 9만원으로 고쳐줘 외상"은 지금 말하는 판매의 정정 (Seed)
_FIX_VERB = re.compile(r"(?:고쳐|수정해|바꿔)\s?(?:줘|주세요|줄래)|정정해")
# ③ 외상 조회 — 묻는 말: "외상 얼마 남았어?" (얼마 전·얼마 동안은 때, 얼마어치는 판매 — Seed "얼마어치 좀 달아놔")
_INQUIRY = re.compile(r"얼마(?!\s?(?:전|동안|간|만에|어치|치))|잔액|(?:외상|미수)\s?(?:좀\s?)?(?:알려|보여|확인해)")
# ④ 외상 옮기기 — 그동안 쌓인 외상 한 덩어리: 기간 + 합친 말, 또는 밀린·쌓인 외상 ("이번 주 김씨네 외상 합쳐서 20만 원")
_PERIOD = re.compile(r"이번\s?주|지난\s?주|저번\s?주|이번\s?달|지난\s?달|저번\s?달|그동안|여태|지금까지|몇\s?달|작년|올해")
_TOTALED = re.compile(r"합쳐서|합해서|모아서|몰아서|전부|모두|다\s?해서|통틀어|누적")
_PILED_CREDIT = re.compile(r"(?:밀린|쌓인)\s?외상|그동안\s?(?:\S+\s)?외상")


def non_sale_kind(text: str, *, has_quantity: bool, amounts: int) -> NonSale | None:
    """Which screen the sentence belongs to, or None for a sale (or anything unsure → the usual questions).

    `text` is the normalized utterance; `has_quantity` = an item quantity was spoken ("3박스"), `amounts` = how many
    "N원" were said.
    """
    complete_sale = has_quantity and amounts == 1
    if _MISRECORDED.search(text) or (_WAS_NOT.search(text) and (
            _RECORD_REF.search(text) or (_TIME_REF.search(text) and not complete_sale))):
        return "edit"  # names the quantities it corrects, so before the quantity check
    if has_quantity:
        return None
    if _FIX_VERB.search(text) and not amounts:
        return "edit"
    # one amount only: "외상값 2만원 받았고 오늘 배추 만원은 현금" also makes a sale (partial-payment look-alike test)
    if amounts <= 1 and ((_DEBT.search(text) and _RECEIVED.search(text)) or _CREDIT_AMOUNT_PAID.search(text)):
        return "payment"
    if not amounts and _INQUIRY.search(text):
        return "inquiry"
    if amounts and "외상" in text and not _RECEIVED.search(text) and (
            _PILED_CREDIT.search(text) or (_PERIOD.search(text) and _TOTALED.search(text))):
        return "moved_credit"
    return None
