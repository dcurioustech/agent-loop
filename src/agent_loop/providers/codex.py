from __future__ import annotations

from .base import Provider
from . import register


@register
class CodexProvider(Provider):
    name = "codex"
    binary = "codex"
    danger_env = "ALLOW_DANGEROUS_CODEX"

    def build_argv(self, prompt: str) -> list[str]:
        flag = (
            "--dangerously-bypass-approvals-and-sandbox"
            if self.dangerous_enabled
            else "--full-auto"
        )
        return [self.binary, "exec", flag, prompt]
