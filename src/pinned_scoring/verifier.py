"""Deterministic narration verifier: blocks claims that differ from the receipt. No LLM, no network."""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .receipt import Receipt

DEFAULT_BANDS = ("low", "moderate", "medium", "high", "extreme", "severe", "critical")
PROVENANCE_WORDS = ("measured", "generated", "fixture")
REASON_WORDS = {"missing": "missing", "expired": "expired", "stale": "expired", "invalid": "invalid"}

_NUM = r"(\d+(?:\.\d+)?)"
_FILLER = (r"(?:(?:of|is|was|at|to|be|are|about|around|approximately|roughly|nearly|now|total|"
           r"overall|final|came|out|reached|equals?)\b[\s:=~]*){0,4}")
_SCORE_AFTER_WORD = re.compile(r"\bscor(?:e|es|ed|ing)\b[\s:=~]*" + _FILLER + _NUM, re.I)
_SCORE_SUFFIX = re.compile(_NUM + r"\s*(?:/\s*100\b|out\s+of\s+100\b|points?\b)", re.I)
# A negator (or a verb that denies the property) scopes over up to three following words of the
# same clause, so "no missing or invalid inputs" and "prevents a complete assessment" are negated.
_NEGATION = re.compile(
    r"(?:\b(?:no|not|nothing|none|without|zero|never|prevents?|preventing|prevented|"
    r"absence\s+of|lacks?|lacking|unable\s+to|cannot)|n't)[^\w,]+(?:\w+[^\w,]+){0,3}$", re.I)
_SENTENCE_SPLIT = re.compile(r"\.(?!\d)|[;\n!?]")
# Clause boundaries inside a sentence: contrast/subordinating words and ", <determiner>".
# Reason and provenance words are attributed only to names in the same clause.
_CLAUSE_SPLIT = re.compile(
    r"\b(?:but|however|although|though|while|whereas|yet|which)\b"
    r"|,\s+(?:and\s+)?(?=(?:the|a|an|this|that|it|its|there)\b)")
_PROV_ONE = r"(?:measur(?:ed|ements?)|generated|fixture)"
_PROV_LIST = rf"{_PROV_ONE}(?:\s*(?:,|/|or|and)\s*(?:valid\s+)?{_PROV_ONE})*"


@dataclass(frozen=True)
class Violation:
    kind: str
    detail: str


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    violations: tuple = field(default_factory=tuple)


def _dec(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _name_pattern(name: str) -> re.Pattern:
    escaped = "[ _]".join(re.escape(part) for part in name.lower().split("_"))
    return re.compile(r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])")


def _score_claims(text: str) -> list:
    seen, claims = set(), []
    for pattern in (_SCORE_AFTER_WORD, _SCORE_SUFFIX):
        for m in pattern.finditer(text):
            if m.start(1) in seen:
                continue
            seen.add(m.start(1))
            claims.append(m.group(1))
    return claims


def _band_claims(text: str, labels) -> list:
    alt = "|".join(sorted((re.escape(b) for b in labels), key=len, reverse=True))
    found = []
    patterns = (
        rf"\b(?:band|rating|rated|category|classified\s+as|classification|risk\s+level|risk)"
        rf"\s*(?:is|of|:|=|as|at|was|in\s+the)?\s*(?:the\s+)?[\"']?({alt})\b",
        rf"\b({alt})[\s-]+(?:band|risk|category|rating)\b",
    )
    for pattern in patterns:
        found.extend(m.group(1).lower() for m in re.finditer(pattern, text, re.I))
    return found


