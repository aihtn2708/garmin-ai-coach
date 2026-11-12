"""Start the mock servers for the JavaScript tests. Prints their URLs as one
JSON line, then runs until stdin closes.  Usage: python -m tests.serve_mocks [model_port]"""

import json
import sys

from . import mocks

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    _, garmin = mocks.start(mocks.GarminMock)
    _, openai = mocks.start(mocks.OpenAIMock, port)
    _, anthropic = mocks.start(mocks.AnthropicMock)
    print(json.dumps({"garmin": garmin, "openai": openai, "anthropic": anthropic}), flush=True)
    sys.stdin.read()
