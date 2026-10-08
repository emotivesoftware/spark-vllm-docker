"""CPU regressions using pinned upstream methods and the DSML decoder.

Protocol wrappers and tokenizer plumbing are lightweight test substitutes.
The serving method, DSML decoding methods and schema utilities are upstream
code. --validate additionally checks the actual image's dependencies.
"""

import ast
import contextlib
import hashlib
import json
import logging
from pathlib import Path
import re
import tempfile
import types
import unittest
import uuid
from unittest.mock import patch

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

import apply_fix

FIXTURES = Path(__file__).parent / "fixtures"


class FunctionDefinition(BaseModel):
    name: str
    description: str | None = None
    parameters: dict | None = None
    defer_loading: bool | None = None


class FunctionCall(BaseModel):
    id: str | None = None
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: "call_" + uuid.uuid4().hex)
    type: str = "function"
    function: FunctionCall


class ExtractedToolCallInformation(BaseModel):
    tools_called: bool
    tool_calls: list[ToolCall]
    content: str | None = None


class ChatCompletionRequest:
    def __init__(self, choice, tools):
        self.tool_choice = choice
        self.tools = tools
        self._grammar_from_tool_parser = False


class ChatCompletionToolsParam:
    def __init__(self, name, properties):
        self.function = types.SimpleNamespace(name=name, parameters={"properties": properties})


class ToolChoiceFunction:
    def __init__(self, name):
        self.name = name


class ChatCompletionNamedToolChoiceParam:
    def __init__(self, name):
        self.function = types.SimpleNamespace(name=name)


class ToolParser:
    supports_required_and_named = True

    def __init__(self, tokenizer, tools):
        self.model_tokenizer = tokenizer
        self.tools = tools


def decoder_namespace():
    ns = dict(json=json, re=re, uuid=uuid, logger=logging.getLogger(__name__),
              ToolParser=ToolParser, ChatCompletionToolsParam=ChatCompletionToolsParam,
              FunctionTool=type("FunctionTool", (), {}),
              FunctionCall=FunctionCall, ToolCall=ToolCall,
              ExtractedToolCallInformation=ExtractedToolCallInformation)
    utils = ast.parse((FIXTURES / "utils.py").read_text())
    names = {"_extract_tool_info", "find_tool_properties", "extract_types_from_schema", "coerce_to_schema_type"}
    nodes = [n for n in utils.body if (isinstance(n, ast.FunctionDef) and n.name in names)
             or (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                 and n.target.id == "_TYPE_ALIASES")]
    parser = ast.parse((FIXTURES / "deepseekv32_tool_parser.py").read_text())
    nodes += [n for n in parser.body if isinstance(n, ast.ClassDef) and n.name == "DeepSeekV32ToolParser"]
    nodes.insert(0, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0))
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, "pinned_dsml_decoder", "exec"), ns)
    base = ns["DeepSeekV32ToolParser"]
    # V4 changes only these delimiters for complete-call extraction.
    ns["DeepSeekV4ToolParser"] = type("DeepSeekV4ToolParser", (base,), {
        "tool_call_start_token": "<｜DSML｜tool_calls>",
        "tool_call_end_token": "</｜DSML｜tool_calls>",
    })
    return ns


