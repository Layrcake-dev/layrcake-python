"""Request and response types for every Layrcake API operation.

They are TypedDicts generated from the API's OpenAPI document, so responses
stay plain dicts at runtime while your editor and type checker know their
shape.

    from layrcake.types import FindLeadsResultData
"""

from ._generated.types import *  # noqa: F403
from ._generated.types import __all__ as __all__
