"""Rupee formatting with Indian digit grouping (``₹1,22,420``)."""

from __future__ import annotations


def format_inr(amount: float) -> str:
    """Format ``amount`` as rupees, grouping digits the Indian way, paise only when present.

    >>> format_inr(10450)
    '₹10,450'
    >>> format_inr(122420)
    '₹1,22,420'
    >>> format_inr(651.5)
    '₹651.50'
    """
    paise_total = round(abs(amount) * 100)
    rupees, paise = divmod(paise_total, 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        pairs: list[str] = []
        while len(head) > 2:
            pairs.append(head[-2:])
            head = head[:-2]
        if head:
            pairs.append(head)
        digits = ",".join([*reversed(pairs), tail])
    sign = "-" if amount < 0 and paise_total else ""
    suffix = f".{paise:02d}" if paise else ""
    return f"{sign}₹{digits}{suffix}"
