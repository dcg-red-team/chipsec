#!/usr/bin/python
#CHIPSEC: Platform Security Assessment Framework
#Copyright (c) 2010-2016, Intel Corporation
#
#This program is free software; you can redistribute it and/or
#modify it under the terms of the GNU General Public License
#as published by the Free Software Foundation; Version 2.
#
#This program is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#GNU General Public License for more details.
#
#You should have received a copy of the GNU General Public License
#along with this program; if not, write to the Free Software
#Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
#Contact information:
#chipsec@intel.com
#


"""
Setup module to install chipsec package via setuptools
"""

import os
import platform
from setuptools import setup, find_packages, Extension
from distutils import log, dir_util
import subprocess
import shutil

from setuptools.command.install import install as _install
from distutils.command.build import build as _build
from setuptools.command.build_ext import build_ext as _build_ext

def long_description():
    return open("README").read()

def version():
    return open(os.path.join("chipsec", "VERSION")).read()

def package_files(directory):
    paths = []
    for (path, directories, filenames) in os.walk(directory):
        for filename in filenames:
            paths.append(os.path.join(path, filename))
    return paths

skip_driver_opt = [("skip-driver", None,
                    ("Do not build the Chipsec kernel driver. "
                     "Only available on Linux."))
]

class build_ext(_build_ext):
    user_options = _build_ext.user_options + skip_driver_opt
    boolean_options = _build_ext.boolean_options + ["skip-driver"]

    def initialize_options(self):
        _build_ext.initialize_options(self)
        self.skip_driver = None

    def finalize_options(self):
        _build_ext.finalize_options(self)
        # Get the value of the skip-driver parameter from the install command.
        self.set_undefined_options("install", ("skip_driver", "skip_driver"))
        self.set_undefined_options("build", ("skip_driver", "skip_driver"))

    def _build_linux_driver(self):
        log.info("building the linux driver")
        build_driver = os.path.join(self.real_build_lib, "drivers", "linux")
        ko_ext = os.path.join(build_driver, "chipsec.ko")
        # We copy the drivers extension to the build directory.
        self.copy_tree(os.path.join("drivers", "linux"), build_driver)
        # Run the makefile there.
        subprocess.check_output(["make", "-C", build_driver])
        # And copy the resulting .ko to the right place.
        # That is to the source directory if we are in "develop" mode,
        # otherwise to the helper subdirectory in the build directory.
        root_dst = "" if self.inplace else self.real_build_lib
        dst = os.path.join(root_dst, "chipsec", "helper", "linux")
        self.copy_file(ko_ext, dst)
        # Finally, we clean up the build directory.
        dir_util.remove_tree(os.path.join(self.real_build_lib, "drivers"))

    def _build_linux_compression(self):
        log.info("building compression executables")
        build_elf = os.path.join(self.real_build_lib, "chipsec_tools", "compression")
        elfs = ["Brotli","LzmaCompress","TianoCompress"]
        #copy the compression files to build directory
        self.copy_tree(os.path.join("chipsec_tools","compression"),build_elf)
        # Run the makefile there
        subprocess.check_output(["make", "-C", build_elf, "-f", "GNUmakefile"])
        # Copy the resulting elf files into the correct place
        root_dst = "" if self.inplace else self.real_build_lib
        dst = os.path.join(root_dst, "chipsec_tools", "compression", "bin")
        try:
            dir_util.remove_tree(dst)
        except:
            pass
        os.mkdir(dst)
        for elf in elfs:
            self.copy_file(os.path.join(build_elf,"bin",elf),dst)
        # Clean up the build directory
        dir_util.remove_tree(os.path.join(self.real_build_lib,"chipsec_tools"))

    def _build_darwin_driver(self):
        log.info("building the OSX driver")
        build_driver = os.path.join(self.real_build_lib, "drivers", "osx")
        xcodeproject = os.path.join(build_driver, "chipsec.xcodeproj")
        # We copy the drivers extension to the build directory.
        self.copy_tree(os.path.join("drivers", "osx"), build_driver)
        # Run the command line version of XCode there.
        subprocess.check_output(["xcodebuild", "-project", xcodeproject,
                                 "-target", "chipsec"])
        # And copy the resulting .kext (directory) to the right place.
        # That is to the source directory if we are in "develop" mode,
        # otherwise to the helper subdirectory in the build directory.
        root_dst = "" if self.inplace else self.real_build_lib
        dst = os.path.join(root_dst, "chipsec", "helper", "osx", "chipsec.kext")
        self.copy_tree(os.path.join(build_driver, "build", "Release", "chipsec.kext"), dst)
        # Finally, we clean up the build directory.
        dir_util.remove_tree(os.path.join(self.real_build_lib, "drivers"))

    def _build_darwin_compression(self):
        log.info("building compression executables")
        build_exe = os.path.join(self.real_build_lib, "chipsec_tools", "compression")
        exes = ["Brotli","LzmaCompress","TianoCompress"]
        #copy the compression files to build directory
        self.copy_tree(os.path.join("chipsec_tools","compression"),build_exe)
        #fix case issues
        try:
            subprocess.check_output(["mv", os.path.join(build_exe,"LZMA","SDK"), os.path.join(build_exe,"LZMA","Sdk")])
        except:
            pass
        # Run the makefile there
        subprocess.check_output(["make", "-C", build_exe, "-f", "GNUmakefile"])
        # Copy the resulting elf files into the correct place
        root_dst = "" if self.inplace else self.real_build_lib
        dst = os.path.join(root_dst, "chipsec_tools", "compression", "bin")
        try:
            dir_util.remove_tree(dst)
        except:
            pass
        os.mkdir(dst)
        for exe in exes:
            self.copy_file(os.path.join(build_exe,"bin",exe),dst)
        # Clean up the build directory
        dir_util.remove_tree(os.path.join(self.real_build_lib,"chipsec_tools"))

    def run(self):
        # First, we build the standard extensions.
        _build_ext.run(self)
        # Then, we build the driver if required.
        if not self.skip_driver:
            self.real_build_lib = os.path.realpath(self.build_lib)
            if platform.system().lower() == "linux":
                self._build_linux_driver()
                self._build_linux_compression()
            elif platform.system().lower() == "darwin":
                self._build_darwin_driver()
                self._build_darwin_compression()

    def get_source_files(self):
        files = _build_ext.get_source_files(self)
        if platform.system().lower() == "linux":
          files.extend(package_files(os.path.join("drivers", "linux")))
        return files

