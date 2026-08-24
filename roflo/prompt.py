"""Prompt rendering.

Every backend in this project talks to a *completion* endpoint, never a chat
endpoint. That means the exact string produced here is the exact string the
model conditions on: no runtime of ours appends a persona, a policy preamble or
a reminder, and no template inserts a default system turn when you leave one
out. If you want a system prompt you write one; if you don't, the model sees
only your turns.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .types import Message


@dataclass(frozen=True, slots=True)
class ChatTemplate:
    name: str
    render: Callable[[Sequence[Message], bool], str]
    stop: tuple[str, ...] = ()
    description: str = ""


def _chatml(messages: Sequence[Message], add_generation_prompt: bool) -> str:
    parts = [
        f"<|im_start|>{m.role}\n{m.content}<|im_end|>\n" for m in messages
    ]
    if add_generation_prompt:
        parts.append("<|im_start|>assistant\n")
    return "".join(parts)


def _llama3(messages: Sequence[Message], add_generation_prompt: bool) -> str:
    parts = ["<|begin_of_text|>"]
    for m in messages:
        parts.append(
            f"<|start_header_id|>{m.role}<|end_header_id|>\n\n{m.content}<|eot_id|>"
        )
    if add_generation_prompt:
        parts.append("<|start_header_id|>assistant<|end_header_id|>\n\n")
    return "".join(parts)


def _mistral(messages: Sequence[Message], add_generation_prompt: bool) -> str:
    # Mistral has no system role: a leading system turn is folded into the
    # first user turn rather than silently dropped.
    msgs = list(messages)
    system = ""
    if msgs and msgs[0].role == "system":
        system = msgs.pop(0).content
    out = ["<s>"]
    for i, m in enumerate(msgs):
        if m.role == "user":
            body = f"{system}\n\n{m.content}" if i == 0 and system else m.content
            out.append(f"[INST] {body} [/INST]")
        else:
            out.append(f" {m.content}</s>")
    if not msgs and system:
        out.append(f"[INST] {system} [/INST]")
    return "".join(out)


def _gemma(messages: Sequence[Message], add_generation_prompt: bool) -> str:
    # Gemma has no system role either; it is prepended to the first user turn.
    msgs = list(messages)
    system = ""
    if msgs and msgs[0].role == "system":
        system = msgs.pop(0).content
    parts = ["<bos>"]
    for i, m in enumerate(msgs):
        role = "model" if m.role == "assistant" else "user"
        body = f"{system}\n\n{m.content}" if i == 0 and system else m.content
        parts.append(f"<start_of_turn>{role}\n{body}<end_of_turn>\n")
    if add_generation_prompt:
        parts.append("<start_of_turn>model\n")
    return "".join(parts)


def _alpaca(messages: Sequence[Message], add_generation_prompt: bool) -> str:
    headers = {
        "system": "",
        "user": "### Instruction:\n",
        "assistant": "### Response:\n",
    }
    parts = []
    for m in messages:
        parts.append(f"{headers[m.role]}{m.content}\n\n")
    if add_generation_prompt:
        parts.append("### Response:\n")
    return "".join(parts)


def _raw(messages: Sequence[Message], add_generation_prompt: bool) -> str:
    """Concatenate content with no markers at all.

    This is the base-model path: a pretrained checkpoint that never saw a chat
    format continues text, it does not answer a turn. Use it with a single
    message whose content is the text you want continued.
    """
    return "".join(m.content for m in messages)


TEMPLATES: dict[str, ChatTemplate] = {
    t.name: t
    for t in (
        ChatTemplate("chatml", _chatml, ("<|im_end|>", "<|im_start|>"),
                     "Qwen, Yi, Nous/Hermes, many finetunes"),
        ChatTemplate("llama3", _llama3, ("<|eot_id|>",), "Llama 3.x instruct"),
        ChatTemplate("mistral", _mistral, ("</s>", "[INST]"),
                     "Mistral / Mixtral instruct"),
        ChatTemplate("gemma", _gemma, ("<end_of_turn>",), "Gemma 2/3 instruct"),
        ChatTemplate("alpaca", _alpaca, ("### Instruction:",),
                     "Alpaca / Vicuna-era finetunes"),
        ChatTemplate("raw", _raw, (), "Base model: pure text continuation"),
    )
}


def get_template(name: str) -> ChatTemplate:
    try:
        return TEMPLATES[name]
    except KeyError:
        known = ", ".join(sorted(TEMPLATES))
        raise ValueError(f"unknown template {name!r}; known templates: {known}") from None


def render(
    messages: Sequence[Message],
    template: str = "chatml",
    system_prompt: str | None = None,
    add_generation_prompt: bool = True,
) -> str:
    """Render ``messages`` into the literal string sent to the model.

    ``system_prompt`` is used only when it is set *and* the caller did not
    already supply a system turn. It is never defaulted to a value of ours.
    """
    tmpl = get_template(template)
    msgs = list(messages)
    if system_prompt and not any(m.role == "system" for m in msgs):
        msgs.insert(0, Message("system", system_prompt))
    return tmpl.render(msgs, add_generation_prompt)
