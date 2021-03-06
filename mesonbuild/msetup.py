# Copyright 2016-2018 The Meson development team

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
import sys, stat
import datetime
import os.path
import platform
import cProfile as profile
import argparse

from . import environment, interpreter, mesonlib
from . import build
from . import mlog, coredata
from .mesonlib import MesonException
from .wrap import WrapMode

def add_arguments(parser):
    coredata.register_builtin_arguments(parser)
    parser.add_argument('--cross-file', default=None,
                        help='File describing cross compilation environment.')
    parser.add_argument('-v', '--version', action='version',
                        version=coredata.version)
    parser.add_argument('--profile-self', action='store_true', dest='profile',
                        help=argparse.SUPPRESS)
    parser.add_argument('--fatal-meson-warnings', action='store_true', dest='fatal_warnings',
                        help='Make all Meson warnings fatal')
    parser.add_argument('--reconfigure', action='store_true',
                        help='Set options and reconfigure the project. Useful when new ' +
                             'options have been added to the project and the default value ' +
                             'is not working.')
    parser.add_argument('builddir', nargs='?', default=None)
    parser.add_argument('sourcedir', nargs='?', default=None)

def wrapmodetype(string):
    try:
        return getattr(WrapMode, string)
    except AttributeError:
        msg = ', '.join([t.name.lower() for t in WrapMode])
        msg = 'invalid argument {!r}, use one of {}'.format(string, msg)
        raise argparse.ArgumentTypeError(msg)

