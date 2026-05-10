import os

# Methods that should NOT be @staticmethod
not_static = {
    '_base_mixin.py': ['_with_debug_snapshot'],
    '_executor_mixin.py': [],  # already fixed
    '_extraction_mixin.py': ['_extract_candidates_with_dom_fallback'],
    '_filters_mixin.py': [
        '_apply_filter_with_retries', '_apply_tag_filter', '_apply_dropdown_filter',
        '_apply_range_filter', '_apply_city_filter', '_apply_autocomplete_filter',
        '_apply_single_city_filter', '_normalize_tag_filter_value',
        '_wait_for_filter_apply', '_wait_for_city_modal_closed',
    ],
    '_pagination_mixin.py': ['_wait_for_page_change'],
    '_candidate_mixin.py': ['list_candidates', 'save_candidate_source'],
    '_criteria_mixin.py': ['create_criteria_version', 'update_criteria_version', 'get_latest_criteria_version'],
    '_event_mixin.py': ['add_event', 'save_decision'],
    '_match_mixin.py': ['list_match_results'],
    '_round_mixin.py': ['create_round', 'update_round'],
    '_session_mixin.py': ['create_session', 'update_session_status'],
}

for mixin_dir in ['liepin_agent/core/search', 'liepin_agent/storage/repos']:
    for fname, names in not_static.items():
        path = os.path.join(mixin_dir, fname)
        if not os.path.exists(path):
            continue
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        remove_indices = []
        for i, line in enumerate(lines):
            if line.strip() == '@staticmethod':
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith('def '):
                    j += 1
                if j < len(lines):
                    name = lines[j].strip().split('(')[0].replace('def ', '')
                    if name in names:
                        remove_indices.append(i)

        for idx in reversed(remove_indices):
            del lines[idx]

        with open(path, 'w', encoding='utf-8') as f:
            f.write(''.join(lines))
        if remove_indices:
            print(f'Fixed {fname}: removed {len(remove_indices)} wrong @staticmethod')

print("Done.")
