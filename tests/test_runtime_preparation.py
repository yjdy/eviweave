"""P2 preparation failure detection; all fixtures remain in project .audit."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'scripts'))
from runtime_support import archive_tree, copy_verified, differences, extract_checked, inventory, local_path
from dataspace_baselines.doctor import DoctorCheck, _inventory_check, inventory as doctor_inventory, run_doctor
from dataspace_baselines.config import load_harness_experiment_config
from dataspace_baselines.cli import main


class RuntimePreparationTests(unittest.TestCase):
    def setUp(self):
        (PROJECT/'.audit').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT/'.audit', prefix='p2 test ')
        self.root = Path(self.tmp.name)
        self.source = self.root/'source'; self.source.mkdir()
        (self.source/'bin').mkdir(); (self.source/'bin/tool').write_bytes(b'original bytes')
        (self.source/'bin/tool').chmod(0o755)
        (self.source/'absolute-link').symlink_to('/bin/tool')
        os.link(self.source/'bin/tool', self.source/'bin/hardlink')
        self.expected = inventory(self.source)

    def tearDown(self): self.tmp.cleanup()

    def test_inventory_and_doctor_detect_content_mode_links_and_extras(self):
        self.assertEqual(self.expected, doctor_inventory(self.source))
        manifest = self.root/'files.json'; manifest.write_text(json.dumps(self.expected))
        self.assertTrue(_inventory_check('tree', self.source, manifest).ok)
        (self.source/'bin/tool').write_bytes(b'corruption')
        (self.source/'bin/tool').chmod(0o644)
        (self.source/'extra').write_text('unexpected')
        changes = differences(self.expected, inventory(self.source))
        self.assertEqual({x['path'] for x in changes}, {'bin/tool','bin/hardlink','extra'})
        self.assertFalse(_inventory_check('tree', self.source, manifest).ok)

    @unittest.skipIf(os.getuid() == 0, 'root bypasses directory read permissions')
    def test_unreadable_directory_fails_instead_of_skipping_entries(self):
        restricted=self.source/'restricted';restricted.mkdir();(restricted/'hidden').write_text('must not skip')
        restricted.chmod(0)
        try:
            with self.assertRaises(PermissionError):inventory(self.source)
            with self.assertRaises(PermissionError):doctor_inventory(self.source)
        finally:
            restricted.chmod(0o755)

    def test_copy_reuse_and_no_shared_source_inode(self):
        target=self.root/'target'
        self.assertEqual(copy_verified(self.source,target,self.root/'tmp',self.expected),'imported')
        self.assertEqual(copy_verified(self.source,target,self.root/'tmp',self.expected),'reused')
        self.assertNotEqual((self.source/'bin/tool').stat().st_ino,(target/'bin/tool').stat().st_ino)
        self.assertEqual((target/'bin/tool').stat().st_ino,(target/'bin/hardlink').stat().st_ino)
        (target/'bin/tool').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'refusing replacement'):
            copy_verified(self.source,target,self.root/'tmp',self.expected)
        self.assertEqual((self.source/'bin/tool').read_bytes(),b'original bytes')

    def test_portable_archive_round_trip(self):
        archive=self.root/'portable.tar'; archive_tree(self.source,archive)
        target=self.root/'unpacked'; extract_checked(archive,target)
        self.assertEqual(inventory(target),self.expected)

    def test_archive_refuses_traversal_and_link_ancestors_before_writes(self):
        for index, members in enumerate([
            [('../escape','file','')],
            [('absolute','link','/outside'),('absolute/file','file','')],
            [('hardlink','hardlink','../escape')],
            [('same','file',''),('same','file','')],
        ]):
            archive=self.root/f'bad-{index}.tar'
            with tarfile.open(archive,'w') as handle:
                for name,kind,link in members:
                    entry=tarfile.TarInfo(name);entry.linkname=link
                    if kind=='link':entry.type=tarfile.SYMTYPE
                    elif kind=='hardlink':entry.type=tarfile.LNKTYPE
                    handle.addfile(entry)
            target=self.root/f'rejected-{index}'
            with self.assertRaises(ValueError):extract_checked(archive,target)
            self.assertFalse(target.exists())

    def test_output_path_rejects_outside_and_symlink_escape(self):
        (self.root/'escape').symlink_to(PROJECT.parent, target_is_directory=True)
        for path in (PROJECT.parent/'unwanted',self.root/'escape/unwanted',PROJECT):
            with self.assertRaises(ValueError):local_path(path)

    def test_offline_skips_only_credential_and_still_fails_runtime_errors(self):
        config=load_harness_experiment_config(PROJECT/'configs/codex.example.yaml')
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,{},clear=True))
            for name in ('_command_check','_image_check','_workbench_rootfs_check','_workbench_tools_check','_mount_check','_jailed_version','_inventory_check'):
                stack.enter_context(patch('dataspace_baselines.doctor.'+name, return_value=DoctorCheck(name,True,'codex-cli 0.145.0')))
            default=run_doctor(config,temp_dir=self.root/'tmp')
            offline=run_doctor(config,offline=True,temp_dir=self.root/'tmp')
            self.assertFalse(next(c for c in default if c.name=='inference credential').ok)
            skipped=next(c for c in offline if c.name=='inference credential')
            self.assertTrue(skipped.skipped)
            self.assertTrue(all(c.ok for c in offline))
            with patch('dataspace_baselines.doctor._mount_check',return_value=DoctorCheck('sandbox mounts',False,'kernel refusal')):
                failed=run_doctor(config,offline=True,temp_dir=self.root/'tmp')
            self.assertTrue(any(not c.ok for c in failed))
            output=io.StringIO()
            with contextlib.redirect_stdout(output), patch('dataspace_baselines.cli.run_doctor',return_value=offline):
                self.assertEqual(main(['doctor','--offline','--config',str(PROJECT/'configs/codex.example.yaml')]),0)
            self.assertIn('[skip] inference credential',output.getvalue())


if __name__ == '__main__': unittest.main()
