import math


def tip(amount: float, pct: float) -> float:
    return round(amount * pct / 100, 2)


def split(total: float, people: int) -> float:
    return math.ceil(total * 100 / people) / 100
