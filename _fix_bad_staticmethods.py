import os

mixin_dirs = ['liepin_agent/core/search', 'liepin_agent/storage/repos']

for mixin_dir in mixin_dirs:
    for fname in sorted(os.listdir(mixin_dir)):
        if not fname.endswith('_mixin.py'):
            continue
        path = os.path.join(mixin_dir, fname)
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.strip() == '@staticmethod':
                # Look ahead to find the def line (might be after blank lines)
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith('def '):
                    j += 1
                if j < len(lines):
                    sig = lines[j].strip()
                    params_part = sig.split('(')[1].split(')')[0]
                    first_param = params_part.split(',')[0].strip().split(':')[0].split('=')[0].strip()
                    if first_param in ('self', 'cls'):
                        print(f'Removing bad @staticmethod from {fname}:{j+1} {sig}')
                        i += 1
                        continue
            new_lines.append(line)
            i += 1

        with open(path, 'w', encoding='utf-8') as f:
            f.write(''.join(new_lines))

print("Done fixing bad staticmethods.")
