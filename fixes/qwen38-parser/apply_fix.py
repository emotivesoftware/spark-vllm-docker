"""Pinned backport of vLLM #52830. Default is read-only; --apply is build-only."""

import argparse
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import types


SOURCE_SHA256 = "3a77693801ffe3fa72a37786b4204535ea0000590266daec70552981d86bb924"
SHORTCUT = """        reasoning_engine_cls = cls._get_parser_engine_cls(reasoning_parser_cls)
        tool_engine_cls = cls._get_parser_engine_cls(tool_parser_cls)
        if reasoning_engine_cls is not None and reasoning_engine_cls is tool_engine_cls:
            return reasoning_engine_cls

"""


def patch_source(source: str) -> str:
    """Refuse any source other than the incident build; preserve adapters."""
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"Unrecognized parser_manager.py sha256: {digest}")
    if source.count(SHORTCUT) != 1:
        raise ValueError("Expected exactly one shared-engine shortcut")
    # The upstream fix removes this early return. Keeping the existing helper
    # unused avoids unrelated edits to the pinned vendor source.
    result = source.replace(SHORTCUT, "")
    result = result.replace(
        "Parser engine adapters backed by the same engine are collapsed back into\n"
        "    that engine. Other parser pairs are composed through ``DelegatingParser``.",
        "Reasoning and tool adapters are preserved through ``DelegatingParser``.",
    ).replace(
        "Reuses a shared parser engine when possible, otherwise composes the\n"
        "        individual parsers into a ``DelegatingParser`` subclass.",
        "Composes individual parsers into a ``DelegatingParser`` subclass.",
    )
    compile(result, "parser_manager.py", "exec")
    return result


def validate(source: str, patched: str, tokenizer_path: str) -> None:
    """CPU-only before/after using installed dependencies; no serving mutation."""
    from transformers import AutoTokenizer
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    for label, code in (("before", source), ("after", patched)):
        module = types.ModuleType(f"isolated_{label}_parser_manager")
        exec(compile(code, module.__name__, "exec"), module.__dict__)
        for thinking, strict in itertools.product((False, True), (None, True)):
            for choice in ("named", "required", "auto", "none"):
                cls = module.ParserManager.get_parser(
                    tool_parser_name="qwen3_xml",
                    reasoning_parser_name="qwen3",
                    enable_auto_tools=True,
                )
                parser = cls(
                    tokenizer, chat_template_kwargs={"enable_thinking": thinking}
                )
                request = ChatCompletionRequest(
                    model="probe",
                    messages=[{"role": "user", "content": "Return value 1"}],
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "probe_schema",
                                "parameters": schema,
                                **({"strict": strict} if strict is not None else {}),
                            },
                        }
                    ],
                    tool_choice={
                        "type": "function",
                        "function": {"name": "probe_schema"},
                    }
                    if choice == "named"
                    else choice,
                    chat_template_kwargs={"enable_thinking": thinking},
                    max_tokens=32,
                )
                structured = parser.adjust_request(request).structured_outputs
                tag = structured.structural_tag if structured else None
                if label == "before":
                    if structured is not None or parser.reasoning_parser is not None:
                        raise RuntimeError("Incident baseline no longer reproduces")
                else:
                    if parser.reasoning_parser is None:
                        raise RuntimeError("Reasoning adapter was lost")
                    if (
                        parser.reasoning_parser._parser_engine.thinking_enabled
                        != thinking
                    ):
                        raise RuntimeError("Thinking configuration was lost")
                    constrained = choice in ("named", "required") or (
                        choice == "auto" and strict
                    )
                    if constrained and not (tag and "probe_schema" in tag):
                        raise RuntimeError("Tool schema constraint was lost")
                    if strict and choice != "none" and "value" not in tag:
                        raise RuntimeError("Strict argument schema was lost")
                    if choice == "none" and structured is not None:
                        raise RuntimeError("tool_choice=none must remain unconstrained")
                    if choice == "auto" and not strict and structured is not None:
                        raise RuntimeError(
                            "Non-strict automatic choice must remain unconstrained"
                        )
                    if not thinking and choice in ("named", "required"):
                        output = "<tool_call>\n<function=probe_schema>\n<parameter=value>1</parameter>\n</function>\n</tool_call>"
                        _, _, calls = parser.parse(
                            output,
                            request,
                            enable_auto_tools=True,
                            model_output_token_ids=tokenizer.encode(
                                output, add_special_tokens=False
                            ),
                        )
                        if len(calls or []) != 1 or calls[0].name != "probe_schema":
                            raise RuntimeError("Native tool-call decoding was lost")
                        if json.loads(calls[0].arguments) != {"value": 1}:
                            raise RuntimeError("Native tool arguments were corrupted")
                print(
                    json.dumps(
                        {
                            "variant": label,
                            "choice": choice,
                            "thinking": thinking,
                            "strict": strict,
                            "has_constraint": bool(tag),
                        }
                    )
                )
    print("READ_ONLY_PARSER_REGRESSION_OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--apply", action="store_true", help="Modify source in an image build only"
    )
    actions.add_argument(
        "--validate", metavar="LOCAL_TOKENIZER", help="Compare in RAM, without writing"
    )
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    path = args.source
    if path is None:
        spec = importlib.util.find_spec("vllm")
        if spec is None or spec.origin is None:
            raise RuntimeError("vLLM package not found")
        path = Path(spec.origin).parent / "parser" / "parser_manager.py"
    original = path.read_text()
    patched = patch_source(original)
    print(
        json.dumps(
            {
                "before_sha256": SOURCE_SHA256,
                "after_sha256": hashlib.sha256(patched.encode()).hexdigest(),
            }
        )
    )
    if args.validate:
        validate(original, patched, args.validate)
        if path.read_text() != original:
            raise RuntimeError("Installed source changed during read-only validation")
    if args.apply:
        # Docker build fails if this write fails; no running service uses this layer.
        path.write_text(patched)
        if path.read_text() != patched:
            raise RuntimeError("Patch write verification failed")


if __name__ == "__main__":
    main()
