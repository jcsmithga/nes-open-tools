"""A standard course's pars, judged in plain Python, for tests of the composed draw."""

from collections import Counter
from collections.abc import Sequence
from itertools import pairwise

NINE = 9
#: four par 3s, ten par 4s and four par 5s
COUNTS = {3: 4, 4: 10, 5: 4}


def is_standard(pars: Sequence[int]) -> bool:
    """Par 72's counts, each par split between the nines within one, no two par 3s or
    par 5s in a row, and no par 3 on the 18th."""
    front, back = Counter(pars[:NINE]), Counter(pars[NINE:])
    return (
        Counter(pars) == COUNTS
        and all(abs(front[par] - back[par]) <= 1 for par in COUNTS)
        and not any(one == other != 4 for one, other in pairwise(pars))
        and pars[-1] != 3
    )
