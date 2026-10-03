from decimal import Decimal, InvalidOperation

NANOS_PER_CURRENCY_UNIT = 1_000_000_000
TOKENS_PER_PRICE_UNIT = 1_000_000
MAX_PRICE_NANOS_PER_MILLION = 1_000_000_000_000_000
MAX_STORED_NANOS = 9_000_000_000_000_000_000


def parse_price_per_million(value: str) -> int:
    return parse_currency_amount(value)


def parse_currency_amount(value: str) -> int:
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
        raise ValueError("金额不得超过 1000000")
    return result


def format_price_per_million(nanos: int) -> str:
    return format_currency_nanos(nanos)


def format_currency_nanos(nanos: int) -> str:
    amount = Decimal(nanos) / NANOS_PER_CURRENCY_UNIT
    return format(amount, "f").rstrip("0").rstrip(".") or "0"


def rounded_token_cost(tokens: int, price_nanos_per_million: int) -> int:
    if tokens < 0 or not 0 <= price_nanos_per_million <= MAX_PRICE_NANOS_PER_MILLION:
        raise ValueError("Token 数和价格必须位于支持范围内")
    numerator = tokens * price_nanos_per_million
    return (numerator + TOKENS_PER_PRICE_UNIT - 1) // TOKENS_PER_PRICE_UNIT


def token_cost_breakdown(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    input_price: int,
    cached_input_price: int,
    output_price: int,
) -> tuple[int, int, int, int]:
    if (
        min(input_tokens, cached_input_tokens, output_tokens) < 0
        or cached_input_tokens > input_tokens
    ):
        raise ValueError("缓存输入 Token 不得超过输入 Token")
    if any(
        not 0 <= price <= MAX_PRICE_NANOS_PER_MILLION
        for price in (input_price, cached_input_price, output_price)
    ):
        raise ValueError("价格必须位于支持范围内")
    regular_input_tokens = input_tokens - cached_input_tokens
    numerators = [
        regular_input_tokens * input_price,
        cached_input_tokens * cached_input_price,
        output_tokens * output_price,
    ]
    component_costs = [
        numerator // TOKENS_PER_PRICE_UNIT for numerator in numerators
    ]
    total_numerator = sum(numerators)
    total_cost = (
        total_numerator + TOKENS_PER_PRICE_UNIT - 1
    ) // TOKENS_PER_PRICE_UNIT
    if total_cost > MAX_STORED_NANOS:
        raise ValueError("成本超过可存储范围")
    unallocated = total_cost - sum(component_costs)
    remainders = [
        numerator % TOKENS_PER_PRICE_UNIT for numerator in numerators
    ]
    for index in sorted(range(3), key=lambda item: (-remainders[item], item))[
        :unallocated
    ]:
        component_costs[index] += 1
    return (*component_costs, total_cost)
