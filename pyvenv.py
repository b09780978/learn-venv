# -*- encoding: utf-8 -*-
import os
import sys
from pathlib import Path, PurePath
from subprocess import Popen, PIPE, check_output
from threading import Thread
from urllib.request import urlretrieve
from urllib.parse import urlparse
import re
import hashlib
import base64
import argparse
import shutil
import venv

APP_NAME = Path('pyvenv') / '.virtualenv'
APP_NAME = str(APP_NAME)

def expanduser(path):
    p = Path(path).expanduser()
    p = str(p)
    if path.startswith("~/") and p.startswith("//"):
        p = p[1:]
    return p

def _get_win_folder_with_ctypes(csidl_name):
    import ctypes
    csidl_const = {
        "CSIDL_APPDATA": 26,
        "CSIDL_COMMON_APPDATA": 35,
        "CSIDL_LOCAL_APPDATA": 28,
    }[csidl_name]

    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl_const, None, 0, buf)

    # Downgrade to short path name if have highbit chars. See
    # <http://bugs.activestate.com/show_bug.cgi?id=85099>.
    has_high_char = False
    for c in buf:
        if ord(c) > 255:
            has_high_char = True
            break
    if has_high_char:
        buf2 = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(buf.value, buf2, 1024):
            buf = buf2

    return buf.value

def _get_win_folder_from_registry(csidl_name):
    import _winreg

    shell_folder_name = {
        "CSIDL_APPDATA": "AppData",
        "CSIDL_COMMON_APPDATA": "Common AppData",
        "CSIDL_LOCAL_APPDATA": "Local AppData",
    }[csidl_name]

    key = _winreg.OpenKey(
        _winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
    )
    directory, _type = _winreg.QueryValueEx(key, shell_folder_name)
    return directory

def user_cache_dir(dir_name, roaming=False):
    if os.name == 'nt':
        try:
            import ctypes
            _get_win_folder = _get_win_folder_with_ctypes
        except ImportError:
            _get_win_folder = _get_win_folder_from_registry

        path = os.path.normpath(_get_win_folder("CSIDL_LOCAL_APPDATA"))
        path = os.path.join(path, dir_name, "Cache")
    elif sys.platform == "darwin":
        path = expanduser("~/Library/Caches")
        path = os.path.join(path, dir_name)
    else:
        path = os.getenv("XDG_CACHE_HOME", expanduser("~/.cache"))
        path = os.path.join(path, dir_name)
    return path

def get_venv_name():
    location = str(Path.cwd())
    current_dir = PurePath(location).parts[-1]
    location = re.sub(r'[ $`!*@"\\\r\n\t]', "_", location)[0:42]
    h = hashlib.sha256(location.encode('utf-8')).digest()[:6]
    ec = base64.urlsafe_b64encode(h).decode()
    return '{}-{}'.format(current_dir, ec[:8])

class VenvBuilder(venv.EnvBuilder):
    def __init__(self, system_site_packages=False, clear=False, symlinks=False, upgrade=False, with_pip=True, prompt=None, venv_name=None, progress=None, verbose=False, with_setuptools=True):
        self._install_pip = with_pip
        self._install_setuptools = with_setuptools
        self.progress = progress
        self.verbose = verbose
        self.venv_name = venv_name
        super(VenvBuilder, self).__init__(system_site_packages, clear, symlinks, upgrade, with_pip, prompt=self.venv_name)
        sys.stderr.write('Create virtual environment: {}\n'.format(self.venv_name))
        sys.stderr.flush()
        self.create(self.venv_name)

    def post_setup(self, context):
        # install setuptools and pip
        os.environ['VIRTUAL_ENV'] = context.env_dir
        if self._install_setuptools:
            self.install_setuptools(context)
        if self._install_pip:
            self.install_pip(context)

    def reader(self, stream, context):
        # get subprocess stream
        progress = self.progress
        while True:
            s = stream.readline()
            if not s:
                break
            if progress is not None:
                progress(s, context)
            else:
                if not self.verbose:
                    sys.stderr.write('.')
                else:
                    sys.stderr.write(s.decode('utf-8'))
                sys.stderr.flush()
        stream.close()

    def install_script(self, context, name, url):
        _, _, path, _, _, _ = urlparse(url)
        package = os.path.split(path)[-1]
        bin_path = context.bin_path
        dist_path = os.path.join(bin_path, package)

        # download package
        urlretrieve(url, dist_path)
        progress = self.progress

        term = '\n' if self.verbose else ''

        if progress is not None:
            progress('Installing {} ...{}'.format(name, term))
        else:
            sys.stderr.write('Installing {} ...{}'.format(name, term))
            sys.stderr.flush()

        setup_cmds = [ context.env_exe, package ]
        p = Popen(setup_cmds, stdout=PIPE, stderr=PIPE, cwd=bin_path)
        t1 = Thread(target=self.reader, args=(p.stdout, 'stdout'))
        t1.start()
        t2 = Thread(target=self.reader, args=(p.stderr, 'stderr'))
        t2.start()
        p.wait()
        t1.join()
        t2.join()

        if progress is not None:
            progress('done.', 'main')
        else:
            sys.stderr.write('done.\n')

        # clean up
        os.unlink(dist_path)

    def install_setuptools(self, context):
        url = 'https://bitbucket.org/pypa/setuptools/downloads/ez_setup.py'
        self.install_script(context, 'setuptools', url)
        pred = lambda o: o.startswith('setuptools-') and o.endswith('.tar.gz')
        files = filter(pred, os.listdir(context.bin_path))
        for f in files:
            f = os.path.join(context.bin_path, f)
            os.unlink(f)

    def install_pip(self, context):
        url = 'https://bootstrap.pypa.io/get-pip.py'
        self.install_script(context, 'pip', url)

