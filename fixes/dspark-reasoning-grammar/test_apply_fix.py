"""CPU regressions executing the actual pinned manager and scheduler code.

Grammar/reasoner/tensor substitutes isolate boundary bookkeeping. These tests
do not establish correctness of the installed GPU engine or grammar backend.
"""
import ast
import copy
import itertools
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from collections.abc import Iterable, Sequence
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from apply_fix import patch_source

ROOT = Path(__file__).parent
MANIFEST = json.loads((ROOT / 'patch.json').read_text())
FIXTURE_NAMES = ['manager.py', 'scheduler.py', 'request.py']
BASE = [(ROOT / 'fixtures' / name).read_text() for name in FIXTURE_NAMES]
PATCHED = [patch_source(source, entry) for source, entry in zip(BASE, MANIFEST['files'])]
MARKER = 99


class Options(Enum):
    STRUCTURAL_TAG = 1
    JSON_OBJECT = 2
    JSON = 3
    REGEX = 4
    CHOICE = 5
    GRAMMAR = 6


class Reasoner:
    def __init__(self):
        self.deltas = []

    def is_reasoning_end(self, tokens):
        return MARKER in tokens

    def is_reasoning_end_streaming(self, tokens, delta):
        delta = list(delta)
        self.deltas.append(delta)
        return MARKER in delta


class Grammar:
    """Small strict FSM: opening token -> key -> closing token -> EOS."""
    def __init__(self):
        self.tokens = []
        self.calls = []
        self.rollbacks = []

    def expected(self):
        return [10, 11, 12, 13][len(self.tokens)]

    def accept_tokens(self, req_id, tokens):
        self.calls.append(list(tokens))
        state = list(self.tokens)
        for token in tokens:
            if len(state) == 4 or token != [10, 11, 12, 13][len(state)]:
                return False
            state.append(token)
        self.tokens = state
        return True

    def is_terminated(self):
        return len(self.tokens) == 4

    def rollback(self, count):
        self.rollbacks.append(count)
        self.tokens = self.tokens[:-count]


class Tensor:
    def __init__(self, size):
        self.rows = [None] * size

    @property
    def shape(self):
        return (len(self.rows), 1)

    def __getitem__(self, index):
        result = Tensor(0)
        result.rows = self.rows[index]
        return result

    def numpy(self):
        return self.rows


def manager_type(source):
    wanted = {'grammar_bitmask', 'should_fill_bitmask', 'should_advance', '_find_reasoning_end_index', 'trim_reasoning_for_advance'}
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef))
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    module = ast.Module(body=[cls], type_ignores=[])
    scope = dict(TYPE_CHECKING=False, itertools=itertools, Iterable=Iterable, Sequence=Sequence, StructuredOutputOptions=Options)
    exec(compile(module, '<actual-manager>', 'exec'), scope)
    return scope[cls.name]


def make_manager(source=PATCHED[0], reasoner=True, constrained_reasoning=False):
    manager = manager_type(source)()
    manager._get_reasoner = lambda req: req.structured_output_request.reasoner
    manager.enable_in_reasoning = constrained_reasoning
    manager._grammar_bitmask = Tensor(20)
    manager.fill_bitmask_parallel_threshold = 128
    manager.vllm_config = NS(speculative_config=NS(num_speculative_tokens=4))
    def fill(batch):
        for grammar, index, constrained in batch:
            manager._grammar_bitmask.rows[index] = ('terminated' if grammar.is_terminated() else grammar.expected()) if constrained else 'free'
    manager._fill_bitmasks = fill
    req = NS(request_id='req', use_structured_output=True, prompt_token_ids=[1, 2, 3], all_token_ids=[1, 2, 3], num_computed_tokens=0, num_output_placeholders=0, status='running', resumable=True)
    req.structured_output_request = NS(reasoner=Reasoner() if reasoner else None, grammar=Grammar(), reasoning_ended=False, reasoning_end_token_index=None, structured_output_key=(Options.JSON_OBJECT, ''))
    return manager, req


