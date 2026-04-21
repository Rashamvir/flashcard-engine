import json
import math
import os
import re
from typing import Any

from dotenv import load_dotenv
from groq import Groq


load_dotenv()

MODEL_NAME = "llama3-8b-8192"
CHUNK_SIZE = 1500
MAX_TOTAL_CARDS = 12
RETRY_PROMPT = (
    "Return only valid JSON as an array of objects with question and answer keys. "
    "No markdown. No code fences. No explanation."
)


class FlashcardGenerationError(Exception):
    pass


def format_groq_error_message(error: Exception, deck_name: str) -> str:
    error_text = str(error)

    if "rate_limit_exceeded" in error_text or "Error code: 429" in error_text:
        retry_match = re.search(r"Please try again in ([0-9.]+)s", error_text)
        wait_time = ""
        if retry_match:
            seconds = float(retry_match.group(1))
            minutes = max(1, round(seconds / 60))
            wait_time = f" Please try again in about {minutes} minute(s)."

        return (
            f"Groq usage limit reached while generating cards for deck '{deck_name}'."
            f"{wait_time} You can also use a different Groq API key or upgrade your Groq plan."
        )

    if "invalid_api_key" in error_text or "401" in error_text:
        return (
            f"Groq rejected the API key while generating cards for deck '{deck_name}'. "
            "Check that GROQ_API_KEY is set correctly."
        )

    return f"Groq could not generate cards for deck '{deck_name}': {error_text}"


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    cleaned = " ".join(text.split())
    return [cleaned[i : i + size] for i in range(0, len(cleaned), size)]


def build_system_prompt(cards_per_chunk: int) -> str:
    return (
        "You are an expert teacher creating high-quality flashcards. Generate "
        f"{cards_per_chunk} flashcards from the provided text. Cover key concepts, "
        "definitions, relationships, edge cases, and worked examples. Each card "
        "should test genuine understanding, not just surface recall. Return ONLY a "
        'JSON array in this exact format, no other text: [{"question": "...", '
        '"answer": "..."}, ...]. Make questions specific and answers concise but '
        "complete. Avoid duplicates, avoid near-duplicates, and prefer the most "
        "important ideas."
    )


def extract_json_array(raw_content: str) -> list[dict[str, str]]:
    cleaned_content = raw_content.strip()
    if cleaned_content.startswith("```"):
        cleaned_content = cleaned_content.strip("`")
        cleaned_content = cleaned_content.replace("json\n", "", 1).strip()

    start = cleaned_content.find("[")
    end = cleaned_content.rfind("]")
    if start == -1 or end == -1 or start >= end:
        raise ValueError("Response did not contain a JSON array.")

    payload = cleaned_content[start : end + 1]
    parsed = json.loads(payload)

    if not isinstance(parsed, list):
        raise ValueError("Parsed response was not a list.")

    cleaned_cards = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if question and answer:
            cleaned_cards.append({"question": question, "answer": answer})

    if not cleaned_cards:
        raise ValueError("No valid flashcards found in response.")

    return cleaned_cards


def request_cards(
    client: Groq,
    text_chunk: str,
    deck_name: str,
    cards_per_chunk: int,
    retry: bool = False,
) -> list[dict[str, str]]:
    prompt = RETRY_PROMPT if retry else build_system_prompt(cards_per_chunk)
    completion = client.chat.completions.create(
        model=MODEL_NAME,
        temperature=0.3,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"Deck name: {deck_name}\n\nCreate flashcards from this text:\n{text_chunk}",
            },
        ],
    )
    content = completion.choices[0].message.content or ""
    if not content.strip():
        raise ValueError("Groq returned an empty response.")
    return extract_json_array(content)


def dedupe_cards(cards: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique_cards = []
    for card in cards:
        key = (card["question"].lower(), card["answer"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique_cards.append(card)
    return unique_cards


def trim_card_list(cards: list[dict[str, str]]) -> list[dict[str, str]]:
    return dedupe_cards(cards)[:MAX_TOTAL_CARDS]


def generate_flashcards(text: str, deck_name: str) -> list[dict[str, Any]]:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise FlashcardGenerationError("GROQ_API_KEY is missing from the server environment.")

    text_chunks = chunk_text(text)
    if not text_chunks:
        raise FlashcardGenerationError("The PDF did not provide enough text to generate flashcards.")

    client = Groq(api_key=api_key, timeout=60, max_retries=2)
    generated_cards: list[dict[str, str]] = []
    cards_per_chunk = max(4, min(8, math.ceil(MAX_TOTAL_CARDS / len(text_chunks))))

    for chunk in text_chunks:
        try:
            generated_cards.extend(request_cards(client, chunk, deck_name, cards_per_chunk))
        except Exception as first_error:
            try:
                generated_cards.extend(request_cards(client, chunk, deck_name, cards_per_chunk, retry=True))
            except Exception as exc:
                raise FlashcardGenerationError(format_groq_error_message(exc, deck_name)) from exc

    return trim_card_list(generated_cards)
