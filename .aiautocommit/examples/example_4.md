## Example 4

```
diff --git a/pyproject.toml b/pyproject.toml
index 75fbb83..b2da683 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -181,3 +181,6 @@ rev = "github-output"
 [tool.uv.sources.pytest-playwright-visual]
 git = "https://github.com/iloveitaly/pytest-playwright-visual.git"
 rev = "global-defaults"
+
+[tool.uv.sources.starlette_context]
+git = "https://github.com/tomwojcik/starlette-context.git"
```

build: switch to git version of starlette_context
