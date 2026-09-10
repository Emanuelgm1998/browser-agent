import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import BaseMessage
from browser_use.llm.ollama.chat import (
    ChatOllama,
    _unwrap_json_content,
)
from browser_use.llm.views import ChatInvokeCompletion

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)


class Qwen3ChatOllama(ChatOllama):
    """ChatOllama wrapper for Qwen3 small models on Ollama.

    Ported/generalized from browser-use-webui's Qwen3ChatOllama for the
    browser-use 0.13.x LLM interface (openai-style BaseChatModel).

    Hardening applied:
    - Forces ``think=False`` (top-level Ollama chat param) so Qwen3 does NOT
      emit  thinking... response  reasoning blocks that break JSON output.
    - Defensively strips any residual thinking block before parsing.
    - Repairs small-model JSON defects before pydantic validation
      (markdown fences are already handled by _unwrap_json_content, plus:
      the ``{name, arguments:{...}}`` tool-call envelope, trailing commas,
      missing closing braces and trailing prose).
    """

    def _strip_thinking(self, content: str) -> str:
        if not isinstance(content, str):
            return content
        stripped = content
        if ' think' in stripped and ' response' in stripped:
            parts = re.split(r' think.*? response', stripped, flags=re.DOTALL)
            stripped = ''.join(parts)
        if ' think' in stripped and ' response' in stripped:
            start = stripped.find(' think')
            end = stripped.find(' response', start + 1)
            if start != -1 and end != -1:
                stripped = stripped[:start] + stripped[end + len(' response'):]
        result = stripped.strip()
        return result if result else content.strip()

    def _balanced_object(self, text: str, start: int) -> str | None:
        if start < 0 or start >= len(text) or text[start] != '{':
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return None

    def _unwrap_arguments(self, content: str) -> str | None:
        try:
            obj = json.loads(content)
        except Exception:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get('arguments'), dict):
            return json.dumps(obj['arguments'])
        if obj is None:
            m = re.search(r'"arguments"\s*:\s*(\{)', content)
            if m:
                inner = self._balanced_object(content, m.start(1))
                if inner is not None:
                    try:
                        return json.dumps(json.loads(inner))
                    except Exception:
                        pass
        return None

    def _repair_json(self, content: str) -> str:
        if not isinstance(content, str):
            return content
        c = content.strip()

        unwrapped = self._unwrap_arguments(c)
        if unwrapped is not None:
            return unwrapped

        try:
            json.loads(c)
            return c
        except Exception:
            pass

        candidate = c
        for _ in range(8):
            candidate = re.sub(r',\s*}(?=\s*$)', '}', candidate)
            if candidate.count('{') > candidate.count('}'):
                candidate += '}'
            try:
                return json.dumps(json.loads(candidate))
            except Exception:
                continue

        first = self._balanced_object(c, c.find('{'))
        if first is not None:
            try:
                return json.dumps(json.loads(first))
            except Exception:
                pass
        return c

    def _clean_completion(self, content: str) -> str:
        content = self._strip_thinking(content)
        return self._repair_json(content)

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        from browser_use.llm.ollama.serializer import OllamaMessageSerializer

        ollama_messages = OllamaMessageSerializer.serialize_messages(messages)

        try:
            options, top_level = self._split_chat_options()
            top_level['think'] = False

            if output_format is None:
                response = await self.get_client().chat(
                    model=self.model,
                    messages=ollama_messages,
                    options=options,
                    **top_level,
                )
                content = self._clean_completion(response.message.content or '')
                return ChatInvokeCompletion(completion=content, usage=None)

            schema = output_format.model_json_schema()
            response = await self.get_client().chat(
                model=self.model,
                messages=ollama_messages,
                format=schema,
                options=options,
                **top_level,
            )

            content = _unwrap_json_content(response.message.content or '')
            content = self._clean_completion(content)
            try:
                parsed = output_format.model_validate_json(content)
            except ValidationError:
                raise ModelProviderError(
                    message=(
                        'Ollama returned invalid JSON even after repair: '
                        f'{content[:500]}'
                    ),
                    model=self.name,
                )
            return ChatInvokeCompletion(completion=parsed, usage=None)

        except ModelProviderError:
            raise
        except Exception as e:
            raise ModelProviderError(message=str(e), model=self.name) from e