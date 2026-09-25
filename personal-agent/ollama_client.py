"""
Talks to Ollama via its OpenAI-compatible /v1 endpoint.
Swapping models = changing config.model_name. Nothing else changes.
"""
import base64
import mimetypes
import re
from collections.abc import Generator
from pathlib import Path

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


def extract_bill_amount(image_path: str, model: str | None = None) -> float | None:
    """Sends a bill/receipt photo to the (multimodal) model and asks it to
    read the total amount off it. Returns None if the image can't be read,
    the model can't find a total, or the call fails."""
    path = Path(image_path)
    if not path.is_file():
        return None

    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")

    try:
        resp = client.chat.completions.create(
            model=model or settings.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": settings.bill_vision_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            temperature=0,
            stream=False,
        )
    except Exception:
        return None

    text = (resp.choices[0].message.content or "").strip()
    if text.upper() == "NONE":
        return None

    match = re.search(r"(\d+(?:,\d{3})*(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None