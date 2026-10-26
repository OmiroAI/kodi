import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts import qa_remote


class QAArchiveTests(unittest.TestCase):
    def archive(self, root, entries):
        path=root/'source.tar'
        with tarfile.open(path,'w') as archive:
            for name, kind, value in entries:
                info=tarfile.TarInfo(name)
                if kind=='file':
                    data=value.encode(); info.size=len(data)
                    archive.addfile(info,io.BytesIO(data))
                else:
                    info.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE
                    info.linkname=value; archive.addfile(info)
        return path

    def extract(self, archive, destination, **profile):
        extract=getattr(qa_remote,'extract_archive',None)
        self.assertTrue(callable(extract),'A bounded source extraction boundary is required')
        extract(archive,destination,profile)

    def test_default_still_rejects_internal_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            archive=self.archive(root,[('config.txt','file','valid'),('alias','symlink','config.txt')])
            with self.assertRaises(ValueError): self.extract(archive,root/'out')

    def test_trusted_profile_materializes_internal_file_and_directory_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            archive=self.archive(root,[('settings/default.yaml','file','value'),
                ('profiles.yaml','symlink','settings/default.yaml'),
                ('.claude/skills','symlink','../settings')])
            self.extract(archive,root/'out',materialize_internal_links=True)
            self.assertEqual((root/'out/profiles.yaml').read_text(),'value')
            self.assertEqual((root/'out/.claude/skills/default.yaml').read_text(),'value')
            self.assertFalse(any(p.is_symlink() for p in (root/'out').rglob('*')))

    def test_unsafe_archives_fail_before_writing_any_source(self):
        cases=[('bad','symlink','../../outside'),('bad','symlink','/etc/passwd'),
               ('../bad','file','escape'),('alias/file','file','collision'),
               ('hard','hardlink','safe')]
        for entry in cases:
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                archive=self.archive(root,[('safe','file','safe'),('alias','symlink','safe'),entry])
                with self.assertRaises(ValueError):
                    self.extract(archive,root/'out',materialize_internal_links=True)
                self.assertFalse((root/'out/safe').exists())

    def test_cycles_and_dangling_links_are_rejected(self):
        for links in ([('a','symlink','b'),('b','symlink','a')],
                      [('a','symlink','missing')], [('a','symlink','.')]):
            with self.subTest(links=links), tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                archive=self.archive(root,links)
                with self.assertRaises(ValueError):
                    self.extract(archive,root/'out',materialize_internal_links=True)

    def test_expansion_limit_includes_alias_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            archive=self.archive(root,[('original','file','12345'),('copy','symlink','original')])
            with self.assertRaises(ValueError):
                self.extract(archive,root/'out',materialize_internal_links=True,expanded_max_bytes=8)
            self.assertFalse((root/'out/original').exists())

    def test_private_profile_bounds_cannot_be_supplied_as_strings_or_booleans(self):
        for options in ({'archive_max_bytes':True},{'archive_max_bytes':1024**3},
                        {'expanded_max_bytes':-1},{'materialize_internal_links':'true'}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'profiles.json'
                path.write_text(json.dumps({'large':{'image':'qa:large','command':'test',**options}}))
                with self.assertRaises(ValueError): qa_remote.profiles(path)

    def test_sender_rejects_invalid_operator_limit_and_removes_archive(self):
        from unittest.mock import patch
        from kodi.runner import qa
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); checkout=root/'checkout';checkout.mkdir()
            with patch('kodi.runner.subprocess.run') as execute:
                with self.assertRaisesRegex(ValueError,'operator QA archive bound'):
                    qa({'qa_archive_max_bytes':True},checkout,'a'*40,'large')
            self.assertEqual(execute.call_count,1)
            self.assertFalse((root/('a'*40+'.tar')).exists())

    def test_private_memory_budget_is_bounded_and_not_a_container_option(self):
        for budget in (True, '4g', 0, 8193):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'profiles.json'
                path.write_text(json.dumps({'large':{'image':'qa:large','command':'test','memory_mb':budget}}))
                with self.assertRaises(ValueError): qa_remote.profiles(path)

    def test_memory_override_preserves_network_credentials_and_runtime_boundary(self):
        import base64
        import contextlib
        import subprocess
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'incoming').mkdir()
            archive=self.archive(root,[('source.py','file','pass')])
            archive.rename(root/'incoming'/('a'*40+'.tar'))
            payload=base64.b64encode(json.dumps({'sha':'a'*40,'suite':'large'}).encode()).decode()
            result=subprocess.CompletedProcess([],0,stdout='passed',stderr='')
            with patch.object(qa_remote,'ROOT',root), \
                 patch.object(qa_remote,'profiles',return_value={'large':{'image':'qa:large','command':'test','memory_mb':4096}}), \
                 patch('sys.argv',['qa_remote',payload]), \
                 patch.object(qa_remote.subprocess,'run',return_value=result) as execute, \
                 contextlib.redirect_stdout(io.StringIO()):
                qa_remote.main()
            arguments=execute.call_args_list[0].args[0]
            self.assertEqual(arguments[arguments.index('--memory')+1],'4096m')
            self.assertEqual(arguments[arguments.index('--memory-swap')+1],'4096m')
            self.assertEqual(arguments[arguments.index('--network')+1],'none')
            self.assertEqual(arguments[arguments.index('--cpus')+1],'1')
            self.assertIn('--read-only',arguments)
            self.assertEqual(execute.call_args_list[0].kwargs['timeout'],600)
