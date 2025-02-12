## Example 6

```
diff --git a/app/configuration/sentry.py b/app/configuration/sentry.py
index 11176f7..c16b302 100644
--- a/app/configuration/sentry.py
+++ b/app/configuration/sentry.py
@@ -1,10 +1,14 @@
 import sentry_sdk
+import sentry_sdk.integrations
 from decouple import config
+from posthog.sentry.posthog_integration import PostHogIntegration

 from ..environments import is_job_monitor, is_production, python_environment


 def configure_sentry():
+    from app import log
+
     if not is_production():
         return

@@ -37,7 +41,9 @@ def configure_sentry():
         environment=python_environment(),
         enable_tracing=True,
         traces_sample_rate=0.1,
-        # integrations=[FlaskIntegration(),],
+        # posthog integration is not a standard integration included with Sentry
+        # https://docs.sentry.io/platforms/python/integrations/
+        integrations=[PostHogIntegration()],
         before_send_transaction=filter_transactions,
         _experiments={
             # Set continuous_profiling_auto_start to True
@@ -46,3 +52,8 @@ def configure_sentry():
             "continuous_profiling_auto_start": True,
         },
     )
+
+    log.info(
+        "sentry configured",
+        integrations=sentry_sdk.integrations._installed_integrations,
+    )
```

feat: add Posthog Sentry configuration
