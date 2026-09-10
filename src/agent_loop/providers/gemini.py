from __future__ import annotations

from .base import Provider
from . import register


@register
class GeminiProvider(Provider):
    name = "gemini"
    binary = "gemini"
    auto_mode_env = "ALLOW_AUTO_MODE_GEMINI"
    model_flag = "-m"

    def build_argv(self, prompt: str) -> list[str]:
        argv = [self.binary, "-p", prompt]
        if self.auto_mode_enabled:
            argv.extend(["--approval-mode", "yolo"])
        argv.extend(self._model_argv())
        return argv
