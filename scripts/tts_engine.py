# tts_engine.py
#
# Each TTS engine has its own audio workflow (config "workflow_file_vibevoice"
# / "workflow_file_breeze"); which one a job uses is decided by
# utils.resolve_engine. detect_engine() checks that a workflow really
# contains that engine's nodes.

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
