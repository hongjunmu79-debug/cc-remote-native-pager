"""cc-remote packaging tooling.

A regular package (not a namespace package) so that when the repository root is
on ``sys.path`` this directory resolves here rather than being shadowed by the
installed ``packaging`` distribution (pytest/setuptools dependency). The
Windows distribution lives in ``packaging.windows``.
"""
