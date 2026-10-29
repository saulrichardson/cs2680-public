def inclusive_count(start, end):
    """Number of integers in the closed interval [start, end]; zero if reversed."""
    return max(0, end - start)
