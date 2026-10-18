"""
The ONE embedding model of the 3GPP corpus and of every sibling repository.

A query must be embedded by the same model as the chunks it is compared with
(768 != 384 dimensions), so siblings import these names instead of pinning their
own. This module imports nothing heavy: a sibling's settings can read the model name
without loading torch (the package __init__ is lazy too).

bge-base-en-v1.5 replaces all-MiniLM-L6-v2: it reads 512 tokens (MiniLM read ~256
and ignored the rest of a chunk) and is strong on technical English.
"""

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
DEFAULT_EMBEDDING_DIM = 768
# Instruction the model was trained to see before a QUERY (never before a passage).
QUERY_PREFIXES = {
    "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
}


def default_device() -> str:
    """'cuda' when a GPU is available, else 'cpu' (imports torch only when called)."""
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"
