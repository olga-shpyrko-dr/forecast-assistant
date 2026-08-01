#!/usr/bin/env bash

# DataRobot containers run in FIPS mode, which blocks hashlib.blake2b(usedforsecurity=True).
# Streamlit's polling file watcher calls blake2b on startup before any config is applied.
# Patch blake2b to pass usedforsecurity=False before Streamlit is imported.
python3 -c "
import hashlib
_orig = hashlib.blake2b
hashlib.blake2b = lambda *a, **kw: _orig(*a, **{**kw, 'usedforsecurity': False})

import sys
sys.argv = ['streamlit', 'run', 'app.py']
from streamlit.web.cli import main
main()
"
