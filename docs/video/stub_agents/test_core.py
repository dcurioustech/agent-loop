from tipcalc.core import split, tip


def test_tip():
    assert tip(100, 15) == 15.0


def test_split_even():
    assert split(30, 3) == 10.0


def test_split_rounds_up():
    assert split(10, 3) == 3.34
