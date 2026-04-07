"""
For celery to properly pick up on all tasks they must all be imported when celery starts.

It's easier for us to auto-import all of them here, so Celery just needs to import app.jobs
"""

import importlib
from pathlib import Path

from app import log
from app.setup import modules_in_folder

for module_name in modules_in_folder(Path(__file__).parent, __package__):
    log.debug("auto importing", job=module_name)
    try:
        module = importlib.import_module(module_name)
    except Exception:
        log.warning("failed to import job module", job=module_name, exc_info=True)
        continue

    # validate that the module has a 'perform' method, this is a convention in this folder
    if not hasattr(module, "perform") or not callable(getattr(module, "perform")):
        log.warning("skipping module without perform", job=module_name)
        continue
