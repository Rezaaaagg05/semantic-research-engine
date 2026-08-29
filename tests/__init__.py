"""Test package.

Making ``tests`` a package means pytest imports every module here as
``tests.test_x``, so a test module can safely share a basename with one of the
legacy exploration scripts in the repository root without an import collision.
"""
