import argparse

from .core import split, tip

p = argparse.ArgumentParser(prog="tipcalc")
p.add_argument("amount", type=float)
p.add_argument("--pct", type=float, default=15)
p.add_argument("--people", type=int, default=1)
a = p.parse_args()
if a.amount < 0:
    p.error("amount must be >= 0")
t = tip(a.amount, a.pct)
print(f"tip {t:.2f} | total {a.amount + t:.2f} | each {split(a.amount + t, a.people):.2f}")