def scheduler_step(source):
    node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.If) and 'self.structured_output_manager.should_advance' in ast.unparse(n.test))
    block = textwrap.dedent(''.join(source.splitlines(keepends=True)[node.lineno-1:node.end_lineno]))
    body = 'def step(self, request, new_token_ids):\n    req_id = request.request_id\n    stopped = False\n' + textwrap.indent(block, '    ') + '    return stopped\n'
    scope = dict(logger=Mock(), RequestStatus=NS(FINISHED_ERROR='error'))
    exec(body, scope)
    return scope['step']


class RegressionTests(unittest.TestCase):
    def test_baseline_placeholder_window_misses_marker(self):
        manager, req = make_manager(BASE[0])
        req.all_token_ids += [7, 8, MARKER, 10]
        req.num_computed_tokens = len(req.all_token_ids)
        req.num_output_placeholders = 1
        self.assertFalse(manager.should_advance(req))
        self.assertEqual(req.structured_output_request.reasoner.deltas[0], [10])
        self.assertFalse(req.structured_output_request.reasoning_ended)

    def test_exact_accepted_window_and_post_marker_advance(self):
        for kind in Options:
            with self.subTest(kind=kind):
                manager, req = make_manager()
                req.structured_output_request.structured_output_key = (kind, '')
                tokens = [7, 8, MARKER, 10, 11]
                req.all_token_ids += tokens
                req.num_computed_tokens = len(req.all_token_ids)
                req.num_output_placeholders = 1
                self.assertFalse(scheduler_step(PATCHED[1])(NS(structured_output_manager=manager), req, tokens))
                structured = req.structured_output_request
                self.assertEqual(structured.reasoner.deltas[0], tokens)
                self.assertTrue(structured.reasoning_ended)
                self.assertEqual(structured.reasoning_end_token_index, 5)
                self.assertEqual(structured.grammar.tokens, [10, 11])

    def test_boundary_at_end_skips_grammar_accept(self):
        manager, req = make_manager()
        tokens = [7, MARKER]
        req.all_token_ids += tokens
        scheduler_step(PATCHED[1])(NS(structured_output_manager=manager), req, tokens)
        self.assertEqual(req.structured_output_request.grammar.calls, [])

    def test_later_step_advances_without_replaying_reasoning(self):
        manager, req = make_manager()
        step = scheduler_step(PATCHED[1])
        req.all_token_ids += [MARKER, 10]
        step(NS(structured_output_manager=manager), req, [MARKER, 10])
        req.all_token_ids += [11, 12]
        step(NS(structured_output_manager=manager), req, [11, 12])
        self.assertEqual(req.structured_output_request.grammar.tokens, [10, 11, 12])

    def test_placeholder_fallback_for_draft_validation_unchanged(self):
        manager, req = make_manager()
        req.all_token_ids = [1, 2, 3, 4, 5]
        req.num_computed_tokens = 5
        req.num_output_placeholders = 2
        self.assertFalse(manager.should_advance(req))
        self.assertEqual(req.structured_output_request.reasoner.deltas[-1], [4, 5])

    def test_baseline_midwindow_bitmask_is_unconstrained(self):
        manager, req = make_manager(BASE[0])
        rows = manager.grammar_bitmask({'req': req}, ['req'], {'req': [7, MARKER, 10]})
        self.assertEqual(rows, ['free'] * 4)

    def test_midwindow_masks_and_grammar_rollback(self):
        manager, req = make_manager()
        rows = manager.grammar_bitmask({'req': req}, ['req'], {'req': [7, MARKER, 10]})
        self.assertEqual(rows, ['free', 'free', 10, 11])
        self.assertEqual(req.structured_output_request.grammar.tokens, [])
        self.assertEqual(req.structured_output_request.grammar.rollbacks, [1])
        self.assertFalse(req.structured_output_request.reasoning_ended)

    def test_invalid_post_marker_draft_does_not_assert(self):
        manager, req = make_manager()
        rows = manager.grammar_bitmask({'req': req}, ['req'], {'req': [MARKER, 27]})
        self.assertEqual(rows, ['free', 10, 10])
        self.assertEqual(req.structured_output_request.grammar.tokens, [])

    def test_invalid_draft_after_content_started_still_asserts(self):
        manager, req = make_manager(reasoner=False)
        with self.assertRaises(AssertionError):
            manager.grammar_bitmask({'req': req}, ['req'], {'req': [27]})

    def test_padding_bonus_remains_constrained(self):
        manager, req = make_manager(reasoner=False)
        rows = manager.grammar_bitmask({'req': req}, ['req'], {'req': [10, -1, -1]})
        self.assertEqual(rows, [10, 11, 'free', 11])
        self.assertEqual(req.structured_output_request.grammar.tokens, [])

    def test_no_reasoner_and_reasoning_constrained_controls(self):
        for has_reasoner, constrained in [(False, False), (True, True)]:
            manager, req = make_manager(reasoner=has_reasoner, constrained_reasoning=constrained)
            req.all_token_ids += [10, 11]
            scheduler_step(PATCHED[1])(NS(structured_output_manager=manager), req, [10, 11])
            self.assertEqual(req.structured_output_request.grammar.tokens, [10, 11])

    def test_non_structured_request_not_advanced(self):
        manager, req = make_manager()
        req.use_structured_output = False
        req.structured_output_request = None
        self.assertFalse(manager.should_advance(req, [10]))

    def test_committed_invalid_suffix_keeps_scheduler_error_path(self):
        manager, req = make_manager()
        req.all_token_ids += [MARKER, 27]
        self.assertTrue(scheduler_step(PATCHED[1])(NS(structured_output_manager=manager), req, [MARKER, 27]))
        self.assertEqual(req.status, 'error')
        self.assertFalse(req.resumable)

    def test_empty_batch_and_empty_request_list(self):
        manager, req = make_manager()
        self.assertIsNone(manager.grammar_bitmask({}, [], {}))
        self.assertFalse(scheduler_step(PATCHED[1])(NS(structured_output_manager=manager), req, []))
        self.assertEqual(req.structured_output_request.grammar.calls, [])

    def test_upstream_boundary_helpers_match_exactly(self):
        upstream = (ROOT / 'fixtures/upstream_manager.py').read_text()
        for name in ['should_advance', '_find_reasoning_end_index', 'trim_reasoning_for_advance']:
            nodes = [next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == name) for src in [upstream, PATCHED[0]]]
            self.assertEqual(ast.dump(nodes[0]), ast.dump(nodes[1]))

    def test_only_three_engine_files_patched_and_all_compile(self):
        self.assertEqual([e['path'] for e in MANIFEST['files']], ['v1/structured_output/__init__.py', 'v1/core/sched/scheduler.py', 'v1/structured_output/request.py'])
        for src, entry in zip(PATCHED, MANIFEST['files']):
            compile(src, entry['path'], 'exec')

    def test_hash_guards_refuse_unknown_repeated_and_bad_anchor(self):
        entry = MANIFEST['files'][0]
        for src in [BASE[0] + '\n', PATCHED[0]]:
            with self.assertRaises(ValueError):
                patch_source(src, entry)
        altered = copy.deepcopy(entry)
        altered['replacements'][0]['old'] = 'absent anchor'
        with self.assertRaises(ValueError):
            patch_source(BASE[0], altered)

    def test_cli_validates_all_files_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = []
            for src, entry in zip(BASE, MANIFEST['files']):
                path = root / entry['path']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(src)
                paths.append(path)
            command = [sys.executable, str(ROOT / 'apply_fix.py'), '--root', str(root)]
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual([p.read_text() for p in paths], BASE)
            paths[-1].write_text(BASE[-1] + '\n')
            result = subprocess.run(command + ['--apply'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(paths[0].read_text(), BASE[0])
            paths[-1].write_text(BASE[-1])
            subprocess.run(command + ['--apply'], check=True, capture_output=True)
            self.assertEqual([p.read_text() for p in paths], PATCHED)


if __name__ == '__main__':
    unittest.main()
