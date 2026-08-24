from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelRole(BaseModel):
    name: str
    label: str
    workspace_alias: str
    api_key_env: str
    primary: str
    fallbacks: list[str]
    job: str

    @property
    def configured(self) -> bool:
        return bool(os.getenv(self.api_key_env) or os.getenv("RAFT_LLM_API_KEY"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    raft_otari_mode: Literal["mock", "live"] = "mock"
    # Otari is the intended gateway. Any OpenAI-compatible endpoint also works,
    # which is what makes live mode reachable in a demo without a workspace key;
    # Otari-only request fields (guardrails, mcp_server_ids, server-side tools)
    # are simply not sent in that mode, and Settings says so.
    raft_llm_provider: Literal["otari", "openai_compatible"] = "otari"
    raft_llm_base_url: str = ""
    raft_llm_api_key: str = ""
    raft_llm_model: str = ""
    raft_database_path: Path = Path("data/raft.db")
    raft_model_roles_path: Path = Path("model-roles.yaml")
    raft_web_dist_path: Path = Path("web/dist")
    raft_app_url: str = "http://localhost:8010"
    raft_public_app_url: str = ""
    raft_public_mcp_url: str = ""
    raft_demo_seed: int = 20260821
    raft_demo_trace_count: int = 847
    raft_local_run_allowance_usd: float = 0.10
    raft_use_bge: bool = False
    raft_embedding_model: str = "BAAI/bge-small-en-v1.5"
    raft_embedding_revision: str = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"

    otari_generation_base_url: str = "https://api.otari.ai"
    otari_platform_base_url: str = "https://app.otari.ai"
    otari_guardrail_profile: str = "prompt-injection"
    otari_planner_mcp_server_id: str = ""
    otari_request_timeout_seconds: float = 90.0
    # Hosted Otari caps each HTTP request body at 2 MiB; stay well under it.
    otari_sandbox_chunk_bytes: int = 900_000

    def load_model_roles(self) -> dict[str, ModelRole]:
        raw = yaml.safe_load(self.raft_model_roles_path.read_text())
        return {
            name: ModelRole(name=name, **definition)
            for name, definition in raw["roles"].items()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
