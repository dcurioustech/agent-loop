from __future__ import annotations

from .base import Provider
from . import register


@register
class GrokProvider(Provider):
    name = "grok"
    binary = "grok"
    auto_mode_env = "ALLOW_AUTO_MODE_GROK"

    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.binary, "-p", prompt]
        if self.auto_mode_enabled:
            argv.append("--always-approve")
        argv.extend(self._model_argv())
        return argv
