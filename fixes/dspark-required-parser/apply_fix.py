"""BRAIN-2255: preserve required native calls after JSON parsing fails.

Default mode checks the pinned source without writing. --apply is image-build
only; never run it inside a serving container.
"""

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path


SOURCE_SHA256 = "a7bca64cebc8670f9644e7ca8a729e5841b07a8e9e2cd6a50aaf886e5a9536a3"
CHANGES = (
    (
        "        function_calls = list[FunctionCall]()\n",
        "        function_calls = list[FunctionCall]()\n"
        "        required_json_failed = False\n",
    ),
    (
        "            with contextlib.suppress(ValidationError):\n"
        "                content = content or \"\"\n"
        "                tool_calls = TypeAdapter(list[FunctionDefinition]).validate_json(\n"
        "                    content\n"
        "                )\n",
        "            try:\n"
        "                tool_calls = TypeAdapter(list[FunctionDefinition]).validate_json(\n"
        "                    content or \"\"\n"
        "                )\n"
        "            except ValidationError:\n"
        "                # A reasoning-enabled model may emit its native tool format.\n"
        "                # Keep that output for the configured parser below.\n"
        "                required_json_failed = bool(\n"
        "                    enable_auto_tools and tool_parser_cls and request.tools\n"
        "                )\n",
    ),
    (
        "            content = None  # Clear content since tool is called.\n"
        "        elif tool_parser_cls and (\n",
        "            if not required_json_failed:\n"
        "                content = None  # Clear content since tool is called.\n"
        "        if tool_parser_cls and (\n",
    ),
    (
        "                    request.tool_choice == \"auto\"\n",
        "                    (required_json_failed and request.tools)\n"
        "                    or request.tool_choice == \"auto\"\n",
    ),
)


def patch_source(source: str) -> str:
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"Unrecognized serving.py sha256: {digest}")
    result = source
    for old, new in CHANGES:
        if result.count(old) != 1:
            raise ValueError("Expected exactly one source anchor")
        result = result.replace(old, new, 1)
    compile(result, "serving.py", "exec")
    return result


def isolated_method(source: str, namespace: dict):
    """Compile only the upstream method, avoiding server/CUDA initialization."""
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "OpenAIServing")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_parse_tool_calls_from_content")
    method.decorator_list = []
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        method,
    ], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "isolated_required_parser", "exec"), namespace)
    return namespace[method.name]


def validate_installed(source: str, patched: str, tokenizer_path: str) -> None:
    """Read-only comparison using the image's real protocol and DSML parser."""
    from transformers import AutoTokenizer
    import vllm.entrypoints.openai.engine.serving as serving
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from vllm.tool_parsers.deepseekv4_tool_parser import DeepSeekV4ToolParser

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
    tools = [{"type": "function", "function": {
        "name": "submit_discovery_artifact",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string"},
            "issues": {"type": "array", "items": {"type": "object"}},
        }, "required": ["summary", "issues"]},
    }}]
    dsml = ('Final findings.\n<｜DSML｜tool_calls>\n'
            '<｜DSML｜invoke name="submit_discovery_artifact">\n'
            '<｜DSML｜parameter name="summary" string="true">Verified risks.</｜DSML｜parameter>\n'
            '<｜DSML｜parameter name="issues" string="false">[{"name":"mass assignment"}]</｜DSML｜parameter>\n'
            '</｜DSML｜invoke>\n</｜DSML｜tool_calls>')
    arguments = {"summary": "Verified risks.", "issues": [{"name": "mass assignment"}]}
    encoded = json.dumps([{"name": "submit_discovery_artifact", "parameters": arguments}])
    for label, code in (("before", source), ("after", patched)):
        parse = isolated_method(code, dict(vars(serving)))
        for choice, text in (("required", dsml), ("required", encoded), ("auto", dsml)):
            request = ChatCompletionRequest(model="probe", messages=[{
                "role": "user", "content": "Return the findings through the tool."
            }], tools=tools, tool_choice=choice, max_tokens=2048)
            calls, _ = parse(request, tokenizer, True, DeepSeekV4ToolParser, text)
            expected = 0 if label == "before" and choice == "required" and text == dsml else 1
            if len(calls or []) != expected:
                raise RuntimeError(f"Unexpected {label}/{choice} call count")
            if expected and (calls[0].name != "submit_discovery_artifact"
                             or json.loads(calls[0].arguments) != arguments):
                raise RuntimeError("Tool identity or arguments changed")
            print(json.dumps({"variant": label, "choice": choice,
                              "format": "DSML" if text == dsml else "JSON",
                              "call_count": len(calls or [])}))
    print("READ_ONLY_DSPARK_REQUIRED_REGRESSION_OK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true", help="Write only in an image build")
    actions.add_argument("--validate", metavar="LOCAL_TOKENIZER", help="Compare installed parsers in RAM")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    path = args.source
    if path is None:
        spec = importlib.util.find_spec("vllm")
        if spec is None or spec.origin is None:
            raise RuntimeError("vLLM package not found")
        path = Path(spec.origin).parent / "entrypoints" / "openai" / "engine" / "serving.py"
    original = path.read_text()
    patched = patch_source(original)
    print(json.dumps({"before_sha256": SOURCE_SHA256,
                      "after_sha256": hashlib.sha256(patched.encode()).hexdigest()}))
    if args.validate:
        validate_installed(original, patched, args.validate)
        if path.read_text() != original:
            raise RuntimeError("Installed source changed during validation")
    if args.apply:
        path.write_text(patched)
        if path.read_text() != patched:
            raise RuntimeError("Patch write verification failed")


if __name__ == "__main__":
    main()
