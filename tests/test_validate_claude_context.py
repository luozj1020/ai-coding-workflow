import importlib.util, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; P=ROOT/'scripts/validate-claude-context.py'
s=importlib.util.spec_from_file_location('validate_claude_context',P); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_complete_packet_enables_execution_only(self):
  text='''## Claude Context Packet\n| Field | Value |\n|---|---|\n| Target files/modules | a.py, b.py |\n| Relevant symbols/functions | f, g |\n| Reference examples / source of truth | ref.py |\n| Do not read / do not modify | vendor/ |\n| Known constraints | no API changes |\n| Narrow validation commands | pytest -q tests/x.py |\n| Context is sufficient for execution? | yes |\n| Execution-only eligible? | yes |\n'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'t.md';p.write_text(text);r=m.validate(p);self.assertTrue(r['complete']);self.assertTrue(r['execution_only_eligible']);self.assertEqual(r['target_file_count'],2)
 def test_missing_fields_fail_closed(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'t.md';p.write_text('## Claude Context Packet\n| Target files/modules | a.py |');self.assertFalse(m.validate(p)['complete'])
if __name__=='__main__': unittest.main()
