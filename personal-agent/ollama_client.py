"""
Talks to Ollama via its OpenAI-compatible /v1 endpoint.
Swapping models = changing config.model_name. Nothing else changes.
"""
from collections.abc import Generator

from openai import OpenAI

from config import settings

client = OpenAI(base_url=settings.ollama_base_url, api_key=settings.ollama_api_key)


def stream_chat(messages: list[dict], model: str | None = None) -> Generator[str, None, None]:
    """Yields text chunks as they arrive from the model."""
    stream = client.chat.completions.create(
        model=model or settings.model_name,
        messages=messages,
        temperature=settings.temperature,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def chat(messages: list[dict], model: str | None = None) -> str:
    """Non-streaming, full response at once — useful for tool-calling/internal steps."""
    resp = client.chat.completions.create(
        model=model or settings.model_name,
        messages=messages,
        temperature=settings.temperature,
        stream=False,
    )
    return resp.choices[0].message.content or ""
