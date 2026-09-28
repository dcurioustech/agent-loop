import subprocess
import sys


def test_negative_amount():
    r = subprocess.run([sys.executable, "-m", "tipcalc", "--", "-5"])
    assert r.returncode == 2
