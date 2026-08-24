import pytest

from roflo.cli import build_config, build_parser, main


def parse(*argv):
    return build_parser().parse_args(argv)


class TestParsing:
    def test_subcommand_is_required(self):
        with pytest.raises(SystemExit):
            parse()

    def test_unknown_backend_is_rejected(self):
        with pytest.raises(SystemExit):
            parse("-b", "banana", "check")

    def test_unknown_template_is_rejected(self):
        with pytest.raises(SystemExit):
            parse("-t", "nope", "check")


class TestOverrides:
    def test_flags_override_the_config_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        args = parse("-b", "vllm", "-m", "org/x", "--base-url", "http://h:1",
                     "-t", "llama3", "--temperature", "0.1", "--max-tokens", "16",
                     "--seed", "5", "check")
        cfg = build_config(args)
        assert cfg.backend.kind == "vllm"
        assert cfg.backend.model == "org/x"
        assert cfg.backend.base_url == "http://h:1"
        assert cfg.template == "llama3"
        assert cfg.sampling.temperature == 0.1
        assert cfg.sampling.max_tokens == 16
        assert cfg.sampling.seed == 5

    def test_system_prompt_defaults_to_unset(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert build_config(parse("check")).system_prompt is None

    def test_empty_system_flag_clears_it(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "roflo.toml"
        cfg_file.write_text('system_prompt = "SYS"\n')
        assert build_config(parse("-s", "", "check")).system_prompt is None
        assert build_config(parse("check")).system_prompt == "SYS"


class TestCommands:
    def test_info_lists_backends_and_templates(self, capsys):
        assert main(["info"]) == 0
        out = capsys.readouterr().out
        assert "ollama" in out and "chatml" in out and "raw" in out

    def test_render_prints_the_prompt_without_generating(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["-b", "echo", "-t", "chatml", "render", "hi"]) == 0
        assert "<|im_start|>user\\nhi" in capsys.readouterr().out

    def test_render_no_repr_prints_literally(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["-b", "echo", "-t", "raw", "render", "--no-repr", "hi"]) == 0
        assert capsys.readouterr().out == "hi"

    def test_complete_streams_to_stdout(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["-b", "echo", "complete", "a b c"]) == 0
        assert capsys.readouterr().out.strip() == "a b c"

    def test_check_succeeds_for_a_reachable_backend(self, capsys, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["-b", "echo", "-m", "demo", "check"]) == 0
        assert "reachable: yes" in capsys.readouterr().out

    def test_missing_config_file_exits_nonzero(self, capsys, tmp_path):
        assert main(["-c", str(tmp_path / "absent.toml"), "check"]) == 1
        assert "error" in capsys.readouterr().err
