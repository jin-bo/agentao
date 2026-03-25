"""Main entry point for Agentao."""

import warnings
warnings.filterwarnings("ignore", message="urllib3.*or chardet.*doesn't match")

from agentao.cli import entrypoint

if __name__ == "__main__":
    entrypoint()
