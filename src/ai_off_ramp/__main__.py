"""Allow running as: python -m ai_off_ramp --config path/to/config.yaml"""

import sys

from .server import main

sys.exit(main())
