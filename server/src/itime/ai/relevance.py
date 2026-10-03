import re
import unicodedata


def normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def text_units(value: str) -> tuple[set[str], set[str]]:
    normalized = normalized_text(value)
    compact = "".join(character for character in normalized if character.isalnum())
    bigrams = {
        compact[index : index + 2]
        for index in range(max(0, len(compact) - 1))
    }
    words = {
        token
        for token in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", normalized)
        if len(token) >= 2
    }
    return words, bigrams


def relevance_score(query: str, candidate: str) -> int:
    normalized_query = normalized_text(query)
    normalized_candidate = normalized_text(candidate[:4000])
    if len(
        "".join(
            character
            for character in normalized_query
            if character.isalnum()
        )
    ) < 2:
        return 0
    query_words, query_bigrams = text_units(normalized_query)
    candidate_words, candidate_bigrams = text_units(normalized_candidate)
    score = 4 * len(query_bigrams & candidate_bigrams)
    score += 12 * len(query_words & candidate_words)
    if normalized_query in normalized_candidate:
        score += 100
    return score
