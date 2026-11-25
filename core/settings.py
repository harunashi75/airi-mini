from pydantic import BaseModel, ConfigDict, Field, model_validator
from core.i18n import t
from core.storage import project_path, read_json


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_path: str = "models/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    n_ctx: int = Field(default=4096, ge=1024)
    n_threads: int = Field(default=6, ge=1)
    n_batch: int = Field(default=256, ge=1)
    n_gpu_layers: int = Field(default=0, ge=-1)
    max_tokens: int = Field(default=512, ge=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_message_chars: int = Field(default=8000, ge=1, le=32000)
    max_history_messages: int = Field(default=40, ge=2, le=200)
    max_saved_messages: int = Field(default=1000, ge=2, le=10000)
    summarize_history: bool = True
    suggest_memories: bool = True
    summary_max_tokens: int = Field(default=300, ge=64, le=1024)
    port: int = Field(default=8000, ge=1, le=65535)
    open_browser: bool = True
    language: str = Field(default="fr", pattern=r"^[a-z]{2,3}(_[A-Z]{2})?$")

    @model_validator(mode="after")
    def check_budget(self):
        if self.max_tokens + 512 >= self.n_ctx:
            raise ValueError(t("settings.context"))
        if self.max_history_messages % 2 or self.max_saved_messages % 2:
            raise ValueError(t("settings.even"))
        return self


def load_settings(path="config/airi.json"):
    return Settings.model_validate(read_json(project_path(path)))
