"""Interval arithmetic on (start, end) pairs, in seconds."""
from __future__ import annotations

Interval = tuple[float, float]


def union(intervals: list[Interval]) -> list[Interval]:
    """Sorted, non-overlapping, non-empty cover of the inputs."""
    out: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def subtract(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """Parts of a not covered by b."""
    out: list[Interval] = []
    b = union(b)
    for start, end in union(a):
        cursor = start
        for bs, be in b:
            if be <= cursor or bs >= end:
                continue
            if bs > cursor:
                out.append((cursor, bs))
            cursor = max(cursor, be)
            if cursor >= end:
                break
        if cursor < end:
            out.append((cursor, end))
    return out


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    a, b = union(a), union(b)
    i = j = 0
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if end > start:
            out.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def total(intervals: list[Interval]) -> float:
    return sum(end - start for start, end in union(intervals))
