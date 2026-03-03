from __future__ import annotations
import sys
import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        # Running as a packaged executable
        return Path(sys.executable).parent
    # Running in development
    return Path(__file__).resolve().parents[2]

def get_data_base_dir() -> Path:
    # Use ~/Documents/PersonalQA as default data directory for packaged app
    if getattr(sys, "frozen", False):
        path = Path.home() / "Documents" / "PersonalQA"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(get_base_dir() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model_base_url: str = "http://127.0.0.1:1234/v1"
    model_chat_model: str = "gpt-oss:20b"
    model_embed_model: str = "nomic-embed-text-v1.5"

    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_user: str = ""
    opensearch_password: str = ""
    opensearch_use_ssl: bool = False
    opensearch_verify_certs: bool = False
    opensearch_index_name: str = "personal_rag"
    
    index_state_file: str = Field(
        default_factory=lambda: str(get_data_base_dir() / "index-files.md")
    )
    index_state_template_file: str = Field(
        default_factory=lambda: str(get_base_dir() / "index-files-template.md")
    )

    data_dir: str = Field(default_factory=lambda: str(get_data_base_dir() / "data"))
    data_text_dir: str = Field(
        default_factory=lambda: str(get_data_base_dir() / "data-text")
    )

    chunk_size: int = 1000
    chunk_overlap: int = 150
    embedding_batch_size: int = 16
    retrieval_top_k: int = 5
    debug: bool = False


settings = Settings()

