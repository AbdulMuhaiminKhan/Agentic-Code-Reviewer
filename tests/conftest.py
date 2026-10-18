import os

# The static scanner shells out to Bandit once per file (~1 s). Graph tests don't need it, so it is off by
# default here; the scanner's own tests switch it on explicitly.
os.environ.setdefault("ACR_SCANNER", "0")
