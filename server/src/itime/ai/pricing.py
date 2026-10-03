from decimal import Decimal, InvalidOperation

NANOS_PER_CURRENCY_UNIT = 1_000_000_000
TOKENS_PER_PRICE_UNIT = 1_000_000
MAX_PRICE_NANOS_PER_MILLION = 1_000_000_000_000_000


def parse_price_per_million(value: str) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("价格必须是普通十进制数字") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("价格不得为负数")
    nanos = amount * NANOS_PER_CURRENCY_UNIT
    if nanos != nanos.to_integral_value():
        raise ValueError("价格最多保留 9 位小数")
    result = int(nanos)
    if result > MAX_PRICE_NANOS_PER_MILLION:
        raise ValueError("每百万 Token 价格不得超过 1000000")
    return result


def format_price_per_million(nanos: int) -> str:
    amount = Decimal(nanos) / NANOS_PER_CURRENCY_UNIT
    return format(amount, "f").rstrip("0").rstrip(".") or "0"


def rounded_token_cost(tokens: int, price_nanos_per_million: int) -> int:
    if tokens < 0 or not 0 <= price_nanos_per_million <= MAX_PRICE_NANOS_PER_MILLION:
        raise ValueError("Token 数和价格必须位于支持范围内")
    numerator = tokens * price_nanos_per_million
    return (numerator + TOKENS_PER_PRICE_UNIT - 1) // TOKENS_PER_PRICE_UNIT
