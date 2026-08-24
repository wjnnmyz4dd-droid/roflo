import pytest

from roflo.config import BackendConfig, Config
from roflo.engine import Engine, estimate_tokens
from roflo.types import Chunk, Message, SamplingParams


class ScriptedBackend:
    """Backend that replays a fixed list of chunks and records its prompt."""

    kind = "scripted"
    base_url = "in-process"

    def __init__(self, pieces, finish="stop"):
        self.pieces = pieces
        self.finish = finish
        self.prompt = None
        self.params = None

    async def stream(self, prompt, params):
        self.prompt = prompt
        self.params = params
        for piece in self.pieces:
            yield Chunk(text=piece)
        if self.finish:
            yield Chunk(finish_reason=self.finish)

    async def list_models(self):
        return []

    async def health(self):
        return True

    async def aclose(self):
        return None


def engine_with(pieces, finish="stop", **config_kwargs):
    cfg = Config(backend=BackendConfig(kind="echo", model="m"), **config_kwargs)
    eng = Engine(cfg)
    eng.backend = ScriptedBackend(pieces, finish)
    return eng


async def collect(engine, messages):
    return [c async for c in engine.stream_chat(messages)]


USER = [Message("user", "hi")]


class TestRendering:
    def test_prompt_reaching_the_backend_has_no_extra_content(self):
        eng = engine_with(["x"])
        rendered = eng.render(USER)
        assert rendered == "<|im_start|>user\nhi<|im_end|>\n<|im_start|>assistant\n"

    async def test_backend_receives_exactly_the_rendered_prompt(self):
        eng = engine_with(["x"])
        await collect(eng, USER)
        assert eng.backend.prompt == eng.render(USER)

    def test_configured_system_prompt_is_applied(self):
        eng = engine_with(["x"], system_prompt="SYS")
        assert "SYS" in eng.render(USER)

    def test_explicit_none_suppresses_the_configured_system_prompt(self):
        eng = engine_with(["x"], system_prompt="SYS")
        assert "SYS" not in eng.render(USER, system_prompt=None)

    def test_template_can_be_overridden_per_call(self):
        eng = engine_with(["x"])
        assert eng.render(USER, template="raw") == "hi"


class TestStopHandling:
    def test_template_stops_are_added(self):
        eng = engine_with(["x"])
        assert "<|im_end|>" in eng.resolve_params().stop

    def test_raw_template_adds_no_stops(self):
        eng = engine_with(["x"], template="raw")
        assert eng.resolve_params().stop == []

    def test_request_stops_survive_the_merge(self):
        eng = engine_with(["x"])
        resolved = eng.resolve_params(SamplingParams(stop=["END"]))
        assert "END" in resolved.stop and "<|im_end|>" in resolved.stop

    async def test_marker_within_one_chunk_is_trimmed(self):
        eng = engine_with(["hello<|im_end|>trailing"], finish=None)
        text = "".join(c.text for c in await collect(eng, USER))
        assert text == "hello"

    async def test_marker_split_across_chunks_is_trimmed(self):
        """The partial marker must not leak out before the next chunk lands."""
        eng = engine_with(["hello", "<|im_", "end|>junk"], finish=None)
        chunks = await collect(eng, USER)
        text = "".join(c.text for c in chunks)
        assert text == "hello"
        assert "<|im_" not in text

    async def test_text_resembling_a_marker_is_still_delivered(self):
        eng = engine_with(["a<|im_", "b"], finish="stop")
        text = "".join(c.text for c in await collect(eng, USER))
        assert text == "a<|im_b"

    async def test_finish_reason_is_forwarded(self):
        eng = engine_with(["a"], finish="length")
        assert (await collect(eng, USER))[-1].finish_reason == "length"

    async def test_stream_ending_without_finish_still_flushes(self):
        eng = engine_with(["abc"], finish=None)
        text = "".join(c.text for c in await collect(eng, USER))
        assert text == "abc"


class TestChat:
    async def test_returns_text_reason_and_usage(self):
        eng = engine_with(["hi ", "there"])
        text, finish, usage = await eng.chat(USER)
        assert text == "hi there"
        assert finish == "stop"
        assert usage.prompt_tokens > 0
        assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens


@pytest.mark.parametrize("text,expected", [("", 0), ("a", 1), ("a" * 400, 100)])
def test_estimate_tokens(text, expected):
    assert estimate_tokens(text) == expected