class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (FIXTURES / "serving.py").read_text()
        cls.patched = apply_fix.patch_source(cls.original)
        ns = decoder_namespace()
        ns.update(contextlib=contextlib, TypeAdapter=TypeAdapter, ValidationError=ValidationError,
                  FunctionDefinition=FunctionDefinition, ChatCompletionRequest=ChatCompletionRequest,
                  ChatCompletionNamedToolChoiceParam=ChatCompletionNamedToolChoiceParam,
                  ToolChoiceFunction=ToolChoiceFunction, is_mistral_tool_parser=lambda _: False)
        cls.before = staticmethod(apply_fix.isolated_method(cls.original, dict(ns)))
        cls.after = staticmethod(apply_fix.isolated_method(cls.patched, dict(ns)))
        cls.parser_cls = ns["DeepSeekV4ToolParser"]

    def setUp(self):
        self.tools = [ChatCompletionToolsParam("submit_discovery_artifact", {
            "summary": {"type": "string"}, "issues": {"type": "array"},
        })]
        self.args = {"summary": "Verified risks.", "issues": [{"name": "mass assignment"}]}
        self.dsml = ('Final findings.\n<｜DSML｜tool_calls>\n'
                     '<｜DSML｜invoke name="submit_discovery_artifact">\n'
                     '<｜DSML｜parameter name="summary" string="true">Verified risks.</｜DSML｜parameter>\n'
                     '<｜DSML｜parameter name="issues" string="false">[{"name":"mass assignment"}]</｜DSML｜parameter>\n'
                     '</｜DSML｜invoke>\n</｜DSML｜tool_calls>')
        self.encoded = json.dumps([{"name": "submit_discovery_artifact", "parameters": self.args}])

    def parse(self, fn, choice, text, *, enabled=True, parser=True, tokenizer=True):
        return fn(ChatCompletionRequest(choice, self.tools), object() if tokenizer else None,
                  enabled, self.parser_cls if parser else None, text)

    @staticmethod
    def signatures(calls):
        return [(c.name, json.loads(c.arguments)) for c in (calls or [])]

    def test_required_dsml_reproduces_loss_then_preserves_name_arguments_and_id(self):
        self.assertEqual(self.parse(self.before, "required", self.dsml), ([], None))
        calls, content = self.parse(self.after, "required", self.dsml)
        self.assertEqual(self.signatures(calls), [("submit_discovery_artifact", self.args)])
        self.assertTrue(calls[0].id)
        self.assertEqual(content, "Final findings.\n")

    def test_valid_required_json_is_unchanged_and_never_uses_native_parser(self):
        with patch.object(self.parser_cls, "extract_tool_calls", side_effect=AssertionError("native parser called")):
            self.assertEqual(self.parse(self.before, "required", self.encoded),
                             self.parse(self.after, "required", self.encoded))

    def test_chat_auto_and_omitted_choice_keep_existing_decoding(self):
        for choice in ("auto", None):
            with self.subTest(choice=choice):
                before, btext = self.parse(self.before, choice, self.dsml)
                after, atext = self.parse(self.after, choice, self.dsml)
                self.assertEqual(self.signatures(before), self.signatures(after))
                self.assertEqual(btext, atext)

    def test_named_and_responses_forced_json_paths_are_unchanged(self):
        text = json.dumps(self.args)
        for choice in (ChatCompletionNamedToolChoiceParam("submit_discovery_artifact"),
                       ToolChoiceFunction("submit_discovery_artifact")):
            self.assertEqual(self.parse(self.before, choice, text), self.parse(self.after, choice, text))

    def test_none_does_not_extract_calls(self):
        self.assertEqual(self.parse(self.before, "none", self.dsml),
                         self.parse(self.after, "none", self.dsml))

    def test_no_native_fallback_when_auto_tools_disabled_or_no_parser_configured(self):
        for kwargs in ({"enabled": False}, {"parser": False}):
            self.assertEqual(self.parse(self.after, "required", self.dsml, **kwargs),
                             self.parse(self.before, "required", self.dsml, **kwargs))

    def test_missing_tokenizer_fails_loudly_in_native_path(self):
        with self.assertRaisesRegex(ValueError, "Tokenizer not available"):
            self.parse(self.after, "required", self.dsml, tokenizer=False)

    def test_missing_malformed_and_incomplete_submissions_never_become_calls(self):
        for text in (None, "", "Only prose", "[{bad json}]", self.dsml.split("</｜DSML｜invoke>")[0]):
            with self.subTest(text=text):
                calls, _ = self.parse(self.after, "required", text)
                self.assertFalse(calls)

    def test_duplicate_submissions_are_preserved_for_caller_cardinality_validation(self):
        calls, _ = self.parse(self.after, "required", self.dsml + self.dsml)
        self.assertEqual(len(calls), 2)

    def test_unknown_source_and_repeat_application_are_refused(self):
        for source in (self.original + "# changed\n", self.patched):
            with self.assertRaisesRegex(ValueError, "Unrecognized"):
                apply_fix.patch_source(source)

    def test_default_cli_is_read_only_and_build_apply_changes_only_copied_source(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "serving.py"
            path.write_text(self.original)
            with patch.object(sys, "argv", ["apply_fix.py", "--source", str(path)]):
                apply_fix.main()
            self.assertEqual(path.read_text(), self.original)
            with patch.object(sys, "argv", ["apply_fix.py", "--source", str(path), "--apply"]):
                apply_fix.main()
            self.assertEqual(path.read_text(), self.patched)
            self.assertNotEqual(hashlib.sha256(path.read_bytes()).hexdigest(), apply_fix.SOURCE_SHA256)


if __name__ == "__main__":
    unittest.main()
