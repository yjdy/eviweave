"""Independent artificial configuration and monetary rounding regressions."""
import copy
import csv
from decimal import Decimal, localcontext
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout, redirect_stderr

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
import experiment as cli
import experiment_config as cfg
import experiment_store as files
import cost_accounting as accounting
import cost_reports as reports
import test_unified_experiment as unified
import test_cost_observability as costs
import verify_release


class ConfigTests(unittest.TestCase):
    setUp = unified.UnifiedTests.setUp
    prepared = unified.UnifiedTests.prepared
    initialize = unified.UnifiedTests.initialize

    def test_unknown_fields_rejected_at_every_level_without_values(self):
        raw = json.loads(self.config.read_text())
        marker = 'ARTIFICIAL-PRIVATE-VALUE'
        for path in ((), ('experiment',), ('experiment', 'model'),
                     ('experiment', 'workbench'), ('harnesses', 'codex')):
            with self.subTest(path=path):
                changed = copy.deepcopy(raw)
                node = changed
                for part in path:
                    node = node[part]
                node['api_key'] = marker
                self.config.write_text(json.dumps(changed))
                with self.assertRaises(ValueError) as error:
                    cfg.resolved(self.config)
                self.assertNotIn(marker, str(error.exception))
        raw['harnesses']['unused'] = {'enabled': False, 'password': marker}
        self.config.write_text(json.dumps(raw))
        with self.assertRaises(ValueError) as error:
            cfg.resolved(self.config)
        self.assertNotIn(marker, str(error.exception))

    def test_nested_leaf_values_types_and_credential_endpoints_rejected(self):
        raw = json.loads(self.config.read_text())
        paths = [('experiment', 'model', 'key'), ('experiment', 'workbench', 'image'),
                 ('harnesses', 'codex', 'version'), ('harnesses', 'codex', 'enabled'),
                 ('harnesses', 'codex', 'simple_fixes')]
        for path in paths:
            for value in ({'password': 'ARTIFICIAL-PRIVATE-VALUE'}, ['ARTIFICIAL-PRIVATE-VALUE']):
                with self.subTest(path=path, kind=type(value).__name__):
                    changed = copy.deepcopy(raw)
                    changed[path[0]][path[1]][path[2]] = value
                    self.config.write_text(json.dumps(changed))
                    with self.assertRaises(ValueError) as error:
                        cfg.resolved(self.config)
                    self.assertNotIn('ARTIFICIAL-PRIVATE-VALUE', str(error.exception))
        for field in ('openai_base_url', 'anthropic_base_url'):
            for address in ('https://user:ARTIFICIAL-PRIVATE-VALUE@fixture.example/v1',
                            'https://fixture.example/v1?key=ARTIFICIAL-PRIVATE-VALUE',
                            'https://fixture.example/v1#ARTIFICIAL-PRIVATE-VALUE'):
                changed = copy.deepcopy(raw)
                changed['experiment']['model'][field] = address
                self.config.write_text(json.dumps(changed))
                with self.assertRaises(ValueError) as error:
                    cfg.resolved(self.config)
                self.assertNotIn('ARTIFICIAL-PRIVATE-VALUE', str(error.exception))

    def test_invalid_run_and_check_have_no_output_or_execution_side_effects(self):
        raw = json.loads(self.config.read_text())
        raw['harnesses']['codex']['api_key'] = 'ARTIFICIAL-PRIVATE-VALUE'
        self.config.write_text(json.dumps(raw))
        exclude = PROJECT / '.git/info/exclude'
        before = exclude.read_bytes() if exclude.exists() else None
        for command, target in (('run', self.root / 'absent'), ('run', self.experiment), ('check', None)):
            args = [command, '--config', str(self.config), '--dataset', str(self.dataset),
                    '--evaluation-root', str(self.evaluation), '--tasks', '1']
            if target:
                args += ['--output', str(target)]
            env, scheduler = Mock(side_effect=AssertionError('must not check environment')), Mock()
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                with self.assertRaises(ValueError) as error:
                    cli.execute(cli.parser().parse_args(args),
                                prepare_fn=lambda c,d,e,t: cfg.prepare(c,d,e,t,env), schedule_fn=scheduler)
            env.assert_not_called()
            scheduler.assert_not_called()
            self.assertNotIn('ARTIFICIAL-PRIVATE-VALUE', output.getvalue() + str(error.exception))
        self.assertFalse((self.root / 'absent').exists())
        self.assertEqual(list(self.experiment.iterdir()), [])
        self.assertEqual(exclude.read_bytes() if exclude.exists() else None, before)

    def test_valid_snapshot_bytes_and_environment_key_are_unchanged(self):
        raw = json.loads(self.config.read_text())
        raw['experiment']['model']['anthropic_base_url'] = ''
        raw['harnesses']['codex']['runtime_path'] = '../runtime with spaces/codex-{version}'
        raw['experiment']['workbench']['rootfs_path'] = '../workbench with spaces/rootfs'
        self.config.write_text(json.dumps(raw))
        expected = copy.deepcopy(raw)
        expected['harnesses']['codex']['runtime_path'] = str((self.config.parent / raw['harnesses']['codex']['runtime_path']).resolve())
        expected['experiment']['workbench']['rootfs_path'] = str((self.config.parent / raw['experiment']['workbench']['rootfs_path']).resolve())
        with patch.dict(os.environ, EVIWEAVE_API_KEY='ARTIFICIAL-ENV-SECRET'):
            _, actual = cfg.resolved(self.config)
        self.assertEqual(actual, (json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode())
        self.assertNotIn(b'ARTIFICIAL-ENV-SECRET', actual)
        self.initialize()
        self.assertEqual((self.experiment / 'config.resolved.yaml').read_bytes(), actual)

    def test_malformed_yaml_and_conversion_errors_do_not_echo_values(self):
        for text in ('experiment: [ARTIFICIAL-PRIVATE-VALUE',
                     '{"experiment": {"model": "ARTIFICIAL-PRIVATE-VALUE"}}'):
            self.config.write_text(text)
            with self.assertRaises(ValueError) as error:
                cfg.resolved(self.config)
            self.assertNotIn('ARTIFICIAL-PRIVATE-VALUE', str(error.exception))

    def test_config_change_during_frozen_load_is_rejected(self):
        import dataspace_baselines.config as frozen
        original = frozen.load_harness_experiment_config
        def changing(path):
            result = original(path)
            path.write_text('{"password":"ARTIFICIAL-PRIVATE-VALUE"}')
            return result
        with patch.object(frozen, 'load_harness_experiment_config', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'configuration changed'):
                cfg.resolved(self.config)


class RoundingTests(unittest.TestCase):
    setUp = costs.CostTests.setUp
    add = costs.CostTests.add
    record = costs.CostTests.record
    snapshot = costs.CostTests.snapshot
    v1_fixture = costs.CostTests.v1_fixture

    def price(self, subtotal, quantum, mode='half_even'):
        p = costs.pricing()
        p['prices'][0]['components'] = [
            {'field': 'input_tokens', 'subtract': [], 'rate': subtotal, 'unit': 'token', 'unit_scale': '100'},
            {'field': 'output_tokens', 'subtract': [], 'rate': '0', 'unit': 'token', 'unit_scale': '1'}]
        p['prices'][0]['rounding'] = {'mode': mode, 'quantum': quantum}
        return p

    def test_arbitrary_quantum_rounding_against_handwritten_results(self):
        record = self.record()
        cases = [('0.215','0.05','half_even','0.20'), ('0.225','0.05','half_even','0.20'),
                 ('0.275','0.05','half_even','0.30'), ('0.225','0.05','half_up','0.25'),
                 ('0.249','0.05','truncate','0.20'), ('0.215','0.01','half_even','0.22'),
                 ('7.5','5','half_even','10'), ('0.215',None,'half_even','0.215'),
                 ('0','0.05','half_even','0'), ('0.25','0.0500','half_even','0.25'),
                 ('0.01','5','half_up','0'), ('0.00000000000000000000275','5e-22','half_even','3e-21'),
                 ('10000000000000000000000000000000000000007.5','5','half_even',
                  '10000000000000000000000000000000000000010')]
        for amount, quantum, mode, expected in cases:
            with self.subTest(amount=amount, quantum=quantum, mode=mode):
                p = self.price(amount, quantum, mode)
                accounting.validate_pricing(p)
                result = accounting.calculate(record, p)
                self.assertTrue(result['complete'])
                self.assertEqual(Decimal(result['amount']), Decimal(expected))
                self.assertEqual(Decimal(result['unrounded_amount']), Decimal(amount))
                self.assertEqual(result['calculation_policy_version'], 'decimal-quantum-v2')

    def test_invalid_quantum_is_rejected(self):
        for quantum in ('0', '-0.01', 'NaN', 'Infinity', True, False, 1, 0.05, [], {}, '1e-1001'):
            with self.subTest(quantum=quantum):
                with self.assertRaises(ValueError):
                    accounting.validate_pricing(self.price('0.215', quantum))

    def test_midpoint_neighbors_do_not_double_round(self):
        record = self.record()
        for tail, expected in (('4' + '9'*990, '0.20'), ('5' + '0'*990, '0.20'),
                               ('5' + '0'*989 + '1', '0.25')):
            amount = '0.22' + tail
            result = accounting.calculate(record, self.price(amount, '0.05'))
            self.assertTrue(result['complete'])
            self.assertEqual(Decimal(result['amount']), Decimal(expected))

    def test_rounding_is_exact_under_small_context_and_rejects_lost_precision(self):
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(accounting.round_amount(Decimal('0.2250000000000000000000001'),
                                                    Decimal('0.05'), 'half_even'), Decimal('0.25'))
            self.assertEqual(accounting.round_amount(Decimal('0.2249999999999999999999999'),
                                                    Decimal('0.05'), 'half_even'), Decimal('0.20'))
        p = self.price('1', '0.05')
        p['prices'][0]['components'][0]['unit_scale'] = '3'
        with self.assertRaisesRegex(ValueError, 'precision'):
            accounting.calculate(self.record(), p)

    def test_report_rounds_each_request_then_sums_and_preserves_raw_paths(self):
        self.add([costs.call(), costs.call('r2', retry_of='r1', retry_index=1)])
        p = self.price('0.225', '0.05')
        output = self.base / 'new valuation'
        source_before = self.snapshot()
        reports.report(self.root, output, p, revaluation=True)
        # v2 may materialize the same raw evidence generation; committed source reports/pricing remain untouched.
        for name, digest in source_before.items():
            self.assertEqual(files.sha(self.root / name), digest)
        cost = files.read(output / 'cost.json')
        self.assertEqual(cost['calculation_policy_version'], 'decimal-quantum-v2')
        self.assertEqual(Decimal(cost['paths']['public_list_calculated']['currencies']['TEST']['total']), Decimal('0.40'))
        self.assertEqual(Decimal(cost['groups']['task_id']['task_1']['costs']['public_list_calculated']['currencies']['TEST']['total']), Decimal('0.40'))
        self.assertEqual(Decimal(cost['paths']['provider_reported']['currencies']['TEST']['total']), Decimal('0.6'))
        self.assertFalse(cost['complete'])
        rows = list(csv.DictReader(io.StringIO((output / 'calls.csv').read_text())))
        self.assertEqual(len(rows), 2)
        for row in rows:
            calculated = json.loads(row['public_list_calculated'])
            self.assertEqual(Decimal(calculated['amount']), Decimal('0.20'))
            self.assertEqual(calculated['unrounded_amount'], '0.225')
        self.add([costs.call(), costs.call('r2', retry_of='r1', retry_index=1)])
        again = self.base / 'second valuation'
        reports.report(self.root, again, p, revaluation=True)
        self.assertEqual(files.read(again / 'cost.json')['paths'], cost['paths'])

    def test_v1_revalue_keeps_old_report_and_source_bytes(self):
        self.v1_fixture()
        before = self.snapshot()
        price = self.base / 'new-price.json'
        files.write(price, self.price('0.215', '0.05'))
        output = self.base / 'new report'
        self.assertEqual(cli.main(['cost','revalue','--experiment',str(self.root),
                                   '--pricing',str(price),'--output',str(output)]), 0)
        self.assertEqual(self.snapshot(), before)
        cost = files.read(output / 'cost.json')
        self.assertEqual(Decimal(cost['paths']['public_list_calculated']['currencies']['TEST']['total']), Decimal('0.20'))
        self.assertEqual(cost['calculation_policy_version'], 'decimal-quantum-v2')


class ProvenanceTests(unittest.TestCase):
    def test_latest_hashes_and_retained_history_are_both_checked(self):
        members = {p.relative_to(PROJECT).as_posix(): p.read_bytes() for p in (PROJECT / 'provenance').glob('*.json')}
        latest = verify_release.effective_provenance(members)
        self.assertEqual(latest['tools/experiment_config.py'], files.sha(PROJECT / 'tools/experiment_config.py'))
        broken = dict(members)
        broken['provenance/bugfix-baseline.json'] += b'\n'
        with self.assertRaisesRegex(ValueError, 'retained provenance'):
            verify_release.effective_provenance(broken)
        newest = json.loads(members['provenance/config-rounding-baseline.json'])
        entry = next(e for e in newest['files'] if e['destination'] == 'tools/experiment_config.py')
        entry['source_sha256'] = '0' * 64
        broken = dict(members, **{'provenance/config-rounding-baseline.json': json.dumps(newest).encode()})
        with self.assertRaisesRegex(ValueError, 'source mismatch'):
            verify_release.effective_provenance(broken)


if __name__ == '__main__':
    unittest.main()
