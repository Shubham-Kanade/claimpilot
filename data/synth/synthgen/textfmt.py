"""Indian-style number, amount-in-words, date and time formatting for templates."""

from __future__ import annotations

from datetime import date, time

_ONES = [
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
_TENS = ["_", "_", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
_SCALES = ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand"), (100, "Hundred"))

DATE_FORMATS: dict[str, str] = {
    "dmy_slash": "%d/%m/%Y",
    "dmy_dash": "%d-%m-%Y",
    "dmy_short": "%d.%m.%y",
    "d_mon_y": "%d-%b-%Y",
    "d_month_y": "%d %b %Y",
    "iso": "%Y-%m-%d",
    "long": "%A, %d %B %Y",
}


def indian_grouping(amount: float, decimals: int = 2) -> str:
    """Format with the Indian digit grouping: 1234567.5 -> '12,34,567.50'."""
    sign = "-" if amount < 0 else ""
    whole, _, frac = f"{abs(amount):.{decimals}f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    grouped = ",".join([*groups, tail]) if groups else tail
    return f"{sign}{grouped}.{frac}" if decimals else f"{sign}{grouped}"


def _words_below_1000(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")
    hundreds, rest = divmod(n, 100)
    return f"{_ONES[hundreds]} Hundred" + (f" {_words_below_1000(rest)}" if rest else "")


def number_in_words(n: int) -> str:
    """Integer in Indian-system words: 1250000 -> 'Twelve Lakh Fifty Thousand'."""
    if n < 100:
        return _words_below_1000(n)
    parts: list[str] = []
    for value, name in _SCALES:
        count, n = divmod(n, value)
        if count:
            parts.append(f"{number_in_words(count)} {name}")
    if n:
        parts.append(_words_below_1000(n))
    return " ".join(parts)


def rupees_in_words(amount: float) -> str:
    """'Rupees One Thousand Two Hundred and Paise Fifty Only' style amount in words."""
    rupees, paise = divmod(round(amount * 100), 100)
    text = f"Rupees {number_in_words(rupees)}"
    if paise:
        text += f" and Paise {number_in_words(paise)}"
    return text + " Only"


def format_date(iso_date: str | None, style: str) -> str:
    if iso_date is None:
        return ""
    return date.fromisoformat(iso_date).strftime(DATE_FORMATS[style])


def format_time(hhmm: str | None, twelve_hour: bool) -> str:
    if hhmm is None:
        return ""
    value = time.fromisoformat(hhmm)
    return value.strftime("%I:%M %p") if twelve_hour else value.strftime("%H:%M")
