"""Keep complete published examples and the bundled release contract executable."""
import json
import re
from pathlib import Path

from app.services.insight_rules import parse_rules

ROOT = Path(__file__).resolve().parents[2]


def test_complete_website_ruleset_examples_are_valid():
    text = (ROOT / 'website/guide/contributing/insight-rules.md').read_text()
    examples = [json.loads(block) for block in re.findall(r'```json\n(.*?)\n```', text, re.DOTALL)
                if block.lstrip().startswith('{')]
    rulesets = [example for example in examples if 'schema_version' in example]
    assert {example['schema_version'] for example in rulesets} == {1, 2}
    for example in rulesets:
        parse_rules(example)
    chain = next(example for example in rulesets if any(
        rule.get('kind') == 'scan_ban_chain' for rule in example['rules']))
    bundled = json.loads((ROOT / 'backend/app/insights/rules/default-rules.json').read_text())
    assert chain['rules'][0] in bundled['rules']


def test_bundled_chain_matches_verified_fixture_and_public_stays_compatible():
    bundled = json.loads((ROOT / 'backend/app/insights/rules/default-rules.json').read_text())
    fixture = json.loads((Path(__file__).parent / 'fixtures/insight-level2.json').read_text())
    public = json.loads((ROOT / 'website/public/rules/insights-rules.json').read_text())
    assert bundled['schema_version'] == 2
    assert public['schema_version'] == 1
    assert next(rule for rule in bundled['rules'] if rule['id'] == fixture['rules'][0]['id']) == fixture['rules'][0]
    parse_rules(bundled)
    parse_rules(public)
