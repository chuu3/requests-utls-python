"""Setuptools command extensions; build releases with ``python -m build``."""

from setuptools import setup

from _build_support import BundledBuildPy, BundledDistribution, BundledWheel


setup(
    distclass=BundledDistribution,
    cmdclass={"build_py": BundledBuildPy, "bdist_wheel": BundledWheel},
)
