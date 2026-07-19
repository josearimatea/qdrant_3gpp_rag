"""Project paths used by the qdrant_3gpp_rag tooling.

Put the path to your local copy of the TSpec-LLM `3GPP-clean` tree here.

This path is relative to the `qdrant_3gpp_rag` package directory and is
convenient when running the scripts from the package workspace folder.
Adjust the string if you run the script from a different working directory.
"""

# Relative path from workspace/qdrant_3gpp_rag to the 3GPP-clean root.
# Update this if your dataset is located elsewhere.
DOCS_DIR = "../../Dataset/TSpec-LLM/3GPP-clean"

__all__ = ["DOCS_DIR"]
