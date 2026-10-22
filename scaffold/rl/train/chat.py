"""
chat.py — the exact text the model sees and emits at inference, so training text == inference text.

At inference MicroAgent's LocalGBNFModel POSTs to llama-server /v1/chat/completions:

    {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
     "chat_template_kwargs": {"enable_thinking": false}, "grammar": <GBNF>, "temperature": 0, ...}

llama-server renders that with the GGUF's embedded Jinja chat template. For Qwen3.5 (template
vendored here as qwen3_5_chat_template.jinja, sha256 273d8e0e...; unsloth's GGUF template differs
only in how tool_call arguments are iterated, which our prompts never use) the result is:

    <|im_start|>system\\n{system|trim}<|im_end|>\\n
    <|im_start|>user\\n{user|trim}<|im_end|>\\n
    <|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n          <- the empty think block (enable_thinking=False)

and the model then generates the reply, ending with <|im_end|> (EOS, generation stops there).
The training completion is therefore ``reply.strip() + "<|im_end|>"`` with no trailing newline,
tokenised separately from the prompt (generation starts at a token boundary after the prompt).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

TEMPLATE_PATH = Path(__file__).with_name("qwen3_5_chat_template.jinja")
IM_START, IM_END = "<|im_start|>", "<|im_end|>"
EMPTY_THINK = "<think>\n\n</think>\n\n"
#: what LocalGBNFModel passes; enable_thinking=False is what makes the template emit EMPTY_THINK
CHAT_TEMPLATE_KWARGS = {"enable_thinking": False}


def messages_for(system: str, user: str) -> List[Dict[str, str]]:
    """The message list LocalGBNFModel.decide sends (system, user)."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _trim(s: str) -> str:
    return s.strip()      # Jinja's |trim == str.strip() (whitespace both ends)


def render_prompt(system: str, user: str) -> str:
    """Qwen3.5 chat template output for [system, user] + add_generation_prompt, enable_thinking=False."""
    return (f"{IM_START}system\n{_trim(system)}{IM_END}\n"
            f"{IM_START}user\n{_trim(user)}{IM_END}\n"
            f"{IM_START}assistant\n{EMPTY_THINK}")


def render_completion(reply: str) -> str:
    """Training target: what the model must generate after render_prompt(), including the stop token."""
    return _trim(reply) + IM_END


def render_template(messages: List[Dict[str, str]], template: str | None = None, **kwargs) -> str:
    """Render the real Jinja template (needs jinja2; used by tests and by mlx_sft's startup check)."""
    import jinja2  # noqa: PLC0415 - optional dependency

    def raise_exception(msg):
        raise jinja2.exceptions.TemplateError(msg)

    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.loopcontrols"])
    env.globals["raise_exception"] = raise_exception
    tmpl = env.from_string(template if template is not None else TEMPLATE_PATH.read_text())
    return tmpl.render(messages=messages, **kwargs)
