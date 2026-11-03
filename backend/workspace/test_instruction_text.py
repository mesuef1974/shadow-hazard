from django.test import SimpleTestCase
from .instruction_text import merge_instructions


class InstructionStructureTests(SimpleTestCase):
    def test_distinct_examples_keep_repeated_fences_and_statements(self):
        first = 'Example one\n```python\n    pass\n```'
        second = 'Example two\n```python\n    pass\n```'
        self.assertEqual(merge_instructions([first, second]), first+'\n\n'+second)

    def test_only_whole_identical_blocks_are_removed(self):
        block = '- first\n- repeated\n- repeated'
        self.assertEqual(merge_instructions([block, '', block, '- repeated']), block+'\n\n- repeated')

    def test_indentation_and_whitespace_are_not_semantically_normalized(self):
        self.assertEqual(merge_instructions(['  rule', 'rule', '  rule']), '  rule\n\nrule')