class MesonApp:
    def __init__(self, options):
        (self.source_dir, self.build_dir) = self.validate_dirs(options.builddir,
                                                               options.sourcedir,
                                                               options.reconfigure)
        self.options = options

    def has_build_file(self, dirname):
        fname = os.path.join(dirname, environment.build_filename)
        return os.path.exists(fname)

    def validate_core_dirs(self, dir1, dir2):
        if dir1 is None:
            if dir2 is None:
                if not os.path.exists('meson.build') and os.path.exists('../meson.build'):
                    dir2 = '..'
                else:
                    raise MesonException('Must specify at least one directory name.')
            dir1 = os.getcwd()
        if dir2 is None:
            dir2 = os.getcwd()
        ndir1 = os.path.abspath(os.path.realpath(dir1))
        ndir2 = os.path.abspath(os.path.realpath(dir2))
        if not os.path.exists(ndir1):
            os.makedirs(ndir1)
        if not os.path.exists(ndir2):
            os.makedirs(ndir2)
        if not stat.S_ISDIR(os.stat(ndir1).st_mode):
            raise MesonException('%s is not a directory' % dir1)
        if not stat.S_ISDIR(os.stat(ndir2).st_mode):
            raise MesonException('%s is not a directory' % dir2)
        if os.path.samefile(dir1, dir2):
            raise MesonException('Source and build directories must not be the same. Create a pristine build directory.')
        if self.has_build_file(ndir1):
            if self.has_build_file(ndir2):
                raise MesonException('Both directories contain a build file %s.' % environment.build_filename)
            return ndir1, ndir2
        if self.has_build_file(ndir2):
            return ndir2, ndir1
        raise MesonException('Neither directory contains a build file %s.' % environment.build_filename)

    def validate_dirs(self, dir1, dir2, reconfigure):
        (src_dir, build_dir) = self.validate_core_dirs(dir1, dir2)
        priv_dir = os.path.join(build_dir, 'meson-private/coredata.dat')
        if os.path.exists(priv_dir):
            if not reconfigure:
                print('Directory already configured.\n'
                      '\nJust run your build command (e.g. ninja) and Meson will regenerate as necessary.\n'
                      'If ninja fails, run "ninja reconfigure" or "meson --reconfigure"\n'
                      'to force Meson to regenerate.\n'
                      '\nIf build failures persist, manually wipe your build directory to clear any\n'
                      'stored system data.\n'
                      '\nTo change option values, run "meson configure" instead.')
                sys.exit(0)
        else:
            if reconfigure:
                print('Directory does not contain a valid build tree:\n{}'.format(build_dir))
                sys.exit(1)
        return src_dir, build_dir

    def check_pkgconfig_envvar(self, env):
        curvar = os.environ.get('PKG_CONFIG_PATH', '')
        if curvar != env.coredata.pkgconf_envvar:
            mlog.warning('PKG_CONFIG_PATH has changed between invocations from "%s" to "%s".' %
                         (env.coredata.pkgconf_envvar, curvar))
            env.coredata.pkgconf_envvar = curvar

    def generate(self):
        env = environment.Environment(self.source_dir, self.build_dir, self.options)
        mlog.initialize(env.get_log_dir(), self.options.fatal_warnings)
        if self.options.profile:
            mlog.set_timestamp_start(time.monotonic())
        with mesonlib.BuildDirLock(self.build_dir):
            self._generate(env)

    def _generate(self, env):
        mlog.debug('Build started at', datetime.datetime.now().isoformat())
        mlog.debug('Main binary:', sys.executable)
        mlog.debug('Python system:', platform.system())
        mlog.log(mlog.bold('The Meson build system'))
        self.check_pkgconfig_envvar(env)
        mlog.log('Version:', coredata.version)
        mlog.log('Source dir:', mlog.bold(self.source_dir))
        mlog.log('Build dir:', mlog.bold(self.build_dir))
        if env.is_cross_build():
            mlog.log('Build type:', mlog.bold('cross build'))
        else:
            mlog.log('Build type:', mlog.bold('native build'))
        b = build.Build(env)

        intr = interpreter.Interpreter(b)
        if env.is_cross_build():
            mlog.log('Host machine cpu family:', mlog.bold(intr.builtin['host_machine'].cpu_family_method([], {})))
            mlog.log('Host machine cpu:', mlog.bold(intr.builtin['host_machine'].cpu_method([], {})))
            mlog.log('Target machine cpu family:', mlog.bold(intr.builtin['target_machine'].cpu_family_method([], {})))
            mlog.log('Target machine cpu:', mlog.bold(intr.builtin['target_machine'].cpu_method([], {})))
        mlog.log('Build machine cpu family:', mlog.bold(intr.builtin['build_machine'].cpu_family_method([], {})))
        mlog.log('Build machine cpu:', mlog.bold(intr.builtin['build_machine'].cpu_method([], {})))
        if self.options.profile:
            fname = os.path.join(self.build_dir, 'meson-private', 'profile-interpreter.log')
            profile.runctx('intr.run()', globals(), locals(), filename=fname)
        else:
            intr.run()
        # Print all default option values that don't match the current value
        for def_opt_name, def_opt_value, cur_opt_value in intr.get_non_matching_default_options():
            mlog.log('Option', mlog.bold(def_opt_name), 'is:',
                     mlog.bold(str(cur_opt_value)),
                     '[default: {}]'.format(str(def_opt_value)))
        try:
            dumpfile = os.path.join(env.get_scratch_dir(), 'build.dat')
            # We would like to write coredata as late as possible since we use the existence of
            # this file to check if we generated the build file successfully. Since coredata
            # includes settings, the build files must depend on it and appear newer. However, due
            # to various kernel caches, we cannot guarantee that any time in Python is exactly in
            # sync with the time that gets applied to any files. Thus, we dump this file as late as
            # possible, but before build files, and if any error occurs, delete it.
            cdf = env.dump_coredata()
            if self.options.profile:
                fname = 'profile-{}-backend.log'.format(intr.backend.name)
                fname = os.path.join(self.build_dir, 'meson-private', fname)
                profile.runctx('intr.backend.generate(intr)', globals(), locals(), filename=fname)
            else:
                intr.backend.generate(intr)
            build.save(b, dumpfile)
            # Post-conf scripts must be run after writing coredata or else introspection fails.
            intr.backend.run_postconf_scripts()
        except:
            if 'cdf' in locals():
                old_cdf = cdf + '.prev'
                if os.path.exists(old_cdf):
                    os.replace(old_cdf, cdf)
                else:
                    os.unlink(cdf)
            raise

def run(options):
    coredata.parse_cmd_line_options(options)
    app = MesonApp(options)
    app.generate()
    return 0
