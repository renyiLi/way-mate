"""配置读取：`.env` 打底，环境变量覆盖。零额外依赖。

凭据绝不入库，也绝不写进日志——日志里只允许出现掩码形式。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class ModelProfile:
    """一个供应商接入点的配置形状。新增第四家只需加一条。"""

    name: str
    key_env: str
    base_env: str
    model_env: str
    default_base: str
    default_models: tuple[str, ...]


PROFILES: dict[str, ModelProfile] = {
    "deepseek": ModelProfile(
        name="deepseek",
        key_env="DEEPSEEK_API_KEY",
        base_env="DEEPSEEK_BASE_URL",
        model_env="DEEPSEEK_MODEL",
        default_base="https://api.deepseek.com",
        default_models=("deepseek-chat",),
    ),
    "ark": ModelProfile(
        name="ark",
        key_env="ARK_API_KEY",
        base_env="ARK_BASE_URL",
        model_env="ARK_MODEL",
        default_base="https://ark.cn-beijing.volces.com/api/v3",
        default_models=("doubao-seed-2-1-pro-260915",),
    ),
}


def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = Path(path) if path else DEFAULT_ENV
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def mask(secret: str) -> str:
    """掩码：日志与终端只允许出现这个形式。"""
    if not secret:
        return "(空)"
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:6]}...{secret[-4:]}"


class Settings:
    def __init__(self, env_path: Path | None = None) -> None:
        self.env_path = Path(env_path) if env_path else DEFAULT_ENV
        self._file = read_env_file(self.env_path)

    def get(self, name: str, default: str | None = None) -> str | None:
        value = os.environ.get(name)
        if value:
            return value
        return self._file.get(name, default)

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise KeyError(f"缺少配置 {name}：请在 {self.env_path} 中设置，或设为环境变量")
        return value

    def masked(self, name: str) -> str:
        return mask(self.get(name) or "")