class install(_install):
    user_options = _install.user_options + skip_driver_opt
    boolean_options = _install.boolean_options + ["skip-driver"]

    def initialize_options(self):
        _install.initialize_options(self)
        self.skip_driver = None

class build(_build):
    user_options = _build.user_options + skip_driver_opt
    boolean_options = _build.boolean_options + ["skip-driver"]

    def initialize_options(self):
        _build.initialize_options(self)
        self.skip_driver = None

package_data = {
    # Include any configuration file.
    "": ["*.ini", "*.cfg", "*.json"],
    "chipsec": ["*VERSION", "WARNING.txt"],
    "chipsec.cfg": ["*.xml", "*.xsd"],
}
data_files = [("", ["chipsec-manual.pdf"])]
install_requires = []
extra_kw = {}

if platform.system().lower() == "windows":
    package_data["chipsec.helper.win"] = ['win7_amd64/*.sys']
    package_data["chipsec.helper.rwe"] = ['win7_amd64/*.sys']
    package_data["chipsec_tools.compression.bin"] = ['*']
    install_requires.append("pypiwin32")

elif platform.system().lower == "linux":
    package_data["chipsec_tools.compression.bin"] = ['*']

elif platform.system().lower() == "darwin":
    package_data["chipsec_tools.compression.bin"] = ['*']

setup(
    name = 'chipsec',
    version = version(),
    description = 'CHIPSEC: Platform Security Assessment Framework',
    author = 'CHIPSEC Team',
    author_email = 'chipsec@intel.com',
    url = 'https://github.com/chipsec/chipsec',
    download_url="https://github.com/chipsec/chipsec",
    license = 'GNU General Public License v2 (GPLv2)',
    platforms=['any'],
    long_description = long_description(),

    classifiers = [
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'License :: OSI Approved :: GNU General Public License v2 (GPLv2)',
        'Natural Language :: English',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: POSIX :: Linux',
        'Operating System :: MacOS :: MacOS X',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Topic :: Security',
        'Topic :: System :: Hardware'
    ],

    data_files = data_files,
    packages = find_packages(exclude=["tests.*", "tests"]),
    package_data = package_data,
    install_requires = install_requires,

    py_modules=['chipsec_main', 'chipsec_util'],
    entry_points = {
        'console_scripts': [
            'chipsec_util=chipsec_util:main',
            'chipsec_main=chipsec_main:main',
        ],
    },
    test_suite="tests",
    cmdclass = {
        'install': install,
        'build': build,
        'build_ext'   : build_ext,
    },
    **extra_kw
)
