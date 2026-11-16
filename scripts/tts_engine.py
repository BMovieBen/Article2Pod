# tts_engine.py
#
# Article2Pod supports one TTS engine at a time, chosen by what is in the
# audio workflow (config "workflow_file", normally workflow/workflow-api.json):
#   - VibeVoice nodes      -> the original VibeVoice path
#   - Breeze TTS 2 nodes   -> the Breeze path (breeze_audio.py)
# To switch, copy workflow/workflow-api.vibevoice.sample.json or
# workflow/workflow-api.breeze.sample.json over workflow-api.json.

import json

VIBEVOICE = 'vibevoice'
BREEZE    = 'breeze'


def load_workflow_file(path):
    """Load an API-format workflow, dropping non-node entries such as the
    "_readme" note in the shipped samples (ComfyUI would reject them)."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if isinstance(v, dict) and 'class_type' in v}


def detect_engine(workflow):
    """Return VIBEVOICE, BREEZE or None for an already-loaded workflow."""
    types = {node.get('class_type', '') for node in workflow.values()}
    if any(t.startswith('BreezeTTS2') for t in types):
        return BREEZE
    if any(t.startswith('VibeVoice') for t in types):
        return VIBEVOICE
    return None


def find_node(workflow, class_type):
    """(node_id, node) of the first node with this class_type, else (None, None)."""
    for node_id, node in workflow.items():
        if node.get('class_type') == class_type:
            return node_id, node
    return None, None
