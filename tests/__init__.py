import os, tempfile

# Keys and settings stored by `ecadlib keys` / the setup page live in a per-user folder (ecadlib.userconfig); the tests
# use an empty one of their own, so a developer's stored keys never change a result.
os.environ["ECADLIB_HOME"] = tempfile.mkdtemp(prefix="ecadlib-test-home-")