def main():
    parser = argparse.ArgumentParser(prog=__name__,
                                     description='a lightweight virtual environment manager.')
    parser.add_argument('--setuptools', default=True, 
                        action='store_true', dest='with_setuptools',
                        help='Install setuptools in virtual environment.')
    parser.add_argument('--pip', default=True,
                        action='store_true', dest='with_pip',
                        help='Install pip in virtual environment.')
    parser.add_argument('--system_site_packages', default=False,
                        action='store_true', dest='system_site_packages',
                        help='Give access to system_site_packages.')

    use_symlinks = False if os.name == 'nt' else True
    parser.add_argument('--symlinks', default=use_symlinks,
                        action='store_true', dest='symlinks',
                        help='Use symlink rather than copy systm packages.')
    parser.add_argument('--clear', default=False,
                        action='store_true', dest='clear',
                        help='Delete virtual environment contents if folder is exist.')
    parser.add_argument('--upgrade', default=False,
                        action='store_true', dest='upgrade',
                        help='Upgrade virtual environment python.')
    parser.add_argument('--verbose', default=False,
                        action='store_true', dest='verbose',
                        help='Display output from processing.')
    parser.add_argument('--rm', default=False,
                        action='store_true', dest='remove',
                        help='Remove virtual environment.')

    subparsers = parser.add_subparsers(title='Sub Commands',
                                        description='Sub commands for management of virtual environment.',
                                        help='sub commands help')

    group_install = subparsers.add_parser('install')
    group_install.add_argument('install', default=False, action='store_true', help='Install packages in virtual environment.')
    group_install.add_argument('install_cmds', metavar='cmds', nargs='+',
                                help='Commands arguments for pip command.')

    group_list = subparsers.add_parser('list')
    group_list.add_argument('list', default=False, action='store_true', help='List install packages in virtual environment.')
    group_list.add_argument('-r', '--requirement', action='store_true', dest='rfile', help='Output to requirements.txt')

    group_upgrade = subparsers.add_parser('upgrade')
    group_upgrade.add_argument('upgrade', default=False, action='store_true', help='Upgrade packages in virtual environment.')
    group_upgrade.add_argument('upgrade_cmds', default='', nargs='*', help='Commands arguments for upgrade package.')


    group_uninstall = subparsers.add_parser('uninstall')
    group_uninstall.add_argument('uninstall', default=False, action='store_true', help='Uninstall packages in virtual environment.')
    group_uninstall.add_argument('uninstall_cmds', default='', nargs='*', help='Commands arguments for uninstall package')

    group_run = subparsers.add_parser('run')
    group_run.add_argument('run', default=False, action='store_true', help='Run command in virtual environment.')
    group_run.add_argument('run_cmds', default='', nargs='*', help='Commands arguments for run')

    group_shell = subparsers.add_parser('shell')
    group_shell.add_argument('shell', default='False', action='store_true', help='Spawn shell in virtual environment.')

    paraent_dir = user_cache_dir(APP_NAME, roaming=True if os.name == 'nt' else False)
    
    venv_location =  paraent_dir / Path(get_venv_name())
    options = parser.parse_args()

    venv_exists = venv_location.exists()

    if not venv_exists:
        builder = VenvBuilder(system_site_packages=options.system_site_packages,
                                clear=options.clear,
                                symlinks=options.symlinks,
                                upgrade=options.upgrade,
                                with_pip=options.with_pip,
                                with_setuptools=options.with_setuptools,
                                venv_name=venv_location,
                                verbose=options.verbose)
        return 0
    venv_name = venv_location

    if os.name == 'nt':
            venv_python = Path(venv_name) / 'Scripts'
    else:
        venv_python = Path(venv_name) / 'bin'
    venv_python = venv_location / venv_python
    exe = 'python.exe' if os.name == 'nt' else 'python'
    exe = str(venv_python / exe)
    
    if venv_python.exists():
        sys.path.insert(0, venv_python)

    if hasattr(options, 'install') and options.install:    
        params = [ v for v in options.install_cmds if v.startswith('-') ]
        packages = [ p for p in options.install_cmds if not p.startswith('-') ]

        cmd_args = [ exe , '-m', 'pip', 'install' ] + params + packages

        p = Popen(cmd_args)
        p.communicate()
        if p.returncode != 0:
            raise RuntimeError('install {} fail.'.format(','.join(packages)))
    
    elif hasattr(options, 'upgrade') and options.upgrade:
        params = [ v for v in options.upgrade_cmds if v.startswith('-') ]
        packages = [ p for p in options.upgrade_cmds if not p.startswith('-') ]
        
        cmd_args = [ exe , '-m', 'pip', 'install', '-U' ] + params + packages

        p = Popen(cmd_args)
        p.communicate()
        if p.returncode != 0:
            raise RuntimeError('upgrade {} fail.'.format(','.join(packages)))

    elif hasattr(options, 'list') and options.list:
        cmd_args = [ exe, '-m', 'pip', 'freeze']
        output = check_output(cmd_args, shell=True)
        if hasattr(options, 'rfile') and options.rfile:
            with open('requirements.txt', 'w') as f:
                f.write(output.decode('utf-8').replace('\r\n', '\n'))
        else:
            sys.stderr.write(output.decode('utf-8'))
            sys.stderr.flush()

    elif hasattr(options, 'uninstall') and options.uninstall:
        params = [ v for v in options.uninstall_cmds if v.startswith('-') ]
        packages = [ p for p in options.uninstall_cmds if not p.startswith('-') ]
        
        cmd_args = [ exe, '-m', 'pip', 'uninstall', ] + params + packages
        p = Popen(cmd_args)
        p.communicate()
        return p.returncode
        
    elif options.clear and options.upgrade:
        raise ValueError('Cannot --upgrade and --clear at the same time.')

    elif hasattr(options, 'run') and options.run:
        cmd_args = [ v for v in options.run_cmds if len(v)!=0 ]
        env = os.environ.copy()
        env['PATH'] = str(venv_python) + ';' + env['PATH']
        env['VIRTUAL_ENV'] = str(venv_python)
        if cmd_args[0] == 'pip' or cmd_args[0] == 'pip.exe':
            cmd_args[0] = str(venv_python / 'pip')

        if cmd_args[0] == 'python' or cmd_args[0] == 'python.exe':
            cmd_args[0] = str(venv_python / 'python')

        p = Popen(cmd_args, env=env)
        p.communicate()
        return p.returncode

    elif hasattr(options, 'shell') and options.shell:
        if os.environ.get('PYVENV', None) is not None:
            sys.stderr.write('Cannot use nested virtual environment shell.')
            sys.stderr.flush()
            return 1

        env = os.environ.copy()
        env['PATH'] = str(venv_python) + ';' + env['PATH']
        env['VIRTUAL_ENV'] = str(venv_python)
        if os.name == 'nt':
            env['PROMPT'] = str(venv_python) + ' (pyvenv):'
            shell = env.get('COMSPEC', None)
        else:
            env['PS1'] = str(venv_python) + ' (pyvenv):'
            shell = env.get('SHELL', None)

        if shell is None:
            raise RuntimeError('Shell not found.')
        env['PYVENV'] = '1'
        p = Popen([shell,], env=env)
        p.communicate()
        return p.returncode

    elif options.remove:
        if Path(venv_name).exists():
            shutil.rmtree(str(venv_location))
            sys.stderr.write('Remove virtual environment {}.\n'.format(venv_name))
            sys.stderr.flush()
        else:
            sys.stderr.write('Virtual environment not exists.\n')
            sys.stderr.flush()
        return 0

if __name__ == '__main__':
    error_code = 1
    try:
        error_code = main()
    except Exception as e:
        print('Error: {}'.format(str(e)))
    sys.exit(error_code)