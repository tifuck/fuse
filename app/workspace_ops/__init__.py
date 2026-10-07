"""Responsibility modules extracted from the workspace facade
(app/workspace.py): shared primitives, search command builders, search
output rendering, previews/diffs, the project instruction cache, and
remote transport scripts. There is no re-export surface here on purpose:
the facade (app.workspace) is the stable import; new code may import the
owning module directly.
"""
