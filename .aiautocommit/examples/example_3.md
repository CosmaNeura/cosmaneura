## Example 3

```
diff --git a/app/routes/middleware/logging.py b/app/routes/middleware/logging.py
index 8f7a4e7..deb81ac 100644
--- a/app/routes/middleware/logging.py
+++ b/app/routes/middleware/logging.py
@@ -13,26 +13,15 @@ References:
 - https://github.com/tomwojcik/starlette-context
 """

-from time import perf_counter
-
 import sentry_sdk
-from decouple import config
 from fastapi import FastAPI
-from fastapi.middleware.trustedhost import TrustedHostMiddleware
 from starlette.middleware.base import RequestResponseEndpoint
-from starlette.middleware.cors import CORSMiddleware
-from starlette.middleware.sessions import SessionMiddleware
 from starlette.requests import Request
 from starlette.responses import Response
-from starlette.routing import Match, Mount
-from starlette.types import Scope
 from starlette_context import context, plugins
 from starlette_context.header_keys import HeaderKeys
 from starlette_context.middleware import RawContextMiddleware

-from app import log
-from app.environments import is_development
-

 def add_middleware(app: FastAPI):
     @app.middleware("http")
```

style: remove unused imports
