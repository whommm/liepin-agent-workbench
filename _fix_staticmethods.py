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
            # Detect a top-level def inside a class (4-space indent)
            if line.startswith('    def ') and not line.startswith('        def '):
                # Check if previous non-empty line is a decorator
                prev = ''
                for j in range(len(new_lines) - 1, -1, -1):
                    if new_lines[j].strip():
                        prev = new_lines[j].strip()
                        break
                # Get first parameter
                sig = line.strip()
                params_part = sig.split('(')[1].split(')')[0]
                first_param = params_part.split(',')[0].strip().split(':')[0].split('=')[0].strip()

                if prev not in ['@staticmethod', '@classmethod', '@contextmanager'] and first_param not in ['self', 'cls']:
                    new_lines.append('    @staticmethod\n')
                    print(f'Added @staticmethod to {fname}:{i+1} {sig}')

            new_lines.append(line)
            i += 1

        with open(path, 'w', encoding='utf-8') as f:
            f.write(''.join(new_lines))

print("Done fixing staticmethods.")
