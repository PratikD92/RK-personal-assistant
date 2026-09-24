"""
Central config. Everything that might change (model, ports, paths) lives here
and is overridable via a .env file, so nothing is hardcoded in the app logic.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Model / Ollama ---
    ollama_base_url: str = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
    ollama_api_key: str = "ollama"  # Ollama ignores this, but the OpenAI SDK requires a value
    model_name: str = "gemma4:e4b"  # swap to "qwen2.5:7b-instruct" etc. later, no code changes
    temperature: float = 0.3
    system_prompt: str = (
        "You are a helpful personal AI agent running locally on the user's MacBook. "
        "Be concise and direct."
    )

    # --- Memory ---
    db_path: str = "./data/agent_memory.db"
    history_window: int = 20  # how many past messages to feed back as context each turn

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8000

    class Config:
        env_file = ".env"


settings = Settings()
