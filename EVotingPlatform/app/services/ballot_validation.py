"""Checks that a submitted vote is formal, and turns it into a clean
preference order that the counting code can use."""
from app.services.errors import ServiceError
from app.utils import constants as C


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_house(house_preferences, division_candidates):
    """House of Representatives: full preferential. The vote is a list of
    candidate ids, most preferred first, containing every candidate exactly once."""
    valid_ids = {c.id for c in division_candidates}
    if not isinstance(house_preferences, list) or not house_preferences:
        raise ServiceError('House of Representatives preferences are required', 422)
    order = [_int(x) for x in house_preferences]
    if None in order or len(set(order)) != len(order):
        raise ServiceError('House preferences contain invalid or repeated candidates', 422)
    if set(order) != valid_ids:
        raise ServiceError('Every House candidate must be numbered exactly once', 422)
    return order


def _numbered_prefix(numbers_by_key):
    """Keys numbered 1, 2, 3 ... in order. Stops at the first missing or
    repeated number (the numbers before it still count)."""
    by_number = {}
    for key, raw in numbers_by_key.items():
        n = _int(raw)
        if n is not None and n >= 1:
            by_number.setdefault(n, []).append(key)
    ordered, n = [], 1
    while n in by_number and len(by_number[n]) == 1:
        ordered.append(by_number[n][0])
        n += 1
    return ordered


def validate_senate(atl, btl, state_candidates):
    """Senate: above the line (groups), below the line (candidates), or both.
    Returns (order, source) where order is a list of candidate ids and source
    is 'btl' or 'atl'. A valid below-the-line vote wins over above-the-line."""
    atl = atl or {}
    btl = btl or {}
    if not isinstance(atl, dict) or not isinstance(btl, dict):
        raise ServiceError('Senate votes must be objects of {box: number}', 422)

    groups = {}
    for c in sorted(state_candidates, key=lambda c: (c.group_code or '', c.group_order or 0, c.id)):
        if c.group_code and c.group_code != C.UNGROUPED:
            groups.setdefault(c.group_code, []).append(c.id)
    valid_candidates = {c.id for c in state_candidates}

    for key in atl:
        if key not in groups:
            raise ServiceError(f'Unknown Senate group: {key}', 422)
    btl_clean = {}
    for key, num in btl.items():
        cid = _int(key)
        if cid not in valid_candidates:
            raise ServiceError(f'Unknown Senate candidate: {key}', 422)
        btl_clean[cid] = num

    atl_min = min(C.SENATE_ATL_MIN, len(groups)) if groups else 0
    btl_min = min(C.SENATE_BTL_MIN, len(valid_candidates))

    btl_order = _numbered_prefix(btl_clean)
    if btl_order and len(btl_order) >= btl_min:
        return btl_order, 'btl'

    atl_order = _numbered_prefix(atl)
    if atl_order and len(atl_order) >= atl_min:
        expanded = []
        for group_code in atl_order:
            expanded.extend(groups[group_code])
        return expanded, 'atl'

    raise ServiceError(
        f'Informal Senate vote: number at least {atl_min} boxes above the line '
        f'or {btl_min} candidates below the line', 422)
