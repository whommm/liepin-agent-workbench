#!/usr/bin/env python3
import os
import subprocess

# 1. Create _models.py with exceptions and dataclasses from original file
original = subprocess.check_output(
    ['git', 'show', 'HEAD:liepin_agent/core/liepin_search_service.py'],
    encoding='utf-8'
)

lines = original.split('\n')
model_lines = []
for line in lines:
    if line.startswith('class LiepinSearchError') or line.startswith('class LiepinSearchCandidate') or line.startswith('@dataclass'):
        model_lines.append(line)
    elif model_lines and (line.startswith('class ') or line.startswith('class LiepinSearchService')):
        break
    elif model_lines:
        model_lines.append(line)

models_content = '"""Models and exceptions for Liepin search."""\n\n'
models_content += 'from __future__ import annotations\n\n'
models_content += 'from dataclasses import dataclass\n'
models_content += 'from typing import Dict, List, Optional, Tuple\n\n\n'
models_content += '\n'.join(model_lines)

with open('liepin_agent/core/search/_models.py', 'w', encoding='utf-8') as f:
    f.write(models_content)
print('Created _models.py')

# 2. Fix imports in all mixin files
mixin_dir = 'liepin_agent/core/search'
for fname in os.listdir(mixin_dir):
    if not fname.endswith('_mixin.py'):
        continue
    path = os.path.join(mixin_dir, fname)
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Add _models import if file references LiepinSearchCandidate etc.
    needs_import = False
    for name in ['LiepinSearchCandidate', 'LiepinSearchControls', 'LiepinFilterFieldSpec',
                 'LiepinSearchError', 'LiepinSearchPageChangedError', 'LiepinSearchNoResultsError']:
        if name in content:
            needs_import = True
            break

    if needs_import and 'from ._models import' not in content:
        # Insert after the playwright try/except block
        marker = 'logger = logging.getLogger(__name__)\n'
        if marker in content:
            import_block = '\nfrom ._models import (\n'
            import_block += '    LiepinSearchCandidate,\n'
            import_block += '    LiepinSearchControls,\n'
            import_block += '    LiepinFilterFieldSpec,\n'
            import_block += '    LiepinSearchError,\n'
            import_block += '    LiepinSearchPageChangedError,\n'
            import_block += '    LiepinSearchNoResultsError,\n'
            import_block += ')\n'
            content = content.replace(marker, marker + import_block)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f'Fixed imports in {fname}')

print('Done fixing mixins.')
