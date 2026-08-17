from __future__ import annotations

from .base import Provider
from . import register


@register
class AntigravityProvider(Provider):
    name = "antigravity"
    binary = "agy"
    danger_env = "ALLOW_DANGEROUS_ANTIGRAVITY"

    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.binary, "-p", prompt]
        if self.dangerous_enabled:
            argv.append("--dangerously-skip-permissions")
        argv.extend(self._model_argv())
        return argv

