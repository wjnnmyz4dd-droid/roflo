import pytest

from roflo.prompt import TEMPLATES, get_template, render
from roflo.types import Message

USER = [Message("user", "hi")]
TURNS = [Message("user", "a"), Message("assistant", "b"), Message("user", "c")]


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_template_renders_all_content(name):
    out = render(TURNS, name)
    for message in TURNS:
        assert message.content in out


def test_no_system_prompt_is_injected_by_default():
    """The core guarantee: an unset system prompt means no system turn."""
    out = render(USER, "chatml")
    assert "system" not in out
    assert out == "<|im_start|>user\nhi<|im_end|>\n<|im_start|>assistant\n"


def test_system_prompt_used_only_when_given():
    out = render(USER, "chatml", system_prompt="be terse")
    assert "<|im_start|>system\nbe terse<|im_end|>" in out


def test_caller_supplied_system_turn_wins():
    messages = [Message("system", "mine"), *USER]
    out = render(messages, "chatml", system_prompt="theirs")
    assert "mine" in out
    assert "theirs" not in out
    assert out.count("system") == 1


def test_empty_system_prompt_adds_nothing():
    assert render(USER, "chatml", system_prompt="") == render(USER, "chatml")


@pytest.mark.parametrize("name", ["mistral", "gemma"])
def test_system_folded_for_templates_without_a_system_role(name):
    """Templates with no system slot must fold it in, never drop it."""
    out = render(USER, name, system_prompt="SYS")
    assert "SYS" in out
    assert "hi" in out


def test_mistral_system_only_conversation():
    out = render([], "mistral", system_prompt="SYS")
    assert "SYS" in out


def test_raw_template_adds_no_markers():
    assert render([Message("user", "once upon")], "raw") == "once upon"


def test_generation_prompt_can_be_suppressed():
    with_prompt = render(USER, "chatml", add_generation_prompt=True)
    without = render(USER, "chatml", add_generation_prompt=False)
    assert with_prompt.endswith("<|im_start|>assistant\n")
    assert not without.endswith("<|im_start|>assistant\n")


def test_unknown_template_lists_known_ones():
    with pytest.raises(ValueError, match="chatml"):
        get_template("nope")


def test_llama3_emits_single_bos():
    assert render(TURNS, "llama3").count("<|begin_of_text|>") == 1
