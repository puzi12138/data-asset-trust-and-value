"""Rules from the bank-additional documentation, plus explicit plausibility bounds."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldRule:
    kind: str
    allowed: tuple[str, ...] = ()
    lower: float | None = None
    upper: float | None = None
    integer: bool = False


def numeric(lower=None, upper=None, integer=False):
    return FieldRule("numeric", lower=lower, upper=upper, integer=integer)


def category(values: str):
    return FieldRule("category", tuple(values.split("|")))


# `unknown` is a permitted source token but treated as missing by the quality/model pipeline.
RULES = {
    "age": numeric(0, 120, True),
    "job": category("admin.|blue-collar|entrepreneur|housemaid|management|retired|self-employed|services|student|technician|unemployed|unknown"),
    "marital": category("divorced|married|single|unknown"),
    "education": category("basic.4y|basic.6y|basic.9y|high.school|illiterate|professional.course|university.degree|unknown"),
    "default": category("no|yes|unknown"),
    "housing": category("no|yes|unknown"),
    "loan": category("no|yes|unknown"),
    "contact": category("cellular|telephone"),
    "month": category("jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"),
    "day_of_week": category("mon|tue|wed|thu|fri"),
    "campaign": numeric(1, None, True),
    "pdays": numeric(0, 999, True),
    "previous": numeric(0, None, True),
    "poutcome": category("failure|nonexistent|success"),
    "emp.var.rate": numeric(),
    "cons.price.idx": numeric(0),
    "cons.conf.idx": numeric(),
    "euribor3m": numeric(0),
    "nr.employed": numeric(0),
    "y": category("no|yes"),
}
FEATURES = [c for c in RULES if c != "y"]
NUMERIC = [c for c in FEATURES if RULES[c].kind == "numeric"]
CATEGORICAL = [c for c in FEATURES if RULES[c].kind == "category"]
