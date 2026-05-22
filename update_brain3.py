path = r'D:\liepin-agent-workbench\liepin_agent\agent\brain.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find LLMAgentBrain section
class_idx = content.find('class LLMAgentBrain')
print('LLMAgentBrain at', class_idx)

# Find build_criteria after class_idx
bc_idx = content.find('def build_criteria', class_idx)
print('build_criteria after class at', bc_idx)
print(repr(content[bc_idx:bc_idx+500]))

# Find observe_round after class_idx
ob_idx = content.find('def observe_round', class_idx)
print('observe_round after class at', ob_idx)
print(repr(content[ob_idx:ob_idx+200]))

# Find decide_fetch after class_idx
df_idx = content.find('def decide_fetch', class_idx)
print('decide_fetch after class at', df_idx)
print(repr(content[df_idx:df_idx+200]))
