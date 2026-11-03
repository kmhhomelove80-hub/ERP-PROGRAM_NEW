"""Exact location selection; never silently return a heuristic allocation."""
from django.core.exceptions import ValidationError

MAX_STATES = 50000
MAX_COMPLEX_LOCATIONS = 200
MAX_TRANSITIONS = 2000000


def choose_locations(capacities, requested, *, budget=None):
    """Input sorted by stable location order. Return selected indices.

    Min cardinality follows from descending capacities. Sparse cardinality/sum
    DP finds the minimum covering sum for exactly that cardinality. A work cap
    rejects pathological subset-sum inputs explicitly, rather than misallocating.
    """
    n = len(capacities)
    if sum(capacities) <= requested:
        return tuple(range(n))
    for i, value in enumerate(capacities):
        if value == requested:
            return (i,)
    seen = {}
    pair = None
    for j, value in enumerate(capacities):
        i = seen.get(requested - value)
        if i is not None:
            candidate = (i, j)
            if pair is None or candidate < pair:
                pair = candidate
        seen.setdefault(value, j)
    if pair is not None:
        return pair
    singles = [(v, i) for i, v in enumerate(capacities) if v >= requested]
    if singles:
        return (min(singles)[1],)
    if n > MAX_COMPLEX_LOCATIONS:
        raise ValidationError('복잡한 조합의 위치 수가 200곳을 초과합니다. 수동 위치 배정을 사용하세요.')
    total, count = 0, 0
    for value in sorted(capacities, reverse=True):
        total += value
        count += 1
        if total >= requested:
            break
    # No covering set of fewer than count locations exists. At that count,
    # covering states need not be expanded, since every capacity is positive.
    states = [dict() for _ in range(count + 1)]
    states[0][0] = ()
    best = None
    transitions = 0
    if budget is None:
        budget = [MAX_TRANSITIONS]
    for i, value in enumerate(capacities):
        for size in range(min(count, i + 1), 0, -1):
            for subtotal, indices in list(states[size - 1].items()):
                transitions += 1
                budget[0] -= 1
                if transitions > MAX_TRANSITIONS or budget[0] < 0:
                    raise ValidationError('위치 조합 계산 한도를 초과했습니다. 수동 위치 배정을 사용하세요. 재고는 변경되지 않았습니다.')
                amount = subtotal + value
                candidate = indices + (i,)
                if size == count:
                    if amount >= requested and (best is None or (amount, candidate) < best):
                        best = (amount, candidate)
                elif amount < requested:
                    old = states[size].get(amount)
                    if old is None or candidate < old:
                        states[size][amount] = candidate
        if sum(len(s) for s in states) > MAX_STATES:
            raise ValidationError('위치 조합 계산 한도를 초과했습니다. 수동 위치 배정을 사용하세요. 재고는 변경되지 않았습니다.')
    return best[1]
