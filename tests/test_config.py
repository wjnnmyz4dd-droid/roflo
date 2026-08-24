import pytest

from roflo import config as config_module

TOML = """
template = "llama3"
system_prompt = "hello"

[backend]
kind = "vllm"
model = "some/model"
base_url = "http://box:8000"

[sampling]
temperature = 0.3
max_tokens = 64

[server]
port = 9999
"""


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in list(os_environ_keys()):
        monkeypatch.delenv(key, raising=False)


def os_environ_keys():
    import os

    return [k for k in os.environ if k.startswith("ROFLO_")]


def write(tmp_path, text):
    path = tmp_path / "roflo.toml"
    path.write_text(text)
    return path


def test_defaults_carry_no_system_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = config_module.load()
    assert cfg.system_prompt is None
    assert cfg.backend.kind == "ollama"
    assert cfg.template == "chatml"


def test_loads_toml(tmp_path):
    cfg = config_module.load(write(tmp_path, TOML))
    assert cfg.backend.kind == "vllm"
    assert cfg.backend.base_url == "http://box:8000"
    assert cfg.template == "llama3"
    assert cfg.sampling.temperature == 0.3
    assert cfg.sampling.max_tokens == 64
    assert cfg.server.port == 9999


def test_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ROFLO_BACKEND", "llamacpp")
    monkeypatch.setenv("ROFLO_TEMPERATURE", "1.5")
    cfg = config_module.load(write(tmp_path, TOML))
    assert cfg.backend.kind == "llamacpp"
    assert cfg.sampling.temperature == 1.5


def test_env_can_clear_system_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("ROFLO_SYSTEM_PROMPT", "")
    cfg = config_module.load(write(tmp_path, TOML))
    assert cfg.system_prompt is None


def test_missing_explicit_config_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        config_module.load(tmp_path / "absent.toml")


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="typo"):
        config_module.load(write(tmp_path, "[backend]\ntypo = 1\n"))


def test_unknown_top_level_key_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="nonsense"):
        config_module.load(write(tmp_path, "nonsense = 1\n"))
