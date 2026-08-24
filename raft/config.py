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
    # Standalone Otari and the hosted platform expose different management
    # paths: standalone serves /v1/models, hosted serves /api/v1/models. Getting
    # this wrong makes every model look unavailable.
    raft_otari_deployment: Literal["standalone", "hosted"] = "standalone"
    # One model call per conversation is unusable in series over hundreds of
    # traces; this is how many run at once.
    raft_aspect_concurrency: int = 24
    raft_embedding_batch: int = 64
    raft_llm_base_url: str = ""
    raft_llm_api_key: str = ""
    raft_llm_model: str = ""
    # Some models reject `temperature` outright (Anthropic's newer ones call it
    # deprecated and return 400). Set to none/empty to omit it entirely.
    raft_llm_temperature: float | None = 0.0
    raft_llm_max_tokens: int = 3000
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
    # "otari" uses the gateway's /v1/embeddings, which standalone Otari mounts
    # and hosted does not. "local" is the corpus-fitted TF-IDF fallback.
    raft_embedding_backend: Literal["auto", "otari", "local"] = "auto"
    raft_otari_embedding_model: str = ""
    raft_embedding_model: str = "BAAI/bge-small-en-v1.5"
    raft_embedding_revision: str = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"

    otari_generation_base_url: str = "https://api.otari.ai"
    otari_platform_base_url: str = "https://app.otari.ai"
    otari_guardrail_profile: str = "prompt-injection"
    otari_planner_mcp_server_id: str = ""
    otari_request_timeout_seconds: float = 90.0
    # Naming groups is decoration: Raft already has a name for every group from
    # its own members. The hosted 70B has been observed taking anywhere from 8
    # to 90 seconds, so this call gets a short leash of its own.
    otari_naming_timeout_seconds: float = 20.0
    # Hosted Otari caps each HTTP request body at 2 MiB; stay well under it.
    otari_sandbox_chunk_bytes: int = 900_000

    @property
    def management_prefix(self) -> str:
        """Where the model/keys/usage routes live for this deployment."""
        return "/v1" if self.raft_otari_deployment == "standalone" else "/api/v1"

    @property
    def uses_otari_embeddings(self) -> bool:
        if self.raft_embedding_backend == "local":
            return False
        if self.raft_embedding_backend == "otari":
            return True
        # auto: only standalone Otari mounts an embeddings route.
        return self.raft_otari_mode == "live" and self.raft_otari_deployment == "standalone"

    def load_model_roles(self) -> dict[str, ModelRole]:
        raw = yaml.safe_load(self.raft_model_roles_path.read_text())
        return {
            name: ModelRole(name=name, **definition)
            for name, definition in raw["roles"].items()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
