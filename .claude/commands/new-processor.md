Scaffold a new middleware processor. The user will provide a name; if not given, ask for one.

Steps:
1. Determine the processor name from the user's request (e.g. "PaceAnalyticsProcessor").
2. Create the file `fit_pipeline/middleware/<snake_case_name>.py` using this template:

```python
"""<One-line description of what this processor does>."""

import logging
from typing import Any

from fit_pipeline.config import Config
from fit_pipeline.processor import Processor

logger = logging.getLogger(__name__)


class <ProcessorName>(Processor):
    """<Docstring describing purpose, inputs consumed, and outputs added to data dict>."""

    def process(self, data: dict[str, Any]) -> dict[str, Any]:
        """Run the processor.

        Args:
            data: Pipeline data dict. Reads from data["activity"] and data["streams"].

        Returns:
            Updated data dict with new fields added to data["computed_metrics"].
        """
        logger.debug("Running <ProcessorName>")

        activity = data.get("activity", {})
        streams = data.get("streams", {})
        metrics = data.get("computed_metrics", {})

        # TODO: implement

        data["computed_metrics"] = metrics
        return data
```

3. Remind the user to add the new class to `processors.py` in the `PROCESSOR_CHAIN` list.
4. Remind the user to create a corresponding test file at `tests/test_<snake_case_name>.py`.
