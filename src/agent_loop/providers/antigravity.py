from __future__ import annotations

from .base import Provider
from . import register


@register
class AntigravityProvider(Provider):
    name = "antigravity"
    binary = "agy"
    auto_mode_env = "ALLOW_AUTO_MODE_ANTIGRAVITY"

    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.binary, "-p", prompt]
        if self.auto_mode_enabled:
            argv.append("--dangerously-skip-permissions")
        argv.extend(self._model_argv())
        return argv

