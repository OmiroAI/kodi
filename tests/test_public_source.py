import ipaddress
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.check_public_source import scan,private_patterns
from scripts.export_public import export
from scripts.qa_remote import profiles


class PrivacyTests(unittest.TestCase):
    def test_private_filename_is_redacted(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'private-widget.md').write_text('No identifying content')
            result=scan(d,['private-widget.md'],['private-widget'])
        self.assertEqual(result,[('<private path>',0,'operator-private path identifier')])
    def test_detects_private_data_without_echoing_values(self):
        address=str(ipaddress.ip_address(0x0A000001))
        domain='node.'+'ts'+'.net';home='/home/'+'person'+'/project'
        token='gh'+'p_'+'x'*32
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'notes.md';p.write_text('\n'.join((address,domain,home,token,'private-widget')))
            result=scan(d,['notes.md'],['private-widget'])
        reasons={r[2] for r in result}
        self.assertEqual(len(reasons),5)
        self.assertNotIn(address,str(result));self.assertNotIn(domain,str(result));self.assertNotIn(token,str(result))

    def test_documentation_and_loopback_addresses_are_portable(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'README.md').write_text('Example: 192.0.2.10 and 198.51.100.20; bind 0.0.0.0 or 127.0.0.1')
            self.assertEqual(scan(d,['README.md']),[])

    def test_credential_filename_is_rejected_without_reading_contents(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'private.key').write_text('synthetic fixture')
            with patch('pathlib.Path.read_bytes',side_effect=AssertionError('must not open')):
                result=scan(d,['private.key'])
        self.assertEqual(result[0][2],'credential, private state or archive file')

    def test_export_has_no_history_private_deleted_files_or_host_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'source';root.mkdir();out=Path(d)/'public'
            def git(*args):
                subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid',*args],cwd=root,check=True,capture_output=True)
            git('init','-b','main')
            private='private-node.'+'internal'
            (root/'old-notes.md').write_text(private);git('add','.');git('commit','-m','private baseline')
            (root/'old-notes.md').unlink();(root/'README.md').write_text('Generic public coordinator')
            git('add','-A');git('commit','-m','public snapshot')
            archive=export(root,out)
            self.assertFalse((out/'.git').exists());self.assertFalse((out/'old-notes.md').exists())
            with tarfile.open(archive) as tar:
                self.assertEqual(tar.getnames(),['kodi/README.md'])
                entry=tar.getmembers()[0];self.assertEqual((entry.uid,entry.gid,entry.uname,entry.gname,entry.mtime),(0,0,'','',0))
                self.assertNotIn(private,tar.extractfile(entry).read().decode())
            with self.assertRaises(ValueError):export(root,out)
            with self.assertRaises(ValueError):export(root,root/'nested-export')

    def test_extra_qa_profiles_remain_external_operator_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'profiles.json'
            p.write_text(json.dumps({'application':{'command':'python -m unittest discover','image':'application-qa:reviewed'}}))
            self.assertIn('application',profiles(p));self.assertIn('system',profiles(p))
            p.write_text(json.dumps({'application':{'command':'true','image':'image --privileged'}}))
            with self.assertRaises(ValueError):profiles(p)

    def test_private_pattern_configuration_is_not_embedded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'patterns.json';p.write_text('["fictional-private-project"]')
            self.assertEqual(private_patterns(p),['fictional-private-project'])
            p.write_text('{}')
            with self.assertRaises(ValueError):private_patterns(p)
