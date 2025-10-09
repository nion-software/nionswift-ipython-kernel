"""Replicate parts of jupyter's paths library. Let's hope they don't change their paths very often, otherwise this code
will be out of date at some point and clients will not be able to find our kernel anymore.
Alternatively we could add jupyter_core as a dependency and use their paths module directly.

The copyright for the original code lies with the Jupyter Development Team with the following coyright notice:

BSD 3-Clause License

- Copyright (c) 2015-, Jupyter Development Team

All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS 'AS IS'
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import typing
import sys
import os
import tempfile


# Capitalize Jupyter in paths only on Windows and MacOS (when not in Homebrew)
if sys.platform == 'win32' or (
    sys.platform == 'darwin' and not sys.prefix.startswith('/opt/homebrew')
):
    APPNAME = 'Jupyter'
else:
    APPNAME = 'jupyter'


_dtemps: dict[str, str] = {}


def _mkdtemp_once(name: str) -> str:
    """Make or reuse a temporary directory.

    If this is called with the same name in the same process, it will return
    the same directory.
    """
    try:
        return _dtemps[name]
    except KeyError:
        d = _dtemps[name] = tempfile.mkdtemp(prefix=name + "-")
        return d



def envset(name: str, default: typing.Optional[bool] = False) -> typing.Optional[bool]:
    """Return the boolean value of a given environment variable.

    An environment variable is considered set if it is assigned to a value
    other than 'no', 'n', 'false', 'off', '0', or '0.0' (case insensitive)

    If the environment variable is not defined, the default value is returned.
    """
    if name not in os.environ:
        return default

    return os.environ[name].lower() not in ['no', 'n', 'false', 'off', '0', '0.0']


def get_home_dir() -> str:
    """Get the real path of the home directory"""
    homedir = os.path.expanduser('~')
    # Next line will make things work even when /home/ is a symlink to
    # /usr/home as it is on FreeBSD, for example
    return os.path.realpath(homedir)


def use_platform_dirs() -> bool:
    """Determine if platformdirs should be used for system-specific paths.

    We plan for this to default to False in jupyter_core version 5 and to True
    in jupyter_core version 6.
    """
    do_use_platform_dirs = typing.cast(bool, envset('JUPYTER_PLATFORM_DIRS', False))
    if do_use_platform_dirs:
        try:
            import platformdirs
        except ImportError as e:
            raise RuntimeError('"platformdirs" must be installed when the environment variable "JUPYTER_PLATFORM_DIRS" is set to "True".') from e
    return do_use_platform_dirs


def jupyter_config_dir() -> str:
    """Get the Jupyter config directory for this platform and user.

    Returns JUPYTER_CONFIG_DIR if defined, otherwise the appropriate
    directory for the platform.
    """

    env = os.environ
    if env.get('JUPYTER_NO_CONFIG'):
        return _mkdtemp_once('jupyter-clean-cfg')

    if env.get('JUPYTER_CONFIG_DIR'):
        return env['JUPYTER_CONFIG_DIR']

    if use_platform_dirs():
        import platformdirs
        return str(platformdirs.user_config_dir(APPNAME, appauthor=False))

    home_dir = get_home_dir()
    return os.path.join(home_dir, '.jupyter')



def jupyter_data_dir() -> str:
    """Get the config directory for Jupyter data files for this platform and user.

    These are non-transient, non-configuration files.

    Returns JUPYTER_DATA_DIR if defined, else a platform-appropriate path.
    """
    env = os.environ

    if env.get('JUPYTER_DATA_DIR'):
        return env['JUPYTER_DATA_DIR']

    if use_platform_dirs():
        import platformdirs
        return str(platformdirs.user_data_dir(APPNAME, appauthor=False))

    home = get_home_dir()

    if sys.platform == 'darwin':
        return os.path.join(home, 'library', 'Jupyter')
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA', None)
        if appdata:
            return os.path.realpath(os.path.join(appdata, 'jupyter'))
        return os.path.join(jupyter_config_dir(), 'data')
    # Linux, non-OS X Unix, AIX, etc.
    xdg = env.get('XDG_DATA_HOME', None)
    if not xdg:
        xdg = os.path.join(home, '.local', 'share')
    return os.path.join(xdg, 'jupyter')


def jupyter_runtime_dir() -> str:
    """Return the runtime dir for transient jupyter files.

    Returns JUPYTER_RUNTIME_DIR if defined.

    The default is now (data_dir)/runtime on all platforms;
    we no longer use XDG_RUNTIME_DIR after various problems.
    """
    env = os.environ

    if env.get('JUPYTER_RUNTIME_DIR'):
        return env['JUPYTER_RUNTIME_DIR']

    return os.path.join(jupyter_data_dir(), 'runtime')
