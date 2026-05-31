from typing import Protocol


class AIError(Exception):
    pass


class ChatAI(Protocol):
    def complete(self, message: str) -> str: ...
    def solve_captcha(self, image_data: bytes) -> str: ...
