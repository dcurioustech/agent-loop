from __future__ import annotations

from .base import Provider
from . import register


@register
class CodexProvider(Provider):
    name = "codex"
    binary = "codex"
    auto_mode_env = "ALLOW_AUTO_MODE_CODEX"
    model_flag = "-m"

    def build_argv(self, prompt: str) -> list[str]:
        flag = (
            "--dangerously-bypass-approvals-and-sandbox"
            if self.auto_mode_enabled
            else "--full-auto"
        )
        # Model must precede the positional prompt so `exec` parses it as a flag.
        return [self.binary, "exec", flag, *self._model_argv(), prompt]
