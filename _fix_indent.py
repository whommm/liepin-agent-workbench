import os

mixin_dir = 'liepin_agent/core/search'
for fname in os.listdir(mixin_dir):
    if not fname.endswith('_mixin.py'):
        continue
    path = os.path.join(mixin_dir, fname)
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    class_found = False
    for line in lines:
        if line.startswith('class '):
            class_found = True
            new_lines.append(line)
            continue
        if class_found and line.strip().startswith('"""') and line.startswith('        '):
            new_lines.append('    ' + line.lstrip())
            continue
        new_lines.append(line)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(''.join(new_lines))
    print(f'Fixed docstring in {fname}')
