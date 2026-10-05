import os
import shutil
import sys
from pathlib import Path


source, destination = map(Path, sys.argv[1:])
for entry in source.iterdir():
    target = destination / entry.name
    if entry.name in ('Documents', 'Downloads') or os.path.lexists(target):
        continue
    if entry.is_symlink():
        target.symlink_to(os.readlink(entry))
    elif entry.is_dir():
        shutil.copytree(entry, target, symlinks=True)
    else:
        shutil.copy2(entry, target)
