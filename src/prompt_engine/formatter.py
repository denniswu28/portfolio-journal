"""
formatter.py - Token estimation and prompt truncation utilities.

Used to keep prompts within LLM context limits.
"""

from __future__ import annotations

from typing import List, Optional


def estimate_tokens(text: str) -> int:
    """
    Rough token count estimate: ~1 token per 4 characters for English text.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Estimated number of tokens.
    """
    return max(1, len(text) // 4)


def estimate_tokens_precise(text: str) -> int:
    """
    More precise token estimation using tiktoken (OpenAI tokenizer).
    Falls back to the character-based estimate if tiktoken is unavailable.

    Args:
        text: The text to estimate tokens for.

    Returns:
        Token count (precise or estimated).
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return estimate_tokens(text)


def truncate_to_budget(
    prompt: str,
    max_tokens: int = 4000,
    precise: bool = False,
) -> str:
    """
    Truncate a prompt to fit within a token budget.

    Strategy: trim from the end (oldest trade history section is last),
    preserving the header and positions table.

    Args:
        prompt: The full rendered prompt string.
        max_tokens: Maximum allowed tokens.
        precise: If True, use tiktoken for accurate counting.

    Returns:
        Truncated prompt string (with a notice appended if truncated).
    """
    count_fn = estimate_tokens_precise if precise else estimate_tokens
    current_tokens = count_fn(prompt)

    if current_tokens <= max_tokens:
        return prompt

    # Binary search for the right character count
    target_chars = int(len(prompt) * (max_tokens / current_tokens) * 0.95)
    truncated = prompt[:target_chars]

    # Try to truncate at a clean line boundary
    last_newline = truncated.rfind("\n")
    if last_newline > len(truncated) // 2:
        truncated = truncated[:last_newline]

    truncated += "\n\n[Prompt truncated to fit token budget]"
    return truncated


def format_currency(value: float) -> str:
    """Format a float as a dollar string: e.g. 1234.5 -> '$1,234.50'"""
    return f"${value:,.2f}"


def format_pct(value: float, sign: bool = True) -> str:
    """Format a float as a percentage string: e.g. 12.5 -> '+12.50%'"""
    if sign:
        return f"{value:+.2f}%"
    return f"{value:.2f}%"
