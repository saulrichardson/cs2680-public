"""Deliberately defective teaching fixture, not a task solution.

Contract: count the integer units covered by the union of finite half-open
intervals [start, stop), where start <= stop. Overlap counts once, order does not
matter, and an empty interval covers nothing. Inputs here have integer endpoints.
"""


def covered_length(spans):
    spans = sorted(spans)
    if not spans:
        return 0
    if any(start > stop for start, stop in spans):
        raise ValueError('start must not exceed stop')
    left, right = spans[0]
    total = 0
    for start, stop in spans[1:]:
        if start > right:
            total += right - left
            left, right = start, stop
        else:
            right = stop  # BUG: can shrink an already covered interval.
    return total + right - left
