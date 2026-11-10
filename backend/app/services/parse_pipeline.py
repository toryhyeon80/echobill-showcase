"""Utterance → raw candidate fields: LLM first when enabled, Rule fallback (CLAUDE.md §4-3)."""

import logging
import re
import time
from decimal import Decimal

from app.config import Settings, get_settings
from app.services import cascade, parse_llm
from app.services.parse_mock import ParserResult, _sanitize_utterance, mock_parse_utterance

logger = logging.getLogger(__name__)

# DL-30 (PM 10/14): the model's judgment counts for purchases and returns only, and only with a word for it in the
# utterance ("낱말만으로 단정하지 않고 LLM 판별과 함께"); partial payment is the Rule's (#128).
# 2차 (PM 10/14): the model called plain sales purchases ("농협마트 감자 … 현금") without any such word.
# No "사입": it is inside 회사입니다·검사입니다 (PM 10/14).
_LLM_TYPE_WORDS = {
    "purchase": re.compile(r"샀|사\s?왔|매입"),
    "return": re.compile(r"반품|환불|취소|돌려받|물렀"),
}


def combined_transaction_type(rule_type: str | None, llm_type: str | None, utterance_text: str) -> str | None:
    """Transaction kind for validation: the Rule's (clear partial payment) first, then an LLM purchase/return
    when the utterance has a word for it (샀·사왔·매입 / 반품·환불·취소·돌려받·물렀).

    The LLM's "sale", a type without its word (and None, flag off) leave the Rule result as is.
    """
    if rule_type is not None:
        return rule_type
    words = _LLM_TYPE_WORDS.get(llm_type or "")
    return llm_type if words is not None and words.search(utterance_text) else None


def rule_parse(utterance_text: str, settings: Settings) -> ParserResult:
    """Rule parser on the same text the LLM gets (sanitize + KO_NUMERAL_NORMALIZE); the stored utterance stays original.

    PM #39 리뷰: "1만원"·"3만6천원" were not read as amounts because only the LLM path was normalized.
    The normalized text is parse input only — `utterance_text` keeps the (sanitized) original (DL-05).
    """
    result = mock_parse_utterance(parse_llm.build_llm_input(utterance_text, settings))
    result.utterance_text = _sanitize_utterance(utterance_text)
    return result


def structure_utterance(utterance_text: str, settings: Settings | None = None) -> ParserResult:
    """
    Structure one utterance for validation.

    LLM_PARSE_ENABLED off → Rule (current mock) only. On → GPT-4o-mini; an LLMParseError
    (timeout over 5s, provider or schema failure, refusal) falls back to Rule with
    is_fallback_used=True, and the request still succeeds (HTTP 200 path). Any other
    exception is a bug and propagates as 500 (CLAUDE.md §3②).
    """
    settings = settings or get_settings()
    if not settings.llm_parse_enabled:
        return rule_parse(utterance_text, settings)

    started = time.perf_counter()
    try:
        llm = parse_llm.parse_utterance_with_llm(utterance_text, settings=settings)
    except parse_llm.LLMParseError as exc:
        logger.warning("LLM parse fell back to rule: %s", type(exc).__name__)
        fallback = rule_parse(utterance_text, settings)
        fallback.is_fallback_used = True
        fallback.processing_time_ms = int((time.perf_counter() - started) * 1000)
        return fallback

    fields = llm.candidate.model_dump()
    for item in fields["items"]:
        if item["qty"] is not None:
            qty = Decimal(str(item["qty"]))  # float 금지 — 금액 계산 오차 (D04-4)
            # LLM은 정수 수량도 2.0으로 준다 → 확인 화면에 "2.0판"이 보이지 않게 2로
            item["qty"] = qty.quantize(Decimal(1)) if qty == qty.to_integral_value() else qty
    rule = rule_parse(utterance_text, settings)
    # 회의 10/23: on a doubt signal a second model is asked; fields where the answers differ are emptied (asked again)
    fields, asked = cascade.apply(utterance_text, fields, llm.postprocess_applied, settings)
    return ParserResult(
        fields=fields,
        utterance_text=_sanitize_utterance(utterance_text),
        # LLM 스키마는 거래처를 하나만 받는다 → 다중 거래처 감지는 Rule 패턴으로 (임시, 근본 해결은 A-4b)
        counterparty_count=rule.counterparty_count,
        # LLM이 품목을 줄여 내도(4개 → 3개) 원문 품목 수로 품목 초과를 판정 (#120)
        item_count=max(len(fields["items"]), rule.spoken_item_count),
        # 확실한 부분 결제(DL-30)는 원문 Rule 판정 (PR #128). LLM_TRANSACTION_TYPE이 켜지면 낱말이 있는 모델의 매입·반품도 (PM 10/14)
        transaction_type=combined_transaction_type(rule.transaction_type, llm.transaction_type, utterance_text),
        parser_type="llm",
        # whole wall time from `started`, like the fallback path: LLM + second model (#213·#226)
        processing_time_ms=int((time.perf_counter() - started) * 1000),
        ai_model_used=llm.response_model or llm.model,
        postprocess_version=llm.postprocess_version,
        postprocess_applied=list(llm.postprocess_applied) + asked,
    )