def verify_narration(text: str, receipt: Receipt, known_bands=DEFAULT_BANDS) -> VerificationResult:
    violations: list = []
    lowered = re.sub(re.escape(receipt.entity_id.lower()), " entity ", text.lower())
    incomplete = receipt.status == "incomplete"

    for raw in _score_claims(lowered):
        claimed = _dec(raw)
        if incomplete:
            violations.append(Violation("score_on_incomplete",
                                        f"states score {raw} but receipt is incomplete"))
        elif claimed is None or claimed != receipt.score:
            violations.append(Violation("score_mismatch",
                                        f"claims score {raw}; receipt score is {receipt.score}"))

    labels = {b.lower() for b in known_bands} | ({receipt.band.lower()} if receipt.band else set())
    for label in _band_claims(lowered, labels):
        if incomplete:
            violations.append(Violation("band_on_incomplete",
                                        f"states band '{label}' but receipt is incomplete"))
        elif label != (receipt.band or "").lower():
            violations.append(Violation("band_mismatch",
                                        f"claims band '{label}'; receipt band is '{receipt.band}'"))

    factor_prov = {c[0].lower(): str(c[2]).lower() for c in receipt.components}
    reason_kind = {}
    for reason in receipt.reasons:
        kind, _, metric = reason.partition(":")
        reason_kind[metric.lower()] = kind
    mentions = [(name, _name_pattern(name), prov) for name, prov in factor_prov.items()]
    mentions += [(metric, _name_pattern(metric), None) for metric in reason_kind]

    clauses = [c for s in _SENTENCE_SPLIT.split(lowered) for c in _CLAUSE_SPLIT.split(s) if c]
    for sentence in clauses:
        hits = []
        for name, pattern, prov in mentions:
            for m in pattern.finditer(sentence):
                hits.append((m.start(), m.end(), name, prov))
        hits.sort()
        for idx, (start, end, name, prov) in enumerate(hits):
            after_end = hits[idx + 1][0] if idx + 1 < len(hits) else len(sentence)
            before_start = hits[idx - 1][1] if idx else 0
            segment = sentence[end:after_end]
            words = [w for w in PROVENANCE_WORDS if re.search(rf"\b{w}\b", segment)]
            if not words:
                segment = sentence[before_start:start]
                words = [w for w in PROVENANCE_WORDS if re.search(rf"\b{w}\b", segment)]
            # "measured or generated" is a disjunction: accept it when the receipt value is one of the listed
            if prov is not None and prov in words and re.search(
                    rf"\b{prov}\b\s*(?:,|/|or|and)\s*\b(?:{'|'.join(PROVENANCE_WORDS)})\b|"
                    rf"\b(?:{'|'.join(PROVENANCE_WORDS)})\b\s*(?:,|/|or|and)\s*\b{prov}\b",
                    segment):
                continue
            for word in words:
                if prov is None:
                    violations.append(Violation(
                        "provenance_unsupported",
                        f"claims {name} is {word}, but receipt has no usable value for it"))
                elif word != prov:
                    violations.append(Violation(
                        "provenance_mismatch", f"claims {name} is {word}; receipt says {prov}"))

        m = re.search(r"\b(?:all|every|each)\b([^,]*)\b(?:inputs?|factors?|components?|data|readings?)\b"
                      rf"[^,]*?\b({_PROV_LIST})", sentence)
        if m:
            claimed = {"measured" if w.startswith("measur") else w
                       for w in re.findall(r"measur\w+|generated|fixture", m.group(2))}
            wrong = sorted({p for p in factor_prov.values() if p not in claimed})
            # "all other factors ..." does not cover the unusable factor
            excludes_failed = re.search(r"\b(?:other|remaining|rest)\b", m.group(1))
            if wrong or (incomplete and not excludes_failed):
                violations.append(Violation(
                    "provenance_mismatch",
                    f"claims all inputs {'/'.join(sorted(claimed))}; "
                    f"receipt has {wrong or 'missing inputs'}"))

        for word, kind in REASON_WORDS.items():
            for wm in re.finditer(rf"\b{word}\b", sentence):
                if _NEGATION.search(sentence[:wm.start()]):
                    continue
                if not incomplete:
                    violations.append(Violation(
                        "completeness_mismatch", f"claims '{word}' but receipt is scored"))
                    continue
                for name, pattern, _ in mentions:
                    if pattern.search(sentence):
                        actual = reason_kind.get(name)
                        if actual != kind:
                            violations.append(Violation(
                                "reason_mismatch",
                                f"claims {name} is {word}; receipt says {actual or 'scored'}"))

        for wm in re.finditer(r"\b(incomplete|unscored|cannot be scored|could not be scored)\b",
                              sentence):
            if not incomplete and not _NEGATION.search(sentence[:wm.start()]):
                violations.append(Violation("completeness_mismatch",
                                            "claims incomplete but receipt is scored"))
        if incomplete:
            for cm in re.finditer(r"(?<!in)(?<!to )\b(complete|fully scored|completely scored)\b",
                                  sentence):
                if not _NEGATION.search(sentence[:cm.start()]):
                    violations.append(Violation("completeness_mismatch",
                                                "claims complete but receipt is incomplete"))
                    break

    unique = tuple(dict.fromkeys(violations))
    return VerificationResult(ok=not unique, violations=unique)
