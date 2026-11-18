import ctypes
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from omnihand_usb import damiao_runtime as runtime
from omnihand_usb.damiao import _NativeDamiaoHand
from omnihand_usb.doctor import diagnose, linux_usb_devices

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location('project_script_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeSelectionTests(unittest.TestCase):
    def test_platform_is_strict_and_architectures_never_share_native_paths(self):
        self.assertEqual(runtime.platform_key('linux', 'x86_64'), 'linux-x64')
        self.assertEqual(runtime.platform_key('linux', 'aarch64'), 'linux-arm64')
        self.assertEqual(runtime.platform_key('win32', 'AMD64'), 'windows-x64')
        for system, arch in [('linux','i686'), ('linux','riscv64'), ('win32','arm64'), ('darwin','arm64')]:
            with self.subTest(system=system,arch=arch),self.assertRaises(RuntimeError):runtime.platform_key(system,arch)
        self.assertNotEqual(runtime.runtime_folder('.', 'linux-x64'),runtime.runtime_folder('.', 'linux-arm64'))
        self.assertEqual(runtime.runtime_folder('cache','windows-x64'),Path('cache'))

    def test_verified_hash_does_not_allow_the_wrong_elf_architecture(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);folder=base/'linux-x64';folder.mkdir()
            data=bytearray(64);data[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',data,18,183)
            (folder/'libdm_device.so').write_bytes(data)
            spec={'files':(('fixture/libdm_device.so',runtime.blob_hash(data)),),'library':'libdm_device.so','elf_machine':62}
            with patch.dict(runtime.RUNTIMES,{'linux-x64':spec}):
                with self.assertRaisesRegex(RuntimeError,'架构'):runtime.checked_runtime(base,'linux-x64',check_package=False)
                struct.pack_into('<H',data,18,62);(folder/'libdm_device.so').write_bytes(data)
                with self.assertRaisesRegex(RuntimeError,'校验'):runtime.checked_runtime(base,'linux-x64',check_package=False)

    def test_bad_or_missing_python_wrapper_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);(base/'test.dll').write_bytes(b'test')
            spec={'files':(('test.dll',runtime.blob_hash(b'test')),),'library':'test.dll','elf_machine':None}
            with patch.dict(runtime.RUNTIMES,{'fixture':spec}),patch('omnihand_usb.damiao_runtime.runtime_folder',return_value=base):
                with patch('importlib.metadata.version',return_value='2.0.0'),self.assertRaisesRegex(RuntimeError,'1.0.4'):
                    runtime.checked_runtime(base,'fixture')

    def test_missing_cpp_dependency_has_a_specific_ubuntu_remedy(self):
        item=runtime.Runtime('linux-x64',Path('.'),Path('libdm_device.so'))
        with patch('ctypes.CDLL',side_effect=OSError('GLIBCXX_3.4.32 not found')):
            with self.assertRaisesRegex(RuntimeError,'bootstrap_ubuntu.py'):runtime.load_linux_library(item)


class LinuxLoadPathTests(unittest.TestCase):
    def test_linux_connection_does_not_call_windows_loader_and_preserves_context_ownership(self):
        context=Mock();device=context.get_device.return_value
        context.find_devices.return_value=1;device.open.return_value=True
        device.get_version.return_value='dual_app v1.0.0.7'
        device.get_channel_baudrate.return_value=SimpleNamespace(canfd=True,can_baudrate=1000000,canfd_baudrate=5000000,can_sp=.8,canfd_sp=.75)
        wrapper=SimpleNamespace(DmCanContext=Mock(return_value=context),dmcan_channel_can_info=SimpleNamespace,
                                dmcan_device_type=SimpleNamespace(USB2CANFD_DUAL=1))
        context_module=SimpleNamespace(find_backend_dll_path=None)
        item=runtime.Runtime('linux-x64',Path('/vendor/linux-x64'),Path('/vendor/linux-x64/libdm_device.so'))
        hand=_NativeDamiaoHand('.')
        with patch.dict('sys.modules',{'dmcan':wrapper}), \
             patch('omnihand_usb.damiao.checked_runtime',return_value=item), \
             patch('omnihand_usb.damiao.load_linux_library') as loader, \
             patch('omnihand_usb.damiao.check_abi'), \
             patch('omnihand_usb.damiao.importlib',SimpleNamespace(import_module=Mock(side_effect=[context_module,SimpleNamespace()]))), \
             patch('os.add_dll_directory',create=True,side_effect=AssertionError('Windows loader used on Linux')), \
             patch.object(hand,'_query',return_value=b'vendor'), \
             patch.object(hand,'positions',return_value=[2000]*10), \
             patch('omnihand_usb.damiao.protocol.vendor_info',return_value={'firmware_version':'1.2.14'}):
            info=hand.open()
            loader.assert_called_once_with(item)
            self.assertEqual(context_module.find_backend_dll_path(),str(item.library))
            self.assertEqual(info['can_channel'],1)
            device.send_can.assert_not_called()
            hand.close();hand.close()
            context.destroy.assert_called_once();device.close.assert_not_called()


class UbuntuSetupTests(unittest.TestCase):
    def test_installer_rejects_foreign_platform_and_never_overwrites_windows_venv(self):
        module=load_script('bootstrap_ubuntu')
        with self.assertRaises(RuntimeError):module.check_target('win32','AMD64')
        with self.assertRaises(RuntimeError):module.check_target('linux','aarch64')
        module.check_target('linux','x86_64')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'.venv/Scripts').mkdir(parents=True)
            marker=root/'.venv/Scripts/python.exe';marker.write_bytes(b'preserve')
            with patch.object(module,'fetch_micromamba') as fetch:
                with self.assertRaisesRegex(RuntimeError,'not be overwritten'):module.prepare_environment(root)
                fetch.assert_not_called();self.assertEqual(marker.read_bytes(),b'preserve')

    def test_environment_plan_uses_only_the_project_prefix_and_fixed_environment_file(self):
        module=load_script('bootstrap_ubuntu')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def created(command,**kwargs):
                self.assertIn('--no-rc',command);self.assertIn('--strict-channel-priority',command)
                self.assertEqual(command[command.index('--prefix')+1],str(root/'.venv'))
                self.assertEqual(command[command.index('--file')+1],str(root/'environment.ubuntu.yml'))
                (root/'.venv/bin').mkdir(parents=True);(root/'.venv/conda-meta').mkdir();(root/'.venv/bin/python').touch()
            with patch.object(module,'fetch_micromamba',return_value=root/'micromamba'),patch.object(module.subprocess,'run',side_effect=created):
                self.assertEqual(module.prepare_environment(root),root/'.venv/bin/python')

    def test_tampered_micromamba_download_is_never_executed(self):
        module=load_script('bootstrap_ubuntu')
        response=Mock();response.read.side_effect=[b'invalid',b''];response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with tempfile.TemporaryDirectory() as temp,patch.object(module.urllib.request,'urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'SHA-256'):module.fetch_micromamba(Path(temp))

    def test_driver_offline_cache_requires_the_exact_vendor_hash(self):
        module=load_script('setup_damiao')
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'runtime.so';target.write_bytes(b'known')
            with patch.object(module.urllib.request,'urlopen') as network:
                module.fetch_file('known.so',runtime.blob_hash(b'known'),target,offline=True)
                with self.assertRaises(RuntimeError):module.fetch_file('known.so','wrong',target,offline=True)
                self.assertEqual(target.read_bytes(),b'known');network.assert_not_called()

    def test_ubuntu_profile_preserves_the_two_distinct_hand_ids_and_raw_switch(self):
        profile=json.loads((ROOT/'config.ubuntu.example.json').read_text())
        self.assertEqual(profile['transport'],'canfd');self.assertEqual(profile['can_id'],9)
        self.assertEqual(profile['raw_public_hand_id'],1);self.assertIs(profile['raw_tactile_probe_enabled'],True)


class DoctorTests(unittest.TestCase):
    def test_usb_permission_scan_filters_only_the_current_dual_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name,vendor,product in [('dual','34b7','6632'),('other','1234','6632')]:
                entry=root/'sys'/name;entry.mkdir(parents=True)
                for filename,value in [('idVendor',vendor),('idProduct',product),('busnum','1'),('devnum','8')]:
                    (entry/filename).write_text(value)
            with patch('os.access',return_value=False):found=linux_usb_devices(root/'sys',root/'dev')
            self.assertEqual(len(found),1);self.assertFalse(found[0]['read_write_access'])
            self.assertEqual(Path(found[0]['node']),root/'dev/001/008')

    def test_doctor_probes_libraries_without_constructing_a_device(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch('omnihand_usb.doctor.check_native',return_value={'device_opened':False}) as check, \
             patch('omnihand_usb.doctor.importlib',SimpleNamespace(metadata=SimpleNamespace(version=Mock(return_value='1.1.11')),import_module=Mock(return_value=SimpleNamespace(__file__='sdk')))), \
             patch('omnihand_usb.damiao.DamiaoHand') as hand:
            result=diagnose(temp,'canfd')
            self.assertTrue(result['ok']);self.assertFalse(result['device_opened']);self.assertEqual(result['can_frames_sent'],0)
            hand.assert_not_called();check.assert_called_once()


if __name__=='__main__':unittest.main()
