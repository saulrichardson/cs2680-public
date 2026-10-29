"""Executable, invented reference models for reading alongside the guides.

Each function declares one illustrative contract. These are not implementations
of a benchmark task or imports required by a target project. Adapt policies,
types, and boundaries to the actual public request. Standard library only.
"""

from bisect import bisect_right
from math import isfinite


def locate_segment(durations, position):
    """Locate finite position in finite half-open segments, skipping zero lengths.

    This model rejects negative/non-finite lengths and positions outside [0,total).
    Other applications may define a different end/clamping/live-duration policy.
    Returns (segment index, segment-local position).
    """
    prefix = [0]
    for duration in durations:
        if not isfinite(duration) or duration < 0:
            raise ValueError("invalid duration")
        total = prefix[-1] + duration
        if not isfinite(total):
            raise ValueError("timeline overflow")
        prefix.append(total)
    if not isfinite(position) or not 0 <= position < prefix[-1]:
        raise ValueError("position outside the modeled timeline")
    index = bisect_right(prefix, position) - 1
    return index, position - prefix[index]


def merge_annotated(left, left_marks, right, right_marks, overlap):
    """Merge one explicitly declared suffix/prefix overlap and remap marks.

    This model deduplicates annotation positions, not arbitrary sequence values.
    It does not decide whether two windows are eligible to merge.
    """
    if not 0 <= overlap <= min(len(left), len(right)):
        raise ValueError("invalid overlap")
    if overlap and list(left[-overlap:]) != list(right[:overlap]):
        raise ValueError("declared overlap does not match")
    if any(not 0 <= j < len(left) for j in left_marks):
        raise ValueError("invalid left annotation")
    if any(not 0 <= j < len(right) for j in right_marks):
        raise ValueError("invalid right annotation")
    shift = len(left) - overlap
    marks = set(left_marks) | {shift + j for j in right_marks}
    return list(left) + list(right[overlap:]), sorted(marks)


def encoded_decimal_spans(component):
    """Find ASCII decimal spans while preserving valid percent escapes.

    Input is already the caller-selected encoded component. This model rejects
    malformed escapes and does not select a URL component, perform arithmetic,
    or choose a leading-zero/overflow policy.
    """
    hexadecimal = frozenset("0123456789abcdefABCDEF")
    spans = []
    index = 0
    while index < len(component):
        if component[index] == "%":
            if index + 2 >= len(component) or any(
                c not in hexadecimal for c in component[index + 1:index + 3]
            ):
                raise ValueError("invalid percent escape")
            index += 3
        elif "0" <= component[index] <= "9":
            start = index
            while index < len(component) and "0" <= component[index] <= "9":
                index += 1
            spans.append((start, index))
        else:
            index += 1
    return spans


def cumulative_total(events):
    """Sum final per-operation snapshots under explicit sequence ordering.

    Events are (operation ID, sequence number, nonnegative cumulative count).
    Lower sequences are stale; conflicting data at one sequence is rejected.
    This model is not for delta streams or providers with no ordering contract.
    """
    snapshots = {}
    for operation, sequence, count in events:
        if sequence < 0 or count < 0:
            raise ValueError("negative sequence/count")
        previous = snapshots.get(operation)
        if previous is None or sequence > previous[0]:
            snapshots[operation] = (sequence, count)
        elif sequence == previous[0] and count != previous[1]:
            raise ValueError("conflicting snapshot")
    return sum(count for _, count in snapshots.values())


def catalog_projection(catalog, selected):
    """Project selected unique IDs in catalog order; reject unknown IDs.

    Catalog is a sequence of (ID, label) pairs. Other contracts can instead
    preserve request order or omit unknown IDs; choose those policies explicitly.
    """
    wanted = set(selected)
    identities = [identity for identity, _ in catalog]
    if len(identities) != len(set(identities)):
        raise ValueError("ambiguous catalog identity")
    if not wanted <= set(identities):
        raise ValueError("unknown selection")
    return [(identity, label) for identity, label in catalog if identity in wanted]
