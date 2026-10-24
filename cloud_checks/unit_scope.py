"""Run the complete declared package scope on CPython 3.10, with explicit exclusions.

The seven independent/exact-host cases require the documented 3.12/3.13 host
profile workflow. They remain mandatory on both supported runtime interpreters.
No unittest skip or arbitrary failure-based exclusion is used.
"""
import argparse,importlib.util,json,sys,unittest
from pathlib import Path
EXCLUDED={
 'test_public_preparation.PublicPreparationTests.test_bad_old_profile_and_runtime_missing_are_rejected',
 'test_public_preparation.PublicPreparationTests.test_inventory_rejects_traversal_types_duplicates_and_unknown_fields',
 'test_public_preparation.PublicPreparationTests.test_missing_namespace_fails_without_fallback',
 'test_public_preparation.PublicPreparationTests.test_profile_rejects_extra_fields_and_modified_dependency',
 'test_public_preparation.PublicPreparationTests.test_profile_selection_is_explicit_and_old_default_survives',
 'test_public_preparation.PublicPreparationTests.test_selected_profile_is_returned_only_when_requested',
 'test_public_preparation.PublicPreparationTests.test_valid_independent_profile_cannot_remove_checks_or_add_hooks',
}
p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args();project=args.project.resolve();sys.path.insert(0,str(project/'tools'))
spec=importlib.util.spec_from_file_location('frozen_unit_worker',project/'tools/unit_worker.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
def flatten(s):
 for t in s:
  if isinstance(t,unittest.TestSuite):yield from flatten(t)
  else:yield t
all_cases=list(flatten(unittest.defaultTestLoader.discover(str(project/'tests'),pattern='test_*.py')))
assert len(all_cases)==232
out_of_scope=[]
if sys.version_info[:2]==(3,10):
 assert EXCLUDED<={t.id() for t in all_cases}
 out_of_scope=[{'case':n,'reason':'independent/exact host profiles are explicitly restricted to CPython3.12/3.13; mandatory on both runtime-supported matrix jobs'} for n in sorted(EXCLUDED)]
 selected=[t for t in all_cases if t.id() not in EXCLUDED]
 import host_profile,environment_lock
 for f in [host_profile.dependency_profile,environment_lock.profile]:
  try:f()
  except ValueError:pass
  else:raise AssertionError('unsupported CPython3.10 host profile must be rejected')
else:selected=all_cases
result=unittest.TextTestRunner(verbosity=2,resultclass=worker.RecordingResult).run(unittest.TestSuite(selected));args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps({'status':'PASS' if result.wasSuccessful() and not result.skipped else 'FAIL','tests':result.testsRun,'discovered':len(all_cases),'not_applicable':out_of_scope,'unsupported_profile_rejection_checks':2 if out_of_scope else 0,'cases':result.cases},ensure_ascii=False,indent=2)+'\n')
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
